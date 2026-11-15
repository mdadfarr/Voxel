"""Capacity check: can the parts you own fill the plan's shapes at all?

Run on a plan before any builder turn. No LLM, no build: only counting.

Unit: a plate-cell is one stud wide, one stud deep and one plate tall. A section
3 studs by 2 studs by 1 brick (3 plates) is 18 plate-cells.

Demand. Each section of the plan says how it is filled:

- fill: "solid" (the whole box) or "shell" (only the four walls of the box,
  `wall` studs thick, open at the top and the bottom);
- cells: the plate-cells it uses per color ("ANY" for cells that cannot be seen,
  where any color will do);
- empty: the plate-cells of the box or ring left open (openings, cutaways).

Code computes the volume of the box or ring from the section's size, and
cells + empty must add up to it, so a plan cannot understate what it needs.

Supply. Fill is counted from plain bricks and plates only ("Brick 2 x 4",
"Plate 1 x 6"): the parts a builder can stack into any shape. Tiles, slopes,
dishes and domes count as "tops" (nothing can be stacked on them). Every other
part (round, cut, with a handle, Technic bricks, brackets) counts only where a
section names it in `parts`: {part, color, count}. The footprint and height come
from the part's name ("Plate 2 x 4", "Brick 1 x 2 x 5"); curved and cut parts
count as their full box.

Why plain parts only: counting every brick- and plate-like part accepted a plan
whose build then ran out of its main color with 126 odd plates of that color
unused (record 20261109-005715-make-me-a-skull-73b41c51, round 1).

Three limits, each one a physical fact:

1. Width. A wall `wall` studs thick only takes parts whose narrow side is at
   most `wall` studs (a solid section: at most its own narrow side).
2. Height. A part taller than the section does not fit in it.
3. Tops. Tiles and slopes fill at most one top per column of a section, and only
   where no other section sits right on top of that column (from the sections'
   positions).

Two margins, both rounded down, so the builder has room for seams, supports and
fixes:

- Planner margin (VOXEL_PLANNER_MARGIN percent, 15 by default). The planner only
  ever sees the owned plate-cells minus this: in the table, and as "your budget"
  in the check's messages.
- Checker margin (VOXEL_CHECKER_MARGIN percent, 5 by default). The check rejects
  a plan that needs more than the owned plate-cells minus this. It is the smaller
  one, so a plan slightly over the planner's budget still passes.

The builder and the only_owned_parts check work with everything owned.

The check adds up, for each color and for all colors together, what the sections
within a width and height limit need, and compares it with the checker's limit
within that width and height. A plan that fails cannot be filled from plain parts plus the special
parts it names; fix it by shrinking, changing colors, or naming more special
parts. A plan that passes may still fail for other reasons (length of the parts,
studs that do not line up).

Run it on a plan file:
    python -m voxel.capacity runs/<record_id>/plan.json [--inventory inventory.json]
Print the table the planner gets:
    python -m voxel.capacity --table [--inventory inventory.json]
The first takes --checker-margin and --planner-margin, the second --planner-margin
(percent).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import bricklayer as bl

from .inventory import Inventory, PartKey, color_name, key_label, load_inventory
from .parts import BASIC_PART, part_name

ANY = "ANY"  # a color key in `cells`: any owned color will do (hidden cells)
ALL = "all colors together"  # the pooled row of the check
FILLS = ("solid", "shell")
STACK_KINDS = ("Brick", "Plate", "Wing", "Arch", "Panel", "Bracket", "Hinge", "Cone")
TOP_KINDS = ("Tile", "Slope", "Dish", "Dome")
FLAT_KINDS = ("Plate", "Tile", "Wing", "Bracket", "Hinge", "Dish")  # one plate tall unless the name says more
PLATES_PER_BRICK = 3
PLANNER_MARGIN_ENV = "VOXEL_PLANNER_MARGIN"
CHECKER_MARGIN_ENV = "VOXEL_CHECKER_MARGIN"
PLANNER_MARGIN = 15.0  # percent of the owned plate-cells the planner never sees
CHECKER_MARGIN = 5.0  # percent of the owned plate-cells a plan may not use
TABLE_MIN_CELLS = 60  # colors with less stackable volume are left out of the table
TABLE_WIDTHS = (1, 2)
MAX_SECTIONS_SHOWN = 4
_SIZE = re.compile(r"(\d+(?:\.\d+)?) x (\d+(?:\.\d+)?)(?: x (\d+(?:\.\d+)?))?")


# --- Supply: what the inventory can fill ---------------------------------------


@dataclass(frozen=True)
class Stock:
    """All owned copies of one plain part (or top) in one color, as fill material."""

    color: int  # LDraw color code
    top: bool  # a tile or slope: nothing stacks on it
    area: float  # studs covered by one part
    thin: float  # its narrow side, in studs
    layers: int  # its height, in plates
    count: int

    @property
    def cells(self) -> float:
        return self.area * self.layers * self.count


def material(name: str) -> tuple[bool, float, float, int] | None:
    """(top, area, narrow side, height in plates) for a part name, or None when the
    part is not fill material (pins, axles, minifig parts, parts with no size)."""
    words = name.split()
    if not words:
        return None
    kind = words[0]
    if kind == "Technic":  # Technic Brick / Technic Plate stack like plain ones
        kind = words[1] if len(words) > 1 and words[1] in ("Brick", "Plate") else ""
    if kind not in STACK_KINDS and kind not in TOP_KINDS:
        return None
    match = _SIZE.search(name)
    if not match:
        return None
    width, depth, height = match.groups()
    w, d = float(width), float(depth)
    if height is not None:
        layers = max(1, round(float(height) * PLATES_PER_BRICK))
    elif kind in FLAT_KINDS or name.startswith("Slope Plate"):
        layers = 1
    else:
        layers = PLATES_PER_BRICK
    return kind in TOP_KINDS, w * d, min(w, d), layers


def pooled(name: str) -> bool:
    """Counted for every section: a plain brick or plate, or a top. Other parts
    count only for the section that names them."""
    found = material(name)
    return found is not None and (found[0] or bool(BASIC_PART.match(name)))


def stock(inventory: Inventory) -> list[Stock]:
    rows = []
    for (part_id, code), count in sorted(inventory.counts.items()):
        name = part_name(part_id)
        if count > 0 and pooled(name):
            rows.append(Stock(code, *material(name), count))  # type: ignore[misc]
    return rows


def owned_cells(
    rows: Iterable[Stock], color: int | None, thin: float, layers: float, *, top: bool
) -> float:
    """Plate-cells owned in a color (None: every color) by parts that fit the limits."""
    return sum(
        r.cells
        for r in rows
        if r.top == top and r.thin <= thin and r.layers <= layers and color in (None, r.color)
    )


def _margin(value: float | None, env: str, default: float) -> float:
    if value is None:
        raw = os.environ.get(env, "").strip()
        try:
            value = float(raw) if raw else default
        except ValueError:
            raise ValueError(f"{env} must be a number of percent, not {raw!r}") from None
    if not 0 <= value < 100:
        raise ValueError(f"a capacity margin must be from 0 to under 100 percent, not {value:g}")
    return float(value)


def planner_margin(value: float | None = None) -> float:
    """Percent held back from the planner: `value`, else VOXEL_PLANNER_MARGIN, else 15."""
    return _margin(value, PLANNER_MARGIN_ENV, PLANNER_MARGIN)


def checker_margin(value: float | None = None) -> float:
    """Percent a plan may not use: `value`, else VOXEL_CHECKER_MARGIN, else 5."""
    return _margin(value, CHECKER_MARGIN_ENV, CHECKER_MARGIN)


def budget(
    rows: Iterable[Stock], color: int | None, thin: float, layers: float, *, top: bool, margin: float
) -> int:
    """owned_cells minus `margin` percent, rounded down: the planner's budget with the
    planner margin, the checker's limit with the checker margin."""
    return math.floor(owned_cells(rows, color, thin, layers, top=top) * (100 - margin) / 100 + 1e-9)


