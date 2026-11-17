"""One build for the website, run as a child process of the site server.

This process never holds the user's Anthropic key. Its ANTHROPIC_API_KEY is a
one-time job token and ANTHROPIC_API_BASE is the site's localhost relay
(voxel.relay), which adds the real key. It does not read .env.

It prints one line `VOXEL_RESULT {json}` at the end; the site reads that line.

With --questions it prints one line `VOXEL_QUESTIONS [json]` after the questions
call and reads one JSON line of answers ({question id: answer}) from stdin. The
site shows the questions on the page. Without it, stdin is closed and the
questions call is skipped.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

RESULT_MARK = "VOXEL_RESULT "
QUESTIONS_MARK = "VOXEL_QUESTIONS "
SETTINGS_FILE = "settings.json"  # in the record: what the person chose on the Build page
MAX_ANSWER_CHARS = 100


def page_asker(typed: list[str]):
    """The questions step for the page: make the questions call, hand the questions
    to the site, and wait for one line of answers. `typed` is filled in question
    order ("" means the default), for design()'s `ask` to give back one at a time."""
    from .expand import ask_questions

    def asker(request, inventory, model):
        asked = ask_questions(request, inventory, model=model)
        print(QUESTIONS_MARK + json.dumps([q.model_dump() for q in asked.questions]), flush=True)
        try:
            answers = json.loads(sys.stdin.readline() or "{}")
        except ValueError:
            answers = {}
        if not isinstance(answers, dict):
            answers = {}
        for q in asked.questions:
            value = " ".join(str(answers.get(q.id) or "").split())[:MAX_ANSWER_CHARS]
            if q.kind == "color" and q.options and value not in q.options:
                value = ""  # a color the person does not own enough of: use the default
            typed.append(value)
        return asked

    return asker


def run(
    request: str,
    inventory: Path,
    records_dir: Path,
    max_cost: float,
    model: str | None = None,
    effort: str | None = None,
    questions: bool = False,
) -> dict:
    from . import sequence
    from .design import DEFAULT_BUILD_EFFORT, BudgetError, design
    from .expand import ExpandError
    from .plan import PlanError

    typed: list[str] = []
    extra = {"asker": page_asker(typed), "ask": lambda line: typed.pop(0) if typed else ""} if questions else {}
    try:
        result = design(
            request,
            inventory,
            build_model=model,
            build_effort=effort or DEFAULT_BUILD_EFFORT,
            max_cost=max_cost,
            records_dir=records_dir,
            use_expand=questions,  # off: no questions call at all
            ask_user=questions,
            use_plan=False,
            **extra,
        )
    except (ExpandError, PlanError, BudgetError) as err:
        return {"ok": False, "error": str(err), "cost_usd": float(getattr(err, "cost_usd", 0.0) or 0.0)}

    out = {
        "ok": bool(result.ok),
        "record_id": result.record_id,
        "cost_usd": result.cost_usd,
        "max_cost_usd": result.max_cost,
        "review_passed": result.review_passed,
        "review_items": result.review_items,
        "review_note": result.review_note,
        "stopped": result.stopped,
        "budget_note": result.budget_note,
        "manual": False,
        "pages": None,
        "parts": None,
    }
    if result.record_id:
        settings = {
            "request": request,
            "model": result.build_model,
            "effort": result.build_effort,
            "max_cost_usd": result.max_cost,
            "questions": bool(questions),
        }
        folder = Path(records_dir) / result.record_id
        if folder.is_dir():
            (folder / SETTINGS_FILE).write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if result.ok and result.record_id:
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            code = sequence.main([result.record_id, "--records-dir", str(records_dir)])
        out["manual"] = code in (0, 3)
        for line in text.getvalue().splitlines():
            name, _, value = line.partition(": ")
            if name == "Pages" and value.isdigit():
                out["pages"] = int(value)
            if name == "Parts" and value.isdigit():
                out["parts"] = int(value)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voxel.webjob")
    parser.add_argument("--request", required=True)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--records-dir", required=True, type=Path)
    parser.add_argument("--max-cost", required=True, type=float)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--questions", action="store_true", help="ask the person the clarifying questions")
    args = parser.parse_args(argv)
    try:
        out = run(
            args.request, args.inventory, args.records_dir, args.max_cost, args.model, args.effort, args.questions
        )
    except Exception as err:  # the site shows one line, not a traceback
        out = {"ok": False, "error": f"The build stopped: {type(err).__name__}"}
    print(RESULT_MARK + json.dumps(out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
