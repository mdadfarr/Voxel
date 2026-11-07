"""Design a build with the BrickWright agent, using only parts you own.

First, one planning call (voxel.plan, Opus by default) turns the request into a
plan: a checklist, the sections of the object, and the owned parts it needs.
The builder (Haiku by default) gets the plan, the planned parts and a spare set
of basic bricks and plates, not the full inventory. The plan is saved as
plan.json in the record folder.

Last, once the build passes the gate, it is rendered and one review call
(voxel.review, Opus) judges it against the plan's checklist. Failed items go
back to the builder through the same retry path as the gate (max 2 reviews).
Without an LDraw library there is no render, and the review is skipped with a
warning. The reviews are saved as review.json in the record folder.

Two layers keep the agent honest (both against the FULL inventory):

1. Soft: the owned parts are listed in the prompt, and the agent's own
   run_tests() includes check_owned(tests), so it sees over-use as a test failure.
2. Hard: check_owned is a required BrickWright check (BRICKWRIGHT_REQUIRED_CHECKS),
   so validate/finalize fail and the agent cannot finalize a build that over-uses
   parts. As a backstop, after the agent finishes Voxel runs the check again
   itself (final_gate). If it fails, the agent is run again from that build with
   the report (up to max_retries times).

Run it:
    python -m voxel.design "a small red tower" --inventory inventory.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from .check import CheckResult, check_build
from .inventory import Inventory, PartKey, color_label, color_name, load_inventory
from .plan import DEFAULT_PLAN_MODEL, Plan, PlanError, PlanResult, make_plan, part_name, spare_set
from .review import DEFAULT_REVIEW_MODEL, Review, ReviewError, review_report
from .review import review as run_review
from .steps import MAX_STEP_PARTS, step_problems

INVENTORY_ENV = "VOXEL_INVENTORY"
REQUIRED_CHECKS_ENV = "BRICKWRIGHT_REQUIRED_CHECKS"  # read by brickwright's validation
OWNED_CHECK = "voxel.design:check_owned"
STEPS_CHECK = "voxel.steps:check_steps"
DEFAULT_MODEL = "claude-haiku-5-5"  # the build model
PLAN_FILE = "plan.json"
REVIEW_FILE = "review.json"
MAX_REVIEW_ROUNDS = 2
GATE_TIMEOUT = 60.0


def load_env(path: str | Path = ".env") -> None:
    """Put KEY=value lines from .env into the environment (never overrides)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


# --- Layer 1: inside the agent ------------------------------------------------


def check_owned(tests, inventory_path: str | Path | None = None) -> None:
    """Call this inside run_tests(). Fails a test when the build uses unowned parts."""
    path = inventory_path or os.environ.get(INVENTORY_ENV)
    if not path:
        tests.check("only_owned_parts", False, f"{INVENTORY_ENV} is not set")
        return
    result = check_build(tests.build, load_inventory(path))
    tests.check("only_owned_parts", result.ok, None if result.ok else result.report())


def require_owned_check() -> None:
    """Make BrickWright run check_owned and check_steps as required checks on every
    validate/finalize."""
    current = [t.strip() for t in os.environ.get(REQUIRED_CHECKS_ENV, "").split(",") if t.strip()]
    missing = [c for c in (OWNED_CHECK, STEPS_CHECK) if c not in current]
    if missing:
        os.environ[REQUIRED_CHECKS_ENV] = ",".join([*current, *missing])


CHECK_OWNED_RULE = (
    "Your run_tests() must include `from voxel import check_owned` and a call "
    "`check_owned(tests)`. Do not remove, weaken or work around it."
)


def steps_rule(with_plan: bool) -> str:
    """How to split the build into steps; Voxel's step check enforces this."""
    rules = [
        f"Call build.add_step() after each group of at most {MAX_STEP_PARTS} parts.",
        "Add parts bottom-up, so every part sits on parts from earlier steps.",
        "Place parts directly in the build. If you use sub-models, each must have at "
        f"most {MAX_STEP_PARTS} parts, because steps can't split a sub-model.",
    ]
    if with_plan:
        rules.append("Follow the plan's sections in order, bottom to top, as the step order.")
    return "BUILD STEPS:\n" + "\n".join(f"- {r}" for r in rules)


