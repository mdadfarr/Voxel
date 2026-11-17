"""What kind of part a part id is, from its LDraw name.

Shared by the inventory summary (expand), the spare set (plan) and prompts.
"""

from __future__ import annotations

import math
import re

import bricklayer as bl

BASIC_PART = re.compile(r"^(Brick|Plate) \d+ x \d+$")
SHAPES = ("slope", "curved", "round")
SPARE_KINDS = ("basic", "slope", "curved", "round", "tile")
_SIZE = re.compile(r"(\d+(?:\.\d+)?) x (\d+(?:\.\d+)?)(?: x (\d+(?:\.\d+)?))?")


def part_name(part_id: str) -> str:
    try:
        return " ".join(bl.PartSpec(part_id).name.split())
    except Exception:
        return part_id


def shape_kind(name: str) -> str | None:
    """'slope', 'curved' or 'round' for a shaped part name, else None."""
    if name.startswith(("Minifig", "Technic")):
        return None
    if "Curved" in name or name.startswith("Arch"):
        return "curved"
    if name.startswith(("Slope", "Wedge")):
        return "slope"
    if "Round" in name or name.startswith(("Dish", "Cone", "Dome")):
        return "round"
    return None


def part_kind(name: str) -> str | None:
    """'basic' (plain Brick/Plate N x M), a shape kind, 'tile', or None."""
    if BASIC_PART.match(name):
        return "basic"
    shape = shape_kind(name)
    if shape:
        return shape
    if name.startswith("Tile"):
        return "tile"
    return None


def part_size(name: str) -> tuple[float, float]:
    """(stud area, height) from the first 'N x M (x K)' in the name; (inf, inf) if none."""
    match = _SIZE.search(name)
    if not match:
        return (math.inf, math.inf)
    w, d, h = match.groups()
    return (float(w) * float(d), float(h) if h else 0.0)
