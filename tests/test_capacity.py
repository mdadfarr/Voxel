"""The capacity check (voxel.capacity): can the owned parts fill a plan's shapes?

The replay tests run the saved plans of real runs against the inventory they
were made for. The saved plans are free-form text, so tests/fixtures/capacity/
holds each one restated with the capacity fields (fill, wall, cells, empty,
position); every section quotes the plan text it comes from in `plan_text`.
make_fixtures.py in that folder wrote them.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory
from voxel import capacity as cap
from voxel import plan as pl
from voxel.inventory import load_inventory

FIXTURES = Path(__file__).parent / "fixtures" / "capacity"
RED, WHITE, GRAY = (c.ldraw_id for c in (bl.Color.RED, bl.Color.WHITE, bl.Color.LIGHT_BLUISH_GRAY))


def saved(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def owned():
    return load_inventory(FIXTURES / "inventory.json")


def section(**changes):
    base = {
        "name": "wall",
        "size": {"width": 4, "depth": 4, "height": 1},
        "position": {"x": 0, "y": 0, "z": 0},
        "fill": "solid",
        "cells": [{"color": "RED", "cells": 48}],
    }
    return {**base, **changes}


def check(inventory, *sections, **kwargs):
    """Without a margin, unless the test gives one: these tests are about the counting."""
    kwargs.setdefault("margin", 0)
    kwargs.setdefault("planner", 0)
    return cap.check_capacity({"sections": list(sections)}, inventory, **kwargs)


# --- Replay: the saved plans, against everything owned (margin 0) ---------------
# These compare the counting model with what really happened in each run.


def test_guitar_plan_that_was_built_is_accepted(owned):
    result = cap.check_capacity(saved("guitar_1c64df97"), owned, margin=0, planner=0)
    assert result.ok, result.report()


def test_skull_plan_that_was_built_in_spec_is_accepted(owned):
    # Round 1 of 9ccc0f34 was built in gray and red only, with every check passing.
    result = cap.check_capacity(saved("skull_9ccc0f34_round1"), owned, margin=0, planner=0)
    assert result.ok, result.report()


@pytest.mark.parametrize(("reading", "short"), [("wide", 448), ("skin", 198)])
def test_skull_plan_whose_build_ran_out_of_gray_is_rejected(owned, reading, short):
    # Round 1 of 73b41c51: all visible cells gray. Its build fell back to white, tan
    # and red. Rejected under both readings of its 2-stud walls.
    result = cap.check_capacity(saved(f"skull_73b41c51_round1_{reading}"), owned, margin=0, planner=0)
    assert not result.ok and not result.errors
    worst = result.shortfalls[0]
    assert (worst.color, worst.short) == ("LIGHT_BLUISH_GRAY", short)
    assert f"LIGHT_BLUISH_GRAY: short by {short} plate-cells" in result.report()
    assert "side and back walls" in result.report()


def test_unbuilt_skull_plans_are_pinned_at_what_the_numbers_say(owned):
    # Never built, so there is no outcome to compare with. Pinned so a change to the
    # model shows up here.
    assert cap.check_capacity(saved("skull_9ccc0f34_round2"), owned, margin=0, planner=0).ok
    assert cap.check_capacity(saved("skull_73b41c51_round2_skin"), owned, margin=0, planner=0).ok
    wide = cap.check_capacity(saved("skull_73b41c51_round2_wide"), owned, margin=0, planner=0)
    assert [(s.color, s.short) for s in wide.shortfalls] == [("RED", 25)]


def test_saved_plans_at_the_default_margins(owned, no_margin_settings):
    # Pinned, not judged: the same plans against the checker's limit (owned minus 5%).
    # The guitar was built and passed its review, but it budgets reddish brown to the
    # last cell (21 needed, 21 owned, limit 19), so it is rejected. That is intended.
    def over(name):
        return [(s.color, s.need, s.limit, s.over) for s in cap.check_capacity(saved(name), owned).shortfalls]

    assert over("guitar_1c64df97") == [("REDDISH_BROWN", 21, 19, 2)]
    assert over("skull_9ccc0f34_round1") == []
    assert over("skull_9ccc0f34_round2") == []
    assert over("skull_73b41c51_round1_wide") == [("LIGHT_BLUISH_GRAY", 1356, 873, 483)]
    assert over("skull_73b41c51_round1_skin") == [("LIGHT_BLUISH_GRAY", 744, 528, 216)]
    assert over("skull_73b41c51_round2_wide") == [("RED", 834, 771, 63)]
    assert over("skull_73b41c51_round2_skin") == []


def test_fixtures_quote_the_plan_they_restate():
    for path in FIXTURES.glob("*_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["source"].startswith("runs/") and data["note"]
        assert all(s["plan_text"] for s in data["sections"]), path.name


# --- Supply --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Plate 2 x 4", (False, 8, 2, 1)),
        ("Brick 1 x 12", (False, 12, 1, 3)),
        ("Brick 3 x 3 x 2 Round Corner with Dome Top", (False, 9, 3, 6)),
        ("Tile 1 x 4 with Groove", (True, 4, 1, 1)),
        ("Slope Brick Curved 2 x 2 x 0.667", (True, 4, 2, 2)),
        ("Slope Plate 45 2 x 1", (True, 2, 1, 1)),
        ("Slope Brick 45 2 x 1", (True, 2, 1, 3)),
        ("Technic Brick 1 x 2 with Hole", (False, 2, 1, 3)),
        ("Technic Angle Connector (3 x 120 degree)", None),
        ("Bar 3L", None),
    ],
)
def test_material_from_the_part_name(name, expected):
    assert cap.material(name) == expected


def test_only_plain_parts_and_tops_are_pooled():
    assert cap.pooled("Plate 2 x 4") and cap.pooled("Brick 1 x 2") and cap.pooled("Tile 1 x 4 with Groove")
    assert not cap.pooled("Plate 1 x 1 Round") and not cap.pooled("Plate 2 x 2 without Corner")


def test_table_lists_plain_cells_by_width(owned):
    table = cap.capacity_table(owned, margin=0)
    assert table.splitlines()[0].startswith("color | parts up to 1 wide | parts up to 2 wide | any width")
    assert "LIGHT_BLUISH_GRAY | 354 | 686 | 978 | 864 | 953" in table


# --- Two margins: the planner sees owned minus 15%, the check allows owned minus 5% ---


@pytest.fixture
def no_margin_settings(monkeypatch):
    monkeypatch.delenv(cap.PLANNER_MARGIN_ENV, raising=False)
    monkeypatch.delenv(cap.CHECKER_MARGIN_ENV, raising=False)


def test_margin_settings(monkeypatch, no_margin_settings):
    assert (cap.planner_margin(), cap.checker_margin()) == (15, 5)
    monkeypatch.setenv(cap.PLANNER_MARGIN_ENV, "30")
    monkeypatch.setenv(cap.CHECKER_MARGIN_ENV, "10")
    assert (cap.planner_margin(), cap.checker_margin()) == (30, 10)
    assert (cap.planner_margin(20), cap.checker_margin(2)) == (20, 2)  # an explicit value wins
    for bad in (-1, 100):
        with pytest.raises(ValueError, match="0 to under 100"):
            cap.checker_margin(bad)
    monkeypatch.setenv(cap.PLANNER_MARGIN_ENV, "lots")
    with pytest.raises(ValueError, match=cap.PLANNER_MARGIN_ENV):
        cap.planner_margin()


def test_table_is_the_planner_budget_and_rounds_down(owned, monkeypatch, no_margin_settings):
    assert "LIGHT_BLUISH_GRAY | 300 | 583 | 831 | 734 | 810" in cap.capacity_table(owned)  # 354 * 0.85 = 300.9
    monkeypatch.setenv(cap.CHECKER_MARGIN_ENV, "0")  # the checker margin does not change the table
    assert "LIGHT_BLUISH_GRAY | 300 | 583 | 831 | 734 | 810" in cap.capacity_table(owned)
    monkeypatch.setenv(cap.PLANNER_MARGIN_ENV, "50")
    assert "LIGHT_BLUISH_GRAY | 177 | 343 | 489 | 432 | 476" in cap.capacity_table(owned)


def test_plan_that_fits_everything_owned_but_not_the_checker_limit_is_rejected(no_margin_settings):
    inv = Inventory({("3001", RED): 2})  # 48 plate-cells owned; checker limit 45; planner budget 40
    assert check(inv, section()).ok  # the 48-cell section fits what is owned
    result = cap.check_capacity({"sections": [section()]}, inv)
    assert [(s.color, s.need, s.limit, s.over) for s in result.shortfalls[:1]] == [("RED", 48, 45, 3)]
    # The message is for the planner: it gives the planner's budget, not the limit or what is owned.
    assert "RED: short by 8 plate-cells" in result.report()
    assert "need 48; your budget is 40 in plain bricks and plates" in result.report()
    assert "45" not in result.report()
    # The same plan through the planner's parser.
    with pytest.raises(ValueError, match="RED: short by 8 plate-cells"):
        pl.parse_plan(json.dumps(PLAN), inv)
    assert pl.parse_plan(json.dumps(PLAN), Inventory({("3001", RED): 3}))  # limit 68


def test_plan_over_the_planner_budget_but_within_the_checker_limit_passes(no_margin_settings):
    inv = Inventory({("3001", RED): 10})  # 240 owned; planner budget 204; checker limit 228
    block = {"size": {"width": 6, "depth": 4, "height": 3}}
    assert cap.check_capacity({"sections": [section(**block, cells=[{"color": "RED", "cells": 216}])]}, inv).ok
    tight = section(**{"size": {"width": 6, "depth": 4, "height": 3.333}}, cells=[{"color": "RED", "cells": 229}], empty=11)
    assert [(s.limit, s.over) for s in cap.check_capacity({"sections": [tight]}, inv).shortfalls[:1]] == [(228, 1)]


def test_both_margins_round_down():
    rows = cap.stock(Inventory({("3001", RED): 2, ("3024", RED): 1}))  # 49 plate-cells
    assert cap.budget(rows, RED, 9, 9, top=False, margin=5) == 46  # 46.55
    assert cap.budget(rows, RED, 9, 9, top=False, margin=15) == 41  # 41.65


def test_margin_also_holds_back_tops():
    tiles = Inventory({("3068b", RED): 4})  # Tile 2 x 2: 16 plate-cells
    layer = section(size={"width": 4, "depth": 4, "height": 0.333}, cells=[{"color": "RED", "cells": 16}])
    assert check(tiles, layer, margin=0).ok
    assert [(s.need, s.stack, s.tops) for s in check(tiles, layer, margin=15).shortfalls[:1]] == [(16, 0, 13)]


def test_planner_prompts_hold_only_budget_numbers(owned, no_margin_settings):
    full = cap.capacity_table(owned, margin=0).splitlines()[1:]
    budget = cap.capacity_table(owned).splitlines()[1:]
    review = SimpleNamespace(verdicts=[])
    prompts = [pl.plan_prompt("a box", owned), pl.revise_prompt(pl.Plan.model_validate(PLAN), review, None, owned)]
    for prompt in prompts:
        assert all(row in prompt for row in budget)
        assert not any(row in prompt for row in full)
        assert "you may plan with" in prompt and "15" not in prompt.split("Example of the shape")[0].split("Rules:")[1]
    small = pl.plan_prompt("a box", Inventory({("3001", RED): 10}))  # 240 plate-cells owned
    assert "RED | 0 | 204 | 204 | 0 | 0" in small and "240" not in small


def test_builder_still_sees_everything_owned():
    from voxel import design as dz

    inv = Inventory({("3001", RED): 10})
    prompt = dz.build_prompt("a box", inv, pl.Plan.model_validate(PLAN), {})
    assert "3001 | Brick 2 x 4 | RED | 2 | 10" in prompt  # planned 2, owned 10: the full count


# --- Demand: the section's own numbers -------------------------------------------


def test_cells_and_empty_must_equal_the_volume():
    inv = Inventory({("3001", RED): 10})
    result = check(inv, section(cells=[{"color": "RED", "cells": 40}]))
    assert result.errors == (
        "section 'wall': its 4 x 4 box is 16 cells x 3 plates = 48 plate-cells, but cells (40) + "
        "empty (0) = 40. They must be equal",
    )
    assert check(inv, section(cells=[{"color": "RED", "cells": 40}], empty=8)).ok


def test_shell_counts_only_its_ring():
    inv = Inventory({("3004", RED): 20})  # Brick 1 x 2: 6 plate-cells each
    ring = section(fill="shell", wall=1, cells=[{"color": "RED", "cells": 36}])  # 12 cells x 3 plates
    assert check(inv, ring).ok
    assert "4 x 4 ring is 12 cells x 3 plates = 36" in check(inv, {**ring, "cells": [{"color": "RED", "cells": 48}]}).errors[0]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"fill": None}, "fill must be"),
        ({"fill": "hollow"}, "fill must be"),
        ({"size": "4 x 4 x 1"}, "size must be"),
        ({"position": "x 0..4"}, "position must be"),
        ({"size": {"width": 4, "depth": 4, "height": 0.5}}, "not a whole number of plates"),
        ({"cells": None}, "cells must be a list"),
        ({"cells": [{"color": "PINKISH", "cells": 48}]}, "unknown color"),
        ({"fill": "shell", "wall": 0}, "wall must be"),
    ],
)
def test_bad_capacity_fields_are_reported_per_section(changes, message):
    result = check(Inventory({("3001", RED): 10}), section(**changes))
    assert not result.ok and message in result.errors[0] and result.errors[0].startswith("section 'wall'")


def test_sections_without_fields_are_skipped_only_when_not_required():
    inv = Inventory({("3001", RED): 10})
    old = {k: v for k, v in section().items() if k not in ("fill", "cells")}
    assert not check(inv, old).ok
    result = check(inv, old, require=False)
    assert result.ok and result.unchecked == ("wall",)


# --- The three limits --------------------------------------------------------------


def test_color_short_is_reported_with_numbers():
    result = check(Inventory({("3001", RED): 1}), section())  # 24 owned, 48 needed
    assert [(s.color, s.need, s.stack, s.short) for s in result.shortfalls] == [("RED", 48, 24, 24), (cap.ALL, 48, 24, 24)]
    assert "RED: short by 24 plate-cells" in result.report()


def test_wide_parts_do_not_count_for_a_thin_wall():
    ring = section(fill="shell", wall=1, cells=[{"color": "RED", "cells": 36}])
    assert not check(Inventory({("3001", RED): 10}), ring).ok  # Brick 2 x 4: too wide
    assert check(Inventory({("3010", RED): 3}), ring).ok  # Brick 1 x 4: 12 plate-cells each


def test_tall_parts_do_not_count_for_a_thin_layer():
    layer = section(size={"width": 4, "depth": 4, "height": 0.333}, cells=[{"color": "RED", "cells": 16}])
    assert not check(Inventory({("3001", RED): 10}), layer).ok  # bricks are 3 plates tall
    assert check(Inventory({("3020", RED): 2}), layer).ok  # Plate 2 x 4


def test_tiles_fill_only_one_top_per_column():
    tiles = Inventory({("3068b", RED): 100})  # Tile 2 x 2: plenty, but tops only
    assert check(tiles, section(size={"width": 4, "depth": 4, "height": 0.333}, cells=[{"color": "RED", "cells": 16}])).ok
    result = check(tiles, section())  # 3 plates tall: only the top plate can be tiles
    assert [(s.need, s.stack, s.tops) for s in result.shortfalls[:1]] == [(48, 0, 16)]


def test_tiles_do_not_count_under_another_section():
    tiles = Inventory({("3068b", RED): 100, ("3020", WHITE): 2})
    floor = section(name="floor", size={"width": 4, "depth": 4, "height": 0.333}, cells=[{"color": "RED", "cells": 16}])
    lid = section(
        name="lid",
        size={"width": 4, "depth": 4, "height": 0.333},
        position={"x": 0, "y": 0, "z": 0.333},
        cells=[{"color": "WHITE", "cells": 16}],
    )
    assert check(tiles, floor).ok
    result = check(tiles, floor, lid)
    assert ("RED", 16) in [(s.color, s.short) for s in result.shortfalls]


def test_any_cells_draw_on_every_color():
    inv = Inventory({("3020", RED): 1, ("3020", WHITE): 1})  # 8 plate-cells each
    layer = {"size": {"width": 4, "depth": 4, "height": 0.333}}
    assert check(inv, section(**layer, cells=[{"color": "ANY", "cells": 16}])).ok
    result = check(inv, section(**layer, cells=[{"color": "RED", "cells": 8}, {"color": "ANY", "cells": 8}]))
    assert result.ok
    result = check(inv, section(**layer, cells=[{"color": "RED", "cells": 12}, {"color": "ANY", "cells": 4}]))
    assert [s.color for s in result.shortfalls] == ["RED"]


# --- Special parts: counted only where a section names them ----------------------


def test_special_parts_count_where_named_and_within_what_is_owned():
    inv = Inventory({("4073", RED): 16})  # Plate 1 x 1 Round: not pooled
    layer = section(size={"width": 4, "depth": 4, "height": 0.333}, cells=[{"color": "RED", "cells": 16}])
    assert not check(inv, layer).ok
    assert check(inv, {**layer, "parts": [{"part": "4073", "color": "RED", "count": 16}]}).ok
    over = check(inv, {**layer, "parts": [{"part": "4073", "color": "RED", "count": 17}]})
    assert over.errors == ("Red part 4073: the sections name 17 in parts, owned 16",)


def test_named_plain_parts_are_not_counted_twice():
    inv = Inventory({("3020", RED): 1})  # 8 plate-cells, pooled
    layer = section(size={"width": 4, "depth": 4, "height": 0.333}, cells=[{"color": "RED", "cells": 16}])
    assert not check(inv, {**layer, "parts": [{"part": "3020", "color": "RED", "count": 1}]}).ok


# --- In the planner (voxel.plan) and on the command line ----------------------------

PLAN = {
    "checklist": ["looks like a red box"],
    "sections": [
        {
            "name": "box",
            "size": {"width": 4, "depth": 2, "height": 2},
            "position": {"x": 0, "y": 0, "z": 0},
            "colors": ["RED"],
            "parts": [{"part": "3001", "color": "RED", "count": 2}],
            "fill": "solid",
            "wall": 1,
            "empty": 0,
            "cells": [{"color": "RED", "cells": 48}],
        }
    ],
    "parts": [{"part": "3001", "color": "RED", "count": 2}],
}


def test_parse_plan_rejects_a_plan_that_does_not_fit():
    inv = Inventory({("3001", RED): 3})  # 72 plate-cells owned, 61 in the budget
    assert pl.parse_plan(json.dumps(PLAN), inv)
    with pytest.raises(ValueError, match="the plan needs more than your budget of parts") as err:
        # Owned in count (2 of 3), but the box is 96 plate-cells.
        bigger = copy.deepcopy(PLAN)
        bigger["sections"][0]["size"]["height"] = 4
        bigger["sections"][0]["cells"][0]["cells"] = 96
        pl.parse_plan(json.dumps(bigger), inv)
    assert "RED: short by 35 plate-cells" in str(err.value)  # 96 needed, planner budget 61


def test_parse_plan_requires_the_capacity_fields():
    old = copy.deepcopy(PLAN)
    for key in ("fill", "cells"):
        del old["sections"][0][key]
    assert pl.Plan.model_validate(old)  # plans made before the check still load
    with pytest.raises(ValueError, match="fill must be"):
        pl.parse_plan(json.dumps(old), Inventory({("3001", RED): 3}))


def test_planner_prompt_has_the_table_and_the_fields():
    prompt = pl.plan_prompt("a box", Inventory({("3001", RED): 10}))
    assert "RED | 0 | 204 | 204 | 0 | 0" in prompt  # 10 bricks 2 x 4, 2 wide: 240 plate-cells minus 15%
    assert "cells + empty must equal the volume" in prompt and '"fill": "solid"' in prompt


def test_command_line(tmp_path, capsys, monkeypatch, no_margin_settings):
    inventory = ["--inventory", str(FIXTURES / "inventory.json")]
    guitar, skull = (str(FIXTURES / f"{n}.json") for n in ("guitar_1c64df97", "skull_73b41c51_round1_wide"))
    assert cap.main([guitar, *inventory, "--checker-margin", "0"]) == 0
    assert "Capacity check passed." in capsys.readouterr().out
    assert cap.main([guitar, *inventory]) == 1  # the default checker margin: limit 19, needs 21
    assert "REDDISH_BROWN: short by 4 plate-cells" in capsys.readouterr().out  # against the budget of 17
    assert cap.main([skull, *inventory]) == 1
    assert "short by 551 plate-cells" in capsys.readouterr().out
    assert cap.main(["--table", *inventory]) == 0
    assert "LIGHT_BLUISH_GRAY | 300" in capsys.readouterr().out
    monkeypatch.setenv(cap.PLANNER_MARGIN_ENV, "0")
    assert cap.main(["--table", *inventory]) == 0
    assert "LIGHT_BLUISH_GRAY | 354" in capsys.readouterr().out
    assert cap.main([str(tmp_path / "missing.json"), *inventory]) == 2
    with pytest.raises(SystemExit):
        cap.main(["--table", *inventory, "--planner-margin", "120"])
