import json
from pathlib import Path
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory, design as dz
from voxel.expand import ExpandError, Expansion, ExpansionResult, Question, QuestionsResult
from voxel.plan import Plan, PlanError, PlanResult, spare_set
from voxel.review import Review, ReviewError, ReviewResult, Verdict

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
            "fill": "solid",
            "wall": 1,
            "empty": 0,
            "cells": [{"color": "RED", "cells": 144}],
        }
    ],
    "parts": [{"part": "3001", "color": "RED", "count": 2}],
}


def fake_planner(cost=0.05):
    calls = []

    def planner(request, inventory, *, model, expansion=None):
        calls.append((request, model))
        planner.expansions.append(expansion)
        return PlanResult(Plan.model_validate(TOWER_PLAN), cost, 1)

    planner.calls = calls
    planner.expansions = []
    return planner


def no_render(record, config):
    return None, "no render in tests"


def fake_renderer(record, config):
    image = Path(record.out_dir) / "build.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"fake png")
    return image, ""


def fake_reviewer(outcomes, cost=0.03):
    """Each call returns the next list of pass/fail values, one per checklist item."""
    calls = []

    def reviewer(plan, expansion, image_path, *, model):
        calls.append({"expansion": expansion, "image": Path(image_path), "model": model})
        passed = outcomes[len(calls) - 1]
        verdicts = [
            Verdict(index=n, item=item, passed=ok, reason="fine" if ok else f"{item} is not met")
            for n, (item, ok) in enumerate(zip(plan.checklist, passed), start=1)
        ]
        return ReviewResult(Review(verdicts=verdicts), cost, 1)

    reviewer.calls = calls
    return reviewer


REVISED_PLAN = {
    **TOWER_PLAN,
    "sections": [
        {
            **TOWER_PLAN["sections"][0],
            "name": "tower v2",
            "size": {"width": 2, "depth": 2, "height": 2.333},
        }
    ],
}


def fake_reviser(cost=0.05, revised=REVISED_PLAN):
    calls = []

    def reviser(old_plan, review, image_path, expansion, inventory, *, model):
        calls.append(
            {"old": old_plan, "review": review, "image": Path(image_path), "expansion": expansion, "model": model}
        )
        return PlanResult(Plan.model_validate(revised), cost, 1)

    reviser.calls = calls
    return reviser


def run_design(tmp_path, inv, run, **kwargs):
    kwargs.setdefault("planner", fake_planner())
    kwargs.setdefault("reviser", fake_reviser())
    kwargs.setdefault("renderer", no_render)
    kwargs.setdefault("use_expand", False)
    return dz.design("a tower", inv, records_dir=tmp_path / "rec", run=run, **kwargs)


def reviews_in(tmp_path, record_id):
    return json.loads((tmp_path / "rec" / record_id / dz.REVIEW_FILE).read_text())


def test_review_all_pass(tmp_path):
    run = fake_run_factory([2])
    reviewer = fake_reviewer([[True, True]])
    result = run_design(tmp_path, make_inventory(tmp_path), run, renderer=fake_renderer, reviewer=reviewer)
    assert result.ok and result.review_passed is True and result.review_rounds == 1
    assert result.rounds == 1 and len(run.calls) == 1
    assert reviewer.calls[0]["expansion"] is None
    assert reviewer.calls[0]["model"] == "claude-opus-5-5"
    assert reviewer.calls[0]["image"].name == "build.png"
    [entry] = reviews_in(tmp_path, result.record_id)
    assert entry["round"] == 1 and entry["passed"] is True and len(entry["verdicts"]) == 2
    assert dz.review_line(result) == "Review: passed after 1 round(s)"
    assert dz.exit_code(result) == 0


def test_review_fails_then_passes_in_round_2(tmp_path):
    run = fake_run_factory([2, 2])
    reviewer = fake_reviewer([[False, True], [True, True]])
    result = run_design(tmp_path, make_inventory(tmp_path), run, renderer=fake_renderer, reviewer=reviewer)
    assert result.ok and result.review_passed is True and result.review_rounds == 2
    assert result.rounds == 2
    first, second = run.calls
    assert result.record_id == second
    retry = record_prompt(tmp_path, second)
    assert '"name": "tower v2"' in retry and '"name": "tower"' not in retry  # the NEW plan
    assert "failed the visual review" in retry
    assert "- looks like a tower: looks like a tower is not met" in retry
    assert "is it red?: " not in retry  # only failed items
    assert [e["round"] for e in reviews_in(tmp_path, second)] == [1, 2]
    assert [e["passed"] for e in reviews_in(tmp_path, second)] == [False, True]
    assert len(reviews_in(tmp_path, first)) == 1
    assert (tmp_path / "rec" / second / dz.PLAN_FILE).exists()


def test_review_still_failing_after_2_rounds(tmp_path):
    run = fake_run_factory([2, 2, 2])
    reviewer = fake_reviewer([[False, True], [False, True]])
    result = run_design(tmp_path, make_inventory(tmp_path), run, renderer=fake_renderer, reviewer=reviewer)
    assert result.ok and result.review_passed is False and result.review_rounds == 2
    assert len(run.calls) == 2 and len(reviewer.calls) == 2  # no third, unreviewed build
    assert "looks like a tower is not met" in result.review_report
    assert dz.review_line(result).startswith("Review: FAILED after 2 round(s):")
    assert dz.exit_code(result) == 3


def test_no_render_skips_review_with_warning(tmp_path, capsys):
    reviewer = fake_reviewer([])
    renderer = lambda record, config: (None, "no LDraw library found (set LDRAW_LIBRARY_PATH in .env)")
    result = run_design(
        tmp_path, make_inventory(tmp_path), fake_run_factory([2]), renderer=renderer, reviewer=reviewer
    )
    assert result.ok and result.review_passed is None and result.review_rounds == 0
    assert reviewer.calls == []
    assert "Warning: review skipped: no LDraw library found" in capsys.readouterr().err
    assert not (tmp_path / "rec" / result.record_id / dz.REVIEW_FILE).exists()
    assert dz.review_line(result).startswith("Review: skipped (no LDraw library found")
    assert dz.exit_code(result) == 0


def test_render_build_reports_missing_ldraw(tmp_path, monkeypatch):
    from brickwright.tools import rendering

    monkeypatch.setattr(rendering, "render_available", lambda: False)
    image, why = dz.render_build(None, None)
    assert image is None and "no LDraw library" in why


def test_all_costs_are_counted(tmp_path):
    run = fake_run_factory([2, 2], costs=0.1)
    reviewer = fake_reviewer([[False, True], [True, True]], cost=0.03)
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        planner=fake_planner(cost=0.05),
        reviser=fake_reviser(cost=0.04),
        renderer=fake_renderer,
        reviewer=reviewer,
    )
    assert result.plan_cost_usd == pytest.approx(0.09)  # plan 0.05 + revision 0.04
    assert result.build_cost_usd == pytest.approx(0.2)
    assert result.review_cost_usd == pytest.approx(0.06)
    assert result.expand_cost_usd == 0.0
    assert result.cost_usd == pytest.approx(0.35)
    assert dz.cost_line(result) == (
        "Cost: $0.350 (expand $0.000 + plan $0.090 + build $0.200 + expert $0.000 + review $0.060)"
    )
    assert [e["cost_usd"] for e in reviews_in(tmp_path, result.record_id)] == [0.03, 0.03]


def test_reviewer_error_cost_counted_and_review_skipped(tmp_path, capsys):
    def broken(plan, expansion, image_path, *, model):
        raise ReviewError("No usable review after 2 attempts.", 0.04, 2)

    result = run_design(
        tmp_path, make_inventory(tmp_path), fake_run_factory([2]), renderer=fake_renderer, reviewer=broken
    )
    assert result.ok and result.review_passed is None
    assert result.review_cost_usd == pytest.approx(0.04)
    assert "Warning: review skipped: No usable review" in capsys.readouterr().err


