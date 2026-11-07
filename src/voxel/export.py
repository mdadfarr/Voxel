"""Turn a finished build into a plain JSON brick list for the viewer.

The viewer plays the build back one layer at a time, so the list is ordered
the way you would build it: bottom layer first, then front to back.

Units: x and y are in studs. z is in "stud units": a brick body is 1.2 tall
and a plate is 0.4. A part's top includes the stud nubs (about 0.2).

Run it:
    python -m voxel.export records/<id>/build.py -o build.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import bricklayer as bl

FORMAT_VERSION = 1
LAYER_PRECISION = 3  # round heights so two bricks at the same level share a layer


def _name(part_id: str) -> str:
    try:
        return " ".join(bl.PartSpec(part_id).name.split())
    except Exception:
        return part_id


def _round(values) -> list[float]:
    return [round(float(v), 4) for v in values]


def export_build(build) -> dict:
    """The build as a JSON-ready dict: bricks in build order, layers, part counts."""
    rows = []
    for placement in build.flattened_placements:
        low, high = placement.bounding_box
        element = placement.content
        color = element.color
        rows.append(
            {
                "part": str(element.ldraw_id),
                "name": _name(str(element.ldraw_id)),
                "color": "#" + str(color.rgb).lstrip("#"),
                "color_name": color.display_name,
                "finish": color.finish.name,
                "min": _round(low.to_stud_units()),
                "max": _round(high.to_stud_units()),
            }
        )

    rows.sort(key=lambda r: (round(r["min"][2], LAYER_PRECISION), r["min"][1], r["min"][0]))
    levels = sorted({round(r["min"][2], LAYER_PRECISION) for r in rows})
    layer_of = {level: i + 1 for i, level in enumerate(levels)}

    bricks = []
    layers: dict[int, list[int]] = {i + 1: [] for i in range(len(levels))}
    for number, row in enumerate(rows, start=1):
        layer = layer_of[round(row["min"][2], LAYER_PRECISION)]
        bricks.append({"id": number, "layer": layer, **row})
        layers[layer].append(number)

    counts = Counter((b["part"], b["name"], b["color_name"]) for b in bricks)
    if bricks:
        size = _round(
            [
                max(b["max"][axis] for b in bricks) - min(b["min"][axis] for b in bricks)
                for axis in range(3)
            ]
        )
    else:
        size = [0.0, 0.0, 0.0]

    return {
        "version": FORMAT_VERSION,
        "units": "x and y in studs; z in stud units (brick body 1.2, plate 0.4)",
        "size": size,
        "bricks": bricks,
        "layers": [
            {"layer": number, "z": levels[number - 1], "bricks": ids}
            for number, ids in layers.items()
        ],
        "parts": [
            {"part": part, "name": name, "color_name": color_name, "count": count}
            for (part, name, color_name), count in sorted(counts.items())
        ],
    }


def load_build(build_path: str | Path):
    """Run a build.py's create_build() and return the build."""
    from brickwright.tools import build_execution

    return build_execution.create_build(str(build_path))


def export_file(build_path: str | Path, out_path: str | Path) -> Path:
    """Write the JSON for one build.py. Returns the path written."""
    data = export_build(load_build(build_path))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m voxel.export", description="Export a build.py as a JSON brick list."
    )
    parser.add_argument("build", help="path to a build.py")
    parser.add_argument("-o", "--out", default="build.json")
    args = parser.parse_args(argv)
    try:
        out = export_file(args.build, args.out)
    except Exception as err:  # the build file is written by an AI, so say what went wrong plainly
        print(f"Error: could not export {args.build}: {err}", file=sys.stderr)
        return 1
    data = json.loads(out.read_text(encoding="utf-8"))
    print(f"Wrote {out}: {len(data['bricks'])} bricks in {len(data['layers'])} layers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())