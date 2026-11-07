import bricklayer as bl

from voxel import design as dz
from voxel.steps import STEPS_CHECK_NAME, check_steps

RED = bl.Color.RED
BRICK_HEIGHT = 1  # z is in brick heights


class FakeTests:
    def __init__(self, build):
        self.build = build
        self.results = []

    def check(self, name, ok, detail=None):
        self.results.append((name, ok, detail))


def stack_build(levels):
    """One 3001 per level at (2,2,level), with a step marker after each brick."""
    build = bl.LegoBuild()
    for level in levels:
        build.add(bl.Element("3001", RED), (2, 2, level * BRICK_HEIGHT))
        build.add_step()
    return build


def run(build):
    tests = FakeTests(build)
    check_steps(tests)
    [(name, ok, detail)] = tests.results
    assert name == STEPS_CHECK_NAME
    return ok, detail


def test_stack_in_order_passes():
    build = stack_build([0, 1, 2])
    assert build.is_valid()
    assert run(build) == (True, None)


def test_top_first_stack_fails_at_step_1():
    # Same final build, so BrickWright's whole-build checks pass. Step 1 holds
    # only the top brick: grounded against its own lowest point, floating
    # against the full build's ground.
    build = stack_build([2, 1, 0])
    assert build.is_valid()
    ok, detail = run(build)
    assert not ok
    lines = detail.splitlines()
    assert lines[0].startswith("step 1: Red 3001 at (2,2,") and lines[0].endswith(") floats")
    assert lines[1].startswith("step 2: Red 3001 at (2,2,")
    assert len(lines) == 2  # the step 1 brick is not reported again at step 2


def test_no_step_markers_fails():
    build = bl.LegoBuild()
    build.add(bl.Element("3001", RED))
    ok, detail = run(build)
    assert not ok and "no step markers" in detail


def test_step_with_13_parts_fails():
    build = bl.LegoBuild()
    for i in range(13):
        build.add(bl.Element("3001", RED), (i * 4, 0, 0))
    build.add_step()
    ok, detail = run(build)
    assert not ok and detail == "step 1: 13 parts (max 12)"


def test_trailing_parts_after_last_marker_are_a_step():
    build = stack_build([0])
    build.add(bl.Element("3001", RED), (2, 2, 5 * BRICK_HEIGHT))
    ok, detail = run(build)
    assert not ok and detail.startswith("step 2: Red 3001") and detail.endswith("floats")


def test_require_owned_check_adds_both_once(monkeypatch):
    monkeypatch.setenv(dz.REQUIRED_CHECKS_ENV, "other:check")
    dz.require_owned_check()
    dz.require_owned_check()
    assert dz.REQUIRED_CHECKS_ENV and dz.os.environ[dz.REQUIRED_CHECKS_ENV] == (
        f"other:check,{dz.OWNED_CHECK},{dz.STEPS_CHECK}"
    )


def test_required_check_runs_through_brickwright(tmp_path, monkeypatch):
    from brickwright.tools.validation import build_and_validate

    src = tmp_path / "build.py"
    src.write_text(
        "import bricklayer as bl\n"
        "def create_build():\n"
        "    b = bl.LegoBuild()\n"
        "    b.add(bl.Element('3001', bl.Color.RED))\n"
        "    return b\n"
        "def run_tests():\n"
        "    return []\n"
    )
    monkeypatch.setenv(dz.REQUIRED_CHECKS_ENV, dz.STEPS_CHECK)
    _, report = build_and_validate(str(src))
    text = str(report)
    assert STEPS_CHECK_NAME in text and "no step markers" in text
