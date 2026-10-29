import bricklayer as bl

from brickwright import TestContext
from voxel import check_owned


def create_build():
    """Return the finished :class:`bricklayer.LegoBuild`.

    A small dark green crenellated tower: a hollow 4x4-stud shell of four
    staggered brick layers, closed by a plate roof with alternating 1x1 merlons.
    Positions are (x studs, y studs, z bricks); z is converted from plates.
    """
    build = bl.LegoBuild()
    green = bl.Color.DARK_GREEN

    def el(part_id):
        return bl.Element(part_id, green)

    def z(plates):
        return bl.to_bricks(plates)

    d0 = bl.Rotation.DEG_0
    d90 = bl.Rotation.DEG_90

    # Walls: one brick per layer, at z = 0, 1, 2, 3 bricks (0, 3, 6, 9 plates).
    # Even layers: long bricks run along x at front/back, short bricks at the sides.
    # Odd layers: long bricks run along y at the sides, short bricks at front/back.
    # Each layer is a 4x4 ring with a 2x2 open interior.

    # Layer 0 (ground)
    build.add(el("3010"), (0, 0, z(0)), d0)
    build.add(el("3010"), (0, 3, z(0)), d0)
    build.add(el("3004"), (0, 1, z(0)), d90)
    build.add(el("3004"), (3, 1, z(0)), d90)

    # Layer 1 (staggered)
    build.add(el("3010"), (0, 0, z(3)), d90)
    build.add(el("3010"), (3, 0, z(3)), d90)
    build.add(el("3004"), (1, 0, z(3)), d0)
    build.add(el("3004"), (1, 3, z(3)), d0)

    # Layer 2
    build.add(el("3010"), (0, 0, z(6)), d0)
    build.add(el("3010"), (0, 3, z(6)), d0)
    build.add(el("3004"), (0, 1, z(6)), d90)
    build.add(el("3005"), (3, 1, z(6)), d0)
    build.add(el("3005"), (3, 2, z(6)), d0)

    # Layer 3
    build.add(el("3010"), (0, 0, z(9)), d90)
    build.add(el("3010"), (3, 0, z(9)), d90)
    build.add(el("3004"), (1, 0, z(9)), d0)
    build.add(el("3005"), (1, 3, z(9)), d0)
    build.add(el("3005"), (2, 3, z(9)), d0)

    # Roof: two 2x4 plates covering the 4x4 top at 12 plates (4 bricks).
    build.add(el("3020"), (0, 0, z(12)), d90)
    build.add(el("3020"), (2, 0, z(12)), d90)

    # Crenellations: alternating 1x1 merlons around the perimeter, on the roof studs.
    for x, y in [(0, 0), (2, 0), (3, 1), (3, 3), (1, 3), (0, 2)]:
        build.add(el("3005"), (x, y, z(13)), d0)

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
        all(
            p.content.color == bl.Color.DARK_GREEN
            for p in build.flattened_placements
        ),
    )
    tests.check("small tower (under 40 parts)", build.brick_count < 40)
    tests.check(
        "tower is taller than wide",
        build.height > build.width and build.height > build.depth,
    )
    return tests.report()