def tallest_top(rows: Iterable[Stock], color: int | None, thin: float, layers: float) -> int:
    return max(
        (r.layers for r in rows if r.top and r.thin <= thin and r.layers <= layers and color in (None, r.color)),
        default=0,
    )


# --- Demand: what the plan needs -----------------------------------------------


@dataclass(frozen=True)
class Demand:
    """One section's need, in plate-cells."""

    name: str
    thin: int  # parts must be at most this wide (narrow side), in studs
    layers: int  # and at most this tall, in plates
    tops: int  # columns that can end in a tile or slope (set by exposed_tops)
    cells: dict[str, int]  # color name (or ANY) -> plate-cells still to fill from plain parts and tops
    special: tuple[tuple[PartKey, int], ...] = ()  # the special parts it names: ((part, color code), count)
    columns: frozenset[tuple[int, int]] = frozenset()  # its (x, y) stud cells
    bottom: int = 0  # z of its underside, in plates

    def need(self, color: str) -> int:
        """Plate-cells this section needs from `color` (ALL: from any color)."""
        return sum(self.cells.values()) if color == ALL else self.cells.get(color, 0)


def _whole_plates(value: float, what: str, *, least: int = 1) -> int:
    plates = float(value) * PLATES_PER_BRICK
    if plates < least - 0.02 or abs(plates - round(plates)) > 0.02:
        raise ValueError(f"{what} {value} is not a whole number of plates (1 brick = 3 plates)")
    return round(plates)


