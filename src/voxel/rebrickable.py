"""Rebrickable: turn LEGO set numbers into an inventory of parts you own.

Spare parts are counted as owned, because they are in the box.
Parts BrickLayer cannot use are reported, never silently dropped.

Run it:
    python -m voxel.rebrickable 10242 75869
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import bricklayer as bl

from .inventory import Inventory, PartKey

API_ROOT = "https://rebrickable.com/api/v3"
CACHE_DIR = Path(".cache") / "rebrickable"
SUPPORTED_COLORS = {c.ldraw_id: c for c in bl.Color}


class RebrickableError(Exception):
    """A problem talking to Rebrickable, worded so a person can act on it."""


def normalize_set_number(text: str) -> str:
    """'75869' becomes '75869-1'. Rebrickable wants the version suffix."""
    value = text.strip()
    if not re.fullmatch(r"[A-Za-z0-9]+(-\d+)?", value):
        raise ValueError(f"{text!r} does not look like a LEGO set number")
    return value if "-" in value else value + "-1"


def read_api_key(env_path: str | Path = ".env") -> str:
    """The key from the REBRICKABLE_API_KEY variable, or from the .env file."""
    key = os.environ.get("REBRICKABLE_API_KEY", "").strip()
    if key:
        return key
    path = Path(env_path)
    if not path.exists():
        raise RebrickableError(
            "No API key found. Put REBRICKABLE_API_KEY=your_key in a .env file "
            "in the folder you run this from."
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("REBRICKABLE_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise RebrickableError("REBRICKABLE_API_KEY is missing or empty in .env")


def http_get_json(url, key, what, *, retries=3, sleep=time.sleep):
    """GET one URL from Rebrickable. Waits and retries when it says slow down."""
    request = urllib.request.Request(url, headers={"Authorization": f"key {key}"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < retries:
                sleep(2 * (attempt + 1))
                continue
            if err.code == 401:
                raise RebrickableError("Rebrickable rejected the API key.") from None
            if err.code == 404:
                raise RebrickableError(f"Rebrickable has no {what}.") from None
            raise RebrickableError(f"Rebrickable returned HTTP {err.code} for {what}.") from None
        except urllib.error.URLError as err:
            raise RebrickableError(f"Could not reach Rebrickable: {err.reason}") from None
    raise RebrickableError("Rebrickable kept asking us to slow down. Try again soon.")


def fetch_set_rows(set_num, key, *, cache_dir=CACHE_DIR, refresh=False, get_json=http_get_json):
    """All part rows for one set. Returns (rows, came_from_cache)."""
    cache_file = Path(cache_dir) / f"{set_num}.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text(encoding="utf-8")), True
    url = (
        f"{API_ROOT}/lego/sets/{set_num}/parts/"
        "?page_size=1000&inc_part_details=1&inc_color_details=1"
    )
    rows: list[dict] = []
    while url:
        page = get_json(url, key, f"set {set_num}")
        rows.extend(page.get("results", []))
        url = page.get("next")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(rows), encoding="utf-8")
    return rows, False


@lru_cache(maxsize=None)
def _catalog_knows(part_id: str) -> bool:
    try:
        bl.Element(part_id, bl.Color.WHITE)
    except bl.InvalidPartError:
        return False
    return True


def pick_part_id(part: dict) -> tuple[str | None, str | None]:
    """The LDraw id BrickLayer knows for a Rebrickable part, or why there is none."""
    ids = [str(i) for i in ((part.get("external_ids") or {}).get("LDraw") or [])]
    if not ids:
        return None, "Rebrickable lists no LDraw id for this part"
    for part_id in ids:
        if _catalog_knows(part_id):
            return part_id, None
    return None, f"LDraw id {ids[0]} is not in BrickLayer's catalog"


def pick_color_code(color: dict) -> tuple[int | None, str | None]:
    """The first LDraw color code BrickLayer supports, or why there is none."""
    entry = (color.get("external_ids") or {}).get("LDraw")
    ids = []
    if isinstance(entry, dict):
        ids = [i for i in entry.get("ext_ids") or [] if isinstance(i, int)]
    if not ids:
        return None, "Rebrickable lists no LDraw color for this part"
    for code in ids:
        if code in SUPPORTED_COLORS:
            return code, None
    return None, f"color code {ids[0]} is not supported by BrickLayer"


@dataclass(frozen=True)
class Unusable:
    """Pieces you own that the design engine cannot use."""

    set_num: str
    part_num: str
    name: str
    color_name: str
    quantity: int
    reason: str


@dataclass(frozen=True)
class Converted:
    counts: Counter
    unusable: list[Unusable]
    spare_pieces: int


def convert_rows(rows: list[dict], set_num: str) -> Converted:
    """Rebrickable rows into counts keyed by (LDraw part id, LDraw color code)."""
    counts: Counter[PartKey] = Counter()
    unusable: list[Unusable] = []
    spare_pieces = 0
    for row in rows:
        quantity = int(row.get("quantity", 0))
        part = row.get("part") or {}
        color = row.get("color") or {}
        part_id, why_part = pick_part_id(part)
        code, why_color = pick_color_code(color)
        reason = "; ".join(r for r in (why_part, why_color) if r)
        if reason:
            unusable.append(
                Unusable(
                    set_num,
                    str(part.get("part_num", "?")),
                    str(part.get("name", "")),
                    str(color.get("name", "")),
                    quantity,
                    reason,
                )
            )
            continue
        counts[(part_id, code)] += quantity
        if row.get("is_spare"):
            spare_pieces += quantity
    return Converted(counts, unusable, spare_pieces)


@dataclass(frozen=True)
class InventoryBuild:
    inventory: Inventory
    unusable: list[Unusable]
    sets: dict[str, tuple[int, int]]  # set number -> (copies, usable pieces per copy)
    spare_pieces: int


def build_inventory(set_inputs, key, *, cache_dir=CACHE_DIR, refresh=False, get_json=None, sleep=time.sleep):
    """Fetch every set, convert, and add everything together.

    Typing the same set twice means you own two copies.
    """
    get_json = get_json or http_get_json
    copies: dict[str, int] = {}
    for text in set_inputs:
        set_num = normalize_set_number(text)
        copies[set_num] = copies.get(set_num, 0) + 1

    totals: Counter[PartKey] = Counter()
    unusable: list[Unusable] = []
    per_set: dict[str, tuple[int, int]] = {}
    spare_pieces = 0
    for n, (set_num, count) in enumerate(copies.items()):
        rows, from_cache = fetch_set_rows(
            set_num, key, cache_dir=cache_dir, refresh=refresh, get_json=get_json
        )
        converted = convert_rows(rows, set_num)
        for part_key, quantity in converted.counts.items():
            totals[part_key] += quantity * count
        unusable += [replace(u, quantity=u.quantity * count) for u in converted.unusable]
        spare_pieces += converted.spare_pieces * count
        per_set[set_num] = (count, sum(converted.counts.values()))
        if not from_cache and n < len(copies) - 1:
            sleep(1.1)  # Rebrickable allows about one request per second
    return InventoryBuild(Inventory(dict(totals)), unusable, per_set, spare_pieces)


def inventory_entries(inventory: Inventory) -> list[dict]:
    """The inventory as the JSON list that load_inventory reads."""
    return [
        {"part": part_id, "color": SUPPORTED_COLORS[code].name, "count": count}
        for (part_id, code), count in sorted(inventory.counts.items())
    ]


def write_outputs(result: InventoryBuild, out: str | Path, unusable_out: str | Path) -> None:
    Path(out).write_text(json.dumps(inventory_entries(result.inventory), indent=2) + "\n", encoding="utf-8")
    report = [u.__dict__ for u in result.unusable]
    Path(unusable_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def summary_text(result: InventoryBuild) -> str:
    usable = sum(result.inventory.counts.values())
    lost = sum(u.quantity for u in result.unusable)
    lines = []
    for set_num, (copies, pieces) in result.sets.items():
        lines.append(f"  {set_num}: {copies} copy, {pieces} usable pieces each" if copies == 1
                     else f"  {set_num}: {copies} copies, {pieces} usable pieces each")
    lines.append(f"Usable pieces: {usable} ({len(result.inventory.counts)} different parts)")
    lines.append(f"Spare pieces included: {result.spare_pieces}")
    if result.unusable:
        lines.append(f"Owned but unusable: {lost} pieces in {len(result.unusable)} part rows")
        for u in result.unusable[:5]:
            lines.append(f"  - {u.quantity} x {u.name} ({u.color_name}): {u.reason}")
        if len(result.unusable) > 5:
            lines.append("  ... the rest are in the unusable report file")
    else:
        lines.append("Owned but unusable: none")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m voxel.rebrickable",
        description="Build an inventory file from LEGO set numbers.",
    )
    parser.add_argument("sets", nargs="+", help="set numbers like 75869 or 10242-1")
    parser.add_argument("-o", "--out", default="inventory.json")
    parser.add_argument("--unusable-out", default="inventory.unusable.json")
    parser.add_argument("--refresh", action="store_true", help="ignore the saved copy and fetch again")
    args = parser.parse_args(argv)
    try:
        key = read_api_key()
        result = build_inventory(args.sets, key, refresh=args.refresh)
    except (RebrickableError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    write_outputs(result, args.out, args.unusable_out)
    print(summary_text(result))
    print(f"Wrote {args.out} and {args.unusable_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())