import json
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory, design as dz
from voxel.plan import Plan, PlanError, PlanResult, spare_set

RED = bl.Color.RED


def make_inventory(tmp_path, count=2):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps([{"part": "3001", "color": "RED", "count": count}]), encoding="utf-8")
    return path


def tower(n):
    build = bl.LegoBuild()
    last = build.add(bl.Element("3001", RED))
    for _ in range(n - 1):
        last = build.stack(bl.Element("3001", RED), on=last)
    return build


class FakeTests:
    def __init__(self, build):
        self.build = build
        self.results = []

    def check(self, name, ok, detail=None):
        self.results.append((name, ok, detail))


def test_prompt_lists_parts_and_rule(tmp_path):
    inv = Inventory({("3001", RED.ldraw_id): 5})
    text = dz.build_prompt("a tower", inv)
    assert text.startswith("a tower")
    assert "3001 | Brick 2 x 4 |" in text and "| 5" in text
    assert "check_owned(tests)" in text and "ONLY" in text


def test_check_owned_passes_and_fails(tmp_path):
    path = make_inventory(tmp_path, count=2)
    good = FakeTests(tower(2))
    dz.check_owned(good, path)
    assert good.results == [("only_owned_parts", True, None)]
    bad = FakeTests(tower(3))
    dz.check_owned(bad, path)
    name, ok, detail = bad.results[0]
    assert not ok and "used 3, owned 2" in detail


def test_check_owned_uses_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv(dz.INVENTORY_ENV, str(make_inventory(tmp_path)))
    t = FakeTests(tower(1))
    dz.check_owned(t)
    assert t.results[0][1] is True
    monkeypatch.delenv(dz.INVENTORY_ENV)
    t = FakeTests(tower(1))
    dz.check_owned(t)
    assert t.results[0][1] is False


BUILD_SRC = """
import bricklayer as bl
def create_build():
    b = bl.LegoBuild()
    last = b.add(bl.Element("3001", bl.Color.RED))
    b.add_step()
    for _ in range({n} - 1):
        last = b.stack(bl.Element("3001", bl.Color.RED), on=last)
        b.add_step()
    return b
"""