def test_rebuild_failing_gate_keeps_reviewed_build(tmp_path, capsys):
    run = fake_run_factory([2, 3, 3, 3])  # the rebuild after the review never passes the gate
    reviewer = fake_reviewer([[False, True]])
    result = run_design(
        tmp_path, make_inventory(tmp_path), run, renderer=fake_renderer, reviewer=reviewer, max_retries=2
    )
    assert result.ok and result.record_id == run.calls[0]
    assert result.review_passed is False and result.review_rounds == 1
    assert result.rounds == 4
    assert "kept " + run.calls[0] in result.review_note
    assert "kept" in capsys.readouterr().err
    assert dz.exit_code(result) == 3


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
    assert "inventory, and parts held up" in retry
    assert "step" not in retry.split("PREVIOUS ATTEMPT")[1]  # steps are Voxel's job now
    assert "PREVIOUS ATTEMPT" not in first


def test_plan_error_propagates(tmp_path):
    def failing(request, inventory, *, model, expansion=None):
        raise PlanError("no plan", 0.07, 2)

    with pytest.raises(PlanError) as info:
        run_design(tmp_path, make_inventory(tmp_path), fake_run_factory([2]), planner=failing)
    assert info.value.cost_usd == pytest.approx(0.07)


def test_prompt_with_plan_lists_only_planned_and_spare_parts():
    inv = Inventory(
        {
            ("3001", RED.ldraw_id): 5,
            ("3023", RED.ldraw_id): 4,  # Plate 1 x 2: spare
            ("3039", RED.ldraw_id): 2,  # slope in a plan color: spare
            ("3039", bl.Color.BLUE.ldraw_id): 2,  # slope in another color: left out
            ("3001", bl.Color.BLUE.ldraw_id): 6,  # other color: left out
        }
    )
    plan = Plan.model_validate(TOWER_PLAN)
    text = dz.build_prompt("a tower", inv, plan, spare_set(inv, plan))
    assert text.startswith("a tower")
    assert '"looks like a tower"' in text
    assert "3001 | Brick 2 x 4 | RED | 2 | 5" in text
    assert "3023 | Plate 1 x 2 | RED | 4" in text
    assert "3039 | Slope Brick 45 2 x 2 | RED | 2" in text and "BLUE" not in text
    assert "z and height are in bricks (1 brick = 3 plates)" in text
    assert "check_owned(tests)" in text and "ONLY" in text
    assert "BUILD STEPS" not in text and "add_step" not in text


def test_prompt_without_plan_has_no_steps_rules_and_no_plan_line():
    text = dz.build_prompt("a tower", Inventory({("3001", RED.ldraw_id): 5}))
    assert "BUILD STEPS" not in text and "add_step" not in text
    assert "plan's sections" not in text


def test_agent_import_line_works():
    from voxel import check_owned

    assert check_owned is dz.check_owned


# --- Expand step ----------------------------------------------------------------

COLOR_QUESTION = Question(
    id="color", text="What color?", kind="color", options=["RED", "WHITE"], default="RED"
)
HEIGHT_QUESTION = Question(id="height", text="How tall?", kind="other", default="6 bricks")
TOWER_EXPANSION = Expansion(
    description="A tall red tower with a white top.",
    size={"width": 4, "depth": 2, "height": 6},
    features=["white top"],
)


def fake_asker(questions=(COLOR_QUESTION, HEIGHT_QUESTION), cost=0.02):
    calls = []

    def asker(request, inventory, *, model):
        calls.append((request, model))
        return QuestionsResult(list(questions), cost, 1)

    asker.calls = calls
    return asker


def fake_expander(cost=0.04):
    calls = []

    def expander(request, answers, inventory, *, model):
        calls.append(answers)
        return ExpansionResult(TOWER_EXPANSION, cost, 1)

    expander.calls = calls
    return expander


def fake_ask(*typed):
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return typed[len(prompts) - 1]

    ask.prompts = prompts
    return ask


def expand_design(tmp_path, **kwargs):
    kwargs.setdefault("asker", fake_asker())
    kwargs.setdefault("expander", fake_expander())
    kwargs.setdefault("use_expand", True)
    return run_design(tmp_path, make_inventory(tmp_path), fake_run_factory([2]), **kwargs)


def test_default_answers_on_empty_input(tmp_path):
    ask, expander, planner = fake_ask("", "  "), fake_expander(), fake_planner()
    result = expand_design(tmp_path, ask=ask, expander=expander, planner=planner)
    assert ask.prompts == ["What color? (RED, WHITE) [RED] ", "How tall? [6 bricks] "]
    [answers] = expander.calls
    assert [(a.value, a.typed) for a in answers] == [("RED", False), ("6 bricks", False)]
    assert planner.expansions == [TOWER_EXPANSION]
    saved = json.loads((tmp_path / "rec" / result.record_id / dz.EXPANSION_FILE).read_text())
    assert saved["request"] == "a tower"
    assert [q["id"] for q in saved["questions"]] == ["color", "height"]
    assert saved["answers"][0] == {"id": "color", "question": "What color?", "answer": "RED", "typed": False}
    assert saved["description"] == "A tall red tower with a white top."
    assert saved["size"] == {"width": 4, "depth": 2, "height": 6, "height_unit": "bricks (1 brick = 3 plates)"}
    assert saved["features"] == ["white top"]
    assert saved["cost_usd"] == pytest.approx(0.06)


def test_typed_answers_are_used(tmp_path):
    expander = fake_expander()
    expand_design(tmp_path, ask=fake_ask("WHITE", "10 bricks"), expander=expander)
    [answers] = expander.calls
    assert [(a.value, a.typed) for a in answers] == [("WHITE", True), ("10 bricks", True)]


def test_end_of_input_uses_the_default(tmp_path):
    def closed(prompt):
        raise EOFError

    expander = fake_expander()
    expand_design(tmp_path, ask=closed, expander=expander)
    assert [a.value for a in expander.calls[0]] == ["RED", "6 bricks"]


def test_no_questions_uses_defaults_without_asking(tmp_path, capsys):
    def never(prompt):
        raise AssertionError("must not ask")

    asker, expander = fake_asker(), fake_expander()
    expand_design(tmp_path, ask=never, ask_user=False, asker=asker, expander=expander)
    assert len(asker.calls) == 1  # the questions call still gives the defaults
    assert [(a.value, a.typed) for a in expander.calls[0]] == [("RED", False), ("6 bricks", False)]
    out = capsys.readouterr().out
    assert "What color? (RED, WHITE) [RED] (default)" in out


def test_no_expand_skips_the_whole_step(tmp_path):
    asker, expander, planner = fake_asker(), fake_expander(), fake_planner()
    reviewer = fake_reviewer([[True, True]])
    result = expand_design(
        tmp_path,
        use_expand=False,
        asker=asker,
        expander=expander,
        planner=planner,
        renderer=fake_renderer,
        reviewer=reviewer,
    )
    assert asker.calls == [] and expander.calls == []
    assert planner.expansions == [None]
    assert reviewer.calls[0]["expansion"] is None
    assert result.expand_cost_usd == 0.0
    assert not (tmp_path / "rec" / result.record_id / dz.EXPANSION_FILE).exists()


def test_expansion_goes_to_the_reviewer(tmp_path):
    reviewer = fake_reviewer([[True, True]])
    expand_design(tmp_path, ask=fake_ask("", ""), renderer=fake_renderer, reviewer=reviewer)
    text = reviewer.calls[0]["expansion"]
    assert text == TOWER_EXPANSION.as_text()
    assert "4 x 2 studs, 6 bricks tall (1 brick = 3 plates)" in text


def test_expand_cost_is_counted(tmp_path):
    result = expand_design(
        tmp_path,
        ask=fake_ask("", ""),
        asker=fake_asker(cost=0.02),
        expander=fake_expander(cost=0.04),
        planner=fake_planner(cost=0.05),
    )
    assert result.expand_cost_usd == pytest.approx(0.06)
    assert result.cost_usd == pytest.approx(0.06 + 0.05 + 0.1)
    assert dz.cost_line(result) == (
        "Cost: $0.210 (expand $0.060 + plan $0.050 + build $0.100 + expert $0.000 + review $0.000)"
    )


