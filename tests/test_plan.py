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
        ("3039", RED): 2,  # Slope in a plan color -> spare
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
    assert pl.spare_set(INVENTORY, plan) == {("3003", WHITE): 3, ("3023", RED): 4, ("3039", RED): 2}


# --- Units: z and height in bricks ------------------------------------------------


def with_section(**changes):
    data = copy.deepcopy(VALID)
    for key, value in changes.items():
        data["sections"][0][key] = {**data["sections"][0][key], **value}
    return data


def test_z_and_height_are_bricks_with_thirds():
    plan = pl.Plan.model_validate(with_section(size={"height": 2.333}, position={"z": 1 / 3}))
    section = plan.sections[0]
    assert section.size.height == 2.333 and section.position.z == 0.333
    assert pl.Plan.model_validate(with_section(position={"z": 2.0})).sections[0].position.z == 2
    with pytest.raises(ValueError, match="not a whole number of plates"):
        pl.Plan.model_validate(with_section(position={"z": 0.5}))
    assert "bricks (1 brick = 3 plates)" in pl.Position.model_fields["z"].description
    assert "bricks (1 brick = 3 plates)" in pl.Size.model_fields["height"].description


def test_units_and_shaped_parts_in_planner_prompt():
    llm = FakeLLM(json.dumps(VALID))
    pl.make_plan("an electric guitar", INVENTORY, complete=llm)
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "z and height are in bricks (1 brick = 3 plates)" in prompt
    assert "in plates" not in prompt
    assert "Use shaped parts (slopes, curved, round, tiles) for outlines and curves" in prompt
    assert "Follow the size and proportions in the description" in prompt


# --- Spare set ------------------------------------------------------------------


def test_spare_set_has_shaped_parts_in_plan_colors_only():
    inv = Inventory(
        {
            ("3001", RED): 8,  # planned
            ("3020", WHITE): 2,  # planned
            ("3039", RED): 2,  # slope, plan color -> spare
            ("3039", BLUE): 2,  # slope, other color -> left out
            ("15068", WHITE): 3,  # curved -> spare
            ("4073", RED): 5,  # round -> spare
            ("3069b", WHITE): 4,  # tile -> spare
            ("3062b", BLUE): 4,  # round, other color -> left out
        }
    )
    spare = pl.spare_set(inv, pl.Plan.model_validate(VALID))
    assert ("3039", RED) in spare and ("3039", BLUE) not in spare
    assert {("15068", WHITE), ("4073", RED), ("3069b", WHITE)} <= set(spare)
    assert ("3001", RED) not in spare and ("3062b", BLUE) not in spare


BIG_IDS = (
    "3001 3002 3003 3004 3005 3009 3010 3020 3021 3022 3023 3024 3031 3032 3034 3035 "
    "3036 3460 3623 3666 3710 3795 3832 4477 3039 3040 3037 3038 3298 3069b 3070b "
    "3068b 2431 15068 4073 3062b"
).split()


def test_spare_set_is_capped_at_40_rows():
    inv = Inventory({**{(p, RED): 5 for p in BIG_IDS}, **{(p, WHITE): 5 for p in BIG_IDS}})
    spare = pl.spare_set(inv, pl.Plan.model_validate(VALID))
    assert len(spare) == pl.MAX_SPARE_ROWS == 40
    assert len(BIG_IDS) * 2 - 2 > 40  # there were more candidates than the cap


def test_spare_set_prefers_main_color_and_small_parts():
    # The plan uses 4 red and 2 white pieces, so red comes first in each round.
    inv = Inventory({**{(p, RED): 5 for p in BIG_IDS}, **{(p, WHITE): 5 for p in BIG_IDS}})
    spare = list(pl.spare_set(inv, pl.Plan.model_validate(VALID), limit=4))
    assert [code for _, code in spare] == [RED, WHITE, RED, WHITE]
    sizes = {pl.part_name(part) for part, _ in spare}
    assert sizes <= {"Brick 1 x 1", "Plate 1 x 1", "Tile 1 x 1 with Groove", "Plate 1 x 1 Round",
                     "Brick 1 x 1 Round with Hollow Stud"}
    big = pl.spare_set(inv, pl.Plan.model_validate(VALID))
    assert ("3036", RED) not in big  # Plate 6 x 8 is cut first


# --- Revising the plan after a failed review -------------------------------------

