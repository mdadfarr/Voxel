import base64
import copy
import json
from types import SimpleNamespace

import pytest

from voxel import review as rv
from voxel.plan import Plan

PLAN = Plan.model_validate(
    {
        "checklist": ["looks like an electric guitar", "is the body red?"],
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

GOOD = {
    "verdicts": [
        {"index": 1, "item": "looks like an electric guitar", "passed": False, "reason": "reads as a box"},
        {"index": 2, "item": "is the body red?", "passed": True, "reason": "body is red"},
    ]
}


def reply(text, cost=0.02):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        _hidden_params={"response_cost": cost},
    )


class FakeLLM:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return reply(self.texts[len(self.calls) - 1])


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "build.png"
    path.write_bytes(b"\x89PNG fake")
    return path


def test_valid_review_with_image_attached(image):
    llm = FakeLLM("```json\n" + json.dumps(GOOD) + "\n```")
    result = rv.review(PLAN, None, image, complete=llm)
    assert result.attempts == 1 and result.cost_usd == pytest.approx(0.02)
    assert not result.review.passed
    assert [v.item for v in result.review.failed()] == ["looks like an electric guitar"]
    assert rv.review_report(result.review) == "- looks like an electric guitar: reads as a box"
    call = llm.calls[0]
    assert call["model"] == "claude-opus-5-5" and "tools" not in call
    text_part, picture = call["messages"][0]["content"]
    text = text_part["text"]
    assert "1. looks like an electric guitar" in text and "image alone" in text
    assert "Detailed description" not in text  # no expansion yet
    url = picture["image_url"]["url"]
    assert url == "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


def test_expansion_is_in_the_prompt(image):
    llm = FakeLLM(json.dumps(GOOD))
    rv.review(PLAN, "a red Strat-style guitar, 30 studs long", image, complete=llm)
    assert "30 studs long" in llm.calls[0]["messages"][0]["content"][0]["text"]


def test_invalid_json_is_retried(image):
    llm = FakeLLM("not json", json.dumps(GOOD))
    result = rv.review(PLAN, None, image, complete=llm)
    assert result.attempts == 2 and result.cost_usd == pytest.approx(0.04)
    assert "not valid JSON" in llm.calls[1]["messages"][2]["content"]


def test_verdicts_must_match_the_checklist(image):
    short = {"verdicts": GOOD["verdicts"][:1]}
    renamed = copy.deepcopy(GOOD)
    renamed["verdicts"][1]["item"] = "is it red?"
    with pytest.raises(ValueError, match="expected 2 verdicts"):
        rv.parse_review(json.dumps(short), PLAN)
    with pytest.raises(ValueError, match="verdict 2 item"):
        rv.parse_review(json.dumps(renamed), PLAN)
    empty_reason = copy.deepcopy(GOOD)
    empty_reason["verdicts"][0]["reason"] = " "
    with pytest.raises(ValueError, match="reason must not be empty"):
        rv.parse_review(json.dumps(empty_reason), PLAN)


def test_unusable_twice_raises_with_cost(image):
    llm = FakeLLM("nope", json.dumps({"verdicts": GOOD["verdicts"][:1]}))
    with pytest.raises(rv.ReviewError) as info:
        rv.review(PLAN, None, image, complete=llm)
    assert info.value.attempts == 2 and info.value.cost_usd == pytest.approx(0.04)


def test_review_asks_for_the_review_schema_and_knows_the_layout(image):
    from voxel.plan import ORIENTATION

    llm = FakeLLM(json.dumps(GOOD))
    rv.review(PLAN, None, image, complete=llm)
    call = llm.calls[0]
    assert call["response_format"] is rv.Review and call["max_tokens"] == 8192
    assert call["timeout"] == 180  # every call but plan and revise
    text = call["messages"][0]["content"][0]["text"]
    assert ORIENTATION in text
    assert "front (top left), side (top right), top (bottom left) and isometric (bottom right)" in text
