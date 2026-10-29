"""Design a build with the BrickWright agent, using only parts you own.

Two layers keep the agent honest:

1. Soft: the owned parts are listed in the prompt, and the agent's own
   run_tests() includes check_owned(tests), so it sees over-use as a test failure.
2. Hard: after the agent finishes, Voxel runs the check again itself, outside
   the agent's code. If it fails, the agent is run again from that build with
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

import bricklayer as bl

from .check import CheckResult, check_build
from .inventory import Inventory, color_label, load_inventory

INVENTORY_ENV = "VOXEL_INVENTORY"
DEFAULT_MODEL = "claude-haiku-5-5"
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


def part_name(part_id: str) -> str:
    try:
        return " ".join(bl.PartSpec(part_id).name.split())
    except Exception:
        return part_id


def build_prompt(request: str, inventory: Inventory) -> str:
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
        + "\n\nYour run_tests() must include `from voxel import check_owned` and a call "
        "`check_owned(tests)`. Do not remove, weaken or work around it."
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


def feedback_prompt(report: str) -> str:
    return (
        "Your build.py failed Voxel's independent inventory check:\n"
        f"{report}\n\n"
        "Fix build.py so it uses only the owned parts and counts from the original "
        "list, keep check_owned(tests) in run_tests(), and keep the design."
    )


# --- The outer loop -----------------------------------------------------------


@dataclass(frozen=True)
class DesignResult:
    ok: bool
    record_id: str
    build_path: Path
    rounds: int
    cost_usd: float
    report: str


def design(
    request,
    inventory_path,
    *,
    model=None,
    max_cost=0.5,
    max_retries=2,
    records_dir=None,
    run=None,
    gate=final_gate,
) -> DesignResult:
    """Design a build from the request, using only what is in the inventory file."""
    from brickwright.config import Config
    from brickwright.driver.loop import run_agent
    from brickwright.record import Record

    run = run or run_agent
    inventory_path = Path(inventory_path).resolve()
    inventory = load_inventory(inventory_path)
    os.environ[INVENTORY_ENV] = str(inventory_path)  # the agent's child processes inherit this
    model = model or os.environ.get("BRICKWRIGHT_GENERATION_MODEL") or DEFAULT_MODEL
    kwargs = {"model": model, "max_cost": max_cost}
    if records_dir is not None:
        kwargs["records_dir"] = Path(records_dir)
    config = Config(**kwargs)

    prompt = build_prompt(request, inventory)
    record = Record.create(config, prompt=prompt)
    cost, report, ok = 0.0, "", False
    for round_no in range(1, max_retries + 2):
        run_report = run(record, config)
        cost += run_report.usage.cost_usd
        ok, report = gate(record.build_path, inventory_path)
        if ok or round_no > max_retries:
            break
        record = Record.create(config, prompt=feedback_prompt(report), from_id=record.id)
    return DesignResult(ok, record.id, Path(record.build_path), round_no, cost, report)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voxel.design")
    parser.add_argument("request", help='what to build, like "a small red tower"')
    parser.add_argument("--inventory", default="inventory.json")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD cap per round")
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
    result = design(
        args.request,
        args.inventory,
        model=args.model,
        max_cost=args.max_cost,
        max_retries=args.max_retries,
    )
    print(f"\n{'PASSED' if result.ok else 'FAILED'} the inventory gate after {result.rounds} round(s).")
    print(f"Cost: ${result.cost_usd:.3f}")
    print(f"Build file: {result.build_path}")
    if not result.ok:
        print(result.report)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
