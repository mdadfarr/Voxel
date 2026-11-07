"""Plan a build before the builder agent runs.

One LLM call (no tools, no loop) turns the request into a plan: a checklist made
from the request, the sections of the object, and the parts it needs. The plan
is checked against the inventory; if it is malformed or uses parts you do not
own, the call is retried once with the error.

The builder then gets the plan, the planned parts, and a "spare set" of basic
bricks and plates in the plan's colors, so it has room to fix things.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import bricklayer as bl
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .inventory import Inventory, PartKey, color_name, key_label
from .llm import default_complete, response_cost, response_text, strip_fences

if TYPE_CHECKING:
    from .expand import Expansion

DEFAULT_PLAN_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8192
BASIC_PART = re.compile(r"^(Brick|Plate) \d+ x \d+$")


def parse_color(value: str) -> int:
    """LDraw color code for a plan color: a bricklayer Color name (RED) or a code ("4")."""
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        return bl.Color[text.upper()].ldraw_id
    except KeyError:
        raise ValueError(f"unknown color {value!r}") from None


def part_name(part_id: str) -> str:
    try:
        return " ".join(bl.PartSpec(part_id).name.split())
    except Exception:
        return part_id


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
    height: int = Field(gt=0, description="plates (a brick is 3 plates)")


class Position(StrictModel):
    x: int = Field(description="studs")
    y: int = Field(description="studs")
    z: int = Field(description="plates above the ground")


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


def spare_set(inventory: Inventory, plan: Plan) -> dict[PartKey, int]:
    """Owned basic bricks and plates in the plan's colors that the plan does not already use."""
    colors = plan.color_codes()
    planned = plan.part_counts()
    return {
        key: n
        for key, n in sorted(inventory.counts.items())
        if n > 0
        and key[1] in colors
        and key not in planned
        and BASIC_PART.match(part_name(key[0]))
    }


# --- The planning call --------------------------------------------------------


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
            "size": {"width": 4, "depth": 4, "height": 12},
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
        f"studs wide, {s.depth} studs deep and {s.height} BRICKS tall; one brick is 3 plates, "
        f"so that is {s.height * 3} plates in this plan's height units.\n"
        "- Each key feature must become its own checklist item, right after the first "
        "(\"looks like ...\") item, in this order:\n"
        f"{features}\n\n"
    )


def plan_prompt(request: str, inventory: Inventory, expansion: Expansion | None = None) -> str:
    lines = [
        f"{part_id} | {part_name(part_id)} | {color_name(code)} | {n}"
        for (part_id, code), n in sorted(inventory.counts.items())
        if n > 0
    ]
    return (
        "You plan LEGO builds. A separate builder will follow your plan, so be concrete.\n\n"
        f"Request: {request}\n\n"
        + (expansion_rules(expansion) if expansion is not None else "")
        + "Reply with ONE JSON object and nothing else, with these keys:\n"
        "- checklist: yes/no questions to judge the finished build. The FIRST item is "
        'always "looks like <the thing asked for>". Then one item for each detail the '
        "request names (colors, parts of the object).\n"
        "- sections: the parts of the object (for a guitar: body, neck, headstock). Each has "
        "name, size {width, depth in studs; height in plates, a brick is 3 plates}, "
        "position {x, y in studs; z in plates above the ground}, colors, and the parts "
        "it uses as {part, color, count}.\n"
        "- parts: every {part, color, count} the whole plan uses, totalled over all sections.\n\n"
        "Rules:\n"
        "- Use ONLY parts and colors from the inventory below, and never more than the count owned.\n"
        "- Write colors exactly as in the inventory (like RED).\n\n"
        f"Example of the shape:\n{json.dumps(EXAMPLE, indent=2)}\n\n"
        "Inventory (part id | name | color | count owned):\n" + "\n".join(lines)
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
    complete = complete or default_complete()
    messages = [{"role": "user", "content": plan_prompt(request, inventory, expansion)}]
    cost, error = 0.0, ""
    for attempt in (1, 2):
        response = complete(model=model, messages=messages, max_tokens=MAX_TOKENS)
        cost += response_cost(response)
        text = response_text(response)
        try:
            return PlanResult(parse_plan(text, inventory, expansion), cost, attempt)
        except ValueError as err:
            error = str(err)
        messages += [
            {"role": "assistant", "content": text or "(empty reply)"},
            {
                "role": "user",
                "content": f"Your plan was rejected: {error}\n\nReply with the corrected JSON object only.",
            },
        ]
    raise PlanError(f"No usable plan after 2 attempts. Last error: {error}", cost, 2)
