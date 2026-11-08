"""Small helpers shared by the single-call LLM steps (expand, plan, review)."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


def default_complete() -> Callable[..., Any]:
    import litellm

    return litellm.completion


def response_text(response: Any) -> str:
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        return ""


def finish_reason(response: Any) -> str:
    try:
        return response.choices[0].finish_reason or ""
    except (AttributeError, IndexError):
        return ""


def response_cost(response: Any) -> float:
    """litellm puts the call's cost on response._hidden_params['response_cost']."""
    hidden = getattr(response, "_hidden_params", None) or {}
    cost = hidden.get("response_cost") if isinstance(hidden, dict) else None
    return float(cost) if isinstance(cost, (int, float)) and cost >= 0 else 0.0


def _tokens(response: Any, name: str) -> int:
    value = getattr(getattr(response, "usage", None), name, None)
    return value if isinstance(value, int) and value >= 0 else 0


@dataclass(frozen=True)
class Usage:
    """Tokens and cost of one or more calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cost_usd + other.cost_usd,
        )

    @classmethod
    def of(cls, response: Any) -> Usage:
        """From a litellm response: usage.prompt_tokens / completion_tokens, and the cost."""
        return cls(_tokens(response, "prompt_tokens"), _tokens(response, "completion_tokens"), response_cost(response))


class ReplyError(Exception):
    """No usable reply after the retry. Carries what the calls used."""

    def __init__(self, message: str, usage: Usage, attempts: int) -> None:
        super().__init__(message)
        self.usage = usage
        self.cost_usd = usage.cost_usd
        self.attempts = attempts


class StepError(Exception):
    """A step (expand, plan, review) got no usable reply. Carries what it used."""

    def __init__(
        self, message: str, cost_usd: float, attempts: int, input_tokens: int = 0, output_tokens: int = 0
    ) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.attempts = attempts
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @property
    def usage(self) -> Usage:
        return Usage(self.input_tokens, self.output_tokens, self.cost_usd)

    @classmethod
    def from_reply(cls, err: ReplyError):
        u = err.usage
        return cls(str(err), u.cost_usd, err.attempts, u.input_tokens, u.output_tokens)


class HasUsage:
    """For result dataclasses with cost_usd, input_tokens and output_tokens fields."""

    @property
    def usage(self) -> Usage:
        return Usage(self.input_tokens, self.output_tokens, self.cost_usd)  # type: ignore[attr-defined]


class CutOff(ValueError):
    """The reply hit the output-token limit, so its JSON is incomplete."""


# --- API errors: retry what may pass, stop cleanly on the rest -----------------

DEFAULT_TIMEOUT = 180.0  # seconds per call; the plan and revise calls get longer
RETRY_WAITS = (5.0, 15.0)  # seconds before retry 1 and retry 2
CREDIT_TEXT = "credit balance is too low"
sleep = time.sleep  # tests replace this


class CallFailed(Exception):
    """An API call failed for good: still failing after the retries, or no credit.
    `reason` is one line for the user; `usage` is what the step used before it."""

    def __init__(self, reason: str, usage: Usage = Usage()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage
        self.cost_usd = usage.cost_usd


def _api_error_types() -> tuple[type, tuple[type, ...]]:
    """(base of every API error, the transient ones worth a retry)."""
    import litellm
    import openai

    transient = (
        litellm.Timeout,
        litellm.APIConnectionError,
        litellm.RateLimitError,
        litellm.InternalServerError,
        litellm.ServiceUnavailableError,
    )
    return openai.APIError, transient


def is_credit_error(err: BaseException) -> bool:
    return CREDIT_TEXT in str(err).lower()


def _short(err: BaseException) -> str:
    text = str(err).strip().splitlines()[0] if str(err).strip() else ""
    return f"{type(err).__name__}: {text[:200]}" if text else type(err).__name__


def call_api(complete: Callable[..., Any], what: str, **kwargs: Any) -> Any:
    """complete(**kwargs), retried up to len(RETRY_WAITS) times on timeouts, rate
    limits, connection errors and server errors (5xx), with a wait before each retry.

    Raises CallFailed when the call still fails after the retries, when the API
    says the credit balance is too low (not retried), or on any other API error
    (not retried). Other exceptions (bugs) pass through unchanged.
    """
    api_error, transient = _api_error_types()
    attempts = len(RETRY_WAITS) + 1
    for attempt in range(1, attempts + 1):
        try:
            return complete(**kwargs)
        except api_error as err:
            if is_credit_error(err):
                raise CallFailed(f"the API says the credit balance is too low ({what} call)") from err
            retryable = isinstance(err, transient) or (getattr(err, "status_code", 0) or 0) >= 500
            if not retryable:
                raise CallFailed(f"the {what} call failed: {_short(err)}") from err
            if attempt == attempts:
                import litellm

                if isinstance(err, litellm.Timeout):
                    limit = kwargs.get("timeout")
                    each = f" ({limit:g} s each)" if isinstance(limit, (int, float)) else ""
                    raise CallFailed(f"the {what} call timed out {attempts} times{each}") from err
                raise CallFailed(f"the {what} call failed {attempts} times: {_short(err)}") from err
            wait = RETRY_WAITS[attempt - 1]
            print(
                f"Warning: the {what} call failed ({type(err).__name__}); "
                f"retry {attempt} of {len(RETRY_WAITS)} in {wait:g} s",
                file=sys.stderr,
            )
            sleep(wait)
    raise AssertionError("unreachable")


def call_with_retry(
    complete: Callable[..., Any],
    *,
    model: str,
    messages: list[dict],
    parse: Callable[[str], Any],
    max_tokens: int,
    what: str,
    schema: type[BaseModel] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[Any, Usage, int]:
    """One call; if `parse` raises ValueError, retry once with the error.

    With a schema (a Pydantic model), the call asks for structured output
    (response_format): litellm sends it as Anthropic's output_format, so the
    reply is JSON in the schema's shape. Constraints the API cannot enforce
    (lengths, minimums, validators) are still checked by `parse`. A reply cut off
    at max_tokens is reported as such, since its JSON is incomplete.

    Each call goes through call_api with `timeout` seconds: API errors are
    retried there, and a call that fails for good raises CallFailed carrying the
    usage of the attempts before it.

    Returns (parsed value, usage of all attempts, attempts). Raises ReplyError
    after 2 unusable replies.
    """
    messages = list(messages)
    extra = {"response_format": schema} if schema is not None else {}
    usage, error = Usage(), ""
    for attempt in (1, 2):
        try:
            response = call_api(
                complete, what, model=model, messages=messages, max_tokens=max_tokens, timeout=timeout, **extra
            )
        except CallFailed as err:
            raise CallFailed(err.reason, usage) from err.__cause__
        usage += Usage.of(response)
        text = response_text(response)
        try:
            if finish_reason(response) == "length":
                raise CutOff(
                    f"the reply was cut off at the {max_tokens}-token output limit, so the JSON "
                    "is incomplete. Write it more concisely (shorter names and reasons)"
                )
            return parse(text), usage, attempt
        except ValueError as err:
            error = str(err)
        messages += [
            {"role": "assistant", "content": text or "(empty reply)"},
            {
                "role": "user",
                "content": f"Your {what} was rejected: {error}\n\nReply with the corrected JSON object only.",
            },
        ]
    raise ReplyError(f"No usable {what} after 2 attempts. Last error: {error}", usage, 2)


def load_json(text: str) -> Any:
    """json.loads after strip_fences. A ValueError names the exact error position."""
    text = strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        near = text[max(0, err.pos - 40) : err.pos + 40]
        raise ValueError(
            f"the reply is not valid JSON: {err.msg} at line {err.lineno} column {err.colno} "
            f"(character {err.pos} of {len(text)}), near: {near!r}"
        ) from None


def image_part(image_path: str | Path) -> dict:
    """An image as a message content part (base64 data URL)."""
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def strip_fences(text: str) -> str:
    """Drop a ```json ... ``` wrapper around a reply, if there is one."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text
