"""Voxel's side of ask_expert: the context hook, the prompt rule, the switch, the
cost on its own rows, the rescue cap over the whole run, and a real ask_expert
call through Voxel's retry wrapper (all with fake responses)."""

import json
from types import SimpleNamespace

import litellm
import pytest
from brickwright.config import Config
from brickwright.record import Record
from brickwright.tools import build_execution, execute_tool
from brickwright.tools import expert as bw_expert

from voxel import design as dz
from voxel import expert as vx
from voxel import llm
from voxel.inventory import load_inventory
from voxel.plan import ORIENTATION, Plan

INVENTORY = [
    {"part": "3001", "color": "RED", "count": 4},
    {"part": "3005", "color": "BLUE", "count": 3},
    {"part": "3023", "color": "RED", "count": 6},
]

PLAN = {
    "checklist": ["looks like a tower", "is it red?"],
    "sections": [
        {
            "name": "base",
            "size": {"width": 4, "depth": 2, "height": 1},
            "position": {"x": 0, "y": 0, "z": 0},
            "colors": ["RED"],
            "parts": [{"part": "3001", "color": "RED", "count": 1}],
        },
        {
            "name": "top",
            "size": {"width": 4, "depth": 2, "height": 2},
            "position": {"x": 0, "y": 0, "z": 3},
            "colors": ["RED", "BLUE"],
            "parts": [{"part": "3001", "color": "RED", "count": 2}],
        },
    ],
    "parts": [{"part": "3001", "color": "RED", "count": 3}],
}

# A red 2x4 on the ground and a blue 1x1 floating at z=3, in the plan's "top".
BUILD = """\
from bricklayer import Color, LegoBuild, Position, brick
from brickwright import TestContext

def create_build():
    m = LegoBuild()
    m.add(brick(4, 2, color=Color.RED), position=Position(0, 0, 0))
    m.add(brick(1, 1, color=Color.BLUE), position=Position(0, 0, 3))
    return m

def run_tests():
    ctx = TestContext(create_build())
    ctx.check_no_floating()
    return ctx.report()
"""


def part(part_id, color, x, y, z):
    return {"part": part_id, "color": color, "position": [x, y, z], "top": [x + 1, y + 1, z + 1], "rotation": 0}


@pytest.fixture
def inventory_file(tmp_path, monkeypatch):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(INVENTORY), encoding="utf-8")
    monkeypatch.setenv(vx.INVENTORY_ENV, str(path))
    return path


# --- The context hook --------------------------------------------------------------


def test_context_lists_owned_parts_left_and_the_plan_section(tmp_path, inventory_file):
    (tmp_path / "plan.json").write_text(json.dumps(PLAN))
    parts = [part("3001", "RED", 0, 0, 0), part("3001", "RED", 0, 0, 1), part("3005", "BLUE", 0, 0, 3)]
    ctx = vx.context(tmp_path, parts, [2])
    assert ctx["reference"].startswith("PARTS THE USER OWNS")
    assert "3001 | Brick 2 x 4 | RED | 4" in ctx["reference"]
    details = ctx["details"]
    assert "3001 | Brick 2 x 4 | RED | 2" in details  # 4 owned, 2 used
    assert "3005 | Brick 1 x 1 | BLUE | 2" in details and "3023 | Plate 1 x 2 | RED | 6" in details
    assert "PLAN SECTION the problem is in: top" in details
    assert "colors: RED, BLUE" in details and "planned parts: 2x 3001 RED" in details
    assert vx.UNITS_TEXT in details and ORIENTATION in details


def test_the_reference_is_the_same_whatever_the_build(tmp_path, inventory_file):
    one = vx.context(tmp_path, [part("3001", "RED", 0, 0, 0)], [0])
    two = vx.context(tmp_path, [part("3005", "BLUE", 9, 9, 9)] * 3, [0])
    assert one["reference"] == two["reference"] and one["details"] != two["details"]


def test_plan_section_is_the_box_holding_the_part_else_the_nearest():
    plan = Plan.model_validate(PLAN)
    assert vx.plan_section(plan, [1, 1, 0.5]).name == "base"
    assert vx.plan_section(plan, [1, 1, 3.5]).name == "top"
    assert vx.plan_section(plan, [1, 1, 2.2]).name == "top"  # between: nearer the top
    assert vx.plan_section(plan, [1, 1, 1.6]).name == "base"


# --- design(): the switch and the prompt rule ---------------------------------------


