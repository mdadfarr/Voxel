"""Review a finished build against the plan's checklist, from its render.

With --no-plan there is no plan: the reviewer gets the fixed Checklist
(fixed_checklist: "looks like ..." plus four general quality items) and the
request with its answers, and judges the render against it the same way. Its
pass rule (review_passes) is looser than a plan's: items 1 and 4 must pass, and
at most one other item may fail.

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

from .llm import HasUsage, ReplyError, StepError, call_with_retry, default_complete, image_part, load_json
from .plan import ORIENTATION, Plan, StrictModel

DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8192


class Checklist(StrictModel):
    """What the reviewer judges when there is no plan (--no-plan): the checklist alone.

    The pass rule: every item in must_pass passes, and at most max_failed of the
    other items fail. The defaults (none named, 0) mean every item must pass."""

    checklist: list[str] = Field(min_length=1)
    must_pass: list[int] = Field(default_factory=list, description="1-based item numbers")
    max_failed: int = Field(default=0, ge=0, description="other items allowed to fail")


NO_PLAN_ITEMS = (
    "colors make sense: each part one clear color, no random stripes",
    "proportions are believable",
    "clean shape: outline reads clearly, no stray or sticking-out pieces",
    "the model is in its natural display pose and its main features face the viewer",
)


def fixed_checklist(request: str) -> Checklist:
    """The same checklist for every --no-plan build. The review fails if item 1
    (looks like it) or item 4 (clean shape) fails, or 2 or more of items 2, 3
    and 5 fail."""
    first = f'looks like what was asked for ("{request.strip()}") to someone who only sees the picture'
    return Checklist(checklist=[first, *NO_PLAN_ITEMS], must_pass=[1, 4], max_failed=1)


def review_passes(review: "Review", plan: "Plan | Checklist") -> bool:
    """The review's result under the checklist's pass rule. A plan's checklist:
    every item must pass."""
    if isinstance(plan, Plan):
        return review.passed
    failed = {v.index for v in review.failed()}
    must = set(plan.must_pass)
    return not (failed & must) and len(failed - must) <= plan.max_failed


def pass_rule(plan: "Plan | Checklist") -> str:
    """The pass rule in words, for the summary and reviews.json."""
    if isinstance(plan, Plan) or (not plan.must_pass and plan.max_failed == 0):
        return "every item must pass"
    numbers = [str(n) for n in plan.must_pass]
    must = numbers[0] if len(numbers) == 1 else ", ".join(numbers[:-1]) + f" and {numbers[-1]}"
    label = "item" if len(numbers) == 1 else "items"
    return f"{label} {must} must pass, at most {plan.max_failed} other(s) may fail"


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


def review_errors(review: Review, plan: Plan | Checklist) -> list[str]:
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


class ReviewError(StepError):
    """The reviewer did not produce a usable review, even after one retry."""


@dataclass(frozen=True)
class ReviewResult(HasUsage):
    review: Review
    cost_usd: float
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0


def review_prompt(plan: Plan | Checklist, expansion: str | None) -> str:
    checklist = "\n".join(f"{n}. {item}" for n, item in enumerate(plan.checklist, start=1))
    shape = {
        "verdicts": [
            {"index": 1, "item": plan.checklist[0], "passed": True, "reason": "short reason"},
        ]
    }
    label = "Detailed description of what was asked for" if isinstance(plan, Plan) else "What was asked for"
    context = f"{label}:\n{expansion}\n\n" if expansion else ""
    if isinstance(plan, Plan):
        context += f"The plan the builder followed:\n{plan.model_dump_json(indent=2)}\n\n"
    return (
        "You review a finished LEGO build. The attached image is its render: four views "
        "of the same build in a 2x2 grid, front (top left), side (top right), top "
        "(bottom left) and isometric (bottom right).\n"
        f"{ORIENTATION}\n\n"
        f"{context}"
        f"Checklist:\n{checklist}\n\n"
        "For EVERY checklist item, in order, decide pass or fail and give a short reason.\n"
        "- Item 1 (\"looks like ...\") must be judged from the image alone: would a person "
        "who sees the render, without reading anything else, recognize the thing? Be strict.\n"
        "- Judge the other items from the image, using the description and plan only to know "
        "what to look for.\n"
        "- Copy each item's text exactly into \"item\".\n\n"
        "Reply with ONE JSON object and nothing else, in this shape (one verdict per item):\n"
        f"{json.dumps(shape, indent=2)}"
    )


def parse_review(text: str, plan: Plan | Checklist) -> Review:
    """Parse and check a review. Raises ValueError with a message meant for the reviewer."""
    data = load_json(text)
    try:
        result = Review.model_validate(data)
    except ValidationError as err:
        raise ValueError(f"the JSON does not match the review shape:\n{err}") from None
    errors = review_errors(result, plan)
    if errors:
        raise ValueError("the verdicts do not match the checklist:\n" + "\n".join(f"- {e}" for e in errors))
    return result


def review(
    plan: Plan | Checklist,
    expansion: str | None,
    image_path: str | Path,
    *,
    model: str = DEFAULT_REVIEW_MODEL,
    complete: Callable[..., Any] | None = None,
) -> ReviewResult:
    """Judge the rendered build against the plan's checklist. Retries once if the reply is unusable."""
    content = [{"type": "text", "text": review_prompt(plan, expansion)}, image_part(image_path)]
    try:
        result, usage, attempts = call_with_retry(
            complete or default_complete(),
            model=model,
            messages=[{"role": "user", "content": content}],
            parse=lambda text: parse_review(text, plan),
            max_tokens=MAX_TOKENS,
            what="review",
            schema=Review,
        )
    except ReplyError as err:
        raise ReviewError.from_reply(err) from None
    return ReviewResult(result, usage.cost_usd, attempts, usage.input_tokens, usage.output_tokens)
