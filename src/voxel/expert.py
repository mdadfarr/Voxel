"""The context hook for BrickWright's ask_expert tool.

design() sets BRICKWRIGHT_EXPERT_CONTEXT to "voxel.expert:context". When the
builder's validate fails and it calls ask_expert, BrickWright builds the packet
(the check, the problem parts, the parts around them) and calls context() for the
inventory side:

- reference: every part you own. It does not change during a run, so it goes in
  the cached start of every expert prompt.
- details: the parts left (owned minus what the build uses), the plan section the
  problem part is in (shape, position, colors, parts), and the units and
  orientation sentences.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import bricklayer as bl

from .inventory import Inventory, PartKey, color_name, load_inventory
from .parts import part_name
from .plan import ORIENTATION, UNITS, Plan, Section

INVENTORY_ENV = "VOXEL_INVENTORY"  # set by design(), like for check_owned
PLAN_FILE = "plan.json"
CONTEXT_HOOK = "voxel.expert:context"
EXPERT_RULE = (
    "When validate fails, call ask_expert and apply its edits exactly. "
    "Don't try to fix it yourself first."
)
UNITS_TEXT = (
    f"Units: x, y, width and depth are in studs; {UNITS}, the same units as "
    "BrickLayer's add() position (x, y, z). A plate-thick step is 1/3 brick."
)


def context(record_dir: str | Path, parts: Sequence[dict], problem: Sequence[int]) -> dict:
    inventory = load_inventory(os.environ[INVENTORY_ENV])
    details = [
        parts_left_text(inventory, parts),
        plan_section_text(Path(record_dir) / PLAN_FILE, parts, problem),
        UNITS_TEXT,
        ORIENTATION,
    ]
    return {"reference": owned_text(inventory), "details": "\n\n".join(details)}


def _line(key: PartKey, count: int) -> str:
    part_id, code = key
    return f"{part_id} | {part_name(part_id)} | {color_name(code)} | {count}"


def owned_text(inventory: Inventory) -> str:
    """Every part owned, in a fixed order (the cached start of the prompt)."""
    rows = [_line(key, n) for key, n in sorted(inventory.counts.items()) if n > 0]
    return "PARTS THE USER OWNS (part id | name | color | count):\n" + "\n".join(rows)


def used_counts(parts: Sequence[dict]) -> Counter[PartKey]:
    """What the build uses, from BrickWright's part list (color as a Color name)."""
    used: Counter[PartKey] = Counter()
    for part in parts:
        try:
            code = bl.Color[part["color"]].ldraw_id
        except KeyError:
            continue
        used[(part["part"], code)] += 1
    return used


def parts_left_text(inventory: Inventory, parts: Sequence[dict]) -> str:
    used = used_counts(parts)
    rows = [
        _line(key, owned - used.get(key, 0))
        for key, owned in sorted(inventory.counts.items())
        if owned - used.get(key, 0) > 0
    ]
    return (
        "PARTS LEFT, the only parts you may add (owned minus what the build uses; "
        "part id | name | color | count):\n" + ("\n".join(rows) if rows else "(none)")
    )


def _gap(section: Section, xyz: Sequence[float]) -> float:
    """Distance from a point to a section's box (0 inside it); z in bricks."""
    p, s = section.position, section.size
    low, high = (p.x, p.y, p.z), (p.x + s.width, p.y + s.depth, p.z + s.height)
    return math.sqrt(sum(max(lo - v, 0.0, v - hi) ** 2 for v, lo, hi in zip(xyz, low, high, strict=True)))


def plan_section(plan: Plan, xyz: Sequence[float]) -> Section:
    """The section whose box holds the point, else the nearest one."""
    return min(plan.sections, key=lambda section: _gap(section, xyz))


def plan_section_text(plan_path: Path, parts: Sequence[dict], problem: Sequence[int]) -> str:
    if not plan_path.is_file() or not problem:
        return "PLAN SECTION: (none)"
    try:
        plan = Plan.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return "PLAN SECTION: (plan.json could not be read)"
    section = plan_section(plan, parts[problem[0]]["position"])
    s, p = section.size, section.position
    planned = ", ".join(f"{q.count}x {q.part} {q.color}" for q in section.parts)
    return (
        f"PLAN SECTION the problem is in: {section.name}\n"
        f"size: width {s.width}, depth {s.depth}, height {s.height:g}; "
        f"position: x {p.x}, y {p.y}, z {p.z:g}; colors: {', '.join(section.colors)}\n"
        f"planned parts: {planned}"
    )
