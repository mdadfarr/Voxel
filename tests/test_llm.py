from types import SimpleNamespace

import pytest

from voxel.llm import ReplyError, Usage, call_with_retry


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
