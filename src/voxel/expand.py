"""Expand a short request into a detailed build description, before planning.

Two LLM calls (no tools, no loop):

1. ask_questions(): at most 3 questions about what the request leaves open
   (type, colors, size, key features), each with a short default answer. Color
   questions may only offer colors you own enough of.
2. expand(): turns the request and the answers into a detailed description:
   overall size, proportions, colors per part, the key features a person would
   recognize, and shaped parts for outlines and curves. The features are also
   returned as a list, so the planner can turn each into a checklist item.

The model sees a short inventory summary (pieces per color, and shaped parts
per color), not the full part list. Each call is retried once if its reply is
unusable.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .inventory import Inventory, color_name
from .llm import ReplyError, call_with_retry, default_complete, strip_fences
from .plan import StrictModel, part_name

DEFAULT_EXPAND_MODEL = "claude-opus-5-5"
MAX_QUESTIONS = 3
MAX_FEATURES = 8
MIN_COLOR_PIECES = 20  # a color is offered only if you own at least this many pieces of it
MAX_TOKENS = 4096
SHAPES = ("slope", "curved", "round")


# --- Inventory summary --------------------------------------------------------


def shape_kind(name: str) -> str | None:
    """'slope', 'curved' or 'round' for a shaped part name, else None."""
    if name.startswith(("Minifig", "Technic")):
        return None
    if "Curved" in name or name.startswith("Arch"):
        return "curved"
    if name.startswith(("Slope", "Wedge")):
        return "slope"
    if "Round" in name or name.startswith(("Dish", "Cone", "Dome")):
        return "round"
    return None


@dataclass(frozen=True)
class ColorStock:
    color: str
    pieces: int
    shapes: dict[str, int]  # shape kind -> pieces


def color_stock(inventory: Inventory) -> list[ColorStock]:
    """Pieces per color, most first, with shaped-part counts."""
    pieces: Counter[str] = Counter()
    shapes: dict[str, Counter[str]] = {}
    for (part_id, code), n in inventory.counts.items():
        if n <= 0:
            continue
        color = color_name(code)
        pieces[color] += n
        kind = shape_kind(part_name(part_id))
        if kind:
            shapes.setdefault(color, Counter())[kind] += n
    return [
        ColorStock(color, n, {k: shapes.get(color, Counter())[k] for k in SHAPES})
        for color, n in sorted(pieces.items(), key=lambda item: (-item[1], item[0]))
    ]


def owned_colors(inventory: Inventory) -> list[str]:
    """Colors you own enough of to offer (MIN_COLOR_PIECES or more), most first."""
    return [s.color for s in color_stock(inventory) if s.pieces >= MIN_COLOR_PIECES]


def inventory_summary(inventory: Inventory) -> str:
    lines = [
        f"{s.color}: {s.pieces} pieces (slopes {s.shapes['slope']}, "
        f"curved {s.shapes['curved']}, round {s.shapes['round']})"
        for s in color_stock(inventory)
        if s.pieces >= MIN_COLOR_PIECES
    ]
    return "\n".join(lines) or "(no color with enough pieces)"


# --- Questions ----------------------------------------------------------------


class Question(StrictModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    kind: Literal["color", "other"]
    options: list[str] = Field(default_factory=list)
    default: str

    @field_validator("default")
    @classmethod
    def _has_default(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("default must not be empty")
        return value


class Questions(StrictModel):
    questions: list[Question] = Field(max_length=MAX_QUESTIONS)

    @model_validator(mode="after")
    def _unique_ids(self) -> Questions:
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")
        return self


@dataclass(frozen=True)
class Answer:
    question: Question
    value: str
    typed: bool  # False when the default was used

    def to_json(self) -> dict:
        return {"id": self.question.id, "question": self.question.text, "answer": self.value, "typed": self.typed}


def question_errors(questions: list[Question], inventory: Inventory) -> list[str]:
    """Color questions that offer a color you do not own enough of."""
    allowed = set(owned_colors(inventory))
    errors = []
    for q in questions:
        if q.kind != "color":
            continue
        for value in [*q.options, q.default]:
            if value.strip().upper() not in allowed:
                errors.append(
                    f"question {q.id!r} offers {value!r}, which is not one of the owned colors"
                )
    return errors


def questions_prompt(request: str, inventory: Inventory) -> str:
    example = {
        "questions": [
            {
                "id": "body_color",
                "text": "What color should the body be?",
                "kind": "color",
                "options": ["RED", "BLACK", "WHITE"],
                "default": "RED",
            }
        ]
    }
    return (
        "You help turn a short LEGO build request into a detailed one.\n\n"
        f"Request: {request}\n\n"
        f"Ask the user at most {MAX_QUESTIONS} short questions about things the request "
        "leaves open and that change how the model looks: its type or style, colors, size, "
        "key features. Do not ask about what the request already says. If nothing "
        "important is open, ask nothing.\n\n"
        "Each question has: id (short snake_case), text, kind (\"color\" for a color "
        "choice, else \"other\"), options (a few short choices, may be empty), and default "
        "(a short, sensible answer used when the user just presses Enter).\n"
        "Color questions: options and default may ONLY be colors from the list below, "
        "written exactly as there (like RED). Other colors are not owned in enough pieces.\n\n"
        "Owned colors (pieces in total, and shaped parts):\n"
        f"{inventory_summary(inventory)}\n\n"
        "Reply with ONE JSON object and nothing else, in this shape:\n"
        f"{json.dumps(example, indent=2)}"
    )


def parse_questions(text: str, inventory: Inventory) -> list[Question]:
    try:
        data = json.loads(strip_fences(text))
    except json.JSONDecodeError as err:
        raise ValueError(f"the reply is not valid JSON: {err}") from None
    try:
        questions = Questions.model_validate(data).questions
    except ValidationError as err:
        raise ValueError(f"the JSON does not match the questions shape:\n{err}") from None
    errors = question_errors(questions, inventory)
    if errors:
        raise ValueError("\n".join(f"- {e}" for e in errors))
    return questions


# --- Expansion ----------------------------------------------------------------


class ModelSize(StrictModel):
    width: int = Field(gt=0, description="studs, left to right")
    depth: int = Field(gt=0, description="studs, front to back")
    height: int = Field(gt=0, description="BRICKS, not plates (1 brick = 3 plates)")


class Expansion(StrictModel):
    description: str = Field(min_length=1)
    size: ModelSize
    features: list[str] = Field(min_length=1, max_length=MAX_FEATURES)

    @field_validator("description")
    @classmethod
    def _has_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be empty")
        return value

    @field_validator("features")
    @classmethod
    def _features_not_empty(cls, values: list[str]) -> list[str]:
        if any(not v.strip() for v in values):
            raise ValueError("features must not be empty")
        return values

    def size_text(self) -> str:
        s = self.size
        return f"{s.width} x {s.depth} studs, {s.height} bricks tall (1 brick = 3 plates)"

    def as_text(self) -> str:
        """The description as given to the planner and the reviewer."""
        features = "\n".join(f"- {f}" for f in self.features)
        return f"{self.description}\n\nOverall size: {self.size_text()}.\nKey features:\n{features}"


def expand_prompt(request: str, answers: list[Answer], inventory: Inventory) -> str:
    answered = "\n".join(f"- {a.question.text} {a.value}" for a in answers) or "(no questions asked)"
    example = {
        "description": "A red electric guitar standing upright ...",
        "size": {"width": 12, "depth": 2, "height": 30},
        "features": ["body outline with two cutaways", "white pickguard", "six tuning pegs"],
    }
    return (
        "Write a detailed description of a LEGO model to build from the request below. "
        "A planner and a builder will follow it, so be concrete.\n\n"
        f"Request: {request}\n\n"
        f"The user's answers:\n{answered}\n\n"
        "The description must give:\n"
        "- Overall size: width and depth in studs, height in BRICKS (1 brick = 3 plates). "
        "Big enough to show detail: not a mini model. It must still be buildable from "
        "the pieces below.\n"
        "- Realistic proportions between the parts of the object. For a guitar: the body "
        "is about a third of the height, the neck is long and thin, the headstock is small.\n"
        "- The colors of each part, only from the colors below.\n"
        "- The key features a person would recognize. For a guitar: body outline with "
        "cutaways, pickguard, pickups, frets, tuning pegs.\n"
        "- That outlines and curves must use shaped parts (slopes, curved bricks, round "
        "tiles and plates), not only plain bricks.\n\n"
        "Owned colors (pieces in total, and shaped parts):\n"
        f"{inventory_summary(inventory)}\n\n"
        "Reply with ONE JSON object and nothing else, with keys description (text), "
        "size {width, depth in studs; height in BRICKS}, and features (1 to "
        f"{MAX_FEATURES} short items, each something you can check by looking at the "
        f"model). Example of the shape:\n{json.dumps(example, indent=2)}"
    )


def parse_expansion(text: str) -> Expansion:
    try:
        data = json.loads(strip_fences(text))
    except json.JSONDecodeError as err:
        raise ValueError(f"the reply is not valid JSON: {err}") from None
    try:
        return Expansion.model_validate(data)
    except ValidationError as err:
        raise ValueError(f"the JSON does not match the description shape:\n{err}") from None


# --- The calls ----------------------------------------------------------------


class ExpandError(Exception):
    """A question or expansion call did not give a usable reply, even after one retry."""

    def __init__(self, message: str, cost_usd: float, attempts: int) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.attempts = attempts


@dataclass(frozen=True)
class QuestionsResult:
    questions: list[Question]
    cost_usd: float
    attempts: int


@dataclass(frozen=True)
class ExpansionResult:
    expansion: Expansion
    cost_usd: float
    attempts: int


def ask_questions(
    request: str,
    inventory: Inventory,
    *,
    model: str = DEFAULT_EXPAND_MODEL,
    complete: Callable[..., Any] | None = None,
) -> QuestionsResult:
    """At most 3 questions about what the request leaves open, each with a default."""
    try:
        questions, cost, attempts = call_with_retry(
            complete or default_complete(),
            model=model,
            messages=[{"role": "user", "content": questions_prompt(request, inventory)}],
            parse=lambda text: parse_questions(text, inventory),
            max_tokens=MAX_TOKENS,
            what="questions",
        )
    except ReplyError as err:
        raise ExpandError(str(err), err.cost_usd, err.attempts) from None
    return QuestionsResult(questions, cost, attempts)


def expand(
    request: str,
    answers: list[Answer],
    inventory: Inventory,
    *,
    model: str = DEFAULT_EXPAND_MODEL,
    complete: Callable[..., Any] | None = None,
) -> ExpansionResult:
    """The detailed description, with its size and key features."""
    try:
        expansion, cost, attempts = call_with_retry(
            complete or default_complete(),
            model=model,
            messages=[{"role": "user", "content": expand_prompt(request, answers, inventory)}],
            parse=parse_expansion,
            max_tokens=MAX_TOKENS,
            what="description",
        )
    except ReplyError as err:
        raise ExpandError(str(err), err.cost_usd, err.attempts) from None
    return ExpansionResult(expansion, cost, attempts)
