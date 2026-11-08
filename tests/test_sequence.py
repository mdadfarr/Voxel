from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import design as dz
from voxel.sequence import GROUND, paginate, sequence, stepped_build
from voxel.steps import MAX_STEP_PARTS, step_problems

RED = bl.Color.RED
BRICK, PLATE, TILE_2X2, PLATE_1X1, PLATE_8X8 = "3001", "3022", "3068b", "3024", "41539"
PLATE_HEIGHT = 1 / 3  # z is in bricks


def add(build, part, x, y, z):
    build.add(bl.Element(part, RED), (x, y, z))


def assert_pages_fit(result, build):
    """Every part on exactly one page, no page over the limit."""
    assert all(0 < len(page) <= MAX_STEP_PARTS for page in result.pages)
    placed = [i for page in result.pages for i in page]
    assert len(placed) == len(set(placed))
    if result.ok:
        assert sorted(placed) == list(range(len(build.flattened_placements)))


def plate_with_studs(build, x, count):
    """An 8x8 plate at (x, 0) and `count` 1x1 plates on it. Returns their indices."""
    base = len(build.flattened_placements)
    add(build, PLATE_8X8, x, 0, 0)
    for k in range(count):
        add(build, PLATE_1X1, x + k % 8, k // 8, PLATE_HEIGHT)
    return base, list(range(base + 1, base + 1 + count))


def test_top_first_stack_comes_out_bottom_up():
    build = bl.LegoBuild()
    for level in (2, 1, 0):  # written top-first
        add(build, BRICK, 2, 2, level)
    result = sequence(build)
    assert result.ok and result.pages == [[2, 1, 0]]
    assert result.moved == 2
    stepped = stepped_build(build, result.pages)
    assert [leaf.bounding_box[0].z for leaf in stepped.flattened_placements] == [0, 1, 2]
    assert stepped.step_markers == (3,)
    assert step_problems(stepped) == []
    assert step_problems(build) != []  # the builder's own order would not stand


def test_sixteen_parts_on_one_part_make_pages_of_12_and_4():
    build = bl.LegoBuild()
    base, studs = plate_with_studs(build, 0, 16)
    result = sequence(build)
    assert result.ok
    assert result.pages == [[base], studs[:12], studs[12:]]
    assert_pages_fit(result, build)
    assert step_problems(stepped_build(build, result.pages)) == []


def test_parts_on_the_same_part_share_a_page():
    build = bl.LegoBuild()
    add(build, PLATE_8X8, 0, 0, 0)  # 0
    add(build, PLATE_8X8, 10, 0, 0)  # 1
    a, b = [], []
    for k in range(7):  # interleaved in code order: one on A, one on B, ...
        a.append(len(build.flattened_placements))
        add(build, PLATE_1X1, k, 0, PLATE_HEIGHT)
        b.append(len(build.flattened_placements))
        add(build, PLATE_1X1, 10 + k, 0, PLATE_HEIGHT)
    result = sequence(build)
    assert result.ok
    assert result.pages == [[0, 1, *a], b]
    assert_pages_fit(result, build)


def test_part_resting_on_a_tile_is_placed():
    build = bl.LegoBuild()
    add(build, "3003", 0, 0, PLATE_HEIGHT)  # 2x2 brick, written first, on the tile
    add(build, TILE_2X2, 0, 0, 0)
    assert build._mating.pairs == ()  # a tile has no studs: nothing clips
    result = sequence(build)
    assert result.ok and result.pages == [[1, 0]]
    assert step_problems(stepped_build(build, result.pages)) == []


def test_part_with_nothing_under_it_is_unplaced_and_the_walk_stops():
    build = bl.LegoBuild()
    add(build, BRICK, 2, 2, 0)
    add(build, BRICK, 2, 2, 5)  # floats
    add(build, BRICK, 2, 2, 6)  # clipped only to the floating brick
    result = sequence(build)
    assert not result.ok
    assert result.pages == [[0]]
    assert [line.split(" at ")[0] for line in result.unplaced] == ["Red 3001", "Red 3001"]
    assert result.unplaced[0].endswith("(2,2,5)") and result.unplaced[1].endswith("(2,2,6)")


def test_no_page_has_more_than_12_parts():
    build = bl.LegoBuild()
    for k in range(30):  # 30 grounded bricks: pages of 12, 12, then 6 and more
        add(build, BRICK, (k % 6) * 4, (k // 6) * 2, 0)
    plate_with_studs(build, 40, 25)
    plate_with_studs(build, 50, 13)
    result = sequence(build)
    assert result.ok and len(result.pages) > 5
    assert_pages_fit(result, build)
    assert step_problems(stepped_build(build, result.pages)) == []


@pytest.mark.parametrize("sizes", [[5, 13, 25, 1], [12, 12], [11, 2, 30], [1] * 40])
def test_paginate_never_goes_over_12_and_keeps_order(sizes):
    this_pass, part = [], 0
    for anchor, size in enumerate(sizes):
        for _ in range(size):
            this_pass.append((part, anchor))
            part += 1
    pages = paginate([[(p, GROUND) for p in range(1000, 1003)], this_pass])
    assert all(0 < len(page) <= 12 for page in pages)
    flat = [p for page in pages for p in page]
    assert flat == [1000, 1001, 1002, *range(part)]


# --- The gate: steps are computed after the inventory check -------------------


def write_inventory(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text('[{"part": "3001", "color": "RED", "count": 5}]', encoding="utf-8")
    return path


def write_build(tmp_path, levels):
    lines = "".join(f"    b.add(bl.Element('3001', bl.Color.RED), (2, 2, {z}))\n" for z in levels)
    path = tmp_path / "build.py"
    path.write_text("import bricklayer as bl\ndef create_build():\n    b = bl.LegoBuild()\n" + lines + "    return b\n")
    return path


def test_gate_writes_stepped_and_unordered_ldr(tmp_path):
    ok, report = dz.final_gate(write_build(tmp_path, [2, 1, 0]), write_inventory(tmp_path))
    assert ok and "1 pages" in report
    stepped = (tmp_path / "out" / "build.ldr").read_text()
    unordered = (tmp_path / "out" / "build.unordered.ldr").read_text()
    assert stepped.count("0 STEP") == 1 and "0 STEP" not in unordered
    heights = lambda text: [line.split()[3] for line in text.splitlines() if line.startswith("1 ")]
    assert heights(stepped) == heights(unordered)[::-1]  # bottom-up instead of top-first


def test_gate_sends_floating_parts_back_without_steps(tmp_path):
    ok, report = dz.final_gate(write_build(tmp_path, [0, 5]), write_inventory(tmp_path))
    assert not ok and dz.NOT_HELD_UP in report and "Red 3001 at (2,2,5)" in report
    assert "step" not in report.lower()
    assert not (tmp_path / "out").exists()
    retry = dz.feedback_prompt(dz.gate_problem(report), "BASE")
    assert "step" not in retry.lower() and "(2,2,5)" in retry


def test_render_puts_the_stepped_ldr_back(tmp_path, monkeypatch):
    from brickwright.tools import rendering

    out = tmp_path / "out"
    out.mkdir()
    (out / "build.ldr").write_text("stepped")
    image = out / "build.png"

    def fake_render(record, config):  # BrickWright rewrites build.ldr from build.py
        (out / "build.ldr").write_text("unordered")
        image.write_bytes(b"png")
        return SimpleNamespace(ok=True, image_path=image, error=None)

    monkeypatch.setattr(rendering, "render_available", lambda: True)
    monkeypatch.setattr(rendering, "render", fake_render)
    assert dz.render_build(SimpleNamespace(path=tmp_path), None) == (image, "")
    assert (out / "build.ldr").read_text() == "stepped"
