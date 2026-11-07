"""Plan a build before the builder agent runs.

One LLM call (no tools, no loop) turns the request into a plan: a checklist made
from the request, the sections of the object, and the parts it needs. The plan
is checked against the inventory; if it is malformed or uses parts you do not
own, the call is retried once with the error.

When the review fails, revise_plan() makes a new plan from the old one, the
failed items and the render, checked the same way.

Units match BrickLayer: x, y, width and depth in studs; z and height in bricks
(1 brick = 3 plates), so a plate-thick step is 1/3 brick.

The builder then gets the plan, the planned parts, and a "spare set" of small
basic and shaped parts in the plan's colors, so it has room to fix things.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import bricklayer as bl
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .inventory import Inventory, PartKey, color_name, key_label
from .llm import ReplyError, call_with_retry, default_complete, image_part, strip_fences
from .parts import SPARE_KINDS, part_kind, part_name, part_size

if TYPE_CHECKING:
    from .expand import Expansion
    from .review import Review

DEFAULT_PLAN_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8192
MAX_SPARE_ROWS = 40
UNITS = "z and height are in bricks (1 brick = 3 plates)"
UNITS_HINT = f"{UNITS}; a plate is 1/3 brick, so use whole numbers or .333 / .667"


def parse_color(value: str) -> int:
    """LDraw color code for a plan color: a bricklayer Color name (RED) or a code ("4")."""
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        return bl.Color[text.upper()].ldraw_id
    except KeyError:
        raise ValueError(f"unknown color {value!r}") from None


def bricks(value: float) -> float:
    """Check a z or height in bricks lands on whole plates; normalize thirds to 3 decimals."""
    plates = value * 3
    if abs(plates - round(plates)) > 0.02:
        raise ValueError(f"{value} is not a whole number of plates ({UNITS_HINT})")
    whole = round(plates)
    return whole // 3 if whole % 3 == 0 else round(whole / 3, 3)


# --- The plan's shape ---------------------------------------------------------


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanPart(StrictModel):
    part: str
    color: str
    count: int = Field(gt=0)

    @field_validator("color")
    @classmethod
    def _known_color(cls, value: str) -> str:
        parse_color(value)
        return value

    @property
    def key(self) -> PartKey:
        return (self.part, parse_color(self.color))


class Size(StrictModel):
    width: int = Field(gt=0, description="studs, left to right")
    depth: int = Field(gt=0, description="studs, front to back")
    height: float = Field(gt=0, description="bricks (1 brick = 3 plates); thirds allowed")

    @field_validator("height")
    @classmethod
    def _whole_plates(cls, value: float) -> float:
        return bricks(value)


class Position(StrictModel):
    x: int = Field(description="studs")
    y: int = Field(description="studs")
    z: float = Field(description="bricks (1 brick = 3 plates) above the ground; thirds allowed")

    @field_validator("z")
    @classmethod
    def _whole_plates(cls, value: float) -> float:
        return bricks(value)


class Section(StrictModel):
    name: str
    size: Size
    position: Position
    colors: list[str] = Field(min_length=1)
    parts: list[PlanPart] = Field(min_length=1)

    @field_validator("colors")
    @classmethod
    def _known_colors(cls, values: list[str]) -> list[str]:
        for value in values:
            parse_color(value)
        return values


class Plan(StrictModel):
    checklist: list[str] = Field(min_length=1)
    sections: list[Section] = Field(min_length=1)
    parts: list[PlanPart] = Field(min_length=1)

    @field_validator("checklist")
    @classmethod
    def _first_item_is_overall_look(cls, items: list[str]) -> list[str]:
        if not items[0].strip().lower().startswith("looks like"):
            raise ValueError('the first checklist item must start with "looks like"')
        return items

    @model_validator(mode="after")
    def _sections_use_listed_parts(self) -> Plan:
        listed = {p.key for p in self.parts}
        for section in self.sections:
            for p in section.parts:
                if p.key not in listed:
                    raise ValueError(
                        f"section {section.name!r} uses {p.part} in {p.color}, "
                        "which is missing from the top-level parts list"
                    )
        return self

    def part_counts(self) -> Counter[PartKey]:
        counts: Counter[PartKey] = Counter()
        for p in self.parts:
            counts[p.key] += p.count
        return counts

    def color_codes(self) -> set[int]:
        codes = {p.key[1] for p in self.parts}
        codes.update(parse_color(c) for s in self.sections for c in s.colors)
        return codes

    def color_order(self) -> list[int]:
        """The plan's colors, most-used first (by planned pieces)."""
        used: Counter[int] = Counter()
        for (_, code), n in self.part_counts().items():
            used[code] += n
        return sorted(self.color_codes(), key=lambda code: (-used[code], code))


