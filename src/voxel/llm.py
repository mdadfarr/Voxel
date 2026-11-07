"""Small helpers shared by the single-call LLM steps (plan, review)."""

from __future__ import annotations

import re
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


def strip_fences(text: str) -> str:
    """Drop a ```json ... ``` wrapper around a reply, if there is one."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text
