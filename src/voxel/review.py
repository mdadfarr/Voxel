"""Review a finished build against the plan's checklist, from its render.

One LLM call (no tools, no loop) with the rendered image attached. For each
checklist item in the plan it answers pass/fail with a short reason. The first
item ("looks like ...") is judged from the image alone. If the reply is
malformed, the call is retried once with the error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import Field, ValidationError, field_validator

from .llm import default_complete, image_part, response_cost, response_text, strip_fences
from .plan import Plan, StrictModel

DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
MAX_TOKENS = 4096


class Verdict(StrictModel):
    index: int = Field(ge=1, description="1-based position in the checklist")
    item: str
    passed: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def _has_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be empty")
        return value


class Review(StrictModel):
    verdicts: list[Verdict] = Field(min_length=1)

    def failed(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.passed]

    @property
    def passed(self) -> bool:
        return not self.failed()


def review_errors(review: Review, plan: Plan) -> list[str]:
    """Differences between the verdicts and the plan's checklist (count, order, text)."""
    checklist = plan.checklist
    if len(review.verdicts) != len(checklist):
        return [f"expected {len(checklist)} verdicts, one per checklist item, got {len(review.verdicts)}"]
    errors = []
    for n, (verdict, item) in enumerate(zip(review.verdicts, checklist), start=1):
        if verdict.index != n:
            errors.append(f"verdict {n} has index {verdict.index}, expected {n}")
        if verdict.item.strip() != item.strip():
            errors.append(f"verdict {n} item is {verdict.item!r}, expected {item!r}")
    return errors


def review_report(review: Review) -> str:
    """The failed items and reasons, as feedback for the builder."""
    return "\n".join(f"- {v.item}: {v.reason}" for v in review.failed())


class ReviewError(Exception):
    """The reviewer did not produce a usable review, even after one retry."""

    def __init__(self, message: str, cost_usd: float, attempts: int) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.attempts = attempts


@dataclass(frozen=True)
class ReviewResult:
    review: Review
    cost_usd: float
    attempts: int


def review_prompt(plan: Plan, expansion: str | None) -> str:
    checklist = "\n".join(f"{n}. {item}" for n, item in enumerate(plan.checklist, start=1))
    shape = {
        "verdicts": [
            {"index": 1, "item": plan.checklist[0], "passed": True, "reason": "short reason"},
        ]
    }
    context = f"Detailed description of what was asked for:\n{expansion}\n\n" if expansion else ""
    return (
        "You review a finished LEGO build. The attached image is its render: front, "
        "side, top and isometric views of the same build.\n\n"
        f"{context}"
        f"The plan the builder followed:\n{plan.model_dump_json(indent=2)}\n\n"
        f"Checklist:\n{checklist}\n\n"
        "For EVERY checklist item, in order, decide pass or fail and give a short reason.\n"
        "- Item 1 (\"looks like ...\") must be judged from the image alone: would a person "
        "who sees the render, without reading the plan, recognize the thing? Be strict.\n"
        "- Judge the other items from the image, using the plan only to know what to look for.\n"
        "- Copy each item's text exactly into \"item\".\n\n"
        "Reply with ONE JSON object and nothing else, in this shape (one verdict per item):\n"
        f"{json.dumps(shape, indent=2)}"
    )


def parse_review(text: str, plan: Plan) -> Review:
    """Parse and check a review. Raises ValueError with a message meant for the reviewer."""
    try:
        data = json.loads(strip_fences(text))
    except json.JSONDecodeError as err:
        raise ValueError(f"the reply is not valid JSON: {err}") from None
    try:
        result = Review.model_validate(data)
    except ValidationError as err:
        raise ValueError(f"the JSON does not match the review shape:\n{err}") from None
    errors = review_errors(result, plan)
    if errors:
        raise ValueError("the verdicts do not match the checklist:\n" + "\n".join(f"- {e}" for e in errors))
    return result


def review(
    plan: Plan,
    expansion: str | None,
    image_path: str | Path,
    *,
    model: str = DEFAULT_REVIEW_MODEL,
    complete: Callable[..., Any] | None = None,
) -> ReviewResult:
    """Judge the rendered build against the plan's checklist. Retries once if the reply is unusable."""
    complete = complete or default_complete()
    content = [{"type": "text", "text": review_prompt(plan, expansion)}, image_part(image_path)]
    messages: list[dict] = [{"role": "user", "content": content}]
    cost, error = 0.0, ""
    for attempt in (1, 2):
        response = complete(model=model, messages=messages, max_tokens=MAX_TOKENS)
        cost += response_cost(response)
        text = response_text(response)
        try:
            return ReviewResult(parse_review(text, plan), cost, attempt)
        except ValueError as err:
            error = str(err)
        messages += [
            {"role": "assistant", "content": text or "(empty reply)"},
            {
                "role": "user",
                "content": f"Your review was rejected: {error}\n\nReply with the corrected JSON object only.",
            },
        ]
    raise ReviewError(f"No usable review after 2 attempts. Last error: {error}", cost, 2)
