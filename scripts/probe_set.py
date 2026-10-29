"""Probe: fetch one set from Rebrickable and show what the data looks like.

Run from the Voxel folder:
    python scripts/probe_set.py            (uses set 10242-1)
    python scripts/probe_set.py 75869-1

It never prints your API key.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def read_key(env_path=".env"):
    if not Path(env_path).exists():
        raise SystemExit(
            "No .env file found. Run this from the Voxel folder, where .env lives."
        )
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("REBRICKABLE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("REBRICKABLE_API_KEY not found in .env")


def has_ldraw(external_ids):
    """True if an external_ids object has an LDraw entry (shape may vary)."""
    if not isinstance(external_ids, dict):
        return False
    entry = external_ids.get("LDraw")
    if entry is None:
        return False
    if isinstance(entry, dict):
        return bool(entry.get("ext_ids"))
    return bool(entry)


def summarize(rows):
    total = len(rows)
    spare = sum(1 for r in rows if r.get("is_spare"))
    part_ok = sum(1 for r in rows if has_ldraw((r.get("part") or {}).get("external_ids")))
    color_ok = sum(1 for r in rows if has_ldraw((r.get("color") or {}).get("external_ids")))
    return {
        "rows": total,
        "spare_rows": spare,
        "rows_with_part_ldraw_id": part_ok,
        "rows_with_color_ldraw_id": color_ok,
    }


def main():
    set_num = sys.argv[1] if len(sys.argv) > 1 else "10242-1"
    key = read_key()
    url = (
        f"https://rebrickable.com/api/v3/lego/sets/{set_num}/parts/"
        "?page_size=1000&inc_part_details=1&inc_color_details=1"
    )
    request = urllib.request.Request(url, headers={"Authorization": f"key {key}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {err.code} from Rebrickable: {body}")
    except urllib.error.URLError as err:
        raise SystemExit(f"Could not reach Rebrickable: {err.reason}")

    rows = data.get("results", [])
    print("top level keys:", sorted(data.keys()))
    print("total count reported:", data.get("count"), "| next page:", bool(data.get("next")))
    print()
    print("summary:", json.dumps(summarize(rows), indent=2))
    if rows:
        print()
        print("first row (raw):")
        print(json.dumps(rows[0], indent=2)[:2500])


if __name__ == "__main__":
    main()