def test_expansion_saved_in_retry_records(tmp_path):
    run = fake_run_factory([3, 2])
    run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        use_expand=True,
        ask=fake_ask("", ""),
        asker=fake_asker(),
        expander=fake_expander(),
    )
    for record_id in run.calls:
        assert (tmp_path / "rec" / record_id / dz.EXPANSION_FILE).exists()


def test_expand_error_carries_the_questions_cost(tmp_path):
    def broken(request, answers, inventory, *, model):
        raise ExpandError("No usable description after 2 attempts.", 0.04, 2)

    with pytest.raises(ExpandError) as info:
        expand_design(tmp_path, ask=fake_ask("", ""), asker=fake_asker(cost=0.02), expander=broken)
    assert info.value.cost_usd == pytest.approx(0.06)


def test_plan_error_after_expand_carries_both_costs(tmp_path):
    def failing(request, inventory, *, model, expansion=None):
        raise PlanError("no plan", 0.07, 2)

    with pytest.raises(PlanError) as info:
        expand_design(tmp_path, ask=fake_ask("", ""), planner=failing)
    assert info.value.cost_usd == pytest.approx(0.06 + 0.07)


@pytest.mark.parametrize(
    "flags, use_expand, ask_user",
    [([], True, True), (["--no-questions"], True, False), (["--no-expand"], False, True)],
)
def test_main_flags(tmp_path, monkeypatch, flags, use_expand, ask_user):
    monkeypatch.chdir(tmp_path)  # no .env here, so main does not load a real key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    inv = make_inventory(tmp_path)
    seen = {}

    def fake_design(request, inventory, **kwargs):
        seen.update(kwargs)
        return dz.DesignResult(True, "rec", tmp_path / "build.py", 1, "ok")

    monkeypatch.setattr(dz, "design", fake_design)
    assert dz.main(["a tower", "--inventory", str(inv), *flags]) == 0
    assert seen["use_expand"] is use_expand and seen["ask_user"] is ask_user


def test_main_reports_expand_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_design(request, inventory, **kwargs):
        raise ExpandError("No usable questions after 2 attempts.", 0.04, 2)

    monkeypatch.setattr(dz, "design", fake_design)
    assert dz.main(["a tower", "--inventory", str(make_inventory(tmp_path))]) == 1
    err = capsys.readouterr().err
    assert "--no-expand" in err and "Cost so far: $0.040" in err


# --- Re-plan after a failed review ----------------------------------------------


def meta(tmp_path, record_id):
    return json.loads((tmp_path / "rec" / record_id / "meta.json").read_text())


def plans_in(tmp_path, record_id):
    return json.loads((tmp_path / "rec" / record_id / dz.PLANS_FILE).read_text())


def test_failed_review_revises_the_plan_and_rebuilds_from_empty(tmp_path):
    run = fake_run_factory([2, 2])
    reviewer = fake_reviewer([[False, True], [True, True]])
    reviser = fake_reviser()
    result = run_design(
        tmp_path, make_inventory(tmp_path), run, renderer=fake_renderer, reviewer=reviewer, reviser=reviser
    )
    first, second = run.calls
    [call] = reviser.calls
    assert call["old"].sections[0].name == "tower"
    assert [v.passed for v in call["review"].verdicts] == [False, True]
    assert call["image"] == tmp_path / "rec" / first / "out" / "build.png"
    assert call["expansion"] is None and call["model"] == "claude-opus-5-5"

    retry = record_prompt(tmp_path, second)
    assert '"name": "tower v2"' in retry and '"height": 2.333' in retry
    assert "The plan above is a NEW plan" in retry and "build.py starts empty" in retry
    assert "- looks like a tower: looks like a tower is not met" in retry
    assert "Keep following the plan" not in retry and "already holds your earlier build" not in retry
    assert meta(tmp_path, second)["forked_from"] is None  # empty build, not the old shape
    # the second review judged the new plan
    assert reviewer.calls[1]["image"].parent.parent.name == second
    assert result.review_passed is True


def test_plans_json_keeps_every_version(tmp_path):
    run = fake_run_factory([2, 2])
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        renderer=fake_renderer,
        reviewer=fake_reviewer([[False, True], [True, True]]),
        reviser=fake_reviser(cost=0.04),
    )
    first, second = run.calls
    history = plans_in(tmp_path, result.record_id)
    assert [(p["version"], p["reason"], p["record_id"]) for p in history] == [
        (1, "initial", first),
        (2, "revised after review round 1", second),
    ]
    assert history[0]["plan"] == TOWER_PLAN
    assert history[1]["plan"]["sections"][0]["name"] == "tower v2"
    assert history[1]["cost_usd"] == 0.04
    # each record's plan.json is its own version
    assert json.loads((tmp_path / "rec" / first / dz.PLAN_FILE).read_text()) == TOWER_PLAN
    assert json.loads((tmp_path / "rec" / second / dz.PLAN_FILE).read_text())["sections"][0]["name"] == "tower v2"


def test_gate_retry_after_revision_continues_from_the_previous_build(tmp_path):
    run = fake_run_factory([2, 3, 2])  # the rebuild of the new plan fails the gate once
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        renderer=fake_renderer,
        reviewer=fake_reviewer([[False, True], [True, True]]),
    )
    first, rebuild, retry = run.calls
    assert meta(tmp_path, rebuild)["forked_from"] is None
    assert meta(tmp_path, retry)["forked_from"] == rebuild  # same plan version: continue
    retry_prompt = record_prompt(tmp_path, retry)
    assert '"name": "tower v2"' in retry_prompt and "Keep following the plan" in retry_prompt
    assert "used 3, owned 2" in retry_prompt
    assert result.ok and result.review_passed is True and result.record_id == retry
    assert [p["record_id"] for p in plans_in(tmp_path, retry)] == [first, rebuild]


def test_revision_failure_keeps_the_build_and_warns(tmp_path, capsys):
    def broken(old_plan, review, image_path, expansion, inventory, *, model):
        raise PlanError("No usable plan after 2 attempts.", 0.03, 2)

    run = fake_run_factory([2])
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        planner=fake_planner(cost=0.05),
        renderer=fake_renderer,
        reviewer=fake_reviewer([[False, True]]),
        reviser=broken,
    )
    assert result.ok and result.review_passed is False and result.record_id == run.calls[0]
    assert result.plan_cost_usd == pytest.approx(0.08)
    assert "plan revision failed" in capsys.readouterr().err
    assert dz.exit_code(result) == 3


def test_kept_build_still_gets_the_full_plan_history(tmp_path):
    run = fake_run_factory([2, 3, 3, 3])  # the new plan's rebuild never passes the gate
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        renderer=fake_renderer,
        reviewer=fake_reviewer([[False, True]]),
        max_retries=2,
    )
    assert result.record_id == run.calls[0]
    assert [p["version"] for p in plans_in(tmp_path, result.record_id)] == [1, 2]


# --- --from-record ----------------------------------------------------------------


def boom(*args, **kwargs):
    raise AssertionError("must not be called with --from-record")


def source_record(tmp_path):
    """A finished run with an expansion, to reuse."""
    return expand_design(tmp_path, ask=fake_ask("", "")).record_id


def reuse(tmp_path, record_id, request="a tower", **kwargs):
    kwargs.setdefault("asker", boom)
    kwargs.setdefault("expander", boom)
    kwargs.setdefault("planner", boom)
    kwargs.setdefault("renderer", no_render)
    kwargs.setdefault("reviser", fake_reviser())
    kwargs.setdefault("use_expand", True)
    run = kwargs.pop("run", None) or fake_run_factory([2, 2, 2])
    result = dz.design(
        request, make_inventory(tmp_path), records_dir=tmp_path / "rec", run=run, from_record=record_id, **kwargs
    )
    return result, run


