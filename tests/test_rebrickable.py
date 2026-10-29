import io
import json
import urllib.error

import bricklayer as bl
import pytest

from voxel import check_build, load_inventory
from voxel import rebrickable as rb

RED = bl.Color.RED.ldraw_id


def row(part_num="3001", ldraw=("3001",), color="Red", color_codes=(4,), qty=2, spare=False):
    """A fake Rebrickable row shaped like the real response."""
    part_ids = {"LDraw": list(ldraw)} if ldraw else {}
    color_ids = (
        {"LDraw": {"ext_ids": list(color_codes), "ext_descrs": [[color]]}} if color_codes else {}
    )
    return {
        "part": {"part_num": part_num, "name": f"Part {part_num}", "external_ids": part_ids},
        "color": {"name": color, "external_ids": color_ids},
        "quantity": qty,
        "is_spare": spare,
    }


def fake_api(pages_by_set):
    """A get_json stand-in that serves canned pages and counts calls."""
    calls = []

    def get_json(url, key, what):
        calls.append(url)
        for set_num, pages in pages_by_set.items():
            if f"/sets/{set_num}/parts/" in url:
                return pages[0]
            if url == f"page2-{set_num}":
                return pages[1]
        raise AssertionError(f"unexpected url {url}")

    get_json.calls = calls
    return get_json


def single(rows):
    return {"count": len(rows), "next": None, "results": rows}


def test_set_number_gets_version_suffix():
    assert rb.normalize_set_number("75869") == "75869-1"
    assert rb.normalize_set_number(" 10242 ") == "10242-1"
    assert rb.normalize_set_number("75869-2") == "75869-2"
    with pytest.raises(ValueError):
        rb.normalize_set_number("not a set!")


def test_spares_count_as_owned_and_rows_merge():
    rows = [row(qty=2), row(qty=3, spare=True)]
    converted = rb.convert_rows(rows, "10242-1")
    assert converted.counts[("3001", RED)] == 5
    assert converted.spare_pieces == 3


def test_color_picks_first_supported_code():
    # Rebrickable returned [0, 256] for black: normal black, then a rubber variant.
    rows = [row(part_num="53451", ldraw=("3001",), color="Black", color_codes=(0, 256), qty=4)]
    converted = rb.convert_rows(rows, "10242-1")
    assert converted.counts[("3001", 0)] == 4


def test_unusable_parts_are_reported_with_reasons():
    rows = [
        row(part_num="no-ldraw", ldraw=()),
        row(part_num="ghost", ldraw=("99999999",)),
        row(part_num="oddcolor", color_codes=(9999,)),
        row(part_num="nocolor", color_codes=()),
        row(qty=7),
    ]
    converted = rb.convert_rows(rows, "10242-1")
    reasons = {u.part_num: u.reason for u in converted.unusable}
    assert "no LDraw id" in reasons["no-ldraw"]
    assert "not in BrickLayer's catalog" in reasons["ghost"]
    assert "not supported" in reasons["oddcolor"]
    assert "no LDraw color" in reasons["nocolor"]
    assert converted.counts[("3001", RED)] == 7
    assert len(converted.unusable) == 4


def test_two_sets_are_added_together(tmp_path):
    api = fake_api(
        {
            "10242-1": [single([row(qty=3), row(ldraw=("3003",), qty=1)])],
            "75869-1": [single([row(qty=4)])],
        }
    )
    result = rb.build_inventory(
        ["10242", "75869"], "key", cache_dir=tmp_path, get_json=api, sleep=lambda s: None
    )
    assert result.inventory.owned(("3001", RED)) == 7
    assert result.inventory.owned(("3003", RED)) == 1


def test_typing_a_set_twice_means_two_copies(tmp_path):
    api = fake_api({"10242-1": [single([row(qty=3)])]})
    result = rb.build_inventory(
        ["10242", "10242-1"], "key", cache_dir=tmp_path, get_json=api, sleep=lambda s: None
    )
    assert result.inventory.owned(("3001", RED)) == 6
    assert len(api.calls) == 1


