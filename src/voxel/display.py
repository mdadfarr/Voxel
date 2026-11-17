"""Turn a --no-plan build for display: the review render and the viewer file.

Without a plan the builder builds in whatever orientation is easiest and names
it in build.py with two string constants:

    FACE = "+z"   # the axis the object's front face points to
    UP = "+y"     # the axis that points to the object's top

Voxel turns the build so that FACE points to +y (the side the front view shows)
and UP to +z (up). Only what is shown is turned: out/build.display.ldr (open it
in an LDraw viewer) and out/build.display.png (the image the reviewer judges).
build.py, out/build.ldr, the gate and the manual (build.stepped.ldr) are never
changed.

The pose is read from build.py's source with ast; the file is not run. A
missing or invalid pose only warns: the build is shown as built, so a front that
does not face the viewer is caught by the review itself (item 5).
"""

from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

AXES: dict[str, tuple[int, int, int]] = {
    "+x": (1, 0, 0),
    "-x": (-1, 0, 0),
    "+y": (0, 1, 0),
    "-y": (0, -1, 0),
    "+z": (0, 0, 1),
    "-z": (0, 0, -1),
}
DISPLAY_FACE = "+y"  # the front view shows the +y side
DISPLAY_UP = "+z"
DISPLAY_LDR = "build.display.ldr"  # in the record's out/ folder
DISPLAY_PNG = "build.display.png"
OUT_DIR = "out"

# Build axes (x, y, z; z up) to LDraw axes: X = x, Y = -z, Z = y (bricklayer's writer).
TO_LDRAW = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])


@dataclass(frozen=True)
class Pose:
    face: str
    up: str

    @property
    def turned(self) -> bool:
        return (self.face, self.up) != (DISPLAY_FACE, DISPLAY_UP)

    def text(self) -> str:
        return f"FACE={self.face}, UP={self.up}"


def _normal(value: str) -> str:
    value = value.strip().lower()
    return value if value[:1] in "+-" else "+" + value


def parse_pose(source: str) -> tuple[Pose | None, str]:
    """(pose, "") or (None, why not) from build.py's source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        return None, f"build.py does not parse ({err.msg})"
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in ("FACE", "UP"):
                found[target.id] = value.value if isinstance(value, ast.Constant) else None
    missing = [name for name in ("FACE", "UP") if name not in found]
    if missing:
        return None, f"build.py does not set {' and '.join(missing)}"
    values = {}
    for name in ("FACE", "UP"):
        raw = found[name]
        if not isinstance(raw, str) or _normal(raw) not in AXES:
            return None, f"{name} = {raw!r} is not one of {', '.join(AXES)}"
        values[name] = _normal(raw)
    if np.dot(AXES[values["FACE"]], AXES[values["UP"]]) != 0:
        return None, f"FACE ({values['FACE']}) and UP ({values['UP']}) must be at right angles"
    return Pose(values["FACE"], values["UP"]), ""


def read_pose(build_path: str | Path) -> tuple[Pose | None, str]:
    path = Path(build_path)
    if not path.exists():
        return None, "no build.py"
    return parse_pose(path.read_text(encoding="utf-8"))


def pose_note(build_path: str | Path) -> str:
    """The pose in one line, for review.json."""
    pose, why = read_pose(build_path)
    return pose.text() if pose is not None else f"none ({why}); shown as built"


def rotation(pose: Pose) -> np.ndarray:
    """The build-frame rotation that sends FACE to +y and UP to +z."""
    face = np.array(AXES[pose.face])
    up = np.array(AXES[pose.up])
    return np.array([np.cross(face, up), face, up])  # rows: orthonormal, det +1


def ldraw_rotation(pose: Pose) -> np.ndarray:
    """rotation(pose) in LDraw axes."""
    return TO_LDRAW @ rotation(pose) @ TO_LDRAW.T


def _number(value: float) -> str:
    value = round(float(value), 6)
    if value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def rotate_ldr(text: str, turn: np.ndarray) -> str:
    """The LDraw text with every part of the main model turned by `turn` (LDraw axes).
    Sub-model blocks of a multipart file keep their own frame."""
    out = []
    files = 0
    for line in text.splitlines():
        fields = line.split()
        if fields[:2] == ["0", "FILE"]:
            files += 1
        if files <= 1 and len(fields) >= 15 and fields[0] == "1":
            nums = np.array([float(v) for v in fields[2:14]])
            position = turn @ nums[:3]
            matrix = turn @ nums[3:].reshape(3, 3)
            values = [*position, *matrix.flatten()]
            line = " ".join(["1", fields[1], *(_number(v) for v in values), " ".join(fields[14:])])
        out.append(line)
    return "".join(line + "\n" for line in out)


def write_display_ldr(record) -> tuple[Path, Pose | None, str]:
    """Write out/build.display.ldr from out/build.ldr: turned if build.py names a pose,
    else a copy. Returns (path, pose or None, why there is no pose)."""
    out = Path(record.path) / OUT_DIR
    source = out / "build.ldr"
    target = out / DISPLAY_LDR
    pose, why = read_pose(record.build_path)
    if pose is not None and pose.turned:
        text = source.read_text(encoding="utf-8")
        target.write_text(rotate_ldr(text, ldraw_rotation(pose)), encoding="utf-8")
    else:
        shutil.copyfile(source, target)
    return target, pose, why


def _render_ldr(ldr: str, glb: str, png: str) -> str:
    """Child process: LDraw -> glTF -> four-view PNG. Returns "" or the error."""
    import traceback

    try:
        from brickwright.tools.rendering import mesh2png
        from ldraw2mesh import convert

        convert(Path(ldr), Path(glb), gaps=True)
        mesh2png(Path(glb), Path(png))
        return ""
    except Exception:
        return traceback.format_exc()


def render_ldr(ldr: Path, png: Path, timeout: float) -> str:
    """Render an LDraw file to a four-view PNG the same way BrickWright renders a
    build, in a guarded child process. Returns "" or the error."""
    from brickwright.tools import build_execution

    glb = png.with_suffix(".glb")
    return build_execution.run_guarded(
        _render_ldr, (str(ldr), str(glb), str(png)), timeout, lambda e: f"render failed: {e}"
    )


def display_image(record, image: Path, timeout: float) -> tuple[Path, str]:
    """The image to review for a build BrickWright has just rendered to `image`
    (out/build.ldr is current). Writes out/build.display.ldr; when build.py names a
    pose that turns the build, renders it to out/build.display.png and returns that.
    Returns (image, warning): the warning is "" unless the build is shown as built
    for want of a usable pose, or the turned render failed."""
    source = Path(record.path) / OUT_DIR / "build.ldr"
    if not source.exists():
        return image, "no out/build.ldr to turn; the build is shown as built"
    ldr, pose, why = write_display_ldr(record)
    if pose is None:
        return image, f"no display pose: {why}; the build is shown as built"
    if not pose.turned:
        return image, ""
    png = ldr.with_name(DISPLAY_PNG)
    error = render_ldr(ldr, png, timeout)
    if error or not png.exists():
        return image, f"the turned render failed, the build is shown as built: {error.strip()[-300:]}"
    return png, ""
