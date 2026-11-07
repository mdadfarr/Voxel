"""Local viewer: watch a design get built in 3D with the real LDraw part shapes.

Run it:
    python -m voxel.serve records/<id>            # a record: its snapshots, then the final build
    python -m voxel.serve path/to/build.py        # one build
    python -m voxel.serve records/<id> --ldraw C:/Users/you/ldraw

Then open the address it prints. It needs your LDraw parts library (the same
one BrickWright uses to render): --ldraw, $LDRAW_LIBRARY_PATH, ~/ldraw or C:/LDraw.

Your own sounds: put audio files in a folder called "sounds" (or pass --sounds) and
name them place1.wav, place2.wav (a brick lands, one is picked at random), layer.wav
(a layer is finished), done.wav (the build passes) and fail.wav (a check fails).
Missing ones fall back to the built in sounds. Only use audio you have the right to use.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import webbrowser
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .export import export_build, load_build

VIEWER = Path(__file__).parent / "web" / "viewer.html"
AUDIO = {".mp3", ".wav", ".ogg", ".m4a"}


def find_ldraw(given: str | None = None) -> Path | None:
    """The LDraw library folder (the one that holds parts/ and p/)."""
    candidates = [given, os.environ.get("LDRAW_LIBRARY_PATH"), Path.home() / "ldraw", "C:/LDraw"]
    for c in candidates:
        if not c:
            continue
        root = Path(c)
        for folder in (root, root / "ldraw"):
            if (folder / "parts").is_dir() and (folder / "p").is_dir():
                return folder
    return None


def collect_versions(path: str | Path) -> list[dict]:
    """The builds to show, in order: a record's snapshots then its final build.py."""
    path = Path(path)
    if path.is_file():
        return [{"id": path.stem, "build": path, "ok": True, "note": "Build ready"}]
    versions = []
    for snap in sorted((path / "snapshots").glob("*/build.py")):
        report = snap.parent / "report.json"
        ok, note = True, "All checks passed"
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            ok = bool(data.get("ok"))
            failed = [c["name"] for c in data.get("checks", []) if not c.get("passed")]
            if data.get("build_error"):
                failed = ["the build crashed"]
            note = "Check failed: " + ", ".join(failed) if not ok and failed else note
        versions.append({"id": snap.parent.name, "build": snap, "ok": ok, "note": note})
    final = path / "build.py"
    if final.exists():
        last = versions[-1] if versions else None
        same = last and last["build"].read_text(encoding="utf-8") == final.read_text(encoding="utf-8")
        if not same:
            versions.append({"id": "final", "build": final, "ok": True, "note": "All checks passed"})
    return versions


def build_payload(versions: list[dict]) -> dict:
    """Everything the page needs, in one JSON-ready dict."""
    palette, part_list, color_at, part_at = [], [], {}, {}
    out = []
    for v in versions:
        try:
            data = export_build(load_build(v["build"]))
        except Exception as err:
            print(f"  skipped {v['id']}: {err}", file=sys.stderr)
            continue
        bricks = data["bricks"]
        if not bricks or any("matrix" not in b for b in bricks):
            print(f"  skipped {v['id']}: no LDraw placement data", file=sys.stderr)
            continue
        sx = min(b["min"][0] for b in bricks)
        sy = min(b["min"][1] for b in bricks)
        sz = min(b["min"][2] for b in bricks)
        rows = []
        for b in bricks:
            if b["color"] not in color_at:
                color_at[b["color"]] = len(palette)
                palette.append([b["color"], b["color_name"], b["finish"]])
            key = (b["part"], b["file"])
            if key not in part_at:
                part_at[key] = len(part_list)
                part_list.append([b["part"], b["name"], b["file"]])
            lo = [round(b["min"][0] - sx, 2), round(b["min"][1] - sy, 2), round(b["min"][2] - sz, 2)]
            hi = [round(b["max"][0] - sx, 2), round(b["max"][1] - sy, 2), round(b["max"][2] - sz, 2)]
            flag = {"": 0, "disconnected": 1, "floating": 2, "colliding": 3}.get(b.get("flag", ""), 0)
            rows.append(lo + hi + [color_at[b["color"]], part_at[key], b["layer"]] + b["matrix"] + [flag])
        out.append({"id": v["id"], "ok": v["ok"], "note": v["note"], "shift": [sx, sy], "bricks": rows})
    return {"palette": palette, "parts": part_list, "versions": out}


def make_handler(payload: bytes, ldraw: Path, sounds: Path):
    page = VIEWER.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, kind: str):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route, _, query = self.path.partition("?")
            if route in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if route == "/data.json":
                return self._send(200, payload, "application/json")
            if route == "/sounds/list":
                names = sorted(f.name for f in sounds.glob("*") if f.suffix.lower() in AUDIO) if sounds.is_dir() else []
                return self._send(200, json.dumps(names).encode(), "application/json")
            if route.startswith("/sounds/"):
                from urllib.parse import unquote

                target = (sounds / unquote(route[len("/sounds/"):])).resolve()
                if sounds.is_dir() and sounds.resolve() in target.parents and target.is_file():
                    return self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "audio/mpeg")
                return self._send(404, b"not found", "text/plain")
            if route == "/model":
                # A one-line LDraw file: one part in one colour. The page loads each part this way.
                q = {k: v[0] for k, v in parse_qs(query).items()}
                try:
                    code = int(q["code"])
                    hex_ = "#" + q["hex"].lstrip("#")[:6]
                    edge = "#" + q["edge"].lstrip("#")[:6]
                    part = Path(q["file"]).name
                except (KeyError, ValueError):
                    return self._send(400, b"bad request", "text/plain")
                alpha = f" ALPHA {int(q['alpha'])}" if q.get("alpha") else ""
                text = (
                    "0 Model\n0 !LDRAW_ORG Model\n"
                    f"0 !COLOUR c{code} CODE {code} VALUE {hex_} EDGE {edge}{alpha}\n"
                    f"1 {code} 0 0 0 1 0 0 0 1 0 0 0 1 {part}\n"
                )
                return self._send(200, text.encode(), "text/plain; charset=utf-8")
            for top in ("parts", "p"):
                if route.startswith(f"/{top}/"):
                    rel = route[1:].replace("\\", "/")
                    for name in (rel, rel.lower()):
                        target = (ldraw / name).resolve()
                        if ldraw.resolve() in target.parents and target.is_file():
                            return self._send(200, target.read_bytes(), "text/plain; charset=utf-8")
                    return self._send(404, b"not found", "text/plain")
            self._send(404, b"not found", "text/plain")

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voxel.serve", description=__doc__.split("\n")[0])
    parser.add_argument("path", help="a record folder or a build.py")
    parser.add_argument("--ldraw", default=None, help="your LDraw library folder")
    parser.add_argument("--sounds", default="sounds", help="folder with your own sound files (optional)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    ldraw = find_ldraw(args.ldraw)
    if ldraw is None:
        print("Error: could not find your LDraw library. Pass --ldraw <folder that holds parts and p>.", file=sys.stderr)
        return 1
    versions = collect_versions(args.path)
    if not versions:
        print(f"Error: no build.py found in {args.path}", file=sys.stderr)
        return 1
    print(f"Reading {len(versions)} build(s)...")
    payload = build_payload(versions)
    if not payload["versions"]:
        print("Error: none of the builds could be read.", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(json.dumps(payload).encode(), ldraw, Path(args.sounds)))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Showing {len(payload['versions'])} version(s) with parts from {ldraw}")
    print(f"Open {url}   (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())