def _cells(raw: object) -> dict[str, int]:
    """`cells` as {COLOR: plate-cells}, from a list of {color, cells}."""
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise ValueError('cells must be a list like [{"color": "RED", "cells": 120}]')
    cells: dict[str, int] = {}
    for entry in raw:
        if not isinstance(entry, Mapping) or "color" not in entry or "cells" not in entry:
            raise ValueError('each cells entry needs "color" and "cells"')
        color = str(entry["color"]).strip().upper()
        if color != ANY and color not in bl.Color.__members__:
            raise ValueError(f"unknown color {entry['color']!r} in cells")
        count = entry["cells"]
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError(f"cells for {color} must be a whole number above 0")
        cells[color] = cells.get(color, 0) + count
    return cells


def section_demand(section: Mapping) -> Demand:
    """A section's Demand. Raises ValueError with a message meant for the planner."""
    name = str(section.get("name", "?"))
    fill = section.get("fill")
    if fill not in FILLS:
        raise ValueError(f'fill must be "solid" or "shell", not {fill!r}')
    size = section.get("size")
    if not isinstance(size, Mapping):
        raise ValueError("size must be {width, depth, height}")
    try:
        width, depth = int(size["width"]), int(size["depth"])
        layers = _whole_plates(size["height"], "height")
    except (KeyError, TypeError) as err:
        raise ValueError(f"size must be {{width, depth, height}} ({err})") from None
    if width <= 0 or depth <= 0:
        raise ValueError("width and depth must be above 0")
    position = section.get("position")
    if not isinstance(position, Mapping):
        raise ValueError("position must be {x, y, z}")
    try:
        x0, y0 = int(position["x"]), int(position["y"])
        bottom = _whole_plates(position["z"], "z", least=0)
    except (KeyError, TypeError) as err:
        raise ValueError(f"position must be {{x, y, z}} ({err})") from None
    narrow = min(width, depth)
    thin, hole = narrow, range(0)
    if fill == "shell":
        wall = section.get("wall", 1)
        if not isinstance(wall, int) or isinstance(wall, bool) or wall < 1:
            raise ValueError("wall must be a whole number of studs, 1 or more")
        if 2 * wall < narrow:
            thin, hole = wall, range(wall, width - wall)
    columns = frozenset(
        (x0 + i, y0 + j)
        for i in range(width)
        for j in range(depth)
        if not (i in hole and wall <= j < depth - wall)
    )
    footprint = len(columns)
    empty = section.get("empty", 0) or 0
    if not isinstance(empty, int) or isinstance(empty, bool) or empty < 0:
        raise ValueError("empty must be a whole number, 0 or more")
    cells = _cells(section.get("cells"))
    volume, stated = footprint * layers, sum(cells.values()) + empty
    if stated != volume:
        shape = f"{width} x {depth} box" if footprint == width * depth else f"{width} x {depth} ring"
        raise ValueError(
            f"its {shape} is {footprint} cells x {layers} plates = {volume:,} plate-cells, but cells "
            f"({sum(cells.values()):,}) + empty ({empty:,}) = {stated:,}. They must be equal"
        )
    special = _credit_special(section.get("parts"), cells, thin, layers)
    return Demand(name, thin, layers, footprint, cells, special, columns, bottom)