def test_from_record_skips_expand_and_plan(tmp_path):
    src = source_record(tmp_path)
    reviewer = fake_reviewer([[True, True]])
    result, run = reuse(tmp_path, src, renderer=fake_renderer, reviewer=reviewer)
    [new] = run.calls
    assert new != src
    prompt = record_prompt(tmp_path, new)
    assert prompt.startswith("a tower") and '"name": "tower"' in prompt
    assert result.expand_cost_usd == 0.0 and result.plan_cost_usd == 0.0
    assert result.reused_from == f"plan v1 and expansion from {src}"
    history = plans_in(tmp_path, new)
    assert history[0]["reason"] == f"from record {src} (version 1)" and history[0]["cost_usd"] == 0.0
    assert history[0]["plan"] == TOWER_PLAN
    saved = json.loads((tmp_path / "rec" / new / dz.EXPANSION_FILE).read_text())
    assert saved["from_record"] == src and saved["features"] == ["white top"]
    assert reviewer.calls[0]["expansion"] == TOWER_EXPANSION.as_text()
    assert [u["model"] for u in result.usage] == ["claude-haiku-5-5", "claude-opus-5-5"]  # build, review


def test_from_record_uses_the_latest_plan_version(tmp_path):
    src_run = fake_run_factory([2, 2])
    src = run_design(
        tmp_path,
        make_inventory(tmp_path),
        src_run,
        renderer=fake_renderer,
        reviewer=fake_reviewer([[False, True], [True, True]]),
    ).record_id
    result, run = reuse(tmp_path, src)
    prompt = record_prompt(tmp_path, run.calls[0])
    assert '"name": "tower v2"' in prompt and '"height": 2.333' in prompt
    assert result.reused_from == f"plan v2 from {src}"  # that run had no expansion
    assert plans_in(tmp_path, run.calls[0])[0]["reason"] == f"from record {src} (version 2)"


def test_from_record_takes_the_saved_request(tmp_path, capsys):
    src = source_record(tmp_path)
    _, run = reuse(tmp_path, src, request="")
    assert record_prompt(tmp_path, run.calls[0]).startswith("a tower\n\n")
    _, run = reuse(tmp_path, src, request="a castle")
    assert "differs from" in capsys.readouterr().err


def test_from_record_with_no_expand_drops_the_expansion(tmp_path):
    src = source_record(tmp_path)
    reviewer = fake_reviewer([[True, True]])
    result, run = reuse(tmp_path, src, use_expand=False, renderer=fake_renderer, reviewer=reviewer)
    assert reviewer.calls[0]["expansion"] is None
    assert not (tmp_path / "rec" / run.calls[0] / dz.EXPANSION_FILE).exists()
    assert result.reused_from == f"plan v1 from {src}"


def test_from_record_still_replans_after_a_failed_review(tmp_path):
    src = source_record(tmp_path)
    reviser = fake_reviser(cost=0.04)
    result, run = reuse(
        tmp_path, src, renderer=fake_renderer, reviewer=fake_reviewer([[False, True], [True, True]]), reviser=reviser
    )
    assert len(reviser.calls) == 1 and result.review_passed is True
    assert result.plan_cost_usd == pytest.approx(0.04)
    assert [p["version"] for p in plans_in(tmp_path, result.record_id)] == [1, 2]


def write_record(tmp_path, record_id, files):
    folder = tmp_path / "rec" / record_id
    folder.mkdir(parents=True)
    for name, content in files.items():
        (folder / name).write_text(content if isinstance(content, str) else json.dumps(content))
    return folder


def version(plan):
    return [{"version": 1, "reason": "initial", "record_id": "x", "cost_usd": 0.0, "plan": plan}]


@pytest.mark.parametrize(
    "files, message",
    [
        (None, "not found in"),
        ({"plan.json": TOWER_PLAN}, "made before z and height moved from plates to bricks"),
        ({"plans.json": "{not json"}, "plans.json could not be read"),
        ({"plans.json": []}, "has no plan versions"),
        ({"plans.json": version({"checklist": ["is it red?"]})}, "plan version 1 is not a valid plan"),
        (
            {"plans.json": version({**TOWER_PLAN, "parts": [{"part": "3001", "color": "RED", "count": 9}]})},
            "uses parts not in your current inventory",
        ),
        (
            {"plans.json": version(TOWER_PLAN), "expansion.json": {"request": "a tower"}},
            "expansion.json is not a valid expansion",
        ),
    ],
)
def test_broken_record_gives_a_clear_error_before_any_call(tmp_path, files, message):
    if files is not None:
        write_record(tmp_path, "old", files)
    with pytest.raises(dz.RecordError, match=message):
        reuse(tmp_path, "old")  # asker, expander and planner would raise AssertionError


def test_main_reports_a_bad_record(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("BRICKWRIGHT_RECORDS_DIR", str(tmp_path / "rec"))
    inv = make_inventory(tmp_path)
    assert dz.main(["--from-record", "nope", "--inventory", str(inv)]) == 1
    err = capsys.readouterr().err
    assert "Error: cannot reuse record nope:" in err and "not found" in err


def test_main_needs_a_request_or_a_record(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        dz.main(["--inventory", str(make_inventory(tmp_path))])


# --- Usage per model -----------------------------------------------------------


def token_run(sizes, cost=0.1, tokens=(50_000, 2_000), known=True):
    calls = []

    def run(record, config):
        record.write_build(BUILD_SRC.format(n=sizes[len(calls)]))
        calls.append(record.id)
        usage = SimpleNamespace(
            cost_usd=cost if known else None,
            known_cost_usd=cost,
            prompt_tokens=tokens[0],
            completion_tokens=tokens[1],
        )
        return SimpleNamespace(usage=usage)

    run.calls = calls
    return run


def test_usage_per_model_adds_up_to_the_total(tmp_path):
    def asker(request, inventory, *, model):
        return QuestionsResult([COLOR_QUESTION], 0.02, 1, 1_000, 200)

    def expander(request, answers, inventory, *, model):
        return ExpansionResult(TOWER_EXPANSION, 0.04, 1, 2_000, 400)

    def planner(request, inventory, *, model, expansion=None):
        return PlanResult(Plan.model_validate(TOWER_PLAN), 0.05, 1, 3_000, 600)

    def reviser(old_plan, review, image_path, expansion, inventory, *, model):
        return PlanResult(Plan.model_validate(REVISED_PLAN), 0.03, 1, 1_500, 300)

    reviews = iter([[False, True], [True, True]])

    def reviewer(plan, expansion, image_path, *, model):
        passed = next(reviews)
        verdicts = [Verdict(index=n, item=i, passed=p, reason="r") for n, (i, p) in enumerate(zip(plan.checklist, passed), 1)]
        return ReviewResult(Review(verdicts=verdicts), 0.01, 1, 800, 100)

    run = token_run([2, 2])
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        use_expand=True,
        ask=fake_ask(""),
        asker=asker,
        expander=expander,
        planner=planner,
        reviser=reviser,
        reviewer=reviewer,
        renderer=fake_renderer,
        review_model="claude-sonnet-5-5",
    )
    rows = {u["model"]: u for u in result.usage}
    assert list(rows) == ["claude-opus-5-5", "claude-haiku-5-5", "claude-sonnet-5-5"]
    opus = rows["claude-opus-5-5"]
    assert opus["input_tokens"] == 1_000 + 2_000 + 3_000 + 1_500
    assert opus["output_tokens"] == 200 + 400 + 600 + 300
    assert opus["cost_usd"] == pytest.approx(0.02 + 0.04 + 0.05 + 0.03)
    assert opus["steps"] == ["questions", "expand", "plan", "revise"]
    haiku = rows["claude-haiku-5-5"]
    assert (haiku["input_tokens"], haiku["output_tokens"]) == (100_000, 4_000)
    assert haiku["cost_usd"] == pytest.approx(0.2) and haiku["steps"] == ["build"]
    assert rows["claude-sonnet-5-5"]["cost_usd"] == pytest.approx(0.02)
    assert sum(u["cost_usd"] for u in result.usage) == pytest.approx(result.cost_usd)
    assert result.cost_usd == pytest.approx(0.14 + 0.2 + 0.02)
    assert dz.models_line(result) == (
        "Models: claude-opus-5-5 7,500 in / 1,500 out $0.140 | "
        "claude-haiku-5-5 100,000 in / 4,000 out $0.200 | "
        "claude-sonnet-5-5 1,600 in / 200 out $0.020"
    )
    saved = json.loads((tmp_path / "rec" / result.record_id / dz.USAGE_FILE).read_text())
    assert [m["model"] for m in saved["models"]] == list(rows)
    assert saved["total_cost_usd"] == pytest.approx(result.cost_usd)
    assert saved["reused_from"] is None


def test_unknown_builder_cost_counts_the_known_part(tmp_path, capsys):
    run = token_run([2], cost=0.02, known=False)
    result = run_design(tmp_path, make_inventory(tmp_path), run)
    assert result.build_cost_usd == pytest.approx(0.02)
    assert "cost is a lower bound" in capsys.readouterr().err


def test_failed_step_usage_is_counted(tmp_path):
    def failing(request, inventory, *, model, expansion=None):
        raise PlanError("no plan", 0.07, 2, 5_000, 900)

    with pytest.raises(PlanError) as info:
        expand_design(tmp_path, ask=fake_ask("", ""), planner=failing)
    assert info.value.cost_usd == pytest.approx(0.06 + 0.07)  # expand + the failed plan calls


# --- One definition of front -----------------------------------------------------


def test_all_prompts_share_one_definition_of_front(tmp_path):
    from voxel.plan import ORIENTATION, plan_prompt, revise_prompt
    from voxel.review import review_prompt

    inv = Inventory({("3001", RED.ldraw_id): 5})
    plan = Plan.model_validate(TOWER_PLAN)
    review = Review(verdicts=[Verdict(index=1, item="looks like a tower", passed=False, reason="no")])
    prompts = {
        "plan": plan_prompt("a tower", inv),
        "revise": revise_prompt(plan, review, None, inv),
        "build": dz.build_prompt("a tower", inv, plan, spare_set(inv, plan)),
        "build without plan": dz.build_prompt("a tower", inv),
        "review": review_prompt(plan, None),
    }
    assert ORIENTATION.startswith("Orientation: the front of the model is its +y side")
    assert [name for name, text in prompts.items() if ORIENTATION not in text] == []
    # No prompt may define front any other way.
    assert not any("-y side" in text or "y=0" in text for text in prompts.values())


# --- One budget for the whole run ------------------------------------------------


def budget_run(sizes, cost=0.1):
    """Like fake_run_factory, and records the budget BrickWright got for each round."""
    run = fake_run_factory(sizes, cost)
    inner = run

    def run_with_config(record, config):
        run_with_config.caps.append((config.max_cost, config.subagent_max_cost))
        return inner(record, config)

    run_with_config.calls = run.calls
    run_with_config.caps = []
    return run_with_config


def test_budget_covers_every_step_and_builder_gets_what_is_left(tmp_path):
    run = budget_run([2, 2])
    reviser = fake_reviser(cost=0.05)
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        use_expand=True,
        ask_user=False,
        asker=fake_asker(cost=0.02),
        expander=fake_expander(cost=0.04),
        planner=fake_planner(cost=0.05),
        renderer=fake_renderer,
        reviewer=fake_reviewer([[False, True], [True, True]], cost=0.03),
        reviser=reviser,
    )
    total = 0.02 + 0.04 + 0.05 + 0.1 + 0.03 + 0.05 + 0.1 + 0.03
    assert result.cost_usd == pytest.approx(total)
    assert result.max_cost == dz.DEFAULT_MAX_COST == 1.5 and result.budget_note == ""
    # Round 1 gets 1.5 minus expand and plan; the rebuild also minus build, review and revise.
    assert run.caps == [(pytest.approx(1.39), 1.0), (pytest.approx(1.21), 1.0)]
    assert dz.budget_line(result) == f"Budget: ${total:.3f} of $1.50 used"
    usage = json.loads((tmp_path / "rec" / result.record_id / dz.USAGE_FILE).read_text())
    assert usage["max_cost_usd"] == 1.5 and usage["budget_note"] is None