def test_final_gate_on_real_build_files(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    ok_file, bad_file, broken = (tmp_path / n for n in ("ok.py", "bad.py", "broken.py"))
    ok_file.write_text(BUILD_SRC.format(n=2))
    bad_file.write_text(BUILD_SRC.format(n=3))
    broken.write_text("def create_build():\n    raise ValueError('boom')\n")
    assert dz.final_gate(ok_file, inv)[0] is True
    ok, report = dz.final_gate(bad_file, inv)
    assert not ok and "used 3, owned 2" in report
    ok, report = dz.final_gate(broken, inv)
    assert not ok and "boom" in report


def fake_run_factory(sizes, costs=0.1):
    """A stand-in agent: each call writes a tower of the next size."""
    calls = []

    def run(record, config):
        n = sizes[len(calls)]
        record.write_build(BUILD_SRC.format(n=n))
        calls.append(record.id)
        return SimpleNamespace(usage=SimpleNamespace(cost_usd=costs))

    run.calls = calls
    return run


TOWER_PLAN = {
    "checklist": ["looks like a tower", "is it red?"],
    "sections": [
        {
            "name": "tower",
            "size": {"width": 4, "depth": 2, "height": 6},
            "position": {"x": 0, "y": 0, "z": 0},
            "colors": ["RED"],
            "parts": [{"part": "3001", "color": "RED", "count": 2}],
        }
    ],
    "parts": [{"part": "3001", "color": "RED", "count": 2}],
}


def fake_planner(cost=0.05):
    calls = []

    def planner(request, inventory, *, model):
        calls.append((request, model))
        return PlanResult(Plan.model_validate(TOWER_PLAN), cost, 1)

    planner.calls = calls
    return planner


def run_design(tmp_path, inv, run, **kwargs):
    kwargs.setdefault("planner", fake_planner())
    return dz.design("a tower", inv, records_dir=tmp_path / "rec", run=run, **kwargs)


def test_outer_loop_retries_then_passes(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    run = fake_run_factory([3, 2])
    result = run_design(tmp_path, inv, run)
    assert result.ok and result.rounds == 2
    assert result.cost_usd == pytest.approx(0.25)  # plan 0.05 + two rounds of 0.1
    assert result.plan_cost_usd == pytest.approx(0.05)
    assert result.build_cost_usd == pytest.approx(0.2)
    assert len(set(run.calls)) == 2


def test_outer_loop_gives_up_after_retries(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    run = fake_run_factory([3, 3, 3])
    result = run_design(tmp_path, inv, run, max_retries=2)
    assert not result.ok and result.rounds == 3
    assert "used 3, owned 2" in result.report


def test_plan_is_saved_in_every_record(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    run = fake_run_factory([3, 2])
    planner = fake_planner()
    run_design(tmp_path, inv, run, planner=planner, plan_model="some-plan-model")
    assert planner.calls == [("a tower", "some-plan-model")]
    for record_id in run.calls:
        saved = json.loads((tmp_path / "rec" / record_id / dz.PLAN_FILE).read_text())
        assert saved == TOWER_PLAN


def record_prompt(tmp_path, record_id):
    return json.loads((tmp_path / "rec" / record_id / "meta.json").read_text())["prompt"]


def test_retry_prompt_repeats_plan_parts_and_report(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            [
                {"part": "3001", "color": "RED", "count": 2},
                {"part": "3023", "color": "RED", "count": 4},  # Plate 1 x 2: spare
            ]
        ),
        encoding="utf-8",
    )
    run = fake_run_factory([3, 2])
    run_design(tmp_path, path, run)
    first, retry = (record_prompt(tmp_path, rid) for rid in run.calls)
    assert retry.startswith(first)  # request, plan, planned parts, spare set
    assert '"looks like a tower"' in retry and '"name": "tower"' in retry
    assert "3001 | Brick 2 x 4 | RED | 2 | 2" in retry
    assert "3023 | Plate 1 x 2 | RED | 4" in retry
    assert "PREVIOUS ATTEMPT" in retry and "used 3, owned 2" in retry
    assert "inventory and build steps" in retry and "at most 12 parts" in retry
    assert "PREVIOUS ATTEMPT" not in first


def test_plan_error_propagates(tmp_path):
    def failing(request, inventory, *, model):
        raise PlanError("no plan", 0.07, 2)

    with pytest.raises(PlanError) as info:
        run_design(tmp_path, make_inventory(tmp_path), fake_run_factory([2]), planner=failing)
    assert info.value.cost_usd == pytest.approx(0.07)


def test_prompt_with_plan_lists_only_planned_and_spare_parts():
    inv = Inventory(
        {
            ("3001", RED.ldraw_id): 5,
            ("3023", RED.ldraw_id): 4,  # Plate 1 x 2: spare
            ("3039", RED.ldraw_id): 2,  # slope: left out
            ("3001", bl.Color.BLUE.ldraw_id): 6,  # other color: left out
        }
    )
    plan = Plan.model_validate(TOWER_PLAN)
    text = dz.build_prompt("a tower", inv, plan, spare_set(inv, plan))
    assert text.startswith("a tower")
    assert '"looks like a tower"' in text
    assert "3001 | Brick 2 x 4 | RED | 2 | 5" in text
    assert "3023 | Plate 1 x 2 | RED | 4" in text
    assert "3039" not in text and "BLUE" not in text
    assert "check_owned(tests)" in text and "ONLY" in text
    assert "build.add_step() after each group of at most 12 parts" in text
    assert "bottom-up" in text and "sub-models" in text
    assert "plan's sections in order, bottom to top" in text


def test_prompt_without_plan_has_steps_rules_but_no_plan_line():
    text = dz.build_prompt("a tower", Inventory({("3001", RED.ldraw_id): 5}))
    assert "build.add_step() after each group of at most 12 parts" in text
    assert "bottom-up" in text and "sub-models" in text
    assert "plan's sections" not in text


def test_agent_import_line_works():
    from voxel import check_owned

    assert check_owned is dz.check_owned
