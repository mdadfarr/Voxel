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


def run_design(tmp_path, inv, run, **kwargs):
    kwargs.setdefault("planner", fake_planner())
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
    assert retry.startswith(record_prompt(tmp_path, first))  # full prompt with the plan
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
        renderer=fake_renderer,
        reviewer=reviewer,
    )
    assert result.plan_cost_usd == pytest.approx(0.05)
    assert result.build_cost_usd == pytest.approx(0.2)
    assert result.review_cost_usd == pytest.approx(0.06)
    assert result.expand_cost_usd == 0.0
    assert result.cost_usd == pytest.approx(0.31)
    assert dz.cost_line(result) == (
        "Cost: $0.310 (expand $0.000 + plan $0.050 + build $0.200 + review $0.060)"
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
    assert "inventory and build steps" in retry and "at most 12 parts" in retry
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
        "Cost: $0.210 (expand $0.060 + plan $0.050 + build $0.100 + review $0.000)"
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