def test_builder_subagent_cap_shrinks_with_the_budget(tmp_path):
    run = budget_run([2])
    run_design(tmp_path, make_inventory(tmp_path), run, max_cost=0.4)
    assert run.caps == [(pytest.approx(0.35), pytest.approx(0.35))]


def test_budget_out_before_the_plan_stops_without_a_build(tmp_path, monkeypatch, capsys):
    planner, run = fake_planner(), fake_run_factory([2])
    with pytest.raises(dz.BudgetError) as info:
        run_design(tmp_path, make_inventory(tmp_path), run, planner=planner, max_cost=0.05)
    assert "ran out before the plan" in str(info.value) and "No build was made" in str(info.value)
    assert planner.calls == [] and run.calls == []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_design(request, inventory, **kwargs):
        raise dz.BudgetError("the budget ($0.05) ran out before the plan", 0.0)

    monkeypatch.setattr(dz, "design", fake_design)
    assert dz.main(["a tower", "--inventory", str(make_inventory(tmp_path))]) == 4
    assert "ran out before the plan" in capsys.readouterr().err


def test_budget_out_before_a_gate_retry(tmp_path):
    run = fake_run_factory([3, 2])  # the first build fails the gate
    result = run_design(tmp_path, make_inventory(tmp_path), run, max_cost=0.2)
    assert len(run.calls) == 1 and not result.ok
    assert result.budget_note == "ran out before gate retry 1"
    assert dz.exit_code(result) == 4


def test_budget_out_before_the_review_keeps_the_build(tmp_path):
    reviewer = fake_reviewer([[True, True]])
    result = run_design(
        tmp_path, make_inventory(tmp_path), fake_run_factory([2]), max_cost=0.19,
        renderer=fake_renderer, reviewer=reviewer,
    )
    assert reviewer.calls == [] and result.ok and result.review_passed is None
    assert dz.review_line(result) == "Review: skipped (budget ran out)"
    assert dz.budget_line(result) == (
        f"Budget: $0.150 of $0.19 used; ran out before review round 1, kept {result.record_id}"
    )
    assert dz.exit_code(result) == 0


def test_budget_out_before_a_revision_keeps_the_reviewed_build(tmp_path):
    run, reviser = fake_run_factory([2, 2]), fake_reviser()
    result = run_design(
        tmp_path, make_inventory(tmp_path), run, max_cost=0.3,
        renderer=fake_renderer, reviewer=fake_reviewer([[False, True]]), reviser=reviser,
    )
    assert reviser.calls == [] and len(run.calls) == 1  # 0.12 left: not enough for revise + build
    assert result.ok and result.review_passed is False and result.record_id == run.calls[0]
    assert result.budget_note == "ran out before plan revision 1"


def test_budget_estimate_uses_the_largest_cost_seen():
    ledger = dz.UsageLedger()
    budget = dz.Budget(1.0, ledger)
    assert budget.estimate("plan") == dz.STEP_FLOOR["plan"]
    ledger.add("opus", "plan", dz.Usage(cost_usd=0.4))
    ledger.add("haiku", "build", dz.Usage(cost_usd=0.5))
    assert budget.estimate("plan") == 0.4
    assert budget.estimate("build") == dz.STEP_FLOOR["build"]  # BrickWright caps a round itself
    assert budget.remaining == pytest.approx(0.1) and not budget.allows("plan")