def exposed_tops(demands: Sequence[Demand]) -> list[Demand]:
    """Set each section's `tops`: its columns with no other section right on top."""
    out = []
    for d in demands:
        top = d.bottom + d.layers
        covered = set().union(
            *(o.columns for o in demands if o is not d and o.bottom <= top < o.bottom + o.layers)
        )
        out.append(replace(d, tops=len(d.columns - covered)))
    return out


def _credit_special(raw: object, cells: dict[str, int], thin: int, layers: int) -> tuple[tuple[PartKey, int], ...]:
    """Take the volume of the section's special parts off `cells` (in place).

    A special part is fill material that is not pooled: a round, cut or otherwise
    modified brick or plate. It fills cells of its own color, else ANY cells.
    Plain parts and tops are pooled already; parts that are not fill material,
    do not fit the section's limits or have no cells to fill are left alone."""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError('parts must be a list like [{"part": "4073", "color": "RED", "count": 4}]')
    credited = []
    for entry in raw:
        try:
            part_id, color, count = str(entry["part"]), str(entry["color"]).strip().upper(), entry["count"]
        except (KeyError, TypeError):
            raise ValueError('each parts entry needs "part", "color" and "count"') from None
        if color not in bl.Color.__members__:
            raise ValueError(f"unknown color {entry['color']!r} in parts")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError(f"count for part {part_id} must be a whole number above 0")
        name = part_name(part_id)
        found = material(name)
        if found is None or pooled(name) or found[2] > thin or found[3] > layers:
            continue
        key = color if color in cells else ANY if ANY in cells else None
        if key is None:
            continue
        cells[key] -= min(cells[key], round(found[1] * found[3] * count))
        if cells[key] == 0:
            del cells[key]
        credited.append(((part_id, bl.Color[color].ldraw_id), count))
    return tuple(credited)


def special_errors(demands: Sequence[Demand], inventory: Inventory) -> list[str]:
    """Special parts the sections name more often than you own them."""
    used: dict[PartKey, int] = {}
    for demand in demands:
        for key, count in demand.special:
            used[key] = used.get(key, 0) + count
    return [
        f"{key_label(key)}: the sections name {n} in parts, owned {inventory.owned(key)}"
        for key, n in sorted(used.items())
        if n > inventory.owned(key)
    ]


# --- The check -------------------------------------------------------------------


@dataclass(frozen=True)
class Shortfall:
    """One color (or all colors together) cannot fill the sections within a limit.

    The plan is over the checker's limit (`over`). The text, which goes back to the
    planner, only gives the planner's budget."""

    color: str  # a Color name, or ALL
    thin: int
    layers: int
    need: int  # plate-cells the sections within the limit need
    stack: int  # planner's budget of plate-cells that stack, within the limit
    tops: int  # planner's budget of tiles and slopes that fit on those sections' tops
    sections: tuple[tuple[str, int], ...]  # (section, plate-cells), biggest first
    limit: int = 0  # the checker's limit (stackable cells plus tops) the plan is over

    @property
    def short(self) -> int:
        """Plate-cells over the planner's budget: what the planner must take out."""
        return self.need - self.stack - self.tops

    @property
    def over(self) -> int:
        """Plate-cells over the checker's limit: why the plan was rejected."""
        return self.need - self.limit

    def text(self) -> str:
        limit = f"at most {self.thin} stud{'s' if self.thin != 1 else ''} wide"
        limit += f" and {self.layers} plate{'s' if self.layers != 1 else ''} tall"
        shown = ", ".join(f"{name} {n:,}" for name, n in self.sections[:MAX_SECTIONS_SHOWN])
        more = len(self.sections) - MAX_SECTIONS_SHOWN
        return (
            f"{self.color}: short by {self.short:,} plate-cells. The sections that need parts "
            f"{limit} need {self.need:,}; your budget is {self.stack:,} in plain bricks and plates"
            f" (+{self.tops:,} tiles and slopes for their tops). Sections: {shown}"
            + (f" and {more} more" if more > 0 else "")
            + "."
        )


