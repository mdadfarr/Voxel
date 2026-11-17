"""The website: key handling, search, gallery. No test here uses the network."""

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from fake_anthropic import FakeAnthropic, text, tool_use
from voxel.relay import REDACTED, Relay, Secret, Upstream
from voxel.site import (
    DEFAULT_SETTINGS,
    Site,
    SiteError,
    clean_questions,
    clean_settings,
    spend_cap,
    _Server,
    anthropic_key_problem,
    child_env,
    gallery,
    gallery_card,
    make_handler,
    redact_tree,
)

KEY = "sk-ant-test-REALKEY-0123456789abcdef"
RB_KEY = "rebrickable-test-key-123456"
INVENTORY = [
    {"part": "3001", "color": "RED", "count": 8},
    {"part": "3003", "color": "RED", "count": 6},
]

# What a hostile model would run through the builder's explore tool: dump
# everything the builder process can see.
HOSTILE = """
import os, sys, glob
print("ENV", dict(os.environ))
print("GLOBALS", globals())
print("ARGV", sys.argv, getattr(sys, "orig_argv", None))
try:
    print("STDIN", repr(sys.stdin.read()))
except Exception as err:
    print("STDIN failed", err)
import litellm
print("LITELLM", litellm.api_key, litellm.anthropic_key, litellm.api_base)
for path in glob.glob("**/*", recursive=True)[:200]:
    if os.path.isfile(path) and os.path.getsize(path) < 200000:
        try:
            print("FILE", path, open(path, errors="replace").read())
        except Exception:
            pass
"""


class FakeRebrickable:
    class RebrickableError(Exception):
        pass

    def __init__(self):
        self.searches = []
        self.times = []

    def read_api_key(self, env_path=".env"):
        return RB_KEY

    def search_sets(self, query, key):
        assert key == RB_KEY
        self.searches.append(query)
        self.times.append(time.monotonic())
        return [{"set_num": "75192-1", "name": "Big Ship", "year": 2017, "num_parts": 7541,
                 "image": "https://cdn.rebrickable.com/media/sets/75192-1.jpg"}]


@pytest.fixture
def upstream():
    fake = FakeAnthropic()
    yield fake
    fake.close()


