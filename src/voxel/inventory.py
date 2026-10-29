"""Inventory: which parts (and how many) the user owns.

A part is identified by its LDraw part id plus its LDraw color code, because a
red 2x4 and a blue 2x4 are different pieces.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import bricklayer as bl

PartKey = tuple[str, int]  # (ldraw part id, ldraw color code)


def color_code(color: bl.Color | object) -> int:
    """LDraw color code for a BrickLayer Color (or an imported LDrawColor)."""
    return int(color.ldraw_id)  # type: ignore[attr-defined]


def color_label(code: int) -> str:
    """Readable color name for messages, falling back to the raw code."""
    for c in bl.Color:
        if c.ldraw_id == code:
            return c.display_name
    return f"color {code}"


def key_label(key: PartKey) -> str:
    part_id, code = key
    return f"{color_label(code)} part {part_id}"


@dataclass(frozen=True)
class Inventory:
    """How many of each (part, color) the user owns."""

    counts: dict[PartKey, int]

    def owned(self, key: PartKey) -> int:
        return self.counts.get(key, 0)

    @classmethod
    def from_entries(cls, entries: list[dict]) -> Inventory:
        """Build from a list like [{"part": "3001", "color": "RED", "count": 8}].

        `color` is a bricklayer Color name (RED) or an LDraw color code (4).
        Repeated entries for the same part and color are added together.
        """
        counts: Counter[PartKey] = Counter()
        for i, entry in enumerate(entries):
            try:
                part = str(entry["part"])
                count = int(entry["count"])
                raw_color = entry["color"]
            except KeyError as missing:
                raise ValueError(f"inventory entry {i} is missing {missing}") from None
            if count < 0:
                raise ValueError(f"inventory entry {i} has a negative count")
            if isinstance(raw_color, int):
                code = raw_color
            else:
                try:
                    code = bl.Color[str(raw_color).upper()].ldraw_id
                except KeyError:
                    raise ValueError(
                        f"inventory entry {i}: unknown color {raw_color!r}"
                    ) from None
            counts[(part, code)] += count
        return cls(dict(counts))


def load_inventory(path: str | Path) -> Inventory:
    """Read an inventory JSON file: a list of {part, color, count} entries."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("inventory file must contain a list of entries")
    return Inventory.from_entries(data)


def count_build(build: bl.LegoBuild) -> Counter[PartKey]:
    """Count every part in a build, including parts inside nested sub-assemblies."""
    counts: Counter[PartKey] = Counter()
    for placement in build.flattened_placements:
        element = placement.content
        counts[(element.ldraw_id, color_code(element.color))] += 1
    return counts