@dataclass(frozen=True)
class CapacityResult:
    errors: tuple[str, ...]  # sections whose capacity fields are missing or wrong
    shortfalls: tuple[Shortfall, ...]  # the worst one per color, biggest first
    unchecked: tuple[str, ...] = ()  # sections without capacity fields (old plans)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.shortfalls

    def report(self) -> str:
        if self.ok:
            return "Capacity check passed."
        lines = [f"- {e}" for e in self.errors] + [f"- {s.text()}" for s in self.shortfalls]
        if self.shortfalls:
            lines.append(
                "Make the model smaller or its walls thinner, move cells to a color you own "
                'more of (or to "ANY" where they cannot be seen), or name the special parts '
                "(round, cut, modified bricks and plates) a section uses in its parts list."
            )
        return "\n".join(lines)


def _code(color: str) -> int | None:
    return None if color in (ALL, ANY) else bl.Color[color].ldraw_id


def shortfall(
    rows: Sequence[Stock],
    demands: Sequence[Demand],
    color: str,
    thin: int,
    layers: int,
    checker: float,
    planner: float,
) -> Shortfall | None:
    """The shortfall of one color within one limit, or None when it fits the
    checker's limit (owned minus `checker` percent)."""
    inside = [d for d in demands if d.thin <= thin and d.layers <= layers and d.need(color) > 0]
    need = sum(d.need(color) for d in inside)
    if need == 0:
        return None
    code = _code(color)
    top_layers = tallest_top(rows, code, thin, layers)
    room = sum(min(d.need(color), d.tops * min(d.layers, top_layers)) for d in inside)

    def allowed(margin: float) -> tuple[int, int]:
        stack = budget(rows, code, thin, layers, top=False, margin=margin)
        return stack, min(budget(rows, code, thin, layers, top=True, margin=margin), room)

    limit = sum(allowed(checker))
    if need <= limit:
        return None
    stack, tops = allowed(planner)
    by_size = sorted(((d.name, d.need(color)) for d in inside), key=lambda item: (-item[1], item[0]))
    return Shortfall(color, thin, layers, need, stack, tops, tuple(by_size), limit)


def check_demands(
    demands: Sequence[Demand],
    inventory: Inventory,
    margin: float | None = None,
    planner: float | None = None,
) -> list[Shortfall]:
    """The worst shortfall per color (and for all colors together), biggest first.

    margin: the checker margin in percent (None: the setting). planner: the planner
    margin, for the budget in the messages; never smaller than the checker margin."""
    margin = checker_margin(margin)
    planner = max(planner_margin(planner), margin)
    rows = stock(inventory)
    colors = sorted({c for d in demands for c in d.cells if c != ANY}) + [ALL]
    limits = sorted({(t, h) for t in {d.thin for d in demands} for h in {d.layers for d in demands}})
    worst: dict[str, Shortfall] = {}
    for color in colors:
        for thin, layers in limits:
            found = shortfall(rows, demands, color, thin, layers, margin, planner)
            if found and (color not in worst or found.over > worst[color].over):
                worst[color] = found
    return sorted(worst.values(), key=lambda s: (-s.over, s.color))


