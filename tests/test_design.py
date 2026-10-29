import json
from types import SimpleNamespace

import bricklayer as bl
import pytest

from voxel import Inventory, design as dz

RED = bl.Color.RED


def make_inventory(tmp_path, count=2):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps([{"part": "3001", "color": "RED", "count": count}]), encoding="utf-8")
    return path


def tower(n):
    build = bl.LegoBuild()
    last = build.add(bl.Element("3001", RED))
    for _ in range(n - 1):
        last = build.stack(bl.Element("3001", RED), on=last)
    return build


class FakeTests:
    def __init__(self, build):
        self.build = build
        self.results = []

    def check(self, name, ok, detail=None):
        self.results.append((name, ok, detail))


def test_prompt_lists_parts_and_rule(tmp_path):
    inv = Inventory({("3001", RED.ldraw_id): 5})
    text = dz.build_prompt("a tower", inv)
    assert text.startswith("a tower")
    assert "3001 | Brick 2 x 4 |" in text and "| 5" in text
    assert "check_owned(tests)" in text and "ONLY" in text


def test_check_owned_passes_and_fails(tmp_path):
    path = make_inventory(tmp_path, count=2)
    good = FakeTests(tower(2))
    dz.check_owned(good, path)
    assert good.results == [("only_owned_parts", True, None)]
    bad = FakeTests(tower(3))
    dz.check_owned(bad, path)
    name, ok, detail = bad.results[0]
    assert not ok and "used 3, owned 2" in detail


def test_check_owned_uses_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv(dz.INVENTORY_ENV, str(make_inventory(tmp_path)))
    t = FakeTests(tower(1))
    dz.check_owned(t)
    assert t.results[0][1] is True
    monkeypatch.delenv(dz.INVENTORY_ENV)
    t = FakeTests(tower(1))
    dz.check_owned(t)
    assert t.results[0][1] is False


BUILD_SRC = """
import bricklayer as bl
def create_build():
    b = bl.LegoBuild()
    last = b.add(bl.Element("3001", bl.Color.RED))
    for _ in range({n} - 1):
        last = b.stack(bl.Element("3001", bl.Color.RED), on=last)
    return b
"""


def test_final_gate_on_real_build_files(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    ok_file, bad_file, broken = (tmp_path / n for n in ("ok.py", "bad.py", "broken.py"))
    ok_file.write_text(BUILD_SRC.format(n=2))
    bad_file.write_text(BUILD_SRC.format(n=3))
    broken.write_text("def create_build():\n    raise ValueError('boom')\n")
    assert dz.final_gate(ok_file, inv)[0] is True
    ok, report = dz.final_gate(bad_file, inv)
    assert not ok and "used 3, owned 2" in report
    ok, report = dz.final_gate(broken, inv)
    assert not ok and "boom" in report


def fake_run_factory(sizes, costs=0.1):
    """A stand-in agent: each call writes a tower of the next size."""
    calls = []

    def run(record, config):
        n = sizes[len(calls)]
        record.write_build(BUILD_SRC.format(n=n))
        calls.append(record.id)
        return SimpleNamespace(usage=SimpleNamespace(cost_usd=costs))

    run.calls = calls
    return run


def test_outer_loop_retries_then_passes(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    run = fake_run_factory([3, 2])
    result = dz.design("a tower", inv, records_dir=tmp_path / "rec", run=run)
    assert result.ok and result.rounds == 2
    assert result.cost_usd == pytest.approx(0.2)
    assert len(set(run.calls)) == 2


def test_outer_loop_gives_up_after_retries(tmp_path):
    inv = make_inventory(tmp_path, count=2)
    run = fake_run_factory([3, 3, 3])
    result = dz.design("a tower", inv, records_dir=tmp_path / "rec", run=run, max_retries=2)
    assert not result.ok and result.rounds == 3
    assert "used 3, owned 2" in result.report


def test_agent_import_line_works():
    from voxel import check_owned

    assert check_owned is dz.check_owned
