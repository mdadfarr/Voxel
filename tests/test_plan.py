import copy
import json
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory
from voxel import plan as pl

RED, WHITE, BLUE = (c.ldraw_id for c in (bl.Color.RED, bl.Color.WHITE, bl.Color.BLUE))

INVENTORY = Inventory(
    {
        ("3001", RED): 8,  # Brick 2 x 4 (planned)
        ("3020", WHITE): 2,  # Plate 2 x 4 (planned)
        ("3023", RED): 4,  # Plate 1 x 2 -> spare
        ("3003", WHITE): 3,  # Brick 2 x 2 -> spare
        ("3039", RED): 2,  # Slope, not basic -> not spare
        ("3001", BLUE): 6,  # basic, but the plan has no blue -> not spare
    }
)

VALID = {
    "checklist": ["looks like an electric guitar", "is the body red?", "is the neck white?"],
    "sections": [
        {
            "name": "body",
            "size": {"width": 4, "depth": 2, "height": 6},
            "position": {"x": 0, "y": 0, "z": 0},
            "colors": ["RED"],
            "parts": [{"part": "3001", "color": "RED", "count": 4}],
        },
        {
            "name": "neck",
            "size": {"width": 2, "depth": 8, "height": 1},
            "position": {"x": 1, "y": 2, "z": 6},
            "colors": ["WHITE"],
            "parts": [{"part": "3020", "color": "WHITE", "count": 2}],
        },
    ],
    "parts": [
        {"part": "3001", "color": "RED", "count": 4},
        {"part": "3020", "color": "WHITE", "count": 2},
    ],
}


def reply(text, cost=0.01):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        _hidden_params={"response_cost": cost},
    )


class FakeLLM:
    """Returns the queued replies in order and records every call."""

    def __init__(self, *texts, cost=0.01):
        self.texts = list(texts)
        self.cost = cost
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return reply(self.texts[len(self.calls) - 1], self.cost)


def with_unowned_part():
    data = copy.deepcopy(VALID)
    data["sections"][0]["parts"].append({"part": "3001", "color": "GREEN", "count": 1})
    data["parts"].append({"part": "3001", "color": "GREEN", "count": 1})
    return data


def test_valid_plan_in_one_call():
    llm = FakeLLM("```json\n" + json.dumps(VALID) + "\n```")
    result = pl.make_plan("an electric guitar", INVENTORY, complete=llm)
    assert result.attempts == 1 and len(llm.calls) == 1
    assert result.cost_usd == pytest.approx(0.01)
    assert llm.calls[0]["model"] == pl.DEFAULT_PLAN_MODEL
    assert "tools" not in llm.calls[0]
    assert [s.name for s in result.plan.sections] == ["body", "neck"]
    assert result.plan.part_counts() == {("3001", RED): 4, ("3020", WHITE): 2}
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "an electric guitar" in prompt and "3039 | Slope" in prompt and "| RED | 2" in prompt


def test_invalid_json_is_retried_with_the_error():
    llm = FakeLLM("here is the plan: {oops", json.dumps(VALID))
    result = pl.make_plan("an electric guitar", INVENTORY, complete=llm)
    assert result.attempts == 2
    assert result.cost_usd == pytest.approx(0.02)
    retry = llm.calls[1]["messages"]
    assert retry[1] == {"role": "assistant", "content": "here is the plan: {oops"}
    assert "not valid JSON" in retry[2]["content"]


def test_invalid_twice_raises_with_cost():
    llm = FakeLLM("nope", "still nope")
    with pytest.raises(pl.PlanError) as info:
        pl.make_plan("an electric guitar", INVENTORY, complete=llm)
    assert len(llm.calls) == 2
    assert info.value.attempts == 2 and info.value.cost_usd == pytest.approx(0.02)


def test_wrong_shape_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["checklist"][0] = "is it a guitar?"
    with pytest.raises(ValueError, match="looks like"):
        pl.parse_plan(json.dumps(bad), INVENTORY)
    missing = copy.deepcopy(VALID)
    missing["parts"] = missing["parts"][:1]  # neck's plate no longer listed
    with pytest.raises(ValueError, match="missing from the top-level parts"):
        pl.parse_plan(json.dumps(missing), INVENTORY)


def test_unowned_part_is_retried_then_fixed():
    llm = FakeLLM(json.dumps(with_unowned_part()), json.dumps(VALID))
    result = pl.make_plan("an electric guitar", INVENTORY, complete=llm)
    assert result.attempts == 2
    error = llm.calls[1]["messages"][2]["content"]
    assert "do not own" in error and "Green part 3001 is not in the inventory" in error


def test_more_than_owned_is_an_error():
    data = copy.deepcopy(VALID)
    data["parts"][0]["count"] = 9
    plan = pl.Plan.model_validate(data)
    assert pl.plan_errors(plan, INVENTORY) == ["Red part 3001: plan uses 9, owned 8"]


def test_spare_set_has_basic_parts_in_plan_colors():
    plan = pl.Plan.model_validate(VALID)
    assert pl.spare_set(INVENTORY, plan) == {("3003", WHITE): 3, ("3023", RED): 4}
