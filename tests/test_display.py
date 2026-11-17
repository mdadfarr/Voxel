import itertools
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from voxel import display as dp


def make_record(tmp_path, build_src: str, ldr: str | None) -> SimpleNamespace:
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    (tmp_path / "build.py").write_text(build_src)
    if ldr is not None:
        (tmp_path / "out" / "build.ldr").write_text(ldr)
    return SimpleNamespace(id="r", path=tmp_path, build_path=tmp_path / "build.py")


# --- Reading FACE and UP ----------------------------------------------------------


def test_parse_pose():
    pose, why = dp.parse_pose('import bricklayer\nFACE = "+z"\nUP: str = "y"\n')
    assert pose == dp.Pose("+z", "+y") and why == ""
    assert dp.parse_pose('FACE = "-X"\nUP = "+z"\n')[0] == dp.Pose("-x", "+z")


@pytest.mark.parametrize(
    "source, why",
    [
        ("x = 1\n", "build.py does not set FACE and UP"),
        ('FACE = "+z"\n', "build.py does not set UP"),
        ('FACE = "+w"\nUP = "+z"\n', "FACE = '+w' is not one of"),
        ('FACE = AXIS\nUP = "+z"\n', "FACE = None is not one of"),
        ('FACE = "+z"\nUP = "-z"\n', "FACE (+z) and UP (-z) must be at right angles"),
        ("def (:\n", "build.py does not parse"),
    ],
)
def test_parse_pose_rejects(source, why):
    pose, got = dp.parse_pose(source)
    assert pose is None and got.startswith(why)


def test_pose_inside_a_function_is_not_read():
    assert dp.parse_pose('def create_build():\n    FACE = "+z"\n    UP = "+y"\n')[0] is None


# --- The rotation -----------------------------------------------------------------

POSES = [dp.Pose(f, u) for f, u in itertools.permutations(dp.AXES, 2) if np.dot(dp.AXES[f], dp.AXES[u]) == 0]


def test_there_are_24_poses():
    assert len(POSES) == 24


@pytest.mark.parametrize("pose", POSES, ids=lambda p: p.text())
def test_rotation_sends_face_front_and_up_up(pose):
    turn = dp.rotation(pose)
    assert round(np.linalg.det(turn)) == 1  # a turn, never a mirror
    assert tuple(turn @ dp.AXES[pose.face]) == dp.AXES["+y"]
    assert tuple(turn @ dp.AXES[pose.up]) == dp.AXES["+z"]


def test_ldraw_rotation_is_the_same_turn_in_ldraw_axes():
    pose = dp.Pose("+z", "+y")
    turn = dp.ldraw_rotation(pose)
    # Build +z (FACE) is LDraw -Y; it must end up at build +y = LDraw +Z.
    assert tuple(turn @ (0, -1, 0)) == (0, 0, 1)
    # Build +y (UP) is LDraw +Z; it must end up at build +z = LDraw -Y.
    assert tuple(turn @ (0, 0, 1)) == (0, -1, 0)


def test_rotate_ldr_turns_the_main_model_only():
    text = (
        "0 FILE main.ldr\n"
        "0 main\n"
        "1 4 10 -24 30 1 0 0 0 1 0 0 0 1 3005.dat\n"
        "1 16 0 0 0 1 0 0 0 1 0 0 0 1 wing.ldr\n"
        "0 FILE wing.ldr\n"
        "1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3005.dat\n"
    )
    out = dp.rotate_ldr(text, dp.ldraw_rotation(dp.Pose("+z", "+y"))).splitlines()
    assert out[0:2] == ["0 FILE main.ldr", "0 main"]
    assert out[2] == "1 4 -10 -30 24 -1 0 0 0 0 -1 0 -1 0 3005.dat"
    assert out[3] == "1 16 0 0 0 -1 0 0 0 0 -1 0 -1 0 wing.ldr"
    assert out[4:] == ["0 FILE wing.ldr", "1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3005.dat"]  # its own frame


# --- display_image: what the reviewer gets ------------------------------------------

LDR = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3005.dat\n"


