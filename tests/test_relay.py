"""The relay: the only holder of the Anthropic key. No test here uses the network."""

import http.client
import json

import pytest

from fake_anthropic import FakeAnthropic, text
from voxel.relay import ANTHROPIC, REDACTED, Relay, Secret, Upstream, scrub

KEY = "sk-ant-test-REALKEY-0123456789abcdef"
TOKEN = "job-token-not-the-key"


@pytest.fixture
def upstream():
    fake = FakeAnthropic()
    yield fake
    fake.close()


@pytest.fixture
def relay(upstream):
    relay = Relay(upstream=Upstream("127.0.0.1", upstream.port, tls=False), price=lambda model, usage: 0.0)
    relay.open(TOKEN, Secret(KEY), spend_cap=1.0)
    yield relay
    relay.shutdown()


def call(relay, *, method="POST", path="/v1/messages", token=TOKEN, host=None, body=b'{"model":"m","messages":[]}'):
    conn = http.client.HTTPConnection("127.0.0.1", relay.port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{relay.port}")
    if token is not None:
        conn.putheader("x-api-key", token)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(len(body)))
    conn.endheaders()
    conn.send(body)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_the_only_destination_outside_tests_is_anthropic():
    assert ANTHROPIC == Upstream("api.anthropic.com", 443, True)
    relay = Relay(price=lambda model, usage: 0.0)
    try:
        assert relay._upstream is ANTHROPIC
    finally:
        relay.shutdown()


def test_swaps_the_job_token_for_the_key(relay, upstream):
    status, data = call(relay)
    assert status == 200
    assert json.loads(data)["content"][0]["text"] == "ok"
    (seen,) = upstream.calls
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == KEY
    assert TOKEN not in json.dumps(seen["headers"])


def test_only_listed_headers_are_passed_on(relay, upstream):
    conn = http.client.HTTPConnection("127.0.0.1", relay.port, timeout=10)
    conn.request(
        "POST",
        "/v1/messages",
        body=b"{}",
        headers={"x-api-key": TOKEN, "anthropic-version": "2023-06-01", "Cookie": "a=b", "X-Other": "1"},
    )
    assert conn.getresponse().status == 200
    conn.close()
    headers = upstream.calls[0]["headers"]
    assert headers["anthropic-version"] == "2023-06-01"
    assert "cookie" not in headers and "x-other" not in headers


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token": "wrong-token"},
        {"token": None},
        {"token": KEY[:10]},
        {"path": "/v1/models"},
        {"path": "/v1/messages/batches"},
        {"path": "/"},
        {"path": "http://evil.example/v1/messages"},
        {"path": "https://evil.example/v1/messages"},
        {"host": "evil.example"},
        {"host": "api.openai.com"},
        {"method": "GET"},
        {"method": "PUT"},
        {"method": "DELETE"},
    ],
)
def test_refuses_and_sends_nothing_upstream(relay, upstream, kwargs):
    status, data = call(relay, **kwargs)
    assert status in (401, 403)
    assert KEY.encode() not in data
    assert upstream.calls == []


def test_refuses_connect(relay, upstream):
    conn = http.client.HTTPConnection("127.0.0.1", relay.port, timeout=10)
    conn.putrequest("CONNECT", "evil.example:443", skip_host=True)
    conn.putheader("Host", "evil.example:443")
    conn.putheader("x-api-key", TOKEN)
    conn.endheaders()
    assert conn.getresponse().status == 403
    conn.close()
    assert upstream.calls == []


def test_token_is_dead_after_close(relay, upstream):
    assert call(relay)[0] == 200
    relay.close(TOKEN)
    assert call(relay)[0] == 401
    assert len(upstream.calls) == 1
    assert relay._grants == {}


def test_close_clears_the_key():
    secret = Secret(KEY)
    relay = Relay(upstream=Upstream("127.0.0.1", 9, tls=False), price=lambda model, usage: 0.0)
    try:
        relay.open(TOKEN, secret, 1.0)
        relay.close(TOKEN)
        assert secret.reveal() == ""
    finally:
        relay.shutdown()


def test_a_reply_that_echoes_the_key_is_scrubbed(upstream, relay):
    upstream.reply = lambda body, headers: (
        401,
        {"type": "error", "error": {"type": "authentication_error", "message": f"invalid x-api-key {headers['x-api-key']}"}},
    )
    status, data = call(relay)
    assert status == 401
    assert KEY.encode() not in data
    assert REDACTED.encode() in data


def test_unreachable_upstream_gives_a_fixed_message():
    relay = Relay(upstream=Upstream("127.0.0.1", 9, tls=False), price=lambda model, usage: 0.0)
    try:
        relay.open(TOKEN, Secret(KEY), 1.0)
        status, data = call(relay, body=b'{"secret":"request text"}')
        assert status == 502
        assert json.loads(data)["error"]["message"] == "the relay could not reach Anthropic"
        assert KEY.encode() not in data and b"request text" not in data
    finally:
        relay.shutdown()


def test_spend_cap_stops_further_calls(upstream):
    relay = Relay(upstream=Upstream("127.0.0.1", upstream.port, tls=False), price=lambda model, usage: 0.6)
    try:
        relay.open(TOKEN, Secret(KEY), spend_cap=1.0)
        assert call(relay)[0] == 200
        assert call(relay)[0] == 200  # 1.2 spent after this one
        assert call(relay)[0] == 403
        assert len(upstream.calls) == 2
    finally:
        relay.shutdown()


def test_secret_never_shows_in_repr_or_str():
    secret = Secret(KEY)
    assert KEY not in repr(secret) and KEY not in str(secret) and KEY not in f"{secret}"
    assert KEY not in repr([secret]) and KEY not in repr({"key": secret})


def test_scrub():
    assert scrub(b"a " + KEY.encode() + b" b", KEY) == b"a " + REDACTED.encode() + b" b"
    assert scrub(b"abc", "") == b"abc"


def test_relay_prints_nothing(relay, upstream, capsys):
    call(relay)
    call(relay, token="wrong")
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
