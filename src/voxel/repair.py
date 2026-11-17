"""One repair round for --no-plan builds, in the builder's own conversation.

BrickWright's critique loop calls review_hook (named by BRICKWRIGHT_REVIEW_HOOK)
after the builder finalizes and the build is rendered. The hook:

1. runs Voxel's gate on build.py; if it fails there is nothing to review (the
   normal gate retry handles it);
2. reviews the render turned by build.py's FACE and UP (voxel.display) against
   the fixed checklist (voxel.review), if the review fits in what is left of the
   budget; the reviewed image is kept as out/review.before_repair.png;
3. passed: done. Failed, a repair is left and the budget still has
   REPAIR_RESERVE after the review: backs up build.py as
   out/build.before_repair.py and returns the failed items as feedback.
   BrickWright appends it (with its own render, as built, not turned) to the
   same conversation and runs another builder pass, which edits build.py,
   validates and finalizes again. The feedback states FACE and UP, so the
   builder can map the reviewer's "front view" and "top" back to build.py's axes.

There is one repair per design() run. The hook's state (the reviews it made, the
repair) lives in the RepairContext that design() opens with active(); the run's
BrickWright total includes the reviews' cost, and design() books them as review.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .display import read_pose
from .llm import CallFailed, Usage
from .review import Checklist, ReviewError, ReviewResult, review_passes

REVIEW_HOOK_ENV = "BRICKWRIGHT_REVIEW_HOOK"  # read by BrickWright's critique loop
HOOK = "voxel.repair:review_hook"
REPAIR_RESERVE = 0.40  # USD the budget must still have after the review for a repair
BEFORE_IMAGE = "review.before_repair.png"  # in the record's out/ folder
BACKUP = "build.before_repair.py"  # in the record's out/ folder
OUT_DIR = "out"


def build_sha(build_path: str | Path) -> str:
    return hashlib.sha256(Path(build_path).read_bytes()).hexdigest()


def frame_note(build_path: str | Path) -> str:
    """How the reviewer's view maps to build.py's axes, for the repair feedback."""
    pose, why = read_pose(build_path)
    if pose is None:
        return (
            f"The reviewer saw the build as built ({why}). The front view shows your +y "
            "side, the top view your +z side."
        )
    if not pose.turned:
        return f"Your build.py sets {pose.text()}: the reviewer saw it as built."
    return (
        f"Your build.py sets {pose.text()}. The reviewer saw the build turned for display: "
        f"its front view shows your {pose.face} side, its top is your {pose.up} side. The "
        "attached render shows the build as you built it, not turned."
    )


def repair_feedback(result: ReviewResult, frame: str = "") -> str:
    """The builder's next user turn after a failed review."""
    failed = "\n".join(f"- {v.item}: {v.reason}" for v in result.review.failed())
    return (
        "A reviewer judged your build's render (attached). These checklist items failed:\n"
        f"{failed}\n\n"
        + (f"{frame}\n\n" if frame else "")
        + 
        "Edit the existing build.py to fix only these. Keep everything else; do not start "
        "over. Then validate and finalize again."
    )


@dataclass
class Reviewed:
    """One review the hook made, inside a builder run."""

    record_id: str
    image: Path
    result: ReviewResult
    passed: bool
    sha: str  # build.py when it was reviewed
    repair: bool  # the builder was asked to repair it
    turns: int  # builder turns before the review
    budget_left: float  # what was left of the run's max_cost at the review


