"""Small helpers shared by the single-call LLM steps (expand, plan, review)."""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def default_complete() -> Callable[..., Any]:
    import litellm

    return litellm.completion


def response_text(response: Any) -> str:
    try:
        return response.choices[0].message.content or ""
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


def call_with_retry(
    complete: Callable[..., Any],
    *,
    model: str,
    messages: list[dict],
    parse: Callable[[str], Any],
    max_tokens: int,
    what: str,
) -> tuple[Any, Usage, int]:
    """One call; if `parse` raises ValueError, retry once with the error.

    Returns (parsed value, usage of all attempts, attempts). Raises ReplyError
    after 2 failures.
    """
    messages = list(messages)
    usage, error = Usage(), ""
    for attempt in (1, 2):
        response = complete(model=model, messages=messages, max_tokens=max_tokens)
        usage += Usage.of(response)
        text = response_text(response)
        try:
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
