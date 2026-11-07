"""Design a build with the BrickWright agent, using only parts you own.

Flow: Expand -> Plan -> Build -> Review.

Expand (voxel.expand, Opus): Voxel asks you at most 3 questions about what the
request leaves open (empty answer = the default shown in brackets), then writes
a detailed description: size, proportions, colors per part and key features.
It is saved as expansion.json in the record folder. --no-questions uses all
defaults; --no-expand skips the step and plans from the short request.
--from-record <id> skips Expand and Plan and reuses that record's expansion and
latest plan, to compare builder models on the exact same plan.

The summary splits the cost by step and by model (tokens in and out); the
per-model usage is saved as usage.json in the final record.

Then one planning call (voxel.plan, Opus by default) turns the request into a
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
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .check import CheckResult, check_build
from .expand import (
    DEFAULT_EXPAND_MODEL,
    Answer,
    Expansion,
    ExpandError,
    Question,
    ask_questions,
    expand,
)
from .inventory import Inventory, PartKey, color_label, color_name, load_inventory
from .llm import Usage
from .parts import part_name
from .plan import (
    DEFAULT_PLAN_MODEL,
    UNITS,
    Plan,
    PlanError,
    PlanResult,
    make_plan,
    plan_errors,
    revise_plan,
    spare_set,
)
from .review import DEFAULT_REVIEW_MODEL, Review, ReviewError, review_report
from .review import review as run_review
from .steps import MAX_STEP_PARTS, step_problems

INVENTORY_ENV = "VOXEL_INVENTORY"
REQUIRED_CHECKS_ENV = "BRICKWRIGHT_REQUIRED_CHECKS"  # read by brickwright's validation
OWNED_CHECK = "voxel.design:check_owned"
STEPS_CHECK = "voxel.steps:check_steps"
DEFAULT_MODEL = "claude-haiku-5-5"  # the build model
PLAN_FILE = "plan.json"
PLANS_FILE = "plans.json"  # every plan version, in the final record
USAGE_FILE = "usage.json"  # tokens and cost per model, in the final record
STEP_GROUP = {  # usage step -> cost line part
    "questions": "expand",
    "expand": "expand",
    "plan": "plan",
    "revise": "plan",
    "build": "build",
    "review": "review",
}
EXPANSION_FILE = "expansion.json"
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
        "its sections give each part of the object with size, position and colors.\n"
        f"Units: x, y, width and depth are in studs; {UNITS}, the same units as "
        "BrickLayer's add() position (x, y, z). A plate-thick step is 1/3 brick.\n"
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


def feedback_prompt(problem: str, base_prompt: str, *, new_plan: bool = False) -> str:
    """Prompt for a retry round, after the gate or the review failed.

    A retry is a fresh conversation, so it repeats the full build prompt
    (request, plan, planned parts, spare set) first. `problem` says what failed:
    gate_problem(report) or review_problem(review).

    new_plan: the plan above was revised after a failed review, and build.py
    starts empty. Otherwise build.py holds the earlier build of the same plan.
    """
    rules = (
        "It must use only the parts and counts listed above, and every build step "
        f"(build.add_step()) must stand on its own with at most {MAX_STEP_PARTS} parts. "
        "Keep check_owned(tests) in run_tests()."
    )
    if new_plan:
        return (
            f"{base_prompt}\n\n"
            f"PREVIOUS ATTEMPT: your earlier build {problem}\n\n"
            "The plan above is a NEW plan, revised because of that review. build.py "
            "starts empty: build the new plan from scratch, using the reasons above to "
            f"avoid the same problems. {rules}"
        )
    return (
        f"{base_prompt}\n\n"
        "PREVIOUS ATTEMPT: build.py already holds your earlier build, but it "
        f"{problem}\n\n"
        f"Fix these problems in build.py. {rules} Keep following the plan."
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
    return save_json(record, REVIEW_FILE, reviews)


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
    usage: tuple[dict, ...] = ()  # per model: model, input_tokens, output_tokens, cost_usd, steps
    reused_from: str = ""  # with --from-record: "plan vN and expansion from <id>"

    @property
    def cost_usd(self) -> float:
        return self.expand_cost_usd + self.plan_cost_usd + self.build_cost_usd + self.review_cost_usd


@dataclass
class UsageLedger:
    """Tokens and cost per (model, step), in order of first use."""

    rows: dict[tuple[str, str], Usage] = field(default_factory=dict)

    def add(self, model: str, step: str, usage: Usage) -> None:
        self.rows[(model, step)] = self.rows.get((model, step), Usage()) + usage

    def step_cost(self, group: str) -> float:
        return sum(u.cost_usd for (_, step), u in self.rows.items() if STEP_GROUP[step] == group)

    @property
    def total_cost(self) -> float:
        return sum(u.cost_usd for u in self.rows.values())

    def by_model(self) -> list[dict]:
        models: dict[str, dict] = {}
        for (model, step), u in self.rows.items():
            row = models.setdefault(
                model, {"model": model, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "steps": []}
            )
            row["input_tokens"] += u.input_tokens
            row["output_tokens"] += u.output_tokens
            row["cost_usd"] += u.cost_usd
            if step not in row["steps"]:
                row["steps"].append(step)
        return list(models.values())


def run_usage(run_report) -> tuple[Usage, bool]:
    """Tokens and cost of one builder run. False when BrickWright could not price it all;
    the cost is then the known part (a lower bound)."""
    u = getattr(run_report, "usage", None)
    cost = getattr(u, "cost_usd", None)
    known = cost is not None
    if not known:
        cost = getattr(u, "known_cost_usd", 0.0) or 0.0
    tokens = [getattr(u, name, 0) or 0 for name in ("prompt_tokens", "completion_tokens")]
    return Usage(int(tokens[0]), int(tokens[1]), float(cost)), known


class RecordError(ValueError):
    """--from-record cannot use that record."""


@dataclass(frozen=True)
class Reused:
    record_id: str
    request: str
    plan: Plan
    plan_version: int
    expansion: Expansion | None
    expansion_data: dict | None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RecordError(f"{path} could not be read: {err}") from None


def load_record(records_dir: str | Path, record_id: str, inventory: Inventory) -> Reused:
    """The expansion and latest plan version of an earlier record, checked for reuse."""
    folder = Path(records_dir) / record_id
    if not folder.is_dir():
        raise RecordError(f"record {record_id!r} not found in {records_dir}")
    plans_path = folder / PLANS_FILE
    if not plans_path.exists():
        hint = ""
        if (folder / PLAN_FILE).exists():
            hint = (
                " It has a plan.json but no plans.json, so it was made before z and height "
                "moved from plates to bricks, and its plan cannot be reused."
            )
        raise RecordError(f"{plans_path} not found.{hint}")
    history = _read_json(plans_path)
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict) or "plan" not in history[-1]:
        raise RecordError(f"{plans_path} has no plan versions")
    latest = history[-1]
    version = latest.get("version", len(history))
    try:
        plan = Plan.model_validate(latest["plan"])
    except ValidationError as err:
        raise RecordError(f"{plans_path}: plan version {version} is not a valid plan:\n{err}") from None
    errors = plan_errors(plan, inventory)
    if errors:
        raise RecordError(
            f"{plans_path}: plan version {version} uses parts not in your current inventory:\n"
            + "\n".join(f"- {e}" for e in errors)
        )

    expansion, data = None, None
    expansion_path = folder / EXPANSION_FILE
    if expansion_path.exists():
        data = _read_json(expansion_path)
        try:
            size = {k: v for k, v in data["size"].items() if k != "height_unit"}
            expansion = Expansion.model_validate(
                {"description": data["description"], "size": size, "features": data["features"]}
            )
        except (KeyError, TypeError, AttributeError, ValidationError) as err:
            raise RecordError(f"{expansion_path} is not a valid expansion: {err}") from None

    request = (data or {}).get("request") or ""
    if not request and (folder / "meta.json").exists():
        meta = _read_json(folder / "meta.json")
        request = str(meta.get("prompt", "")).split("\n\n")[0].strip() if isinstance(meta, dict) else ""
    if not request:
        raise RecordError(f"could not find the request in {record_id}; pass it on the command line")
    return Reused(record_id, request, plan, version, expansion, data)


def collect_answers(questions: list[Question], ask=input, interactive: bool = True) -> list[Answer]:
    """Ask each question on the terminal. An empty answer (or no input) means the default."""
    answers = []
    for q in questions:
        options = f" ({', '.join(q.options)})" if q.options else ""
        line = f"{q.text}{options} [{q.default}]"
        typed = ""
        if interactive:
            try:
                typed = ask(line + " ").strip()
            except EOFError:
                typed = ""
        else:
            print(f"{line} (default)")
        answers.append(Answer(q, typed or q.default, typed=bool(typed)))
    return answers


def expansion_record(
    request: str, answers: list[Answer], expansion: Expansion, cost_usd: float
) -> dict:
    return {
        "request": request,
        "questions": [a.question.model_dump() for a in answers],
        "answers": [a.to_json() for a in answers],
        "description": expansion.description,
        "size": {**expansion.size.model_dump(), "height_unit": "bricks (1 brick = 3 plates)"},
        "features": expansion.features,
        "cost_usd": cost_usd,
    }


def save_json(record, name: str, data) -> Path:
    path = Path(record.path) / name
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def plan_version(version: int, reason: str, plan: Plan, cost_usd: float) -> dict:
    """One entry of plans.json. record_id is filled in by the first record built from it."""
    return {
        "version": version,
        "reason": reason,
        "record_id": None,
        "cost_usd": cost_usd,
        "plan": plan.model_dump(),
    }


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
    reviser=revise_plan,
    use_expand=True,
    ask_user=True,
    ask=input,
    expand_model=DEFAULT_EXPAND_MODEL,
    asker=ask_questions,
    expander=expand,
    from_record: str | None = None,
) -> DesignResult:
    """Expand, plan, build and review a design, using only what is in the inventory file.

    Flow: Expand (questions, then a detailed description; skipped when
    use_expand is False; defaults used without asking when ask_user is False)
    -> Plan -> Build until the gate passes (max_retries retries, each continuing
    from the previous build) -> render -> Review. If review items fail, the plan
    is revised (reviser) and the builder starts again from an empty build with
    the new plan and the review's reasons (max MAX_REVIEW_ROUNDS reviews). If that
    rebuild cannot pass the gate, the last build that passed it is kept.

    from_record: skip Expand and Plan, and reuse that record's expansion and
    latest plan version (request may then be empty: the record's is used).

    Raises RecordError if from_record cannot be used (before any LLM call), and
    ExpandError or PlanError if no usable reply comes back; their cost_usd is
    everything spent until then.
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

    ledger = UsageLedger()
    expansion: Expansion | None = None
    expansion_data: dict | None = None
    reused_from = ""

    if from_record:
        reused = load_record(config.records_dir, from_record, inventory)
        if not request:
            request = reused.request
        elif request != reused.request:
            warn(f"the request differs from {from_record}'s ({reused.request!r}); the build prompt will too")
        if use_expand and reused.expansion is not None:
            expansion = reused.expansion
            expansion_data = {**reused.expansion_data, "from_record": from_record}
        elif use_expand:
            print(f"Note: {from_record} has no expansion.json; running without an expansion.")
        plan = reused.plan
        plans: list[dict] = [
            plan_version(1, f"from record {from_record} (version {reused.plan_version})", plan, 0.0)
        ]
        reused_from = f"plan v{reused.plan_version}" + (" and expansion" if expansion else "") + f" from {from_record}"
    else:
        if use_expand:
            try:
                asked = asker(request, inventory, model=expand_model)
            except ExpandError as err:
                ledger.add(expand_model, "questions", err.usage)
                raise ExpandError(str(err), ledger.total_cost, err.attempts) from None
            ledger.add(expand_model, "questions", asked.usage)
            answers = collect_answers(asked.questions, ask, interactive=ask_user)
            try:
                expanded = expander(request, answers, inventory, model=expand_model)
            except ExpandError as err:
                ledger.add(expand_model, "expand", err.usage)
                raise ExpandError(str(err), ledger.total_cost, err.attempts) from None
            ledger.add(expand_model, "expand", expanded.usage)
            expansion = expanded.expansion
            expansion_data = expansion_record(request, answers, expansion, ledger.step_cost("expand"))
        try:
            planned: PlanResult = planner(request, inventory, model=plan_model, expansion=expansion)
        except PlanError as err:
            ledger.add(plan_model, "plan", err.usage)
            raise PlanError(str(err), ledger.total_cost, err.attempts) from None
        ledger.add(plan_model, "plan", planned.usage)
        plan = planned.plan
        plans = [plan_version(1, "initial", plan, planned.cost_usd)]
    prompt = build_prompt(request, inventory, plan, spare_set(inventory, plan))
    expansion_text = expansion.as_text() if expansion is not None else None

    def new_record(record_prompt: str, from_id: str | None = None):
        rec = Record.create(config, prompt=record_prompt, from_id=from_id)
        if plans[-1]["record_id"] is None:
            plans[-1]["record_id"] = rec.id  # the first record built from this version
        save_plan(rec, plan)
        save_json(rec, PLANS_FILE, plans)
        if expansion_data is not None:
            save_json(rec, EXPANSION_FILE, expansion_data)
        return rec

    rounds = 0

    def build_until_gate(record):
        nonlocal rounds
        for attempt in range(1, max_retries + 2):
            used, known = run_usage(run(record, config))
            ledger.add(build_model, "build", used)
            if not known:
                warn(
                    f"BrickWright could not price all of the builder's calls in {record.id}; "
                    f"counting the known ${used.cost_usd:.3f}, so the cost is a lower bound"
                )
            rounds += 1
            ok, report = gate(record.build_path, inventory_path)
            if ok or attempt > max_retries:
                return record, ok, report
            record = new_record(feedback_prompt(gate_problem(report), prompt), record.id)

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
            result = reviewer(plan, expansion_text, image, model=review_model)
        except ReviewError as err:
            ledger.add(review_model, "review", err.usage)
            note, review_passed = str(err), None
            warn(f"review skipped: {err}")
            break
        ledger.add(review_model, "review", result.usage)
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
        try:
            revised = reviser(plan, result.review, image, expansion, inventory, model=plan_model)
        except PlanError as err:
            ledger.add(plan_model, "revise", err.usage)
            note = f"plan revision failed, keeping {record.id}: {err}"
            warn(note)
            break
        ledger.add(plan_model, "revise", revised.usage)
        plan = revised.plan
        plans.append(
            plan_version(len(plans) + 1, f"revised after review round {review_round}", plan, revised.cost_usd)
        )
        prompt = build_prompt(request, inventory, plan, spare_set(inventory, plan))
        # A new plan starts from an empty build, not from the shape it replaces.
        record = new_record(feedback_prompt(review_problem(result.review), prompt, new_plan=True))

    save_json(record, PLANS_FILE, plans)  # the full history, also when an earlier build was kept
    usage = ledger.by_model()
    save_json(
        record,
        USAGE_FILE,
        {"models": usage, "total_cost_usd": ledger.total_cost, "reused_from": reused_from or None},
    )

    return DesignResult(
        ok=ok,
        record_id=record.id,
        build_path=Path(record.build_path),
        rounds=rounds,
        report=report,
        expand_cost_usd=ledger.step_cost("expand"),
        plan_cost_usd=ledger.step_cost("plan"),
        build_cost_usd=ledger.step_cost("build"),
        review_cost_usd=ledger.step_cost("review"),
        review_passed=review_passed,
        review_rounds=len(reviews),
        review_report=failed_text if review_passed is False else "",
        review_note=note,
        usage=tuple(usage),
        reused_from=reused_from,
    )


def models_line(result: DesignResult) -> str:
    rows = [
        f"{u['model']} {u['input_tokens']:,} in / {u['output_tokens']:,} out ${u['cost_usd']:.3f}"
        for u in result.usage
    ]
    return "Models: " + (" | ".join(rows) if rows else "none")


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
    parser.add_argument(
        "request",
        nargs="?",
        default="",
        help='what to build, like "a small red tower" (optional with --from-record)',
    )
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
    parser.add_argument(
        "--no-questions",
        action="store_true",
        help="do not ask questions; use the default answers",
    )
    parser.add_argument(
        "--no-expand",
        action="store_true",
        help="skip the questions and the detailed description; plan from the short request",
    )
    parser.add_argument(
        "--from-record",
        metavar="RECORD_ID",
        default=None,
        help="reuse that record's expansion and latest plan; skip questions, expand and plan",
    )
    args = parser.parse_args(argv)
    if not args.request and not args.from_record:
        parser.error("the request is required unless --from-record is given")
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
            use_expand=not args.no_expand,
            ask_user=not args.no_questions,
            from_record=args.from_record,
        )
    except RecordError as err:
        print(f"Error: cannot reuse record {args.from_record}: {err}", file=sys.stderr)
        return 1
    except ExpandError as err:
        print(f"Error: expanding the request failed. {err}", file=sys.stderr)
        print("Run again with --no-expand to plan from the short request.", file=sys.stderr)
        print(f"Cost so far: ${err.cost_usd:.3f}", file=sys.stderr)
        return 1
    except PlanError as err:
        print(f"Error: planning failed. {err}", file=sys.stderr)
        print(f"Cost so far: ${err.cost_usd:.3f}", file=sys.stderr)
        return 1
    print(f"\n{'PASSED' if result.ok else 'FAILED'} the inventory and step gate after {result.rounds} round(s).")
    if result.ok:
        print(review_line(result))
        if result.review_passed is False and result.review_note:
            print(f"Note: {result.review_note}")
    if result.reused_from:
        print(f"Reused {result.reused_from}.")
    print(cost_line(result))
    print(models_line(result))
    print(f"Build file: {result.build_path}")
    if not result.ok:
        print(result.report)
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