def plan_errors(plan: Plan, inventory: Inventory) -> list[str]:
    """Parts in the plan that are not owned, or not owned in that number."""
    errors = []
    for key, n in sorted(plan.part_counts().items()):
        owned = inventory.owned(key)
        if owned == 0:
            errors.append(f"{key_label(key)} is not in the inventory")
        elif n > owned:
            errors.append(f"{key_label(key)}: plan uses {n}, owned {owned}")
    return errors


def spare_set(inventory: Inventory, plan: Plan, limit: int = MAX_SPARE_ROWS) -> dict[PartKey, int]:
    """Owned spare parts in the plan's colors that the plan does not already use.

    Spare kinds: plain bricks and plates, slopes, curved and round parts, tiles.
    At most `limit` rows: taken round-robin over the plan's colors (most-used
    color first), smallest parts first within each color.
    """
    planned = plan.part_counts()
    colors = plan.color_order()
    by_color: dict[int, list[tuple]] = {code: [] for code in colors}
    for key, n in inventory.counts.items():
        part_id, code = key
        if n <= 0 or code not in by_color or key in planned:
            continue
        name = part_name(part_id)
        if part_kind(name) in SPARE_KINDS:
            by_color[code].append((*part_size(name), name, part_id, key))
    queues = [sorted(by_color[code]) for code in colors]
    picked: list[PartKey] = []
    while len(picked) < limit and any(queues):
        for queue in queues:
            if queue and len(picked) < limit:
                picked.append(queue.pop(0)[-1])
    return {key: inventory.counts[key] for key in picked}


# --- The planning calls -------------------------------------------------------


class PlanError(Exception):
    """The planner did not produce a usable plan, even after one retry."""

    def __init__(self, message: str, cost_usd: float, attempts: int) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.attempts = attempts


@dataclass(frozen=True)
class PlanResult:
    plan: Plan
    cost_usd: float
    attempts: int


EXAMPLE = {
    "checklist": [
        "looks like a red mailbox",
        "is the body red?",
        "does it have a white slot on the front?",
    ],
    "sections": [
        {
            "name": "body",
            "size": {"width": 4, "depth": 4, "height": 4},
            "position": {"x": 0, "y": 0, "z": 0},
            "colors": ["RED"],
            "parts": [{"part": "3001", "color": "RED", "count": 8}],
        }
    ],
    "parts": [{"part": "3001", "color": "RED", "count": 8}],
}


def expansion_rules(expansion: Expansion) -> str:
    """What the planner must take from the detailed description."""
    s = expansion.size
    features = "\n".join(f"{n}. {f}" for n, f in enumerate(expansion.features, start=2))
    return (
        f"Detailed description (follow it):\n{expansion.as_text()}\n\n"
        "Rules from the description:\n"
        f"- Follow its overall size and the proportions between the parts. It is {s.width} "
        f"studs wide, {s.depth} studs deep and {s.height} bricks tall, in the same units "
        "as this plan.\n"
        "- Each key feature must become its own checklist item, right after the first "
        "(\"looks like ...\") item, in this order:\n"
        f"{features}\n\n"
    )


def plan_format_and_rules(inventory: Inventory) -> str:
    """The reply format, rules and inventory, shared by make_plan and revise_plan."""
    lines = [
        f"{part_id} | {part_name(part_id)} | {color_name(code)} | {n}"
        for (part_id, code), n in sorted(inventory.counts.items())
        if n > 0
    ]
    return (
        "Reply with ONE JSON object and nothing else, with these keys:\n"
        "- checklist: yes/no questions to judge the finished build. The FIRST item is "
        'always "looks like <the thing asked for>". Then one item for each detail the '
        "request names (colors, parts of the object).\n"
        "- sections: the parts of the object (for a guitar: body, neck, headstock). Each has "
        "name, size {width, depth in studs; height in bricks}, position {x, y in studs; "
        "z in bricks above the ground}, colors, and the parts it uses as {part, color, count}.\n"
        "- parts: every {part, color, count} the whole plan uses, totalled over all sections.\n\n"
        "Rules:\n"
        f"- Units: x, y, width and depth are in studs; {UNITS_HINT}. This is the unit "
        "BrickLayer's add() uses.\n"
        "- Use ONLY parts and colors from the inventory below, and never more than the count owned.\n"
        "- Write colors exactly as in the inventory (like RED).\n"
        "- Use shaped parts (slopes, curved, round, tiles) for outlines and curves, not only "
        "plain bricks.\n"
        "- Follow the size and proportions in the description, if one is given.\n\n"
        f"Example of the shape:\n{json.dumps(EXAMPLE, indent=2)}\n\n"
        "Inventory (part id | name | color | count owned):\n" + "\n".join(lines)
    )