def test_build_prompt_tells_the_builder_to_ask_the_expert(tmp_path):
    inv = load_inventory_from(tmp_path)
    plan = Plan.model_validate(PLAN)
    rule = "When validate fails, call ask_expert and apply its edits exactly. Don't try to fix it yourself first."
    assert vx.EXPERT_RULE == rule
    assert rule in dz.build_prompt("a tower", inv, plan, {})
    assert rule in dz.build_prompt("a tower", inv)
    assert rule not in dz.build_prompt("a tower", inv, plan, {}, expert=False)


def load_inventory_from(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text(json.dumps(INVENTORY))
    return load_inventory(path)


@pytest.fixture
def design_kit():
    """test_design's fakes (fake_run_factory, run_design, make_inventory)."""
    import test_design

    return test_design


def test_design_turns_the_tool_on_and_no_expert_turns_it_off(tmp_path, design_kit, monkeypatch):
    monkeypatch.delenv(bw_expert.EXPERT_CONTEXT_ENV, raising=False)
    seen = []

    def run(record, config):
        seen.append(dict(on=bw_expert.expert_enabled(), prompt=record.read_meta().prompt))
        return design_kit.fake_run_factory([2])(record, config)

    design_kit.run_design(tmp_path, design_kit.make_inventory(tmp_path), run)
    assert seen[-1]["on"] and vx.EXPERT_RULE in seen[-1]["prompt"]
    assert vx.CONTEXT_HOOK == "voxel.expert:context"

    design_kit.run_design(tmp_path, design_kit.make_inventory(tmp_path), run, use_expert=False)
    assert not seen[-1]["on"] and vx.EXPERT_RULE not in seen[-1]["prompt"]


def test_no_expert_flag(tmp_path, monkeypatch, design_kit):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def fake_design(request, inventory, **kwargs):
        seen.update(kwargs)
        return dz.DesignResult(True, "rec", tmp_path / "build.py", 1, "ok")

    monkeypatch.setattr(dz, "design", fake_design)
    inv = str(design_kit.make_inventory(tmp_path))
    dz.main(["a tower", "--inventory", inv])
    assert seen["use_expert"] is True
    dz.main(["a tower", "--inventory", inv, "--no-expert"])
    assert seen["use_expert"] is False


# --- Cost: expert calls on their own rows, inside the budget -------------------------


def expert_call(model, role, cost, outcome="answered"):
    return {
        "problem": 1, "check": "no_floating", "role": role, "model": model, "outcome": outcome,
        "cost_usd": cost, "input_tokens": 1000, "output_tokens": 100,
    }


def run_with_expert_calls(design_kit, rounds_calls, sizes, total_cost=0.6):
    """A fake builder whose run made these ask_expert calls (written to expert.json,
    as BrickWright does) and reports a total that includes them."""
    inner = design_kit.fake_run_factory(sizes, total_cost)
    configs = []

    def run(record, config):
        configs.append(config)
        calls = rounds_calls[len(configs) - 1]
        bw_expert.save_state(record.path, {"problems": [], "calls": calls})
        return inner(record, config)

    run.calls, run.configs = inner.calls, configs
    return run


def test_expert_cost_has_its_own_rows_and_counts_in_the_total(tmp_path, design_kit):
    calls = [expert_call("claude-sonnet-5-5", "expert", 0.2), expert_call("claude-opus-5-5", "rescue", 0.3)]
    run = run_with_expert_calls(design_kit, [calls], [2])
    result = design_kit.run_design(tmp_path, design_kit.make_inventory(tmp_path), run)
    assert result.expert_cost_usd == pytest.approx(0.5)
    assert result.build_cost_usd == pytest.approx(0.1)  # 0.6 from BrickWright, minus the expert
    assert result.cost_usd == pytest.approx(0.05 + 0.6)  # plan + the builder run
    line = dz.models_line(result)
    assert "claude-sonnet-5-5 1,000 in / 100 out $0.200" in line
    assert "claude-opus-5-5 1,000 in / 100 out $0.350" in line  # Opus also made the plan ($0.05)
    assert "expert $0.500" in dz.cost_line(result)
    usage = json.loads((tmp_path / "rec" / result.record_id / dz.USAGE_FILE).read_text())
    rows = {row["model"]: row for row in usage["models"]}
    assert rows["claude-opus-5-5"]["steps"] == ["plan", "expert"]
    assert rows["claude-sonnet-5-5"]["steps"] == ["expert"]


def test_rescue_cap_is_for_the_whole_run(tmp_path, design_kit):
    two_rescues = [expert_call("claude-opus-5-5", "rescue", 0.1)] * 2
    run = run_with_expert_calls(design_kit, [two_rescues, []], [3, 2], total_cost=0.2)  # round 1 fails the gate
    design_kit.run_design(tmp_path, design_kit.make_inventory(tmp_path), run)
    assert [c.expert_max_rescues for c in run.configs] == [3, 1]


def test_the_expert_shares_the_builders_budget(tmp_path, design_kit):
    run = run_with_expert_calls(design_kit, [[]], [2], total_cost=0.1)
    design_kit.run_design(tmp_path, design_kit.make_inventory(tmp_path), run, max_cost=0.4)
    assert run.configs[0].max_cost == pytest.approx(0.35)  # what the tool may spend, at most


# --- A real ask_expert call with Voxel's hook and retry wrapper -----------------------


@pytest.fixture
def expert_record(tmp_path, inventory_file, monkeypatch):
    monkeypatch.setattr(build_execution, "run_in_subprocess", lambda fn, args, timeout: fn(*args))
    dz.set_expert(True)
    record = Record.create(Config(records_dir=tmp_path / "rec"), prompt="a tower")
    record.write_build(BUILD)
    (record.path / "plan.json").write_text(json.dumps(PLAN))
    yield record
    dz.set_expert(False)


def fake_seam(monkeypatch, outcomes):
    """BrickWright's completion seam: each call raises or returns the next outcome."""
    from brickwright.driver import loop

    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(loop, "litellm_complete", lambda: complete)
    monkeypatch.setattr(llm, "sleep", lambda s: None)
    return calls


def answer(cost=0.08):
    text = json.dumps(
        {
            "diagnosis": "the blue brick floats",
            "edits": [
                {"action": "remove", "part": "3005", "color": "BLUE", "x": 0, "y": 0, "z": 3},
                {"action": "add", "part": "3005", "color": "BLUE", "x": 0, "y": 0, "z": 1},
            ],
        }
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=2000, completion_tokens=150, total_tokens=2150),
        _hidden_params={"response_cost": cost},
    )


