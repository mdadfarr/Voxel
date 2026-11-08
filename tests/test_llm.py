import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from voxel.llm import ReplyError, Usage, call_with_retry, load_json


def reply(text, cost, prompt_tokens, completion_tokens):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        _hidden_params={"response_cost": cost},
    )


def test_usage_of_a_response():
    assert Usage.of(reply("x", 0.02, 1200, 300)) == Usage(1200, 300, 0.02)
    assert Usage.of(SimpleNamespace()) == Usage(0, 0, 0.0)  # no usage, no cost: zeros


def parse(text):
    if text != "good":
        raise ValueError("bad reply")
    return text


def test_call_with_retry_adds_usage_over_attempts():
    replies = iter([reply("bad", 0.01, 1000, 100), reply("good", 0.02, 1500, 200)])
    value, usage, attempts = call_with_retry(
        lambda **kw: next(replies), model="m", messages=[], parse=parse, max_tokens=10, what="thing"
    )
    assert value == "good" and attempts == 2
    assert usage == Usage(2500, 300, pytest.approx(0.03))


def test_reply_error_carries_usage():
    replies = iter([reply("bad", 0.01, 1000, 100), reply("bad", 0.01, 1000, 100)])
    with pytest.raises(ReplyError) as info:
        call_with_retry(lambda **kw: next(replies), model="m", messages=[], parse=parse, max_tokens=10, what="thing")
    assert info.value.usage == Usage(2000, 200, pytest.approx(0.02))
    assert info.value.cost_usd == pytest.approx(0.02)


class Schema(BaseModel):
    name: str


def test_call_with_retry_asks_for_structured_output():
    calls = []

    def complete(**kw):
        calls.append(kw)
        return reply('{"name": "x"}', 0.01, 10, 5)

    call_with_retry(complete, model="m", messages=[], parse=json.loads, max_tokens=10, what="thing", schema=Schema)
    assert calls[0]["response_format"] is Schema
    call_with_retry(complete, model="m", messages=[], parse=json.loads, max_tokens=10, what="thing")
    assert "response_format" not in calls[1]  # no schema, no structured output


def test_cut_off_reply_is_retried_with_the_reason():
    cut = reply('{"name": "x', 0.01, 10, 10)
    cut.choices[0].finish_reason = "length"
    replies = iter([cut, reply('{"name": "x"}', 0.01, 10, 5)])
    calls = []

    def complete(**kw):
        calls.append(copy.deepcopy(kw))
        return next(replies)

    value, _, attempts = call_with_retry(
        complete, model="m", messages=[], parse=json.loads, max_tokens=10, what="plan", schema=Schema
    )
    assert value == {"name": "x"} and attempts == 2
    retry = calls[1]["messages"][-1]["content"]
    assert "cut off at the 10-token output limit" in retry and "more concisely" in retry


def test_load_json_names_the_error_position():
    with pytest.raises(ValueError) as info:
        load_json('{\n  "a": 1,\n  "b": [1, 2,,]\n}')
    message = str(info.value)
    assert "line 3 column 14 (character 25 of 29)" in message
    assert "near: " in message and "[1, 2,," in message
    assert load_json('```json\n{"a": 1}\n```') == {"a": 1}