def build_prompt(
    request: str,
    inventory: Inventory,
    plan: Plan | None = None,
    spare: dict[PartKey, int] | None = None,
) -> str:
    """The builder's prompt. With a plan, only the planned parts and the spare set are listed."""
    if plan is None:
        lines = []
        for key in sorted(inventory.counts):
            part_id, code = key
            lines.append(f"{part_id} | {part_name(part_id)} | {color_label(code)} | {inventory.counts[key]}")
        return (
            f"{request}\n\n"
            "HARD RULE: use ONLY the parts listed below, in those colors, and never more "
            "than the count given. Do not use any other part or color.\n"
            "Format: part id | name | color | count owned\n"
            + "\n".join(lines)
            + "\n\n"
            + steps_rule(with_plan=False)
            + "\n\n"
            + CHECK_OWNED_RULE
        )

    planned = [
        f"{part_id} | {part_name(part_id)} | {color_name(code)} | {n} | {inventory.owned((part_id, code))}"
        for (part_id, code), n in sorted(plan.part_counts().items())
    ]
    spares = [
        f"{part_id} | {part_name(part_id)} | {color_name(code)} | {n}"
        for (part_id, code), n in sorted((spare or {}).items())
    ]
    return (
        f"{request}\n\n"
        "Follow this plan. Its checklist is how the finished build will be judged; "
        "its sections give each part of the object with size (studs; height in plates), "
        "position and colors.\n"
        f"PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
        "HARD RULE: use ONLY the parts listed below (planned parts and spare parts), "
        "in those colors, and never more than the count owned. Do not use any other "
        "part or color. Colors are bricklayer Color names (RED means bl.Color.RED).\n\n"
        "Planned parts (part id | name | color | planned | owned):\n"
        + "\n".join(planned)
        + "\n\nSpare parts, to fix problems the plan did not foresee "
        "(part id | name | color | owned):\n"
        + ("\n".join(spares) if spares else "(none)")
        + "\n\n"
        + steps_rule(with_plan=True)
        + "\n\n"
        + CHECK_OWNED_RULE
    )


# --- Layer 2: the independent gate --------------------------------------------


def _gate_worker(build_path: str, inventory_path: str):
    """Runs in a child process. Returns (CheckResult, step problems), or an error string."""
    try:
        from brickwright.tools import build_execution

        build = build_execution.create_build(build_path)
        return check_build(build, load_inventory(inventory_path)), step_problems(build)
    except BaseException:
        return traceback.format_exc()[-2000:]


def final_gate(build_path: str | Path, inventory_path: str | Path) -> tuple[bool, str]:
    """Check a finished build.py against the inventory and its steps. Returns (ok, report)."""
    from brickwright.tools import build_execution

    try:
        outcome = build_execution.run_in_subprocess(
            _gate_worker, (str(build_path), str(inventory_path)), GATE_TIMEOUT
        )
    except (TimeoutError, RuntimeError) as err:
        return False, f"Inventory gate could not run the build: {err}"
    if isinstance(outcome, str):
        return False, f"Inventory gate could not build build.py:\n{outcome}"
    inventory_result, steps = outcome
    assert isinstance(inventory_result, CheckResult)
    ok = inventory_result.ok and not steps
    if ok:
        return True, "Inventory and step checks passed."
    parts = [] if inventory_result.ok else [inventory_result.report()]
    if steps:
        parts.append("Step check failed:\n" + "\n".join(f"  - {p}" for p in steps))
    return False, "\n".join(parts)


def feedback_prompt(problem: str, base_prompt: str) -> str:
    """Prompt for a retry round, after the gate or the review failed.

    A retry is a fresh conversation, so it repeats the full build prompt
    (request, plan, planned parts, spare set) first. `problem` says what failed:
    gate_problem(report) or review_problem(review).
    """
    return (
        f"{base_prompt}\n\n"
        "PREVIOUS ATTEMPT: build.py already holds your earlier build, but it "
        f"{problem}\n\n"
        "Fix these problems in build.py. It must still use only the parts and counts "
        "listed above, and every build step (build.add_step()) must stand on its own "
        f"with at most {MAX_STEP_PARTS} parts. Keep check_owned(tests) in run_tests(), "
        "and keep following the plan."
    )


def gate_problem(report: str) -> str:
    return f"failed Voxel's independent checks (inventory and build steps):\n{report}"


def review_problem(result: Review) -> str:
    return "failed the visual review. These checklist items failed:\n" + review_report(result)


# --- Review: render the build and judge it against the checklist ---------------