def test_max_cost_default_is_one_total(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def fake_design(request, inventory, **kwargs):
        seen.update(kwargs)
        return dz.DesignResult(True, "rec", tmp_path / "build.py", 1, "ok")

    monkeypatch.setattr(dz, "design", fake_design)
    dz.main(["a tower", "--inventory", str(make_inventory(tmp_path))])
    assert seen["max_cost"] == 1.5


# --- API errors: retried, then a clean stop with exit 5 ------------------------

import litellm  # noqa: E402

from voxel import llm  # noqa: E402
from voxel import plan as pl  # noqa: E402
from voxel import review as rv  # noqa: E402


def api_timeout():
    return litellm.Timeout(message="Request timed out", model="m", llm_provider="anthropic")


def server_error():
    return litellm.InternalServerError(message="overloaded", model="m", llm_provider="anthropic")


def credit_error():
    return litellm.BadRequestError(
        message="Your credit balance is too low to access the Anthropic API.", model="m", llm_provider="anthropic"
    )


def scripted(*outcomes):
    """A fake completion: each call raises or returns the next outcome."""
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    complete.calls = calls
    return complete


@pytest.fixture
def no_waits(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "sleep", seen.append)
    return seen


@pytest.fixture
def fake_seam(monkeypatch):
    """BrickWright's completion seam, faked: set .complete to the fake completion."""
    from brickwright.driver import loop

    seam = SimpleNamespace(complete=None)
    factory = lambda: seam.complete  # noqa: E731
    monkeypatch.setattr(loop, "litellm_complete", factory)
    seam.factory = factory
    return seam


def seam_run(seam, outcomes_per_round, cost=0.1):
    """A stand-in builder that makes one LLM call per round through BrickWright's
    seam (the fake completion follows that round's script), then writes a 2-brick tower."""
    from brickwright.driver import loop

    calls, configs = [], []

    def run(record, config):
        configs.append(config)
        seam.complete.script = scripted(*outcomes_per_round[len(calls)])
        complete = loop.litellm_complete()  # what BrickWright's loop does each run
        calls.append(record.id)
        complete(model=config.model, timeout=config.request_timeout)
        record.write_build(BUILD_SRC.format(n=2))
        return SimpleNamespace(usage=SimpleNamespace(cost_usd=cost))

    run.calls, run.configs = calls, configs
    return run


@pytest.fixture
def builder_api(fake_seam):
    """fake_seam whose completion delegates to the script the current round set."""

    def complete(**kwargs):
        return complete.script(**kwargs)

    fake_seam.complete = complete
    return fake_seam


def test_builder_calls_are_retried_and_the_seam_put_back(builder_api, no_waits, tmp_path):
    from brickwright.driver import loop

    run = seam_run(builder_api, [[server_error(), "reply"]])
    result = run_design(tmp_path, make_inventory(tmp_path), run)
    assert result.ok and not result.stopped and dz.exit_code(result) == 0
    assert no_waits == [5.0]
    assert run.configs[0].request_timeout == dz.BUILD_CALL_TIMEOUT == 600
    assert loop.litellm_complete is builder_api.factory  # unwrapped after the run


def test_builder_failing_for_good_keeps_the_earlier_build(builder_api, no_waits, tmp_path):
    # Round 1 passes the gate, the review fails, the plan is revised, and the
    # rebuild's call hits a credit error: round 1's build is kept, exit 5.
    run = seam_run(builder_api, [["reply"], [credit_error()]])
    result = run_design(
        tmp_path, make_inventory(tmp_path), run,
        renderer=fake_renderer, reviewer=fake_reviewer([[False, True]]),
    )
    assert len(run.calls) == 2 and result.ok and result.record_id == run.calls[0]
    assert result.stopped == "the API says the credit balance is too low (builder call), in build round 2"
    assert dz.stopped_line(result) == f"Stopped: {result.stopped}; kept {run.calls[0]}."
    assert dz.exit_code(result) == 5 and no_waits == []
    usage = json.loads((tmp_path / "rec" / result.record_id / dz.USAGE_FILE).read_text())
    assert usage["stopped"] == result.stopped


def test_builder_failing_in_the_first_round_keeps_nothing(builder_api, no_waits, tmp_path):
    run = seam_run(builder_api, [[server_error()] * 3])
    result = run_design(tmp_path, make_inventory(tmp_path), run)
    assert not result.ok and dz.exit_code(result) == 5
    assert result.stopped.startswith("the builder call failed 3 times: ")
    assert result.stopped.endswith(", in build round 1")
    assert dz.stopped_line(result).endswith("; no build passed the gate.")
    assert no_waits == [5.0, 15.0]


def test_a_builder_timeout_is_not_retried(builder_api, no_waits, tmp_path):
    run = seam_run(builder_api, [[api_timeout(), "reply"]])
    result = run_design(tmp_path, make_inventory(tmp_path), run)
    assert not result.ok and dz.exit_code(result) == 5
    assert result.stopped == "the builder call timed out after 600 s (timeouts are not retried), in build round 1"
    assert no_waits == []


def run_main(tmp_path, monkeypatch, capsys, **design_kwargs):
    """main() with design() running on fakes (in tmp_path), no real API call."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    real_design = dz.design

    def fake_design(request, inventory, **kwargs):
        kwargs.update(design_kwargs)
        kwargs.setdefault("planner", fake_planner())
        kwargs["use_expand"] = False
        return real_design(request, inventory, records_dir=tmp_path / "rec", **kwargs)

    monkeypatch.setattr(dz, "design", fake_design)
    code = dz.main(["a tower", "--inventory", str(make_inventory(tmp_path))])
    return code, capsys.readouterr().out


def test_three_review_timeouts_stop_cleanly_with_the_summary(tmp_path, monkeypatch, capsys, no_waits):
    api = scripted(api_timeout(), api_timeout(), api_timeout())

    def reviewer(plan, expansion, image, *, model):
        return rv.review(plan, expansion, image, model=model, complete=api)

    run = fake_run_factory([2])
    code, out = run_main(tmp_path, monkeypatch, capsys, run=run, renderer=fake_renderer, reviewer=reviewer)
    assert code == 5 and len(api.calls) == 3 and no_waits == [5.0, 15.0]
    lines = out.strip().splitlines()
    assert lines[0] == (
        "Stopped: the review call timed out 3 times (180 s each), in review round 1; "
        f"kept {run.calls[0]}."
    )
    assert "PASSED the gate" in out and "Review: skipped (the review call failed)" in out
    assert "Cost: $0.150 (expand $0.000 + plan $0.050 + build $0.100 + expert $0.000 + review $0.000)" in out
    assert any(line.startswith("Models: ") for line in lines)
    assert "Budget: $0.150 of $1.50 used" in out and "Build file:" in out
    assert "Traceback" not in out


def test_a_credit_error_in_the_plan_stops_cleanly(tmp_path, monkeypatch, capsys, no_waits):
    api = scripted(credit_error())

    def planner(request, inventory, *, model, expansion=None):
        return pl.make_plan(request, inventory, model=model, expansion=expansion, complete=api)

    run = fake_run_factory([2])
    code, out = run_main(tmp_path, monkeypatch, capsys, run=run, planner=planner)
    assert code == 5 and len(api.calls) == 1 and no_waits == [] and run.calls == []
    lines = out.strip().splitlines()
    assert lines[0] == "Stopped: the API says the credit balance is too low (plan call); no build passed the gate."
    assert "the gate" not in "\n".join(lines[1:])
    assert "Cost: $0.000" in out and "Models: claude-opus-5-5 0 in / 0 out $0.000" in out and "Budget: $0.000 of $1.50 used" in out
    assert "Build file:" not in out


# --- The builder's reasoning effort ------------------------------------------------


def effort_run(seen):
    """A stand-in agent that notes the config it was given."""
    inner = fake_run_factory([2, 2, 2])

    def run(record, config):
        seen.append(config)
        return inner(record, config)

    return run


def test_builder_effort_is_medium_by_default_and_shown_in_the_summary(tmp_path, capsys):
    seen = []
    result = run_design(tmp_path, make_inventory(tmp_path), effort_run(seen))
    assert [c.effort.value for c in seen] == ["medium"]
    assert (result.build_model, result.build_effort) == ("claude-haiku-5-5", "medium")
    dz.print_summary(result)
    assert "Builder: claude-haiku-5-5, effort medium" in capsys.readouterr().out.splitlines()
    usage = json.loads((tmp_path / "rec" / result.record_id / dz.USAGE_FILE).read_text())
    assert usage["build_effort"] == "medium" and usage["build_model"] == "claude-haiku-5-5"


def test_build_effort_setting_changes_only_the_builder(tmp_path, capsys):
    seen = []
    result = run_design(tmp_path, make_inventory(tmp_path), effort_run(seen), build_effort="high")
    assert [c.effort.value for c in seen] == ["high"] and result.build_effort == "high"
    dz.print_summary(result)
    assert "Builder: claude-haiku-5-5, effort high" in capsys.readouterr().out
    # The plan, description, questions and review calls take no effort from it.
    from voxel import expand, llm, plan, review

    for module in (expand, llm, plan, review):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "reasoning_effort" not in source and "build_effort" not in source
    with pytest.raises(ValueError, match="build_effort must be one of"):
        run_design(tmp_path, make_inventory(tmp_path), effort_run([]), build_effort="extreme")


def test_build_effort_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    got = {}

    def fake_design(request, inventory, **kwargs):
        got.update(kwargs)
        return dz.DesignResult(True, "rec", None, 1, "", build_model="claude-haiku-5-5", build_effort=kwargs["build_effort"])

    monkeypatch.setattr(dz, "design", fake_design)
    inv = str(make_inventory(tmp_path))
    dz.main(["a tower", "--inventory", inv])
    assert got["build_effort"] == "medium"
    dz.main(["a tower", "--inventory", inv, "--build-effort", "high"])
    assert got["build_effort"] == "high"
    with pytest.raises(SystemExit):
        dz.main(["a tower", "--inventory", inv, "--build-effort", "extreme"])


# --- No plan (--no-plan): the build model designs and builds alone ------------------


def no_plan_design(tmp_path, run, **kwargs):
    planner, reviser = fake_planner(), fake_reviser()
    kwargs.setdefault("asker", fake_asker())
    kwargs.setdefault("expander", fake_expander())
    result = run_design(
        tmp_path,
        make_inventory(tmp_path),
        run,
        planner=planner,
        reviser=reviser,
        use_expand=True,
        ask_user=False,
        use_plan=False,
        **kwargs,
    )
    return result, planner, reviser


def test_no_plan_skips_the_planner_and_writes_no_plan_files(tmp_path):
    seen = []
    run = effort_run(seen)
    result, planner, reviser = no_plan_design(tmp_path, run)
    assert result.ok and planner.calls == [] and reviser.calls == []
    assert result.plan_cost_usd == 0
    folder = tmp_path / "rec" / result.record_id
    assert not (folder / dz.PLAN_FILE).exists() and not (folder / dz.PLANS_FILE).exists()
    assert (folder / dz.EXPANSION_FILE).exists()
    assert json.loads((folder / dz.USAGE_FILE).read_text())["plan"] is False


def test_no_plan_builder_prompt_is_request_answers_and_parts(tmp_path):
    prompts = []
    inner = fake_run_factory([2])

    def run(record, config):
        prompts.append(record.read_meta().prompt)
        return inner(record, config)

    expander = fake_expander()
    no_plan_design(tmp_path, run, expander=expander)
    assert expander.calls == []  # no description call
    [prompt] = prompts
    assert prompt.startswith("a tower\nAnswers: color: RED; height: 6 bricks\n\nOrientation:")
    assert "3001 | Brick 2 x 4 | Red | 2" in prompt  # every owned part, full count
    assert prompt.endswith(dz.CHECK_OWNED_RULE)
    for gone in (
        TOWER_EXPANSION.description,
        "white top",
        "looks like",
        "Choose the overall size",
        "Plate-cells you may plan with",
        "Units:",
        "There is no plan",
        "PLAN:",
        "Spare parts",
        "ask_expert",
    ):
        assert gone not in prompt


def test_no_plan_saves_the_answers_without_a_description(tmp_path):
    result, _, _ = no_plan_design(tmp_path, fake_run_factory([2]))
    data = json.loads((tmp_path / "rec" / result.record_id / dz.EXPANSION_FILE).read_text())
    assert [a["answer"] for a in data["answers"]] == ["RED", "6 bricks"]
    assert "description" not in data and "features" not in data
    assert result.expand_cost_usd == pytest.approx(0.02)  # the questions call only


def test_no_plan_without_expand_builds_from_the_request_alone(tmp_path):
    prompts = []
    inner = fake_run_factory([2])

    def run(record, config):
        prompts.append(record.read_meta().prompt)
        return inner(record, config)

    asker = fake_asker()
    result = run_design(tmp_path, make_inventory(tmp_path), run, use_plan=False, use_expand=False, asker=asker)
    assert result.ok and asker.calls == []
    assert prompts[0].startswith("a tower\n\nOrientation:") and "Answers:" not in prompts[0]
    assert not (tmp_path / "rec" / result.record_id / dz.EXPANSION_FILE).exists()


def test_no_plan_turns_the_expert_off(tmp_path, monkeypatch):
    from brickwright.tools.expert import EXPERT_CONTEXT_ENV

    no_plan_design(tmp_path, fake_run_factory([2]))
    assert EXPERT_CONTEXT_ENV not in dz.os.environ


FIXED_ITEMS = [
    'looks like what was asked for ("a tower") to someone who only sees the picture',
    "colors make sense: each part one clear color, no random stripes",
    "proportions are believable",
    "clean shape: outline reads clearly, no stray or sticking-out pieces",
    "main features are visible in the front or top view",
]


def test_no_plan_reviews_against_the_fixed_checklist_once_and_keeps_the_build(tmp_path):
    # A run that never calls the review hook (no render inside it): the review after it decides.
    seen_plans = []
    reviewer = fake_reviewer([[True, False, True, True, False]])

    def recording_reviewer(plan, expansion, image_path, *, model):
        seen_plans.append(plan)
        return reviewer(plan, expansion, image_path, model=model)

    run = fake_run_factory([2, 2])
    result, _, reviser = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=recording_reviewer)
    [checklist] = seen_plans
    assert checklist.checklist == FIXED_ITEMS
    assert reviewer.calls[0]["expansion"] == "a tower\nAnswers: color: RED; height: 6 bricks"
    assert result.ok and result.review_passed is False and result.review_rounds == 1
    assert reviser.calls == [] and len(run.calls) == 1  # no re-plan, no rebuild
    [entry] = reviews_in(tmp_path, result.record_id)
    assert entry["passed"] is False and entry["failed_items"] == [2, 5]
    assert entry["rule"] == "items 1 and 4 must pass, at most 1 other(s) may fail" and entry["stage"] == "after run"
    assert dz.review_line(result).startswith(
        "Review: FAILED after 1 round(s) (3 of 5 items; rule: items 1 and 4 must pass, at most 1 other(s) may fail):\n"
    )
    assert dz.exit_code(result) == 3


def test_no_plan_review_passes_with_one_quality_item_failed(tmp_path):
    reviewer = fake_reviewer([[True, True, False, True, True]])
    result, _, _ = no_plan_design(tmp_path, fake_run_factory([2]), renderer=fake_renderer, reviewer=reviewer)
    assert result.review_passed is True and dz.exit_code(result) == 0
    line = dz.review_line(result)
    assert line.startswith("Review: passed after 1 round(s) (4 of 5 items;")
    assert "proportions are believable" in line  # the failed item is still shown
    [entry] = reviews_in(tmp_path, result.record_id)
    assert entry["passed"] is True and entry["failed_items"] == [3]


def test_no_plan_review_fails_when_it_does_not_look_like_the_request(tmp_path):
    reviewer = fake_reviewer([[False, True, True, True, True]])
    result, _, _ = no_plan_design(tmp_path, fake_run_factory([2]), renderer=fake_renderer, reviewer=reviewer)
    assert result.review_passed is False


def test_review_prompt_without_a_plan():
    from voxel.review import fixed_checklist, review_prompt

    prompt = review_prompt(fixed_checklist("a tower"), "a tower\nAnswers: color: RED")
    assert "Checklist:\n" + "\n".join(f"{n}. {i}" for n, i in enumerate(FIXED_ITEMS, start=1)) in prompt
    assert "What was asked for:\na tower\nAnswers: color: RED" in prompt
    assert "Detailed description" not in prompt and "The plan the builder followed" not in prompt
    with_plan = review_prompt(Plan.model_validate(TOWER_PLAN), "A tall tower.")
    assert "Detailed description of what was asked for:\nA tall tower." in with_plan  # pipeline unchanged
    assert "The plan the builder followed" in with_plan


def test_pipeline_review_still_needs_every_item(tmp_path):
    run = fake_run_factory([2, 2, 2])
    reviewer = fake_reviewer([[True, False], [True, False]])
    result = run_design(tmp_path, make_inventory(tmp_path), run, renderer=fake_renderer, reviewer=reviewer)
    assert result.review_passed is False
    assert "failed_items" not in reviews_in(tmp_path, result.record_id)[0]
    assert "rule:" not in dz.review_line(result)


def test_no_plan_cannot_reuse_a_record(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="no plan cannot reuse"):
        run_design(tmp_path, make_inventory(tmp_path), fake_run_factory([2]), use_plan=False, from_record="x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    inv = str(make_inventory(tmp_path))
    with pytest.raises(SystemExit):
        dz.main(["a tower", "--inventory", inv, "--no-plan", "--from-record", "x"])


def test_no_plan_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    got = {}

    def fake_design(request, inventory, **kwargs):
        got.update(kwargs)
        return dz.DesignResult(True, "rec", None, 1, "")

    monkeypatch.setattr(dz, "design", fake_design)
    inv = str(make_inventory(tmp_path))
    dz.main(["a tower", "--inventory", inv])
    assert got["use_plan"] is True
    dz.main(["a tower", "--inventory", inv, "--no-plan"])
    assert got["use_plan"] is False


# --- The repair round (--no-plan): review inside the run, one repair ------------------


def repairing_run(after=2, *, before=2, done_after=True, repair_cost=0.2, build_cost=0.1):
    """A stand-in agent that, like BrickWright with the review hook on, builds,
    renders, calls the hook, and on a failed review edits build.py once more."""
    from brickwright.driver.review_hook import review_hook, review_hook_enabled

    calls = []

    def run(record, config):
        call = {"record": record.id, "config": config, "feedback": None}
        calls.append(call)
        record.write_build(BUILD_SRC.format(n=before))
        cost, turns = build_cost, 4
        image = Path(record.out_dir) / "build.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"fake png")
        done = True
        Path(record.messages_path).write_text('{"role": "assistant"}\n' * turns)  # the turns so far
        if config.max_critique_rounds and review_hook_enabled():
            outcome = review_hook()(record, image, config.max_cost - cost)
            cost += outcome.usage.cost_usd if outcome.usage else 0.0
            if not outcome.done:
                call["feedback"] = outcome.feedback
                record.write_build(BUILD_SRC.format(n=after))
                cost += repair_cost
                turns += 3
                done = done_after
        return SimpleNamespace(usage=SimpleNamespace(cost_usd=cost), done=done, turns=turns)

    run.calls = calls
    return run


FAIL_SHAPE = [True, True, True, False, True]  # item 4 (clean shape) fails: must pass
ALL_PASS = [True] * 5


def test_failed_review_gets_one_repair_in_the_same_run(tmp_path):
    run = repairing_run(after=1)
    reviewer = fake_reviewer([FAIL_SHAPE, ALL_PASS])
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=3.0)

    [call] = run.calls  # one builder run: the repair is inside it, no new record
    assert call["config"].max_critique_rounds == 1
    assert call["feedback"].startswith("A reviewer judged your build's render (attached).")
    assert "- clean shape: outline reads clearly, no stray or sticking-out pieces: " in call["feedback"]
    assert "Edit the existing build.py to fix only these" in call["feedback"]
    assert len(reviewer.calls) == 2  # before and after the repair
    assert result.ok and result.review_passed is True and result.review_rounds == 2

    before, after = reviews_in(tmp_path, result.record_id)
    assert (before["stage"], before["passed"], before["failed_items"]) == ("before repair", False, [4])
    assert (after["stage"], after["passed"], after["failed_items"]) == ("after repair", True, [])
    assert before["image"].endswith("review.before_repair.png") and Path(before["image"]).exists()
    folder = tmp_path / "rec" / result.record_id
    assert (folder / "out" / "build.before_repair.py").read_text() == BUILD_SRC.format(n=2)
    assert (folder / "build.py").read_text() == BUILD_SRC.format(n=1)  # the repaired build is kept

    # Costs: both reviews on the review rows, the rest of the run as build.
    assert result.review_cost_usd == pytest.approx(0.06)
    assert result.build_cost_usd == pytest.approx(0.3)
    repair = json.loads((folder / dz.USAGE_FILE).read_text())["repair"]
    assert repair["ran"] and repair["kept"] and repair["turns"] == 3
    assert repair["cost_usd"] == pytest.approx(0.2)
    assert result.repair == "Repair: 1 round, 3 turn(s), $0.200; review 4 of 5 -> 5 of 5 items"


def test_passed_review_inside_the_run_is_not_repeated(tmp_path):
    run = repairing_run()
    reviewer = fake_reviewer([ALL_PASS])
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=3.0)
    assert run.calls[0]["feedback"] is None
    assert len(reviewer.calls) == 1  # the in-run review is reused for the same build.py
    [entry] = reviews_in(tmp_path, result.record_id)
    assert entry["stage"] == "in run" and entry["passed"] is True
    assert result.review_passed is True and result.repair == "Repair: not needed"
    assert result.review_cost_usd == pytest.approx(0.03)


def test_there_is_only_one_repair(tmp_path):
    run = repairing_run(after=1)
    reviewer = fake_reviewer([FAIL_SHAPE, FAIL_SHAPE])
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=3.0)
    assert len(run.calls) == 1 and len(reviewer.calls) == 2
    assert result.review_passed is False and dz.exit_code(result) == 3
    assert [r["stage"] for r in reviews_in(tmp_path, result.record_id)] == ["before repair", "after repair"]


def test_a_repair_that_fails_the_gate_is_undone(tmp_path):
    run = repairing_run(after=3)  # 3 bricks, 2 owned: the gate fails
    reviewer = fake_reviewer([FAIL_SHAPE])
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=3.0)
    folder = tmp_path / "rec" / result.record_id
    assert result.ok and len(run.calls) == 1  # no gate retry: the old build is put back
    assert (folder / "build.py").read_text() == BUILD_SRC.format(n=2)
    assert len(reviewer.calls) == 1 and result.review_passed is False  # review 1 stands
    assert result.repair_note == "the repair failed the gate; kept the build from before it"
    assert json.loads((folder / dz.USAGE_FILE).read_text())["repair"]["kept"] is False
    assert "undone" in result.repair


def test_a_repair_that_does_not_finalize_is_undone(tmp_path):
    run = repairing_run(after=1, done_after=False)
    reviewer = fake_reviewer([FAIL_SHAPE])
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=3.0)
    assert (tmp_path / "rec" / result.record_id / "build.py").read_text() == BUILD_SRC.format(n=2)
    assert result.repair_note == "the repair did not finalize; kept the build from before it"


def test_no_repair_without_the_reserve(tmp_path):
    from voxel.repair import REPAIR_RESERVE

    run = repairing_run(after=1)
    reviewer = fake_reviewer([FAIL_SHAPE])
    # Questions $0.02, build $0.10, review $0.03: less than the reserve is left.
    budget = 0.02 + 0.1 + 0.03 + REPAIR_RESERVE - 0.01
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=budget)
    assert run.calls[0]["feedback"] is None and len(reviewer.calls) == 1
    assert result.review_passed is False and result.repair == "Repair: did not run"
    [entry] = reviews_in(tmp_path, result.record_id)
    assert entry["stage"] == "in run"


def test_no_review_inside_the_run_when_the_gate_fails(tmp_path):
    run = repairing_run(before=3)  # every build fails the gate: nothing to review
    reviewer = fake_reviewer([])
    result, _, _ = no_plan_design(tmp_path, run, renderer=fake_renderer, reviewer=reviewer, max_cost=3.0)
    assert reviewer.calls == [] and not result.ok
    assert len(run.calls) == 3  # the normal gate retries


def test_the_pipeline_has_no_review_hook(tmp_path):
    from brickwright.driver.review_hook import REVIEW_HOOK_ENV

    seen = []
    inner = fake_run_factory([2])

    def run(record, config):
        seen.append((config.max_critique_rounds, dz.os.environ.get(REVIEW_HOOK_ENV)))
        return inner(record, config)

    run_design(tmp_path, make_inventory(tmp_path), run)
    assert seen == [(0, None)]
    assert REVIEW_HOOK_ENV not in dz.os.environ