def plan_prompt(request: str, inventory: Inventory, expansion: Expansion | None = None) -> str:
    return (
        "You plan LEGO builds. A separate builder will follow your plan, so be concrete.\n\n"
        f"Request: {request}\n\n"
        + (expansion_rules(expansion) if expansion is not None else "")
        + plan_format_and_rules(inventory)
    )


def parse_plan(text: str, inventory: Inventory, expansion: Expansion | None = None) -> Plan:
    """Parse and check a plan. Raises ValueError with a message meant for the planner."""
    try:
        data = json.loads(strip_fences(text))
    except json.JSONDecodeError as err:
        raise ValueError(f"the reply is not valid JSON: {err}") from None
    try:
        plan = Plan.model_validate(data)
    except ValidationError as err:
        raise ValueError(f"the JSON does not match the plan shape:\n{err}") from None
    if expansion is not None:
        needed = 1 + len(expansion.features)
        if len(plan.checklist) < needed:
            raise ValueError(
                f"the checklist has {len(plan.checklist)} items but needs at least {needed}: "
                f'"looks like ..." plus one item per key feature ({len(expansion.features)} features)'
            )
    errors = plan_errors(plan, inventory)
    if errors:
        raise ValueError("the plan uses parts you do not own:\n" + "\n".join(f"- {e}" for e in errors))
    return plan


def _plan_call(complete, model, content, parse) -> PlanResult:
    try:
        plan, cost, attempts = call_with_retry(
            complete or default_complete(),
            model=model,
            messages=[{"role": "user", "content": content}],
            parse=parse,
            max_tokens=MAX_TOKENS,
            what="plan",
        )
    except ReplyError as err:
        raise PlanError(str(err), err.cost_usd, err.attempts) from None
    return PlanResult(plan, cost, attempts)


def make_plan(
    request: str,
    inventory: Inventory,
    *,
    expansion: Expansion | None = None,
    model: str = DEFAULT_PLAN_MODEL,
    complete: Callable[..., Any] | None = None,
) -> PlanResult:
    """Ask the model for a plan. Retries once with the error if the plan is unusable.

    With an expansion, the planner follows its description, size and proportions,
    and turns each key feature into a checklist item.
    """
    return _plan_call(
        complete,
        model,
        plan_prompt(request, inventory, expansion),
        lambda text: parse_plan(text, inventory, expansion),
    )


def revise_prompt(old_plan: Plan, review: Review, expansion: Expansion | None, inventory: Inventory) -> str:
    verdicts = "\n".join(
        f"{v.index}. [{'pass' if v.passed else 'FAIL'}] {v.item}: {v.reason}" for v in review.verdicts
    )
    return (
        "You plan LEGO builds. A builder built the plan below; the attached image is its "
        "render (front, side, top and isometric views). A reviewer checked it against the "
        "plan's checklist, and some items FAILED.\n\n"
        "The plan itself is the likely cause. Revise the DESIGN so the failed items pass: "
        "change sections, sizes, positions, proportions and parts as needed. Do not just "
        "patch details. The builder will build your new plan from an empty build.\n\n"
        f"Review:\n{verdicts}\n\n"
        f"Old plan:\n{old_plan.model_dump_json(indent=2)}\n\n"
        + (expansion_rules(expansion) if expansion is not None else "")
        + "Keep the checklist EXACTLY the same: the same items, in the same order.\n\n"
        + plan_format_and_rules(inventory)
    )


def parse_revision(
    text: str, old_plan: Plan, inventory: Inventory, expansion: Expansion | None
) -> Plan:
    plan = parse_plan(text, inventory, expansion)
    old = [item.strip() for item in old_plan.checklist]
    if [item.strip() for item in plan.checklist] != old:
        raise ValueError(
            "the checklist changed; keep it exactly the same as the old plan's:\n"
            + "\n".join(f"- {item}" for item in old_plan.checklist)
        )
    return plan


def revise_plan(
    old_plan: Plan,
    review: Review,
    image_path: str | Path,
    expansion: Expansion | None,
    inventory: Inventory,
    *,
    model: str = DEFAULT_PLAN_MODEL,
    complete: Callable[..., Any] | None = None,
) -> PlanResult:
    """A new plan after a failed review, from the old plan, the review and the render.

    Checked like make_plan (shape, owned parts, features), and the checklist must
    stay the same. Retries once with the error if the plan is unusable.
    """
    content = [
        {"type": "text", "text": revise_prompt(old_plan, review, expansion, inventory)},
        image_part(image_path),
    ]
    return _plan_call(
        complete, model, content, lambda text: parse_revision(text, old_plan, inventory, expansion)
    )