def render_build(record, config) -> tuple[Path | None, str]:
    """Render the record's build to a PNG. Returns (image, "") or (None, why not)."""
    try:
        from brickwright.tools.rendering import render, render_available

        if not render_available():
            return None, "no LDraw library found (set LDRAW_LIBRARY_PATH in .env)"
        report = render(record, config)
    except Exception as err:
        return None, f"render failed: {err}"
    if not report.ok or report.image_path is None:
        return None, f"render failed: {report.error}"
    return Path(report.image_path), ""


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def save_reviews(record, reviews: list[dict]) -> Path:
    path = Path(record.path) / REVIEW_FILE
    path.write_text(json.dumps(reviews, indent=2) + "\n", encoding="utf-8")
    return path


# --- The outer loop -----------------------------------------------------------


@dataclass(frozen=True)
class DesignResult:
    ok: bool  # the kept build passed the inventory and step gate
    record_id: str
    build_path: Path
    rounds: int  # builder runs, over all gate retries and review rounds
    report: str  # the gate report
    expand_cost_usd: float = 0.0
    plan_cost_usd: float = 0.0
    build_cost_usd: float = 0.0
    review_cost_usd: float = 0.0
    review_passed: bool | None = None  # None: not reviewed (skipped, or the gate failed)
    review_rounds: int = 0
    review_report: str = ""  # failed items of the last review
    review_note: str = ""  # why the review was skipped, or that an earlier build was kept

    @property
    def cost_usd(self) -> float:
        return self.expand_cost_usd + self.plan_cost_usd + self.build_cost_usd + self.review_cost_usd


def save_plan(record, plan: Plan) -> Path:
    path = Path(record.path) / PLAN_FILE
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def design(
    request,
    inventory_path,
    *,
    build_model=None,
    plan_model=DEFAULT_PLAN_MODEL,
    review_model=DEFAULT_REVIEW_MODEL,
    max_cost=0.5,
    max_retries=2,
    records_dir=None,
    run=None,
    gate=final_gate,
    planner=make_plan,
    renderer=render_build,
    reviewer=run_review,
) -> DesignResult:
    """Plan, build and review a design, using only what is in the inventory file.

    Flow: (Expand) -> Plan -> Build until the gate passes (max_retries retries) ->
    render -> Review. If review items fail, the builder runs again from that
    record with the failed items (max MAX_REVIEW_ROUNDS reviews). If that rebuild
    cannot pass the gate, the last build that passed it is kept.

    Raises PlanError (carrying the planning cost) if no usable plan comes back.
    """
    from brickwright.config import Config
    from brickwright.driver.loop import run_agent
    from brickwright.record import Record

    run = run or run_agent
    inventory_path = Path(inventory_path).resolve()
    inventory = load_inventory(inventory_path)
    os.environ[INVENTORY_ENV] = str(inventory_path)  # the agent's child processes inherit this
    require_owned_check()
    build_model = build_model or os.environ.get("BRICKWRIGHT_GENERATION_MODEL") or DEFAULT_MODEL
    kwargs = {"model": build_model, "max_cost": max_cost}
    if records_dir is not None:
        kwargs["records_dir"] = Path(records_dir)
    config = Config(**kwargs)

    expansion: str | None = None  # set by the Expand step once it exists
    expand_cost = 0.0
    planned: PlanResult = planner(request, inventory, model=plan_model)
    plan = planned.plan
    prompt = build_prompt(request, inventory, plan, spare_set(inventory, plan))

    def new_record(record_prompt: str, from_id: str | None = None):
        rec = Record.create(config, prompt=record_prompt, from_id=from_id)
        save_plan(rec, plan)
        return rec

    build_cost, rounds = 0.0, 0

    def build_until_gate(record):
        nonlocal build_cost, rounds
        for attempt in range(1, max_retries + 2):
            build_cost += run(record, config).usage.cost_usd
            rounds += 1
            ok, report = gate(record.build_path, inventory_path)
            if ok or attempt > max_retries:
                return record, ok, report
            record = new_record(feedback_prompt(gate_problem(report), prompt), record.id)

    review_cost = 0.0
    reviews: list[dict] = []
    review_passed: bool | None = None
    failed_text = note = ""
    kept = None  # (record, report) of the last build that passed the gate
    record = new_record(prompt)
    for review_round in range(1, MAX_REVIEW_ROUNDS + 1):
        record, ok, report = build_until_gate(record)
        if not ok:
            if kept is not None:
                note = f"the rebuild after the review failed the gate ({record.id}); kept {kept[0].id}"
                warn(note)
                (record, report), ok = kept, True
            break
        kept = (record, report)
        image, why = renderer(record, config)
        if image is None:
            note, review_passed = why, None
            warn(f"review skipped: {why}")
            break
        try:
            result = reviewer(plan, expansion, image, model=review_model)
        except ReviewError as err:
            review_cost += err.cost_usd
            note, review_passed = str(err), None
            warn(f"review skipped: {err}")
            break
        review_cost += result.cost_usd
        review_passed = result.review.passed
        failed_text = review_report(result.review)
        reviews.append(
            {
                "round": review_round,
                "record_id": record.id,
                "image": str(image),
                "passed": review_passed,
                "cost_usd": result.cost_usd,
                "verdicts": [v.model_dump() for v in result.review.verdicts],
            }
        )
        save_reviews(record, reviews)
        if review_passed or review_round == MAX_REVIEW_ROUNDS:
            break
        record = new_record(feedback_prompt(review_problem(result.review), prompt), record.id)

    return DesignResult(
        ok=ok,
        record_id=record.id,
        build_path=Path(record.build_path),
        rounds=rounds,
        report=report,
        expand_cost_usd=expand_cost,
        plan_cost_usd=planned.cost_usd,
        build_cost_usd=build_cost,
        review_cost_usd=review_cost,
        review_passed=review_passed,
        review_rounds=len(reviews),
        review_report=failed_text if review_passed is False else "",
        review_note=note,
    )