REVIEW = SimpleNamespace(
    verdicts=[
        SimpleNamespace(index=1, item=VALID["checklist"][0], passed=False, reason="looks like a hammer"),
        SimpleNamespace(index=2, item=VALID["checklist"][1], passed=True, reason="body is red"),
        SimpleNamespace(index=3, item=VALID["checklist"][2], passed=True, reason="neck is white"),
    ]
)


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "build.png"
    path.write_bytes(b"\x89PNG fake")
    return path


def revised_plan():
    data = copy.deepcopy(VALID)
    data["sections"][0]["size"] = {"width": 4, "depth": 2, "height": 1.333}
    return data


def test_revise_plan_sees_old_plan_review_and_image(image):
    llm = FakeLLM(json.dumps(revised_plan()))
    old = pl.Plan.model_validate(VALID)
    result = pl.revise_plan(old, REVIEW, image, None, INVENTORY, complete=llm)
    assert result.plan.sections[0].size.height == 1.333
    assert result.cost_usd == pytest.approx(0.01) and result.attempts == 1
    call = llm.calls[0]
    assert call["model"] == "claude-opus-5-5"
    text_part, picture = call["messages"][0]["content"]
    text = text_part["text"]
    assert "1. [FAIL] looks like an electric guitar: looks like a hammer" in text
    assert "2. [pass] is the body red?: body is red" in text
    assert '"name": "neck"' in text  # the old plan
    assert "Keep the checklist EXACTLY the same" in text
    assert "z and height are in bricks (1 brick = 3 plates)" in text
    assert picture["image_url"]["url"].startswith("data:image/png;base64,")


def test_revise_plan_rejects_changed_checklist_and_unowned_parts(image):
    changed = revised_plan()
    changed["checklist"] = changed["checklist"][:2]
    llm = FakeLLM(json.dumps(changed), json.dumps(with_unowned_part()))
    with pytest.raises(pl.PlanError) as info:
        pl.revise_plan(pl.Plan.model_validate(VALID), REVIEW, image, None, INVENTORY, complete=llm)
    assert info.value.cost_usd == pytest.approx(0.02)
    assert "the checklist changed" in llm.calls[1]["messages"][2]["content"]


def test_revise_plan_retries_then_passes(image):
    llm = FakeLLM(json.dumps(with_unowned_part()), json.dumps(revised_plan()))
    result = pl.revise_plan(pl.Plan.model_validate(VALID), REVIEW, image, None, INVENTORY, complete=llm)
    assert result.attempts == 2
    assert "do not own" in llm.calls[1]["messages"][2]["content"]


def test_revise_plan_real_call_is_blocked(image):
    with pytest.raises(AssertionError, match="pass a fake instead"):
        pl.revise_plan(pl.Plan.model_validate(VALID), REVIEW, image, None, INVENTORY)


# --- Structured output, front and tiles -----------------------------------------


def test_plan_and_revise_ask_for_the_plan_schema(image):
    llm = FakeLLM(json.dumps(VALID), json.dumps(revised_plan()))
    pl.make_plan("a guitar", INVENTORY, complete=llm)
    pl.revise_plan(pl.Plan.model_validate(VALID), REVIEW, image, None, INVENTORY, complete=llm)
    assert [call["response_format"] for call in llm.calls] == [pl.Plan, pl.Plan]
    assert all(call["max_tokens"] == pl.MAX_TOKENS == 16384 for call in llm.calls)
    assert all(call["timeout"] == pl.PLAN_TIMEOUT == 360 for call in llm.calls)


def test_plan_and_revise_prompts_define_front_and_ask_for_tiles(image):
    llm = FakeLLM(json.dumps(VALID), json.dumps(revised_plan()))
    pl.make_plan("a guitar", INVENTORY, complete=llm)
    pl.revise_plan(pl.Plan.model_validate(VALID), REVIEW, image, None, INVENTORY, complete=llm)
    plan_text = llm.calls[0]["messages"][0]["content"]
    revise_text = llm.calls[1]["messages"][0]["content"][0]["text"]
    for text in (plan_text, revise_text):
        assert pl.ORIENTATION in text and "+y side" in text
        assert "Cover visible surfaces with tiles (smooth parts with no studs) for a clean look." in text


def test_tiles_survive_the_spare_cap():
    inv = Inventory({**{(p, RED): 5 for p in BIG_IDS}, **{(p, WHITE): 5 for p in BIG_IDS}})
    spare = pl.spare_set(inv, pl.Plan.model_validate(VALID))
    assert len(spare) == 40
    tiles = [key for key in spare if pl.part_kind(pl.part_name(key[0])) == "tile"]
    assert {part for part, _ in tiles} >= {"3070b", "3069b"}  # 1x1 and 1x2 tiles, both colors