def ask(record):
    with dz.builder_retries():
        return execute_tool(record, Config(), "ask_expert", {"record_id": record.id, "check": "no_floating"})


def timeout():
    return litellm.Timeout(message="Request timed out", model="m", llm_provider="anthropic")


def test_ask_expert_with_voxels_hook_and_a_retried_timeout(expert_record, monkeypatch):
    calls = fake_seam(monkeypatch, [timeout(), answer()])
    outcome = ask(expert_record)
    assert outcome.ok and "1. remove BLUE 3005 at (0,0,3)" in outcome.content
    assert "2. add BLUE 3005 at (0,0,1)" in outcome.content
    assert len(calls) == 2  # the timeout was retried by Voxel's call_api
    system, packet = calls[1]["messages"][0]["content"][0]["text"], calls[1]["messages"][1]["content"]
    assert "PARTS THE USER OWNS" in system and "3001 | Brick 2 x 4 | RED | 4" in system
    assert "PLAN SECTION the problem is in: top" in packet
    assert "3005 | Brick 1 x 1 | BLUE | 2" in packet  # parts left: 3 owned, 1 used
    assert calls[1]["model"] == "claude-sonnet-5-5" and calls[1]["timeout"] == 180
    [entry] = bw_expert.load_state(expert_record.path)["calls"]
    assert entry["outcome"] == "answered" and entry["cost_usd"] == pytest.approx(0.08)


def test_an_expert_that_keeps_timing_out_does_not_end_the_run(expert_record, monkeypatch):
    fake_seam(monkeypatch, [timeout(), timeout(), timeout()])
    outcome = ask(expert_record)
    assert not outcome.ok and outcome.content == "The expert is unavailable right now; fix this yourself."
    [entry] = bw_expert.load_state(expert_record.path)["calls"]
    assert entry["outcome"] == "unavailable" and "timed out 3 times" in entry["reason"]


def test_a_credit_error_from_the_expert_stops_the_run(expert_record, monkeypatch):
    credit = litellm.BadRequestError(
        message="Your credit balance is too low to access the Anthropic API.", model="m", llm_provider="anthropic"
    )
    fake_seam(monkeypatch, [credit])
    with pytest.raises(llm.CallFailed, match="credit balance is too low"):
        ask(expert_record)