def cost_line(result: DesignResult) -> str:
    return (
        f"Cost: ${result.cost_usd:.3f} (expand ${result.expand_cost_usd:.3f} + "
        f"plan ${result.plan_cost_usd:.3f} + build ${result.build_cost_usd:.3f} + "
        f"review ${result.review_cost_usd:.3f})"
    )


def review_line(result: DesignResult) -> str:
    if result.review_passed is None:
        return f"Review: skipped ({result.review_note})" if result.review_note else "Review: not run"
    if result.review_passed:
        return f"Review: passed after {result.review_rounds} round(s)"
    return f"Review: FAILED after {result.review_rounds} round(s):\n{result.review_report}"


def exit_code(result: DesignResult) -> int:
    """0: gate passed and review passed or skipped. 2: gate failed. 3: review failed."""
    if not result.ok:
        return 2
    return 3 if result.review_passed is False else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voxel.design")
    parser.add_argument("request", help='what to build, like "a small red tower"')
    parser.add_argument("--inventory", default="inventory.json")
    parser.add_argument(
        "--build-model",
        "--model",
        dest="build_model",
        default=None,
        help=f"model for the builder agent (default {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--plan-model",
        default=DEFAULT_PLAN_MODEL,
        help=f"model for the planning call (default {DEFAULT_PLAN_MODEL})",
    )
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD cap per build round")
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args(argv)
    if argv is None and os.name == "nt" and not sys.flags.utf8_mode:
        # BrickWright writes its transcript with the Windows default encoding,
        # which cannot hold some characters. Run again in UTF-8 mode.
        env = dict(os.environ, PYTHONUTF8="1")
        return subprocess.call([sys.executable, "-m", "voxel.design", *sys.argv[1:]], env=env)
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: put ANTHROPIC_API_KEY=... in your .env file.", file=sys.stderr)
        return 1
    if not Path(args.inventory).exists():
        print(f"Error: {args.inventory} not found. Run python -m voxel.rebrickable first.", file=sys.stderr)
        return 1
    try:
        result = design(
            args.request,
            args.inventory,
            build_model=args.build_model,
            plan_model=args.plan_model,
            max_cost=args.max_cost,
            max_retries=args.max_retries,
        )
    except PlanError as err:
        print(f"Error: planning failed. {err}", file=sys.stderr)
        print(f"Cost: ${err.cost_usd:.3f} (plan, {err.attempts} call(s))", file=sys.stderr)
        return 1
    print(f"\n{'PASSED' if result.ok else 'FAILED'} the inventory and step gate after {result.rounds} round(s).")
    if result.ok:
        print(review_line(result))
        if result.review_passed is False and result.review_note:
            print(f"Note: {result.review_note}")
    print(cost_line(result))
    print(f"Build file: {result.build_path}")
    if not result.ok:
        print(result.report)
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
