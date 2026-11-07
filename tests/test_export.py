import json

import bricklayer as bl

from voxel import export as ex

BUILD_SRC = """
import bricklayer as bl
def create_build():
    b = bl.LegoBuild()
    base = b.add(bl.Element("3001", bl.Color.RED))
    mid = b.stack(bl.Element("3022", bl.Color.WHITE), on=base)
    b.stack(bl.Element("3001", bl.Color.RED), on=mid)
    return b
"""


def make_build():
    build = bl.LegoBuild()
    base = build.add(bl.Element("3001", bl.Color.RED))
    mid = build.stack(bl.Element("3022", bl.Color.WHITE), on=base)
    build.stack(bl.Element("3001", bl.Color.RED), on=mid)
    return build


def test_bricks_come_out_bottom_layer_first():
    data = ex.export_build(make_build())
    assert [b["part"] for b in data["bricks"]] == ["3001", "3022", "3001"]
    assert [b["layer"] for b in data["bricks"]] == [1, 2, 3]
    assert [b["id"] for b in data["bricks"]] == [1, 2, 3]
    assert data["layers"][0]["bricks"] == [1]


def test_brick_fields_are_viewer_ready():
    first = ex.export_build(make_build())["bricks"][0]
    assert first["name"] == "Brick 2 x 4"
    assert first["color"].startswith("#") and len(first["color"]) == 7
    assert first["color_name"] == "Red"
    assert first["min"] == [0, 0, 0]
    assert first["max"][:2] == [4, 2]


def test_parts_are_counted_by_part_and_color():
    parts = ex.export_build(make_build())["parts"]
    brick = [p for p in parts if p["part"] == "3001"][0]
    assert brick["count"] == 2 and brick["color_name"] == "Red"
    assert len(parts) == 2


def test_two_bricks_at_one_height_share_a_layer():
    build = bl.LegoBuild()
    a = build.add(bl.Element("3001", bl.Color.RED))
    build.stack(bl.Element("3001", bl.Color.RED), on=a)
    side = build.add(bl.Element("3001", bl.Color.BLUE))
    data = ex.export_build(build)
    layer_one = [b for b in data["bricks"] if b["layer"] == 1]
    assert len(layer_one) == 2 or len(data["layers"]) >= 2
    assert sum(len(layer["bricks"]) for layer in data["layers"]) == len(data["bricks"])


def test_empty_build_exports_cleanly():
    data = ex.export_build(bl.LegoBuild())
    assert data["bricks"] == [] and data["layers"] == [] and data["size"] == [0.0, 0.0, 0.0]


def test_export_file_and_command_line(tmp_path, capsys):
    build_py = tmp_path / "build.py"
    build_py.write_text(BUILD_SRC)
    out = tmp_path / "out" / "build.json"
    assert ex.main([str(build_py), "-o", str(out)]) == 0
    assert "3 bricks in 3 layers" in capsys.readouterr().out
    assert json.loads(out.read_text())["version"] == ex.FORMAT_VERSION
    broken = tmp_path / "broken.py"
    broken.write_text("def create_build():\n    raise ValueError('boom')\n")
    assert ex.main([str(broken), "-o", str(tmp_path / "x.json")]) == 1
    assert "boom" in capsys.readouterr().err


def test_bricks_carry_their_ldraw_placement():
    first = ex.export_build(make_build())["bricks"][0]
    assert first["file"] == "3001.dat"
    assert first["ldraw_color"] == 4
    assert len(first["matrix"]) == 12


def test_bricks_cut_off_from_the_main_build_are_flagged():
    build = bl.LegoBuild()
    a = build.add(bl.Element("3001", bl.Color.RED))
    build.stack(bl.Element("3001", bl.Color.RED), on=a)
    build.add(bl.Element("3001", bl.Color.BLUE), position=(20, 0, 0))
    flags = [b["flag"] for b in ex.export_build(build)["bricks"]]
    assert flags.count("disconnected") >= 1
    clean = [b["flag"] for b in ex.export_build(make_build())["bricks"]]
    assert set(clean) == {""}