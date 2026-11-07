import copy
import json
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory
from voxel import expand as ex
from voxel import plan as pl

RED, BLUE = bl.Color.RED.ldraw_id, bl.Color.BLUE.ldraw_id

INVENTORY = Inventory(
    {
        ("3001", RED): 30,  # Brick 2 x 4
        ("3039", RED): 4,  # Slope Brick 45 2 x 2 -> slope
        ("15068", RED): 5,  # Slope Brick Curved 2 x 2 x 0.667 -> curved
        ("4073", RED): 3,  # Plate 1 x 1 Round -> round
        ("3001", BLUE): 3,  # too few blue pieces to offer
    }
)

COLOR_Q = {
    "id": "body_color",
    "text": "What color should the body be?",
    "kind": "color",
    "options": ["RED"],
    "default": "RED",
}
STYLE_Q = {
    "id": "style",
    "text": "Which guitar style?",
    "kind": "other",
    "options": ["Strat", "Les Paul"],
    "default": "Strat",
}
EXPANSION = {
    "description": "A red electric guitar standing upright, body a third of the height.",
    "size": {"width": 12, "depth": 2, "height": 30},
    "features": ["body outline with two cutaways", "white pickguard", "six tuning pegs"],
}


def reply(text, cost=0.02):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        _hidden_params={"response_cost": cost},
    )


class FakeLLM:
    def __init__(self, *texts, cost=0.02):
        self.texts = list(texts)
        self.cost = cost
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return reply(self.texts[len(self.calls) - 1], self.cost)

    def prompt(self, n=0):
        return self.calls[n]["messages"][0]["content"]


def test_shape_kinds():
    assert ex.shape_kind("Slope Brick 45 2 x 2") == "slope"
    assert ex.shape_kind("Slope Brick Curved 2 x 1") == "curved"
    assert ex.shape_kind("Arch 1 x 5 x 2") == "curved"
    assert ex.shape_kind("Plate 1 x 1 Round") == "round"
    assert ex.shape_kind("Dish 2 x 2 Inverted") == "round"
    assert ex.shape_kind("Minifig Binoculars with Round Eyepiece") is None
    assert ex.shape_kind("Brick 2 x 4") is None


def test_inventory_summary_counts_colors_and_shapes():
    assert ex.inventory_summary(INVENTORY) == "RED: 42 pieces (slopes 4, curved 5, round 3)"
    assert ex.owned_colors(INVENTORY) == ["RED"]


def test_questions_valid():
    llm = FakeLLM(json.dumps({"questions": [COLOR_Q, STYLE_Q]}))
    result = ex.ask_questions("an electric guitar", INVENTORY, complete=llm)
    assert [q.id for q in result.questions] == ["body_color", "style"]
    assert result.questions[0].default == "RED"
    assert result.cost_usd == pytest.approx(0.02) and result.attempts == 1
    assert llm.calls[0]["model"] == "claude-opus-5-5" and "tools" not in llm.calls[0]
    prompt = llm.prompt()
    assert "an electric guitar" in prompt and "at most 3" in prompt
    assert "RED: 42 pieces (slopes 4, curved 5, round 3)" in prompt
    assert "BLUE" not in prompt  # not the full part list, and not unowned colors
    assert "3001" not in prompt


def test_color_question_may_only_offer_owned_colors():
    blue = {**COLOR_Q, "options": ["RED", "BLUE"]}
    llm = FakeLLM(json.dumps({"questions": [blue]}), json.dumps({"questions": [COLOR_Q]}))
    result = ex.ask_questions("an electric guitar", INVENTORY, complete=llm)
    assert result.attempts == 2 and result.cost_usd == pytest.approx(0.04)
    error = llm.calls[1]["messages"][2]["content"]
    assert "'BLUE', which is not one of the owned colors" in error
    with pytest.raises(ValueError, match="'GREEN'"):
        ex.parse_questions(json.dumps({"questions": [{**COLOR_Q, "default": "GREEN"}]}), INVENTORY)
    # a non-color question may name anything
    other = {**STYLE_Q, "options": ["BLUE trim"]}
    assert ex.parse_questions(json.dumps({"questions": [other]}), INVENTORY)[0].options == ["BLUE trim"]


def test_at_most_three_questions_and_defaults_required():
    four = [{**STYLE_Q, "id": f"q{n}"} for n in range(4)]
    with pytest.raises(ValueError, match="at most 3"):
        ex.parse_questions(json.dumps({"questions": four}), INVENTORY)
    with pytest.raises(ValueError, match="default must not be empty"):
        ex.parse_questions(json.dumps({"questions": [{**STYLE_Q, "default": " "}]}), INVENTORY)
    assert ex.parse_questions(json.dumps({"questions": []}), INVENTORY) == []