def check_capacity(
    plan: Mapping,
    inventory: Inventory,
    *,
    require: bool = True,
    margin: float | None = None,
    planner: float | None = None,
) -> CapacityResult:
    """Check a plan (as a dict, like plan.json) against the checker's limit: the
    inventory minus `margin` percent (None: the VOXEL_CHECKER_MARGIN setting).
    `planner` is the planner margin, used for the budget in the messages.

    require: every section must have the capacity fields (fill, cells). With
    require=False, sections without `fill` are skipped and listed as unchecked,
    for plans made before this check."""
    sections = plan.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, str) or not sections:
        return CapacityResult(("the plan has no sections",), ())
    demands, errors, unchecked = [], [], []
    for n, section in enumerate(sections, start=1):
        name = str(section.get("name", n)) if isinstance(section, Mapping) else str(n)
        if not isinstance(section, Mapping):
            errors.append(f"section {name}: not an object")
        elif section.get("fill") is None and not require:
            unchecked.append(name)
        else:
            try:
                demands.append(section_demand(section))
            except ValueError as err:
                errors.append(f"section {name!r}: {err}")
    errors += special_errors(demands, inventory)
    if errors:  # the numbers cannot be trusted yet
        return CapacityResult(tuple(errors), (), tuple(unchecked))
    return CapacityResult((), tuple(check_demands(exposed_tops(demands), inventory, margin, planner)), tuple(unchecked))


# --- The table the planner gets ---------------------------------------------------


def capacity_table(inventory: Inventory, margin: float | None = None) -> str:
    """The planner's budget: plate-cells of plain bricks and plates per color, by how
    wide the parts are, after the planner margin (percent; None: the
    VOXEL_PLANNER_MARGIN setting). Never the full owned numbers."""
    margin = planner_margin(margin)
    rows = stock(inventory)
    inf = float("inf")
    lines = []
    for code in {r.color for r in rows}:
        total = budget(rows, code, inf, inf, top=False, margin=margin)
        if total < TABLE_MIN_CELLS:
            continue
        widths = [budget(rows, code, w, inf, top=False, margin=margin) for w in TABLE_WIDTHS]
        flat = budget(rows, code, inf, 1, top=False, margin=margin)
        tops = budget(rows, code, inf, inf, top=True, margin=margin)
        cells = " | ".join(f"{v:,}" for v in [*widths, total, flat, tops])
        lines.append((-total, f"{color_name(code)} | {cells}"))
    head = (
        "color | "
        + " | ".join(f"parts up to {w} wide" for w in TABLE_WIDTHS)
        + " | any width | of that, 1 plate tall | tops only (tiles, slopes)"
    )
    return "\n".join([head, *(line for _, line in sorted(lines))])


CAPACITY_RULES = (
    "Capacity: a plate-cell is 1 stud x 1 stud x 1 plate (a 2 x 4 brick is 24 plate-cells). "
    "The table shows the plate-cells you may plan with per color, in plain bricks and plates, "
    "the only parts counted as fill. A wall N studs thick can only use parts at most N wide, so thin "
    "walls draw on the first columns only. Tiles and slopes only fit on top of a column. "
    "Tall shapes are expensive: every brick of height costs 3 plate-cells per stud. Other "
    "parts (round, cut, modified bricks and plates) count only in a section that names "
    "them in its parts list. Size every section so the colors fit."
)


# --- Command line -----------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that the parts you own can fill a plan.")
    parser.add_argument("plan", nargs="?", help="a plan.json with fill / cells on every section")
    parser.add_argument("--inventory", default="inventory.json")
    parser.add_argument("--table", action="store_true", help="print the plate-cells to plan with, per color")
    parser.add_argument(
        "--planner-margin",
        type=float,
        default=None,
        help=f"percent held back from the planner (default: ${PLANNER_MARGIN_ENV}, else {PLANNER_MARGIN:g})",
    )
    parser.add_argument(
        "--checker-margin",
        type=float,
        default=None,
        help=f"percent a plan may not use (default: ${CHECKER_MARGIN_ENV}, else {CHECKER_MARGIN:g})",
    )
    args = parser.parse_args(argv)
    try:
        planner, checker = planner_margin(args.planner_margin), checker_margin(args.checker_margin)
    except ValueError as err:
        parser.error(str(err))
    inventory = load_inventory(args.inventory)
    if args.table:
        print(CAPACITY_RULES + "\n\n" + capacity_table(inventory, planner))
        return 0
    if not args.plan:
        parser.error("give a plan file, or --table")
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        print(f"{args.plan} could not be read: {err}", file=sys.stderr)
        return 2
    if not isinstance(plan, Mapping):
        print(f"{args.plan} is not a JSON object", file=sys.stderr)
        return 2
    result = check_capacity(plan, inventory, margin=checker, planner=planner)
    print(result.report())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
