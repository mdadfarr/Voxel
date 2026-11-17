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


# --- API errors: retries and clean stops ---------------------------------------

import litellm  # noqa: E402

from voxel import llm  # noqa: E402
from voxel.llm import CallFailed, call_api  # noqa: E402


def timeout_error():
    return litellm.Timeout(message="Request timed out", model="m", llm_provider="anthropic")


def credit_error():
    return litellm.BadRequestError(
        message='{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance '
        'is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."}}',
        model="m",
        llm_provider="anthropic",
    )


@pytest.fixture
def waits(monkeypatch):
    """The retry waits, without waiting."""
    seen = []
    monkeypatch.setattr(llm, "sleep", seen.append)
    return seen


def scripted(*outcomes):
    """A fake completion: each call raises or returns the next outcome."""
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    complete.calls = calls
    return complete


def test_a_timeout_then_success_is_retried(waits):
    complete = scripted(timeout_error(), reply("good", 0.02, 1500, 200))
    value, usage, attempts = call_with_retry(complete, model="m", messages=[], parse=parse, max_tokens=10, what="plan")
    assert value == "good" and attempts == 1 and usage.cost_usd == pytest.approx(0.02)
    assert len(complete.calls) == 2 and waits == [5.0]
    assert all(call["timeout"] == llm.DEFAULT_TIMEOUT == 180 for call in complete.calls)


def test_three_timeouts_stop_with_the_reason_and_the_usage_so_far(waits):
    # One unusable reply (paid), then the retry for it times out three times.
    complete = scripted(reply("bad", 0.01, 1000, 100), timeout_error(), timeout_error(), timeout_error())
    with pytest.raises(CallFailed) as info:
        call_with_retry(complete, model="m", messages=[], parse=parse, max_tokens=10, what="review")
    assert info.value.reason == "the review call timed out 3 times (180 s each)"
    assert info.value.usage == Usage(1000, 100, pytest.approx(0.01))
    assert len(complete.calls) == 4 and waits == [5.0, 15.0]


def test_a_credit_error_stops_at_once(waits):
    complete = scripted(credit_error())
    with pytest.raises(CallFailed) as info:
        call_api(complete, "plan", model="m")
    assert info.value.reason == "the API says the credit balance is too low (plan call)"
    assert len(complete.calls) == 1 and waits == []


def test_rate_limits_and_server_errors_are_retried(waits):
    complete = scripted(
        litellm.RateLimitError(message="rate limited", llm_provider="anthropic", model="m"),
        litellm.InternalServerError(message="overloaded", llm_provider="anthropic", model="m"),
        "ok",
    )
    assert call_api(complete, "plan", model="m") == "ok"
    assert waits == [5.0, 15.0]


def test_other_api_errors_stop_without_a_retry(waits):
    complete = scripted(litellm.AuthenticationError(message="invalid x-api-key", llm_provider="anthropic", model="m"))
    with pytest.raises(CallFailed) as info:
        call_api(complete, "plan", model="m")
    assert info.value.reason.startswith("the plan call failed: AuthenticationError")
    assert len(complete.calls) == 1 and waits == []


def test_bugs_are_not_api_errors(waits):
    with pytest.raises(KeyError):
        call_api(scripted(KeyError("x")), "plan", model="m")