@dataclass
class RepairContext:
    checklist: Checklist
    asked: str  # what the reviewer is told was asked for
    model: str
    reviewer: Callable[..., ReviewResult]
    gate: Callable[[Any, Any], tuple[bool, str]]
    inventory_path: Path
    review_estimate: float  # USD the budget must have for a review
    display: Callable[[Any, Path], tuple[Path, str]] | None = None  # (record, render) -> (image, warning)
    repairs_left: int = 1
    reviews: list[Reviewed] = field(default_factory=list)
    unbooked: list[Usage] = field(default_factory=list)  # review usage design() has not booked
    notes: list[str] = field(default_factory=list)

    def take_usage(self) -> list[Usage]:
        """The hook's review usage since the last call (for the cost ledger)."""
        taken, self.unbooked = self.unbooked, []
        return taken

    def repaired(self, record_id: str) -> Reviewed | None:
        """The review that asked for a repair in this record, if any."""
        return next((r for r in self.reviews if r.record_id == record_id and r.repair), None)

    def last_review(self, record_id: str) -> Reviewed | None:
        return next((r for r in reversed(self.reviews) if r.record_id == record_id), None)


_active: RepairContext | None = None


@contextmanager
def active(context: RepairContext) -> Iterator[RepairContext]:
    """Turn the review hook on for builder runs inside this block."""
    global _active
    previous_env = os.environ.get(REVIEW_HOOK_ENV)
    _active = context
    os.environ[REVIEW_HOOK_ENV] = HOOK
    try:
        yield context
    finally:
        _active = None
        if previous_env is None:
            os.environ.pop(REVIEW_HOOK_ENV, None)
        else:
            os.environ[REVIEW_HOOK_ENV] = previous_env


def _hook_usage(usage: Usage):
    from brickwright.record import Usage as BwUsage

    return BwUsage(
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        total_tokens=usage.input_tokens + usage.output_tokens,
        cost_usd=usage.cost_usd,
        known_cost_usd=usage.cost_usd,
    )


def assistant_turns(record) -> int:
    """Builder turns so far in this record's conversation."""
    path = Path(record.messages_path)
    if not path.exists():
        return 0
    turns = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            turns += json.loads(line).get("role") == "assistant"
        except (json.JSONDecodeError, AttributeError):
            continue
    return turns


def review_hook(record, image_path: Path, budget_left: float):
    """BrickWright's review hook (see the module docstring)."""
    from brickwright.driver.review_hook import HookResult

    ctx = _active
    if ctx is None or ctx.repairs_left <= 0:
        return HookResult(done=True)
    ok, _ = ctx.gate(record.build_path, ctx.inventory_path)
    if not ok:
        return HookResult(done=True)  # the gate retry handles it
    if budget_left < ctx.review_estimate:
        ctx.notes.append("no review inside the run: budget")
        return HookResult(done=True)
    out = Path(record.path) / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    shown = Path(image_path)
    if ctx.display is not None:
        shown, warning = ctx.display(record, shown)
        if warning:
            ctx.notes.append(warning)
    image = out / BEFORE_IMAGE
    shutil.copyfile(shown, image)
    try:
        result = ctx.reviewer(ctx.checklist, ctx.asked, image, model=ctx.model)
    except (ReviewError, CallFailed) as err:
        ctx.unbooked.append(err.usage)
        ctx.notes.append(f"the review inside the run failed: {err}")
        return HookResult(done=True, usage=_hook_usage(err.usage))
    ctx.unbooked.append(result.usage)
    passed = review_passes(result.review, ctx.checklist)
    repair = not passed and budget_left - result.cost_usd >= REPAIR_RESERVE
    if not passed and not repair:
        ctx.notes.append(f"no repair: less than ${REPAIR_RESERVE:.2f} left")
    if repair:
        shutil.copyfile(record.build_path, out / BACKUP)
        ctx.repairs_left -= 1
    ctx.reviews.append(
        Reviewed(
            record.id, image, result, passed, build_sha(record.build_path), repair, assistant_turns(record), budget_left
        )
    )
    return HookResult(
        done=not repair,
        feedback=repair_feedback(result, frame_note(record.build_path)) if repair else "",
        usage=_hook_usage(result.usage),
    )


def restore(record) -> None:
    """Put the build from before the repair back."""
    backup = Path(record.path) / OUT_DIR / BACKUP
    record.write_build(backup.read_text(encoding="utf-8"))
