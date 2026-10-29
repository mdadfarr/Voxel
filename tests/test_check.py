import bricklayer as bl
import pytest

from voxel import Inventory, check_build, count_build

RED, BLUE = bl.Color.RED, bl.Color.BLUE
BRICK_2X4 = "3001"


def tower(color, height):
    """A stack of `height` red or blue 2x4 bricks."""
    build = bl.LegoBuild()
    last = build.add(bl.Element(BRICK_2X4, color), name="b0")
    for _ in range(height - 1):
        last = build.stack(bl.Element(BRICK_2X4, color), on=last)
    return build


def owned(part, color, count):
    return Inventory.from_entries([{"part": part, "color": color.name, "count": count}])


def test_passes_when_enough_owned():
    result = check_build(tower(RED, 3), owned(BRICK_2X4, RED, 5))
    assert result.ok
    assert "passed" in result.report()


def test_fails_when_too_few_owned():
    result = check_build(tower(RED, 3), owned(BRICK_2X4, RED, 2))
    assert not result.ok
    assert result.shortfalls[0].over == 1
    assert "used 3, owned 2 (1 over)" in result.report()


def test_part_you_do_not_own_counts_as_zero_owned():
    result = check_build(tower(RED, 1), Inventory({}))
    assert not result.ok
    assert "used 1, owned 0 (1 over)" in result.report()


def test_color_matters():
    result = check_build(tower(RED, 1), owned(BRICK_2X4, BLUE, 10))
    assert not result.ok


def test_nested_subassembly_is_counted():
    pair = tower(RED, 2)
    model = bl.LegoBuild()
    model.add(pair, (0, 0, 0), name="left")
    model.add(pair, (4, 0, 0), name="right")
    assert sum(count_build(model).values()) == 4


def test_leftover_is_reported():
    result = check_build(tower(RED, 2), owned(BRICK_2X4, RED, 5))
    assert result.leftover == {(BRICK_2X4, RED.ldraw_id): 3}


def test_inventory_accepts_color_code_and_merges_duplicates():
    inv = Inventory.from_entries(
        [
            {"part": "3001", "color": "RED", "count": 2},
            {"part": "3001", "color": RED.ldraw_id, "count": 3},
        ]
    )
    assert inv.owned((BRICK_2X4, RED.ldraw_id)) == 5


def test_inventory_rejects_bad_input():
    with pytest.raises(ValueError, match="unknown color"):
        Inventory.from_entries([{"part": "3001", "color": "NOPE", "count": 1}])
    with pytest.raises(ValueError, match="missing"):
        Inventory.from_entries([{"part": "3001", "count": 1}])
    with pytest.raises(ValueError, match="negative"):
        Inventory.from_entries([{"part": "3001", "color": "RED", "count": -1}])