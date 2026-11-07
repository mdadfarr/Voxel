"""Design a build with the BrickWright agent, using only parts you own.

First, one planning call (voxel.plan, Opus by default) turns the request into a
plan: a checklist, the sections of the object, and the owned parts it needs.
The builder (Haiku by default) gets the plan, the planned parts and a spare set
of basic bricks and plates, not the full inventory. The plan is saved as
plan.json in the record folder.

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
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from .check import CheckResult, check_build
from .inventory import Inventory, PartKey, color_label, color_name, load_inventory
from .plan import DEFAULT_PLAN_MODEL, Plan, PlanError, PlanResult, make_plan, part_name, spare_set

INVENTORY_ENV = "VOXEL_INVENTORY"
REQUIRED_CHECKS_ENV = "BRICKWRIGHT_REQUIRED_CHECKS"  # read by brickwright's validation
OWNED_CHECK = "voxel.design:check_owned"
DEFAULT_MODEL = "claude-haiku-5-5"  # the build model
PLAN_FILE = "plan.json"
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
    """Make BrickWright run check_owned as a required check on every validate/finalize."""
    current = [t.strip() for t in os.environ.get(REQUIRED_CHECKS_ENV, "").split(",") if t.strip()]
    if OWNED_CHECK not in current:
        os.environ[REQUIRED_CHECKS_ENV] = ",".join([*current, OWNED_CHECK])


CHECK_OWNED_RULE = (
    "Your run_tests() must include `from voxel import check_owned` and a call "
    "`check_owned(tests)`. Do not remove, weaken or work around it."
)


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
        + CHECK_OWNED_RULE
    )


# --- Layer 2: the independent gate --------------------------------------------


def _gate_worker(build_path: str, inventory_path: str):
    """Runs in a child process. Returns a CheckResult, or an error string."""
    try:
        from brickwright.tools import build_execution

        build = build_execution.create_build(build_path)
        return check_build(build, load_inventory(inventory_path))
    except BaseException:
        return traceback.format_exc()[-2000:]


def final_gate(build_path: str | Path, inventory_path: str | Path) -> tuple[bool, str]:
    """Check a finished build.py against the inventory. Returns (ok, report)."""
    from brickwright.tools import build_execution

    try:
        outcome = build_execution.run_in_subprocess(
            _gate_worker, (str(build_path), str(inventory_path)), GATE_TIMEOUT
        )
    except (TimeoutError, RuntimeError) as err:
        return False, f"Inventory gate could not run the build: {err}"
    if isinstance(outcome, str):
        return False, f"Inventory gate could not build build.py:\n{outcome}"
    assert isinstance(outcome, CheckResult)
    return outcome.ok, "Inventory check passed." if outcome.ok else outcome.report()


def feedback_prompt(report: str, base_prompt: str) -> str:
    """Prompt for a retry round. A retry is a fresh conversation, so it repeats
    the full build prompt (request, plan, planned parts, spare set) first."""
    return (
        f"{base_prompt}\n\n"
        "PREVIOUS ATTEMPT: build.py already holds your earlier build, but it failed "
        "Voxel's independent inventory check:\n"
        f"{report}\n\n"
        "Fix build.py so it uses only the parts and counts listed above, keep "
        "check_owned(tests) in run_tests(), and keep following the plan."
    )


# --- The outer loop -----------------------------------------------------------


@dataclass(frozen=True)
class DesignResult:
    ok: bool
    record_id: str
    build_path: Path
    rounds: int
    cost_usd: float  # plan + build rounds
    report: str
    plan_cost_usd: float = 0.0

    @property
    def build_cost_usd(self) -> float:
        return self.cost_usd - self.plan_cost_usd


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
    max_cost=0.5,
    max_retries=2,
    records_dir=None,
    run=None,
    gate=final_gate,
    planner=make_plan,
) -> DesignResult:
    """Plan, then design a build from the request, using only what is in the inventory file.

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

    planned: PlanResult = planner(request, inventory, model=plan_model)
    spare = spare_set(inventory, planned.plan)
    prompt = build_prompt(request, inventory, planned.plan, spare)
    record = Record.create(config, prompt=prompt)
    save_plan(record, planned.plan)
    cost, report, ok = planned.cost_usd, "", False
    for round_no in range(1, max_retries + 2):
        run_report = run(record, config)
        cost += run_report.usage.cost_usd
        ok, report = gate(record.build_path, inventory_path)
        if ok or round_no > max_retries:
            break
        record = Record.create(config, prompt=feedback_prompt(report, prompt), from_id=record.id)
        save_plan(record, planned.plan)
    return DesignResult(
        ok, record.id, Path(record.build_path), round_no, cost, report, planned.cost_usd
    )


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
    print(f"\n{'PASSED' if result.ok else 'FAILED'} the inventory gate after {result.rounds} round(s).")
    print(
        f"Cost: ${result.cost_usd:.3f} "
        f"(plan ${result.plan_cost_usd:.3f} + build ${result.build_cost_usd:.3f})"
    )
    print(f"Build file: {result.build_path}")
    if not result.ok:
        print(result.report)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
