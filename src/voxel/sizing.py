"""Size the model from the parts owned, instead of asking the user.

The user is not asked how big the model should be. The description and the plan
get the planner's budget table (voxel.capacity) and one rule: as big as the
tightest color allows. After a plan passes the capacity check, measure() works
out how well it follows that rule:

- Utilization of a color: the plate-cells the plan fills with it, divided by the
  planner's budget for it, at the width and height limit where that color is
  tightest (the same numbers the capacity check uses). Always the planner's
  budget, never the checker's limit. A plan between the two passes the check
  with a utilization a little over 100%.
- The tightest color: the one with the highest utilization.

Too small: the tightest color is under MIN_FILL percent. The planner gets one
retry for this, separate from its retry for an unusable plan; after that the
plan is kept with a warning. There is no upper size limit other than the
capacity check itself.

Exception: a request that already says a size ("a small guitar", "30 studs
tall") keeps that size, and the size check is skipped.

Setting (environment variable): VOXEL_MIN_FILL, 60 (percent).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .capacity import (
    ALL,
    ANY,
    PLATES_PER_BRICK,
    limits,
    need_and_budget,
    plan_demands,
    planner_margin,
    stock,
)
from .inventory import Inventory

MIN_FILL_ENV = "VOXEL_MIN_FILL"
MIN_FILL = 60.0  # percent of the tightest color's budget
TARGET_FILL = "70 to 100%"  # what the description and the plan aim for

_SIZE_WORD = re.compile(
    r"\b(small|smaller|big|bigger|large|larger|tiny|tall|taller|huge|giant|mini|miniature)\b"
    r"|\b(small|medium|large|life|full)[- ]sized?\b",
    re.IGNORECASE,
)
_SIZE_NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s*-?\s*(studs?|bricks?|plates?|cm|mm|centimet(?:er|re)s?|inch(?:es)?)\b",
    re.IGNORECASE,
)


def _number(env: str, default: float, least: float) -> float:
    raw = os.environ.get(env, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        raise ValueError(f"{env} must be a number, not {raw!r}") from None
    if value < least:
        raise ValueError(f"{env} must be at least {least:g}, not {value:g}")
    return value


def min_fill() -> float:
    return _number(MIN_FILL_ENV, MIN_FILL, 0)


def states_size(request: str) -> bool:
    """The request already says how big: a size word (small, big, large, tiny, tall)
    or a number with studs, bricks, plates, cm, mm or inches."""
    return bool(_SIZE_WORD.search(request) or _SIZE_NUMBER.search(request))


def size_rule(user_size: bool = False) -> str:
    """The size rule for the description and the plan. user_size: the request
    states a size, so keep that size."""
    if user_size:
        return (
            "Size: the request says how big the model should be. Keep that size; do not "
            "scale it to the budget."
        )
    return (
        "Size: do not pick from fixed sizes like small, medium or large. Choose the overall "
        "size so that the color you use most, relative to its budget, uses "
        f"{TARGET_FILL} of its budget."
    )


# --- Measuring a plan -----------------------------------------------------------


@dataclass(frozen=True)
class Use:
    """One color in a plan, at the width and height limit where it is tightest."""

    color: str
    cells: int  # plate-cells the plan fills with it, within that limit
    budget: int  # the planner's budget within that limit (stackable cells plus tops)

    @property
    def ratio(self) -> float:
        return self.cells / self.budget if self.budget else float("inf")

    @property
    def percent(self) -> int:
        return min(round(self.ratio * 100), 999)


@dataclass(frozen=True)
class Sizing:
    width: int  # studs
    depth: int  # studs
    height: float  # bricks
    limited_by: str  # the tightest color
    utilization: int  # percent of that color's planner budget
    user_size: bool  # the request said the size: no size check
    problem: str  # "", or the "Too small" sentence for the planner
    line: str  # the "Size: ..." line

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Measure:
    sizing: Sizing
    uses: tuple[Use, ...]  # every color, tightest first

    def report(self) -> str:
        """What the capacity command prints after the check."""
        s = self.sizing
        lines = ["Utilization (plate-cells used / your budget, where each color is tightest):"]
        lines += [f"  {u.color}: {u.cells:,} / {u.budget:,} = {u.percent}%" for u in self.uses]
        lines += [
            f"Tightest color: {s.limited_by} ({s.utilization}% of budget).",
            s.line,
        ]
        if s.user_size:
            lines.append("The request states the size: no size check.")
        elif s.problem:
            lines.append(s.problem)
        return "\n".join(lines)


def _use(rows, demands, color: str, margin: float) -> Use:
    """The limit where `color` is tightest (highest cells / budget)."""
    best = Use(color, 0, 1)
    for thin, layers in limits(demands):
        need, stack, tops, _ = need_and_budget(rows, demands, color, thin, layers, margin)
        found = Use(color, need, stack + tops)
        if need and found.ratio > best.ratio:
            best = found
    return best


def measure(plan: Mapping, inventory: Inventory, *, user_size: bool = False, planner: float | None = None) -> Measure:
    """Measure a plan (a dict like plan.json, with the capacity fields on every section).

    user_size: the request states the size, so no "too small" problem.
    Raises ValueError when a section's capacity fields are missing or wrong."""
    demands = plan_demands(plan)
    rows = stock(inventory)
    margin = planner_margin(planner)
    colors = sorted({c for d in demands for c in d.cells if c != ANY}) or [ALL]
    uses = sorted((_use(rows, demands, c, margin) for c in colors), key=lambda u: (-u.ratio, u.color))
    tightest = uses[0]

    xs = [x for d in demands for x, _ in d.columns]
    ys = [y for d in demands for _, y in d.columns]
    low = min(d.bottom for d in demands)
    high = max(d.bottom + d.layers for d in demands)
    width, depth = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    height = round((high - low) / PLATES_PER_BRICK, 2)

    problem = ""
    if not user_size and tightest.ratio * 100 < min_fill():
        problem = (
            f"Too small: {tightest.color} uses only {tightest.percent}% of its budget. "
            "Scale the model up."
        )
    line = (
        f"Size: {width} x {depth} studs x {height:g} bricks, limited by {tightest.color} "
        f"({tightest.percent}% of budget)."
    )
    sizing = Sizing(width, depth, height, tightest.color, tightest.percent, user_size, problem, line)
    return Measure(sizing, tuple(uses))
