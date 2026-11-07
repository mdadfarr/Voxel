"""Small helpers shared by the single-call LLM steps (plan, review)."""

from __future__ import annotations

import base64
import mimetypes
import re
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


class ReplyError(Exception):
    """No usable reply after the retry. Carries what the calls cost."""

    def __init__(self, message: str, cost_usd: float, attempts: int) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.attempts = attempts


def call_with_retry(
    complete: Callable[..., Any],
    *,
    model: str,
    messages: list[dict],
    parse: Callable[[str], Any],
    max_tokens: int,
    what: str,
) -> tuple[Any, float, int]:
    """One call; if `parse` raises ValueError, retry once with the error.

    Returns (parsed value, total cost, attempts). Raises ReplyError after 2 failures.
    """
    messages = list(messages)
    cost, error = 0.0, ""
    for attempt in (1, 2):
        response = complete(model=model, messages=messages, max_tokens=max_tokens)
        cost += response_cost(response)
        text = response_text(response)
        try:
            return parse(text), cost, attempt
        except ValueError as err:
            error = str(err)
        messages += [
            {"role": "assistant", "content": text or "(empty reply)"},
            {
                "role": "user",
                "content": f"Your {what} was rejected: {error}\n\nReply with the corrected JSON object only.",
            },
        ]
    raise ReplyError(f"No usable {what} after 2 attempts. Last error: {error}", cost, 2)


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
