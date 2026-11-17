"""Sizing (voxel.sizing): the size comes from the parts owned, not from a question.

Fake LLM replies only; conftest.py blocks real calls.
"""

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory
from voxel import capacity as cap
from voxel import design as dz
from voxel import expand as ex
from voxel import plan as pl
from voxel import sizing as sz

FIXTURES = Path(__file__).parent / "fixtures" / "capacity"
RED = bl.Color.RED.ldraw_id


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    for name in (sz.MIN_FILL_ENV, cap.PLANNER_MARGIN_ENV, cap.CHECKER_MARGIN_ENV):
        monkeypatch.delenv(name, raising=False)


def reply(text, cost=0.01):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        _hidden_params={"response_cost": cost},
    )


class FakeLLM:
    """Returns the queued replies in order and records every call."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return reply(self.texts[len(self.calls) - 1])

    def prompt(self, n=0):
        return self.calls[n]["messages"][0]["content"]


def box(width, depth, height):
    """A plan of one solid red box (as JSON text), with the capacity fields."""
    part = {"part": "3001", "color": "RED", "count": 2}
    return json.dumps(
        {
            "checklist": ["looks like a red box"],
            "sections": [
                {
                    "name": "box",
                    "size": {"width": width, "depth": depth, "height": height},
                    "position": {"x": 0, "y": 0, "z": 0},
                    "colors": ["RED"],
                    "parts": [part],
                    "fill": "solid",
                    "wall": 1,
                    "empty": 0,
                    "cells": [{"color": "RED", "cells": width * depth * height * 3}],
                }
            ],
            "parts": [part],
        }
    )


# 142 plate-cells owned -> a planner budget of 120 (minus 15%, rounded down).
SMALL_INV = Inventory({("3001", RED): 5, ("3020", RED): 2, ("3023", RED): 3})
AT_40 = box(4, 2, 2)  # 48 plate-cells: 40% of 120
AT_80 = box(4, 2, 4)  # 96 plate-cells: 80%
# 2,880 plate-cells owned -> budget 2,448; the checker allows 2,736.
BIG_INV = Inventory({("3001", RED): 120})
MANY_PARTS = box(20, 21, 2)  # 2,520 plate-cells: 103% of the budget, hundreds of parts

SIZE_Q = {"id": "size", "text": "How big should the guitar be?", "kind": "other", "options": ["small", "large"], "default": "medium"}
SCALE_Q = {"id": "model_scale", "text": "Which scale do you want?", "kind": "other", "options": [], "default": "1:4"}
COLOR_Q = {"id": "body_color", "text": "What color should the body be?", "kind": "color", "options": ["RED"], "default": "RED"}
COLOR_INV = Inventory({("3001", RED): 30})


# --- 1. No size question -----------------------------------------------------------


def test_no_size_question_is_asked_for_make_me_a_guitar():
    # Even if the model asks about size anyway, the question never reaches the user.
    llm = FakeLLM(json.dumps({"questions": [SIZE_Q, COLOR_Q, SCALE_Q]}))
    result = ex.ask_questions("make me a guitar", COLOR_INV, complete=llm)
    assert [q.id for q in result.questions] == ["body_color"]
    assert len(llm.calls) == 1
    prompt = llm.prompt()
    assert "NEVER ask about size, scale or how big" in prompt
    assert "colors, size," not in prompt


@pytest.mark.parametrize(
    ("request_text", "sized"),
    [
        ("make me a guitar", False),
        ("a red electric guitar with a white pickguard", False),
        ("a minifigure stand", False),
        ("make me a small guitar", True),
        ("a BIG castle", True),
        ("a large, detailed skull", True),
        ("a tiny car", True),
        ("a tall red tower", True),
        ("a guitar 30 studs long", True),
        ("a tower of 12 bricks", True),
        ("a 20 cm boat", True),
    ],
)
def test_states_size(request_text, sized):
    assert sz.states_size(request_text) is sized


# --- 2. The size rule in the prompts -------------------------------------------------


def test_expand_and_planner_get_the_budget_table_and_the_size_rule():
    rule = (
        "Choose the overall size so that the color you use most, relative to its budget, uses "
        "70 to 100% of its budget."
    )
    table_row = "RED | 5 | 120 | 120 | 18 | 0"  # the planner's budget, not the 142 owned
    prompts = [ex.expand_prompt("make me a guitar", [], SMALL_INV), pl.plan_prompt("make me a guitar", SMALL_INV)]
    for prompt in prompts:
        assert rule in prompt and table_row in prompt
        assert "do not pick from fixed sizes like small, medium or large" in prompt
        assert "142" not in prompt.split("Inventory (part id")[0]
        assert "parts." not in prompt.split("70 to 100% of its budget.")[1][:40]  # no part cap in the rule


def test_request_with_a_size_keeps_it_and_skips_the_size_check():
    request = "make me a small guitar"
    for prompt in (ex.expand_prompt(request, [], SMALL_INV), pl.plan_prompt(request, SMALL_INV)):
        assert "Keep that size" in prompt and "70 to 100%" not in prompt
    llm = FakeLLM(AT_40)  # 40% would be "too small", but the user asked for small
    result = pl.make_plan(request, SMALL_INV, complete=llm)
    assert len(llm.calls) == 1 and not result.size_retried
    sizing = result.plan.sizing
    assert sizing.user_size and sizing.problem == "" and sizing.utilization == 40
    # A later revision of that plan keeps the size too.
    assert "Keep that size" in pl.revise_prompt(result.plan, SimpleNamespace(verdicts=[]), None, SMALL_INV)


# --- 3. The size check and its one retry -----------------------------------------------


def test_plan_at_40_percent_gets_one_too_small_retry():
    llm = FakeLLM(AT_40, AT_80)
    result = pl.make_plan("make me a guitar", SMALL_INV, complete=llm)
    assert len(llm.calls) == 2 and result.size_retried and result.size_warning == ""
    first, second = llm.calls
    assert len(first["messages"]) == 1
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["messages"][-1]["content"].startswith(
        "Too small: RED uses only 40% of its budget. Scale the model up."
    )
    assert json.loads(second["messages"][1]["content"])["sections"][0]["size"]["height"] == 2  # its own plan back
    assert "sizing" not in second["messages"][1]["content"]
    assert result.plan.sections[0].size.height == 4 and result.plan.sizing.utilization == 80
    assert result.cost_usd == pytest.approx(0.02) and result.attempts == 2


def test_there_is_no_part_cap():
    # A big plan that fits the parts is accepted as it is: no "too big", no retry.
    llm = FakeLLM(MANY_PARTS)
    result = pl.make_plan("make me a guitar", BIG_INV, complete=llm)
    assert len(llm.calls) == 1 and not result.size_retried
    assert result.plan.sizing.problem == "" and result.plan.sizing.utilization == 103
    for gone in ("max_parts", "cells_per_part", "MAX_PARTS", "CELLS_PER_PART"):
        assert not hasattr(sz, gone)
    assert "parts" not in pl.Sizing.model_fields and "parts" not in result.plan.sizing.line


def test_after_one_failed_size_retry_the_plan_is_kept_with_a_warning(capsys):
    llm = FakeLLM(AT_40, AT_40, "never used")
    result = pl.make_plan("make me a guitar", SMALL_INV, complete=llm)
    assert len(llm.calls) == 2  # one size retry, not two
    assert result.plan.sections[0].size.height == 2
    assert result.size_warning == result.plan.sizing.problem
    assert result.size_warning.startswith("Too small: RED uses only 40% of its budget.")
    err = capsys.readouterr().err
    assert "Warning: the plan is still too small after one retry; keeping it. Too small: RED uses only 40%" in err


def test_size_retry_is_separate_from_the_retry_for_an_unusable_plan(capsys):
    llm = FakeLLM("not json", AT_40, "not json", AT_80)  # each call has its own unusable-plan retry
    result = pl.make_plan("make me a guitar", SMALL_INV, complete=llm)
    assert len(llm.calls) == 4 and result.plan.sizing.utilization == 80 and result.attempts == 4
    # If the size retry gives no usable plan at all, the first plan is kept.
    llm = FakeLLM(AT_40, "not json", "still not json")
    result = pl.make_plan("make me a guitar", SMALL_INV, complete=llm)
    assert result.plan.sizing.utilization == 40 and result.size_warning.startswith("Too small")
    assert "the size retry gave no usable plan; keeping the first plan" in capsys.readouterr().err


def test_revised_plans_are_size_checked_too(tmp_path):
    image = tmp_path / "build.png"
    image.write_bytes(b"\x89PNG fake")
    old = pl.parse_plan(AT_80, SMALL_INV)
    review = SimpleNamespace(verdicts=[])
    llm = FakeLLM(AT_40, AT_80)
    result = pl.revise_plan(old, review, image, None, SMALL_INV, complete=llm)
    assert len(llm.calls) == 2 and result.size_retried and result.plan.sizing.utilization == 80


def test_min_fill_setting(monkeypatch):
    assert sz.min_fill() == 60
    assert sz.measure(json.loads(AT_40), SMALL_INV).sizing.problem.startswith("Too small")
    monkeypatch.setenv(sz.MIN_FILL_ENV, "35")
    assert sz.measure(json.loads(AT_40), SMALL_INV).sizing.problem == ""
    monkeypatch.setenv(sz.MIN_FILL_ENV, "lots")
    with pytest.raises(ValueError, match=sz.MIN_FILL_ENV):
        sz.min_fill()


# --- The numbers ---------------------------------------------------------------------


def test_utilization_is_taken_where_the_color_is_tightest():
    # 20 bricks 2 x 4 (wide) and 4 bricks 1 x 4 (48 plate-cells, budget 40): a thin
    # ring wall can only use the 1-wide ones, so red is at 90% there, not 36 / 448.
    inv = Inventory({("3001", RED): 20, ("3010", RED): 4})
    ring = {
        "name": "ring",
        "size": {"width": 4, "depth": 4, "height": 1},
        "position": {"x": 0, "y": 0, "z": 0},
        "fill": "shell",
        "wall": 1,
        "cells": [{"color": "RED", "cells": 36}],
    }
    measured = sz.measure({"sections": [ring]}, inv)
    assert [(u.color, u.cells, u.budget, u.percent) for u in measured.uses] == [("RED", 36, 40, 90)]


# --- 5. The "Size:" line and the sizing field ---------------------------------------------


def test_size_line_and_sizing_field():
    plan = pl.parse_plan(AT_80, SMALL_INV)
    line = "Size: 4 x 2 studs x 4 bricks, limited by RED (80% of budget)."
    assert plan.sizing.model_dump() == {
        "width": 4,
        "depth": 2,
        "height": 4.0,
        "limited_by": "RED",
        "utilization": 80,
        "user_size": False,
        "problem": "",
        "line": line,
    }
    # The planner cannot write its own sizing: code always overwrites it.
    lying = json.loads(AT_80)
    lying["sizing"] = {**plan.sizing.model_dump(), "utilization": 99, "line": "Size: huge"}
    assert pl.parse_plan(json.dumps(lying), SMALL_INV).sizing.line == line


def test_saved_plan_sizes(tmp_path):
    owned = dz.load_inventory(FIXTURES / "inventory.json")
    guitar = sz.measure(json.loads((FIXTURES / "guitar_1c64df97.json").read_text()), owned)
    assert guitar.sizing.line == (
        "Size: 12 x 31 studs x 1.33 bricks, limited by REDDISH_BROWN (124% of budget)."
    )
    skull = sz.measure(json.loads((FIXTURES / "skull_9ccc0f34_round1.json").read_text()), owned)
    assert skull.sizing.line == "Size: 10 x 11 studs x 7.33 bricks, limited by RED (78% of budget)."
    assert skull.sizing.problem == ""


def test_design_prints_the_size_line_and_saves_sizing_in_plan_json(tmp_path, capsys):
    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(
        json.dumps([{"part": p, "color": "RED", "count": n} for p, n in (("3001", 5), ("3020", 2), ("3023", 3))])
    )
    planned = pl.parse_plan(AT_80, SMALL_INV)

    def planner(request, inventory, *, model, expansion=None):
        return pl.PlanResult(planned, 0.05, 1)

    def run(record, config):
        return SimpleNamespace(usage=SimpleNamespace(cost_usd=0.01, prompt_tokens=10, completion_tokens=5))

    result = dz.design(
        "make me a guitar",
        inv_path,
        records_dir=tmp_path / "rec",
        run=run,
        gate=lambda build_path, inventory_path: (True, "ok"),
        planner=planner,
        renderer=lambda record, config: (None, "no render in tests"),
        use_expand=False,
    )
    line = "Size: 4 x 2 studs x 4 bricks, limited by RED (80% of budget)."
    assert line in capsys.readouterr().out.splitlines()
    saved = json.loads((tmp_path / "rec" / result.record_id / dz.PLAN_FILE).read_text())
    assert saved["sizing"]["line"] == line and saved["sizing"]["limited_by"] == "RED"
    assert saved["sizing"]["utilization"] == 80 and "parts" not in saved["sizing"]


# --- 6. The capacity command ---------------------------------------------------------------


def run_cli(capsys, *args):
    code = cap.main([*args, "--inventory", str(FIXTURES / "inventory.json")])
    return code, capsys.readouterr().out


def test_capacity_cli_prints_utilization_and_the_tightest_color(capsys):
    code, out = run_cli(capsys, str(FIXTURES / "skull_9ccc0f34_round1.json"), "--no-save")
    assert code == 0
    assert "Capacity check passed." in out
    assert "  RED: 716 / 919 = 78%" in out and "  LIGHT_BLUISH_GRAY: 214 / 366 = 58%" in out
    assert "Tightest color: RED (78% of budget)." in out
    assert "Size: 10 x 11 studs x 7.33 bricks, limited by RED (78% of budget)." in out
    assert "Too small" not in out and "Too big" not in out and "parts" not in out.lower()


def test_capacity_cli_says_too_small_unless_the_request_states_the_size(capsys, tmp_path):
    small = tmp_path / "plan.json"
    small.write_text(AT_40)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps([{"part": p, "color": "RED", "count": n} for p, n in (("3001", 5), ("3020", 2), ("3023", 3))])
    )
    args = [str(small), "--inventory", str(inventory), "--no-save"]
    assert cap.main(args) == 3
    assert "Too small: RED uses only 40% of its budget. Scale the model up." in capsys.readouterr().out
    assert cap.main([*args, "--request", "make me a small guitar"]) == 0
    out = capsys.readouterr().out
    assert "Too small" not in out and "The request states the size: no size check." in out
    # A plan that fits is never "too big": the skull that used to be over the part cap passes.
    code, out = run_cli(capsys, str(FIXTURES / "skull_73b41c51_round2_skin.json"), "--no-save")
    assert code == 0 and "Too big" not in out


def test_capacity_cli_saves_sizing_in_the_plan_file_only_when_it_fits(capsys, tmp_path):
    plan = tmp_path / "plan.json"
    shutil.copy(FIXTURES / "skull_9ccc0f34_round1.json", plan)
    code, out = run_cli(capsys, str(plan))
    assert code == 0 and f'Saved as "sizing" in {plan}.' in out
    saved = json.loads(plan.read_text())
    assert saved["sizing"]["limited_by"] == "RED" and saved["sizing"]["utilization"] == 78
    assert saved["sizing"]["line"].startswith("Size: 10 x 11 studs x 7.33 bricks")
    assert saved["sections"][0]["name"] == "base"  # the rest of the plan is untouched

    rejected = tmp_path / "rejected.json"
    shutil.copy(FIXTURES / "skull_73b41c51_round1_wide.json", rejected)
    code, out = run_cli(capsys, str(rejected))
    assert code == 1 and "LIGHT_BLUISH_GRAY: 1,356 / 805 = 168%" in out  # utilization is still printed
    assert "sizing" not in json.loads(rejected.read_text())