def test_display_image_as_built_pose_reuses_the_render(tmp_path):
    record = make_record(tmp_path, 'FACE = "+y"\nUP = "+z"\n', LDR)
    image = tmp_path / "out" / "build.png"
    assert dp.display_image(record, image, 10) == (image, "")
    assert (tmp_path / "out" / dp.DISPLAY_LDR).read_text() == LDR  # the viewer file


def test_display_image_without_a_pose_warns_and_shows_the_build_as_built(tmp_path):
    record = make_record(tmp_path, "x = 1\n", LDR)
    image = tmp_path / "out" / "build.png"
    shown, warning = dp.display_image(record, image, 10)
    assert shown == image
    assert warning == "no display pose: build.py does not set FACE and UP; the build is shown as built"
    assert (tmp_path / "out" / dp.DISPLAY_LDR).read_text() == LDR


def test_display_image_without_an_ldr(tmp_path):
    record = make_record(tmp_path, 'FACE = "+z"\nUP = "+y"\n', None)
    image = tmp_path / "out" / "build.png"
    assert dp.display_image(record, image, 10) == (image, "no out/build.ldr to turn; the build is shown as built")


def test_display_never_changes_the_build(tmp_path, monkeypatch):
    src = 'FACE = "+z"\nUP = "+y"\n'
    record = make_record(tmp_path, src, LDR)
    monkeypatch.setattr(dp, "render_ldr", lambda ldr, png, timeout: png.write_bytes(b"png") and "")
    shown, warning = dp.display_image(record, tmp_path / "out" / "build.png", 10)
    assert shown.name == dp.DISPLAY_PNG and warning == ""
    assert (tmp_path / "build.py").read_text() == src
    assert (tmp_path / "out" / "build.ldr").read_text() == LDR
    assert (tmp_path / "out" / dp.DISPLAY_LDR).read_text() != LDR


# --- The real render: FACE faces the camera in the front view -------------------------


def _render_ok() -> bool:
    try:
        from brickwright.tools.rendering import render_available

        return render_available()
    except Exception:
        return False


def _marker_pixels(png: Path) -> dict[str, tuple[int, float]]:
    """Red and blue pixels in the front view (the top-left tile): (count, mean row)."""
    from PIL import Image

    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    front = a[: a.shape[0] // 2, : a.shape[1] // 2]
    r, g, b = front[..., 0], front[..., 1], front[..., 2]
    masks = {
        "red": (r > 120) & (r > g + 60) & (r > b + 60),
        "blue": (b > 100) & (b > r + 50) & (b > g + 30),
    }
    return {name: (int(m.sum()), float(np.argwhere(m)[:, 0].mean()) if m.any() else -1.0) for name, m in masks.items()}


@pytest.mark.skipif(not _render_ok(), reason="no LDraw library or render stack")
def test_face_faces_the_camera_in_the_real_render(tmp_path):
    """A gray 1x1 brick built lying down: FACE = "+z" (a red brick on top of it, the
    front) and UP = "+y" (a blue brick on its +y side, the top). The real render
    pipeline (ldraw2mesh + BrickWright's mesh2png, as for the review) must show the
    red front facing the camera in the front view, with the blue top above it. As a
    control, the same build not turned shows the blue brick to the camera instead."""
    unit = "1 0 0 0 1 0 0 0 1 3005.dat"
    ldr = f"1 71 0 0 0 {unit}\n1 4 0 -24 0 {unit}\n1 1 0 0 20 {unit}\n"
    record = make_record(tmp_path, 'FACE = "+z"\nUP = "+y"\n', ldr)

    shown, warning = dp.display_image(record, tmp_path / "out" / "build.png", 120)
    assert warning == "" and shown.name == dp.DISPLAY_PNG
    turned = _marker_pixels(shown)
    assert turned["red"][0] > 2 * turned["blue"][0]  # the front (FACE) faces the camera
    assert 0 <= turned["blue"][1] < turned["red"][1]  # UP is above it (smaller row)

    control = tmp_path / "as_built.png"
    assert dp.render_ldr(tmp_path / "out" / "build.ldr", control, 120) == ""
    as_built = _marker_pixels(control)
    assert as_built["blue"][0] > 2 * as_built["red"][0]  # not turned: the +y side faces the camera
