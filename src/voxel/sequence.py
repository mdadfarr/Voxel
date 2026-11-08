"""Compute the build steps in code, so the builder only cares how the build looks.

sequence(build) orders the parts so each one is placed on, under or against a
part placed before it, and splits that order into pages of at most
MAX_STEP_PARTS parts:

- Neighbors: parts clipped together (build._mating.pairs, both directions),
  and parts resting on another (build._support_edges, supporter to supported).
- Start: every part touching the ground, in code order.
- Walk: go through the parts in code order and place each part with a placed
  neighbor; skip the rest. Repeat from the top until all parts are placed, or
  until a full pass places nothing: those parts are returned as unplaced.
- Pages: each placed part's anchor is its earliest-placed neighbor (the ground
  for the first parts). Within a pass, parts with the same anchor go on the
  same page when they fit; a group bigger than a page fills whole pages and
  leaves the rest open for the next group.

stepped_build(build, pages) is the same build with the parts in page order and
a step marker after each page; voxel.steps.check_steps is the safety check on it.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from itertools import accumulate

import bricklayer as bl
from bricklayer.modeling.build import _is_grounded

from .steps import MAX_STEP_PARTS, describe_leaf

GROUND = -1  # the anchor of parts that touch the ground


@dataclass(frozen=True)
class Sequence:
    pages: list[list[int]]  # leaf indices into build.flattened_placements, per page
    unplaced: list[str]  # parts nothing holds up: "Red 3001 at (x,y,z)"
    moved: int  # parts out of code order: parts minus the longest run kept in code order

    @property
    def ok(self) -> bool:
        return not self.unplaced


def holders(build: bl.LegoBuild) -> list[set[int]]:
    """For each part, the parts that can hold it up once placed."""
    held_by: list[set[int]] = [set() for _ in build.flattened_placements]
    for a, b in build._mating.pairs:  # clipped together: both ways
        held_by[a].add(b)
        held_by[b].add(a)
    for supporter, supported in build._support_edges:  # resting on: one way
        held_by[supported].add(supporter)
    return held_by


def walk(build: bl.LegoBuild) -> tuple[list[list[tuple[int, int]]], list[int]]:
    """Passes of (part, anchor), in placing order, and the parts never placed."""
    leaves = build.flattened_placements
    held_by = holders(build)
    placed: dict[int, int] = {}  # part -> placing order
    grounded = [i for i, leaf in enumerate(leaves) if _is_grounded(leaf, build._ground)]
    for i in grounded:
        placed[i] = len(placed)
    passes = [[(i, GROUND) for i in grounded]]
    while len(placed) < len(leaves):
        this_pass = []
        for i in range(len(leaves)):
            if i in placed:
                continue
            ready = [h for h in held_by[i] if h in placed]
            if ready:
                anchor = min(ready, key=placed.__getitem__)
                placed[i] = len(placed)
                this_pass.append((i, anchor))
        if not this_pass:
            break
        passes.append(this_pass)
    unplaced = [i for i in range(len(leaves)) if i not in placed]
    return passes, unplaced


def paginate(passes: list[list[tuple[int, int]]], limit: int = MAX_STEP_PARTS) -> list[list[int]]:
    """Pages of at most `limit` parts; parts with the same anchor share a page when they fit."""
    pages: list[list[int]] = []
    current: list[int] = []
    for this_pass in passes:
        groups: dict[int, list[int]] = {}  # anchor -> parts, in order of first appearance
        for part, anchor in this_pass:
            groups.setdefault(anchor, []).append(part)
        for group in groups.values():
            if len(current) + len(group) <= limit:
                current += group
                continue
            if current:
                pages.append(current)
            current = []
            for start in range(0, len(group), limit):
                chunk = group[start : start + limit]
                if len(chunk) == limit:
                    pages.append(chunk)
                else:
                    current = chunk
    if current:
        pages.append(current)
    return pages


def moved_parts(order: list[int]) -> int:
    """Parts out of code order: the fewest parts to move to get back to code order."""
    tails: list[int] = []  # longest increasing subsequence, patience style
    for value in order:
        k = bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
        else:
            tails[k] = value
    return len(order) - len(tails)


def sequence(build: bl.LegoBuild) -> Sequence:
    passes, unplaced = walk(build)
    leaves = build.flattened_placements
    pages = paginate(passes)
    return Sequence(
        pages=pages,
        unplaced=[describe_leaf(leaves[i]) for i in unplaced],
        moved=moved_parts([i for page in pages for i in page]),
    )


def stepped_build(build: bl.LegoBuild, pages: list[list[int]]) -> bl.LegoBuild:
    """The build's parts in page order, flattened, with build.add_step() after each page."""
    leaves = build.flattened_placements
    return bl.LegoBuild(
        [leaves[i] for page in pages for i in page],
        steps=list(accumulate(len(page) for page in pages)),  # one marker after each page
    )