def test_cache_avoids_a_second_request(tmp_path):
    api = fake_api({"10242-1": [single([row(qty=3)])]})
    kwargs = dict(cache_dir=tmp_path, get_json=api, sleep=lambda s: None)
    rb.build_inventory(["10242"], "key", **kwargs)
    rb.build_inventory(["10242"], "key", **kwargs)
    assert len(api.calls) == 1
    rb.build_inventory(["10242"], "key", refresh=True, **kwargs)
    assert len(api.calls) == 2


def test_follows_next_page(tmp_path):
    page1 = {"count": 2, "next": "page2-10242-1", "results": [row(qty=1)]}
    page2 = single([row(ldraw=("3003",), qty=2)])
    api = fake_api({"10242-1": [page1, page2]})
    rows, _ = rb.fetch_set_rows("10242-1", "key", cache_dir=tmp_path, get_json=api)
    assert len(rows) == 2


def test_written_inventory_works_with_the_checker(tmp_path):
    api = fake_api({"10242-1": [single([row(qty=2), row(ldraw=("3003",), qty=5)])]})
    result = rb.build_inventory(
        ["10242"], "key", cache_dir=tmp_path / "c", get_json=api, sleep=lambda s: None
    )
    out, bad = tmp_path / "inventory.json", tmp_path / "unusable.json"
    rb.write_outputs(result, out, bad)
    inventory = load_inventory(out)
    assert inventory.owned(("3001", RED)) == 2

    build = bl.LegoBuild()
    last = build.add(bl.Element("3001", bl.Color.RED))
    for _ in range(2):
        last = build.stack(bl.Element("3001", bl.Color.RED), on=last)
    assert not check_build(build, inventory).ok  # 3 used, 2 owned


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self, *args):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(b"body"))


def test_slow_down_response_is_retried(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise http_error(429)
        return FakeResponse({"ok": True})

    monkeypatch.setattr(rb.urllib.request, "urlopen", fake_urlopen)
    waits = []
    data = rb.http_get_json("https://x.test/", "key", "set X", sleep=waits.append)
    assert data == {"ok": True}
    assert len(attempts) == 3 and waits == [2, 4]


def test_clear_errors_for_bad_key_and_unknown_set(monkeypatch):
    def raising(code):
        def fake_urlopen(request, timeout=None):
            raise http_error(code)

        return fake_urlopen

    monkeypatch.setattr(rb.urllib.request, "urlopen", raising(401))
    with pytest.raises(rb.RebrickableError, match="rejected the API key"):
        rb.http_get_json("https://x.test/", "key", "set X")
    monkeypatch.setattr(rb.urllib.request, "urlopen", raising(404))
    with pytest.raises(rb.RebrickableError, match="no set 99999-1"):
        rb.http_get_json("https://x.test/", "key", "set 99999-1")


def test_missing_key_gives_a_helpful_message(tmp_path, monkeypatch):
    monkeypatch.delenv("REBRICKABLE_API_KEY", raising=False)
    with pytest.raises(rb.RebrickableError, match="No API key"):
        rb.read_api_key(tmp_path / "nope.env")
    env = tmp_path / ".env"
    env.write_text('REBRICKABLE_API_KEY="abc123"\n', encoding="utf-8")
    assert rb.read_api_key(env) == "abc123"


def test_command_line_end_to_end(tmp_path, monkeypatch, capsys):
    api = fake_api({"10242-1": [single([row(qty=3), row(ldraw=(), qty=9)])]})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REBRICKABLE_API_KEY", "key")
    monkeypatch.setattr(rb, "http_get_json", api)
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    assert rb.main(["10242"]) == 0
    printed = capsys.readouterr().out
    assert "Usable pieces: 3" in printed
    assert "Owned but unusable: 9 pieces" in printed
    assert (tmp_path / "inventory.json").exists()
    assert (tmp_path / "inventory.unusable.json").exists()
    assert rb.main(["not a set!"]) == 1