import bricklayer as bl

from brickwright import TestContext
from voxel import check_owned

DG = bl.Color.DARK_GREEN


def create_build():
    """Return the finished :class:`bricklayer.LegoBuild`."""
    build = bl.LegoBuild()

    # Base, layer 1 (z=0): four 1x4 bricks running along x, forming a 4x4 footing.
    for y in range(4):
        build.add(bl.Element("3010", DG), (0, y, 0), bl.Rotation.DEG_0)

    # Base, layer 2 (z=1 brick): four 1x4 bricks running along y, crossing the layer below.
    for x in range(4):
        build.add(bl.Element("3010", DG), (x, 0, 1), bl.Rotation.DEG_90)

    # Upper tower, layer 3 (z=2 bricks): two 1x2 bricks along x on the centre 2x2.
    build.add(bl.Element("3004", DG), (1, 1, 2), bl.Rotation.DEG_0)
    build.add(bl.Element("3004", DG), (1, 2, 2), bl.Rotation.DEG_0)

    # Upper tower, layer 4 (z=3 bricks): two 1x2 bricks along y, staggered over layer 3.
    build.add(bl.Element("3004", DG), (1, 1, 3), bl.Rotation.DEG_90)
    build.add(bl.Element("3004", DG), (2, 1, 3), bl.Rotation.DEG_90)

    # Corner battlements on the base ledge (z=2 bricks).
    for x, y in [(0, 0), (3, 0), (0, 3), (3, 3)]:
        build.add(bl.Element("3005", DG), (x, y, 2), bl.Rotation.DEG_0)

    # Roof cap: a 2x2 dark green tile on top of the upper tower.
    build.add(bl.Element("3068b", DG), (1, 1, 4), bl.Rotation.DEG_0)

    return build


def run_tests():
    """Create the build and return a report of which checks passed."""
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    check_owned(tests)
    tests.check(
        "all parts dark green",
        all(p.content.color == DG for p in build.flattened_placements),
    )
    tests.check("small tower under 30 parts", build.brick_count <= 30)
    return tests.report()