def test_questions_unusable_twice_raises_with_cost():
    llm = FakeLLM("nope", "still nope")
    with pytest.raises(ex.ExpandError) as info:
        ex.ask_questions("an electric guitar", INVENTORY, complete=llm)
    assert info.value.cost_usd == pytest.approx(0.04) and info.value.attempts == 2


def answers():
    color = ex.Question.model_validate(COLOR_Q)
    style = ex.Question.model_validate(STYLE_Q)
    return [ex.Answer(color, "RED", typed=False), ex.Answer(style, "Les Paul", typed=True)]


def test_expand_valid():
    llm = FakeLLM("```json\n" + json.dumps(EXPANSION) + "\n```", cost=0.05)
    result = ex.expand("an electric guitar", answers(), INVENTORY, complete=llm)
    assert result.cost_usd == pytest.approx(0.05) and result.attempts == 1
    assert result.expansion.size.height == 30
    assert result.expansion.features[1] == "white pickguard"
    prompt = llm.prompt()
    assert "Which guitar style? Les Paul" in prompt
    assert "height in BRICKS (1 brick = 3 plates)" in prompt
    assert "not a mini model" in prompt and "body is about a third of the height" in prompt
    assert "pickguard, pickups, frets, tuning pegs" in prompt
    assert "shaped parts (slopes, curved bricks, round tiles and plates)" in prompt
    assert "RED: 42 pieces" in prompt
    text = result.expansion.as_text()
    assert "12 x 2 studs, 30 bricks tall (1 brick = 3 plates)" in text
    assert "- six tuning pegs" in text


def test_expansion_height_is_documented_in_bricks():
    doc = ex.ModelSize.model_fields["height"].description
    assert "BRICKS" in doc and "1 brick = 3 plates" in doc


def test_expand_invalid_is_retried_then_raises():
    no_features = {**EXPANSION, "features": []}
    llm = FakeLLM(json.dumps(no_features), "not json")
    with pytest.raises(ex.ExpandError) as info:
        ex.expand("an electric guitar", answers(), INVENTORY, complete=llm)
    assert info.value.cost_usd == pytest.approx(0.04)
    assert "features" in llm.calls[1]["messages"][2]["content"]


# --- The planner gets the expansion -------------------------------------------

PLAN_INV = Inventory({("3001", RED): 8, ("3020", bl.Color.WHITE.ldraw_id): 2})


def plan_json(checklist):
    return json.dumps(
        {
            "checklist": checklist,
            "sections": [
                {
                    "name": "body",
                    "size": {"width": 4, "depth": 2, "height": 6},
                    "position": {"x": 0, "y": 0, "z": 0},
                    "colors": ["RED"],
                    "parts": [{"part": "3001", "color": "RED", "count": 4}],
                }
            ],
            "parts": [{"part": "3001", "color": "RED", "count": 4}],
        }
    )


def test_features_end_up_in_the_plan_prompt():
    expansion = ex.Expansion.model_validate(EXPANSION)
    full = ["looks like an electric guitar", *EXPANSION["features"]]
    llm = FakeLLM(plan_json(full))
    pl.make_plan("an electric guitar", PLAN_INV, expansion=expansion, complete=llm)
    prompt = llm.prompt()
    assert EXPANSION["description"] in prompt
    assert "2. body outline with two cutaways\n3. white pickguard\n4. six tuning pegs" in prompt
    assert "Each key feature must become its own checklist item" in prompt
    assert "Follow its overall size and the proportions" in prompt
    assert "30 BRICKS tall; one brick is 3 plates, so that is 90 plates" in prompt


def test_plan_without_expansion_has_no_feature_rules():
    llm = FakeLLM(plan_json(["looks like a tower"]))
    pl.make_plan("a tower", PLAN_INV, complete=llm)
    assert "key feature" not in llm.prompt()


def test_plan_checklist_too_short_for_the_features_is_retried():
    expansion = ex.Expansion.model_validate(EXPANSION)
    short = ["looks like an electric guitar", "is it red?"]
    full = ["looks like an electric guitar", *EXPANSION["features"]]
    llm = FakeLLM(plan_json(short), plan_json(full))
    result = pl.make_plan("an electric guitar", PLAN_INV, expansion=expansion, complete=llm)
    assert result.attempts == 2
    assert "needs at least 4" in llm.calls[1]["messages"][2]["content"]


def test_real_calls_are_blocked_in_tests():
    # conftest.py replaces litellm.completion; the default path must hit that guard.
    with pytest.raises(AssertionError, match="pass a fake instead"):
        ex.ask_questions("an electric guitar", INVENTORY)
    with pytest.raises(AssertionError, match="pass a fake instead"):
        ex.expand("an electric guitar", [], INVENTORY)