@pytest.fixture
def served(tmp_path, upstream):
    """A running site in an empty folder, with the relay pointed at the fake Anthropic."""
    relay = Relay(upstream=Upstream("127.0.0.1", upstream.port, tls=False), price=lambda model, usage: 0.0)
    rb = FakeRebrickable()
    site = Site(tmp_path, relay=relay, inventory_for=lambda sets: INVENTORY, rebrickable=rb, max_cost=0.5)
    server = _Server(("127.0.0.1", 0), None)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(site, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield site, port, rb
    server.shutdown()
    server.server_close()
    relay.shutdown()


def request(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    data = json.dumps(body).encode() if body is not None else b""
    for name, value in (headers or {}).items():
        conn.putheader(name, value)
    if body is not None:
        conn.putheader("Content-Length", str(len(data)))
    conn.endheaders()
    conn.send(data)
    response = conn.getresponse()
    out = response.read()
    conn.close()
    return response.status, out


def post_build(port, body, origin=None):
    headers = {"Content-Type": "application/json", "Origin": origin or f"http://127.0.0.1:{port}"}
    return request(port, "POST", "/api/build", body, headers)


def wait_for(site, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        if site.job is not None and site.job.state != "building":
            return site.job
        time.sleep(0.2)
    raise AssertionError("the build did not finish in time")


def all_files(folder: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}


# --- Startup check -------------------------------------------------------------


def test_refuses_to_start_with_the_key_in_the_environment(tmp_path):
    assert "environment" in anthropic_key_problem(tmp_path, {"ANTHROPIC_API_KEY": KEY})
    assert anthropic_key_problem(tmp_path, {"ANTHROPIC_API_KEY": "  "}) == ""
    assert anthropic_key_problem(tmp_path, {}) == ""


@pytest.mark.parametrize(
    "line",
    ["ANTHROPIC_API_KEY=sk-ant-x", 'ANTHROPIC_API_KEY="sk-ant-x"', "export ANTHROPIC_API_KEY=sk-ant-x", "  ANTHROPIC_API_KEY = sk-ant-x"],
)
def test_refuses_to_start_with_the_key_in_dotenv(tmp_path, line):
    (tmp_path / ".env").write_text(f"REBRICKABLE_API_KEY=abc\n{line}\n", encoding="utf-8")
    problem = anthropic_key_problem(tmp_path, {})
    assert ".env" in problem and "sk-ant-x" not in problem


def test_starts_with_only_other_keys_in_dotenv(tmp_path):
    (tmp_path / ".env").write_text("REBRICKABLE_API_KEY=abc\nANTHROPIC_API_KEY=\n# ANTHROPIC_API_KEY=old\n", encoding="utf-8")
    assert anthropic_key_problem(tmp_path, {}) == ""


def test_run_site_returns_1_and_does_not_print_the_key(tmp_path, monkeypatch, capsys):
    from voxel.site import run_site

    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert run_site(port=0, open_browser=False, root=tmp_path) == 1
    printed = capsys.readouterr()
    assert KEY not in printed.out + printed.err


# --- Localhost only ------------------------------------------------------------


def test_binds_to_loopback_only(served):
    import inspect

    from voxel import site as site_module

    assert '_Server(("127.0.0.1", port)' in inspect.getsource(site_module.run_site)


def test_rejects_other_hosts_and_origins(served):
    site, port, _ = served
    assert request(port, "GET", "/api/gallery")[0] == 200
    assert request(port, "GET", "/api/gallery", host="evil.example")[0] == 403
    assert request(port, "GET", "/api/gallery", host=f"evil.example:{port}")[0] == 403
    assert request(port, "GET", "/api/gallery", headers={"Origin": "http://evil.example"})[0] == 403
    body = {"key": KEY, "request": "a tower", "sets": ["75192-1"]}
    assert post_build(port, body, origin="http://evil.example")[0] == 403
    assert request(port, "POST", "/api/build", body, {"Content-Type": "application/json"})[0] == 403  # no Origin
    assert request(port, "POST", "/api/build", body, {"Content-Type": "text/plain", "Origin": f"http://127.0.0.1:{port}"})[0] == 415
    assert site.job is None


def test_bad_build_requests_do_not_echo_the_key(served):
    site, port, _ = served
    for body in ({"key": KEY, "request": "", "sets": ["1-1"]}, {"key": KEY, "request": "x", "sets": []},
                 {"key": KEY, "request": "x", "sets": ["../etc"]}, {"key": KEY + " x", "request": "x", "sets": ["1-1"]}):
        status, data = post_build(port, body)
        assert status == 400 and KEY.encode() not in data
    assert site.job is None


def test_record_files_cannot_escape_the_records_folder(served, tmp_path):
    site, port, _ = served
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    for path in ("/records/../secret.txt", "/records/..%2Fsecret.txt/picture", "/records/nope/picture", "/static/../site.py"):
        assert request(port, "GET", path)[0] == 404


# --- Builder environment and the scan ------------------------------------------


def test_child_env_is_an_allowlist():
    env = child_env(
        {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": KEY, "REBRICKABLE_API_KEY": RB_KEY, "OPENAI_API_KEY": "o",
         "AWS_SECRET_ACCESS_KEY": "a", "VOXEL_MIN_FILL": "60", "VOXEL_SECRET_TOKEN": "t", "RANDOM_THING": "r"},
        "http://127.0.0.1:1", "job-token", Path("/records"),
    )
    assert env["ANTHROPIC_API_KEY"] == "job-token" and env["ANTHROPIC_API_BASE"] == "http://127.0.0.1:1"
    assert env["PATH"] == "/bin" and env["VOXEL_MIN_FILL"] == "60"
    for name in ("REBRICKABLE_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "VOXEL_SECRET_TOKEN", "RANDOM_THING"):
        assert name not in env
    assert KEY not in json.dumps(env) and RB_KEY not in json.dumps(env)


def test_redact_tree_removes_every_copy(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "messages.jsonl").write_text(f"x {KEY} y {KEY}\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("nothing", encoding="utf-8")
    hit = redact_tree([tmp_path, tmp_path / "missing"], [KEY, ""])
    assert [p.name for p in hit] == ["messages.jsonl"]
    assert all(KEY.encode() not in data for data in all_files(tmp_path).values())
    assert REDACTED in (tmp_path / "a" / "messages.jsonl").read_text(encoding="utf-8")


def test_a_key_found_after_the_run_is_removed_and_reported(served, tmp_path):
    from voxel.site import Job

    site, _, _ = served
    record = site.records_dir / "20260101-000000-a-tower-abcdef12"
    record.mkdir(parents=True)
    (record / "messages.jsonl").write_text(f"ANTHROPIC_API_KEY={KEY}\n", encoding="utf-8")
    (record / "notes.txt").write_text(f"rebrickable {RB_KEY}\n", encoding="utf-8")
    job = Job("j", "a tower", ["1-1"])
    assert site._remove_secrets(job, Secret(KEY), tmp_path / "runs" / "web" / "j", {"record_id": record.name}, b"") is True
    assert all(KEY.encode() not in d and RB_KEY.encode() not in d for d in all_files(tmp_path).values())
    clean = Job("k", "a tower", ["1-1"])
    assert site._remove_secrets(clean, Secret(KEY), tmp_path / "x", {"record_id": record.name}, b"fine") is False
    out = Job("o", "a tower", ["1-1"])
    assert site._remove_secrets(out, Secret(KEY), tmp_path / "x", {}, b"log " + KEY.encode()) is True
    assert KEY.encode() not in out.output


# --- A whole build with hostile model code --------------------------------------


def hostile_script(body, headers):
    tools = [t.get("name") for t in (body or {}).get("tools") or []]
    if "explore" not in tools:  # the questions call: no questions
        return 200, text(json.dumps({"questions": []}))
    said = json.dumps(body.get("messages"))
    if "toolu_test1" not in said:  # the builder's first turn: run the hostile code
        return 200, tool_use("explore", {"code": HOSTILE})
    # Then echo the key this server received back to the builder, as a model would not.
    return 200, text(f"I give up. By the way the header was {headers.get('x-api-key')}")


def test_hostile_model_code_cannot_find_the_key(served, upstream, tmp_path, capfd):
    site, port, _ = served
    upstream.reply = hostile_script
    (tmp_path / ".env").write_text("REBRICKABLE_API_KEY=not-the-anthropic-key\n", encoding="utf-8")

    status, data = post_build(port, {"key": KEY, "request": "a small red tower", "sets": ["75192-1"]})
    assert status == 202 and KEY.encode() not in data
    job = wait_for(site)

    # The relay was used with the real key, and the builder really ran the hostile code.
    assert upstream.calls, "the builder never reached the relay"
    assert all(c["headers"]["x-api-key"] == KEY for c in upstream.calls)
    assert any("toolu_test1" in json.dumps(c["body"].get("messages")) for c in upstream.calls), job.output[-3000:]
    dumped = [c for c in upstream.calls if "ANTHROPIC_API_BASE" in json.dumps(c["body"])]
    assert dumped, "the hostile code's output never came back to the model"

    # The key is nowhere: not in what the model saw, the page, the output, any file.
    for c in upstream.calls:
        assert KEY not in json.dumps(c["body"])
    assert KEY not in json.dumps(job.public())
    assert KEY.encode() not in job.output
    status, data = request(port, "GET", f"/api/build/{job.id}")
    assert status == 200 and KEY.encode() not in data
    files = all_files(tmp_path)
    assert any(p.name == "messages.jsonl" for p in files), "no record was written"
    for path, content in files.items():
        assert KEY.encode() not in content, path
    printed = capfd.readouterr()
    assert KEY not in printed.out + printed.err

    # And the server has let go of it.
    assert site.relay._grants == {}

    # The run went all the way: a record with a picture and a manual, both served.
    assert job.state == "done"
    result = job.result
    assert request(port, "GET", result["picture"])[0] == 200
    status, manual = request(port, "GET", result["manual"])
    assert status == 200 and KEY.encode() not in manual
    assert "\n" not in result["review"]["skipped"]  # one plain line, no internals


def test_one_build_at_a_time(served, upstream):
    site, port, _ = served
    gate = threading.Event()
    site._inventory_for = lambda sets: (gate.wait(20), [])[1]
    body = {"key": KEY, "request": "a tower", "sets": ["75192-1"]}
    assert post_build(port, body)[0] == 202
    assert post_build(port, body)[0] == 409
    gate.set()
    job = wait_for(site)
    assert job.state == "failed" and "no parts" in job.error
    assert upstream.calls == []


# --- Search --------------------------------------------------------------------


def test_search_keeps_the_rebrickable_key_on_the_server(served):
    site, port, rb = served
    status, data = request(port, "GET", "/api/sets?q=big%20ship")
    assert status == 200 and RB_KEY.encode() not in data
    (row,) = json.loads(data)["results"]
    assert row["set_num"] == "75192-1" and row["name"] == "Big Ship"
    assert row["image"] == "/api/set-image/75192-1"  # the page never gets an outside address
    assert b"rebrickable.com" not in data


def test_search_waits_caches_and_spaces_calls(served):
    site, port, rb = served
    assert json.loads(request(port, "GET", "/api/sets?q=a")[1])["results"] == []  # too short
    request(port, "GET", "/api/sets?q=ship")
    request(port, "GET", "/api/sets?q=SHIP")  # cached
    request(port, "GET", "/api/sets?q=castle")
    assert rb.searches == ["ship", "castle"]
    assert rb.times[1] - rb.times[0] >= 1.0


def test_set_pictures_come_only_from_the_picture_host(served):
    site, port, rb = served
    rb.fetch_set = lambda set_num, key: {"image": "https://evil.example/x.jpg"}
    assert request(port, "GET", "/api/set-image/1234-1")[0] == 404
    assert request(port, "GET", "/api/set-image/..%2F..%2Fx")[0] == 404


# --- Gallery -------------------------------------------------------------------


def make_record(records, name, *, prompt, expansion=None, usage=None, picture="build.display.png", turns=7):
    record = records / name
    (record / "out").mkdir(parents=True)
    meta = {"id": name, "created": "2026-01-01T00:00:00", "prompt": prompt, "turns": turns,
            "generation": {"model": "claude-haiku-5-5"}, "usage": {"cost_usd": 0.25}}
    (record / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if expansion is not None:
        (record / "expansion.json").write_text(json.dumps(expansion), encoding="utf-8")
    if usage is not None:
        (record / "usage.json").write_text(json.dumps(usage), encoding="utf-8")
    if picture:
        (record / "out" / picture).write_bytes(b"\x89PNG")
    return record


INTERNAL = "HARD RULE: use ONLY the parts listed below\n3001 | Brick 2 x 4 | Red | 8"


def test_card_shows_the_typed_request_and_never_the_internal_prompt(tmp_path):
    record = make_record(
        tmp_path, "20260101-000000-make-me-a-guitar-abcdef12",
        prompt="make me a guitar\nAnswers: body: RED\n\n" + INTERNAL,
        expansion={"request": "make me a guitar"},
        usage={"build_model": "claude-opus-5-5", "total_cost_usd": 0.94, "plan": False, "prompt_style": "one-line"},
    )
    (record / "review.json").write_text(json.dumps([{"passed": True, "verdicts": [{"item": "a", "passed": True, "reason": "r"}]}]), encoding="utf-8")
    card = gallery_card(record)
    assert card["request"] == "make me a guitar" and card["style"] == "one-line"
    assert card["model"] == "Opus" and card["cost_usd"] == 0.94 and card["turns"] == 7
    assert card["review"] == {"passed": True, "passed_count": 1, "total": 1}
    text_ = json.dumps(card)
    assert "HARD RULE" not in text_ and "Answers:" not in text_ and "plan" not in text_.lower()


def test_old_record_request_comes_from_the_first_line_only_when_the_id_confirms_it(tmp_path):
    ok = make_record(tmp_path, "20260101-000000-a-small-red-tower-abcdef12", prompt="a small red tower\n" + INTERNAL)
    card = gallery_card(ok)
    assert card["request"] == "a small red tower" and card["style"] == "one-line"
    assert card["model"] == "Haiku" and card["cost_usd"] == 0.25
    other = make_record(tmp_path, "20260101-000001-a-small-red-tower-abcdef13", prompt=INTERNAL)
    assert gallery_card(other)["request"] == "" and gallery_card(other)["style"] == ""


def test_style_label_and_override(tmp_path):
    long = "an electric guitar standing upright, with a body, a neck and six strings"
    record = make_record(tmp_path, "20260101-000000-an-electric-guitar-stand-abcdef12", prompt=long, expansion={"request": long})
    assert gallery_card(record)["style"] == "detailed"
    (record / "label.json").write_text(json.dumps({"prompt_style": "one-line"}), encoding="utf-8")
    assert gallery_card(record)["style"] == "one-line"


def test_gallery_skips_records_without_a_picture_and_serves_pictures(served):
    site, port, _ = served
    make_record(site.records_dir, "20260101-000000-a-tower-abcdef12", prompt="a tower")
    make_record(site.records_dir, "20260101-000001-a-house-abcdef13", prompt="a house", picture=None)
    assert [c["id"] for c in gallery(site.records_dir)] == ["20260101-000000-a-tower-abcdef12"]
    status, data = request(port, "GET", "/api/gallery")
    assert status == 200 and b"HARD RULE" not in data
    assert request(port, "GET", "/records/20260101-000000-a-tower-abcdef12/picture") == (200, b"\x89PNG")
    assert request(port, "GET", "/records/20260101-000000-a-tower-abcdef12/manual.ldr")[0] == 404


def test_pages_and_static_files_load_and_never_mention_internals(served):
    site, port, _ = served
    for path in ("/", "/guide", "/static/site.css", "/static/site.js", "/static/fonts/figtree-latin-400-normal.woff2"):
        assert request(port, "GET", path)[0] == 200, path
    pages = b"".join(request(port, "GET", p)[1] for p in ("/", "/guide", "/static/site.js")).lower()
    for word in (b"no-plan", b"no plan", b"meta.json", b"localstorage.setitem(\"voxel.key", b"https://"):
        assert word not in pages, word
    index = request(port, "GET", "/")[1]
    assert b'type="password"' in index and b'autocomplete="off"' in index


# --- Build settings --------------------------------------------------------------


def test_settings_default_and_validation():
    defaults = dict(DEFAULT_SETTINGS)
    assert defaults == {"model": "Opus", "effort": "medium", "max_cost_usd": 2.5, "questions": False}
    assert clean_settings(None, defaults) == defaults
    assert clean_settings({"model": "Haiku", "effort": "high", "max_cost_usd": 1, "questions": True}, defaults) == {
        "model": "Haiku", "effort": "high", "max_cost_usd": 1.0, "questions": True}
    for bad in ({"model": "gpt"}, {"model": "claude-opus-5-5"}, {"effort": "max"}, {"max_cost_usd": 0},
                {"max_cost_usd": 100}, {"max_cost_usd": "2"}, {"max_cost_usd": True}, {"questions": "yes"}):
        with pytest.raises(SiteError):
            clean_settings(bad, defaults)


def test_bad_settings_are_refused_before_anything_runs(served):
    site, port, _ = served
    body = {"key": KEY, "request": "a tower", "sets": ["75192-1"], "settings": {"max_cost_usd": 500}}
    status, data = post_build(port, body)
    assert status == 400 and b"max cost" in data and KEY.encode() not in data
    assert site.job is None


class RecordingRelay:
    """Wraps the real relay and records the spending cap each job is opened with."""

    def __init__(self, relay):
        self.relay, self.caps = relay, []
        self.url = relay.url

    def open(self, token, key, cap):
        self.caps.append(cap)
        self.relay.open(token, key, cap)

    def __getattr__(self, name):
        return getattr(self.relay, name)


def builder_script(answers_seen=None):
    """A fake model that asks one question (when asked to), then finishes at once."""
    state = {"asked": False}

    def reply(body, headers):
        tools = [t.get("name") for t in (body or {}).get("tools") or []]
        if not tools and "Questions" in json.dumps(body) and not state["asked"]:
            state["asked"] = True
            question = {"id": "shape", "text": "Which shape?", "kind": "other",
                        "options": ["Square", "Round"], "default": "Square"}
            return 200, text(json.dumps({"questions": [question]}))
        if not tools:
            return 200, text("{}")  # the review: unusable, so it is skipped
        return 200, text("done")

    return reply


def test_settings_reach_the_builder_the_relay_and_the_record(served, upstream, tmp_path):
    site, port, _ = served
    site.relay = RecordingRelay(site.relay)
    upstream.reply = builder_script()
    chosen = {"model": "Sonnet", "effort": "low", "max_cost_usd": 1.0, "questions": False}
    status, data = post_build(port, {"key": KEY, "request": "a tower", "sets": ["75192-1"], "settings": chosen})
    assert status == 202 and json.loads(data)["settings"] == chosen
    job = wait_for(site)

    assert site.relay.caps == [spend_cap(1.0)]  # the cap follows the chosen max cost
    models = {c["body"]["model"] for c in upstream.calls if c["body"].get("tools")}
    assert models == {"claude-sonnet-5-5"}
    assert not any("Questions" in json.dumps(c["body"]) for c in upstream.calls)  # questions off: no call
    assert job.public()["settings"] == chosen

    (record,) = [p for p in site.records_dir.iterdir() if p.is_dir()]
    saved = json.loads((record / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"request": "a tower", "model": "claude-sonnet-5-5", "effort": "low",
                     "max_cost_usd": 1.0, "questions": False}
    assert KEY not in json.dumps(saved)
    (record / "out").mkdir(exist_ok=True)
    (record / "out" / "build.png").write_bytes(b"\x89PNG")
    card = gallery_card(record)
    assert card["model"] == "Sonnet" and card["effort"] == "low"
    assert card["max_cost_usd"] == 1.0 and card["questions"] is False and card["request"] == "a tower"


def test_default_settings_build_with_opus_and_a_2_50_cap(served, upstream):
    site, port, _ = served
    site.relay = RecordingRelay(site.relay)
    site.defaults = dict(DEFAULT_SETTINGS)
    upstream.reply = builder_script()
    assert post_build(port, {"key": KEY, "request": "a tower", "sets": ["75192-1"]})[0] == 202
    wait_for(site)
    assert site.relay.caps == [spend_cap(2.5)]
    assert {c["body"]["model"] for c in upstream.calls if c["body"].get("tools")} == {"claude-opus-5-5"}


def test_questions_are_asked_on_the_page_and_answers_reach_the_build(served, upstream):
    site, port, _ = served
    upstream.reply = builder_script()
    chosen = {"model": "Haiku", "effort": "medium", "max_cost_usd": 1.0, "questions": True}
    status, data = post_build(port, {"key": KEY, "request": "a tower", "sets": ["75192-1"], "settings": chosen})
    job_id = json.loads(data)["id"]

    end = time.time() + 120
    while time.time() < end and site.job.state != "asking":
        assert site.job.state == "building", site.job.public()
        time.sleep(0.2)
    status, data = request(port, "GET", f"/api/build/{job_id}")
    shown = json.loads(data)
    assert shown["state"] == "asking"
    assert shown["questions"] == [{"id": "shape", "text": "Which shape?", "options": ["Square", "Round"], "default": "Square"}]

    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    assert request(port, "POST", "/api/build/0000/answers", {"answers": {}}, headers)[0] == 404
    status, _ = request(port, "POST", f"/api/build/{job_id}/answers", {"answers": {"shape": "Round", "other": "x"}}, headers)
    assert status == 200
    assert request(port, "POST", f"/api/build/{job_id}/answers", {"answers": {}}, headers)[0] == 409
    job = wait_for(site)

    (record,) = [p for p in site.records_dir.iterdir() if p.is_dir()]
    expansion = json.loads((record / "expansion.json").read_text(encoding="utf-8"))
    assert expansion["answers"][0]["answer"] == "Round" and expansion["answers"][0]["typed"] is True
    assert json.loads((record / "settings.json").read_text(encoding="utf-8"))["questions"] is True
    for path, content in all_files(record).items():
        assert KEY.encode() not in content, path
    assert job.public()["settings"]["questions"] is True


def test_clean_questions_keeps_only_what_the_page_shows():
    raw = [{"id": "a", "text": "Q?", "kind": "color", "options": ["RED", {"x": 1}], "default": "RED", "extra": "y"},
           {"text": "no id"}, "junk"]
    assert clean_questions(raw) == [{"id": "a", "text": "Q?", "options": ["RED"], "default": "RED"}]
    assert clean_questions("junk") == []


def test_old_records_show_the_settings_they_recorded(tmp_path):
    record = make_record(
        tmp_path, "20260101-000000-make-me-a-guitar-abcdef12", prompt="make me a guitar",
        expansion={"request": "make me a guitar", "answers": [{"id": "c", "answer": "RED", "typed": False}]},
        usage={"build_model": "claude-opus-5-5", "build_effort": "medium", "max_cost_usd": 2.5},
    )
    card = gallery_card(record)
    assert (card["effort"], card["max_cost_usd"], card["questions"]) == ("medium", 2.5, None)
    bare = make_record(tmp_path, "20260101-000001-a-tower-abcdef13", prompt="a tower")
    card = gallery_card(bare)
    assert (card["effort"], card["max_cost_usd"], card["questions"]) == ("", None, None)
