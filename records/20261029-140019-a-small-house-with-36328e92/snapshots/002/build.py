import bricklayer as bl

from brickwright import TestContext
from voxel import check_owned

# Small house: 6 x 4 studs, three brick courses in running bond, gable roof.
# Courses alternate: long front/back walls (1x6) on odd courses, long side walls
# (two 1x2 each) on the middle course, so every course locks to the one below.
WALL = bl.Color.REDDISH_BROWN
ROOF = bl.Color.WHITE
WALL_Z = (0, 1, 2)  # brick levels of the three wall courses
ROOF_Z = 3  # roof sits on top of the walls


def create_build():
    """Return the finished :class:`bricklayer.LegoBuild`."""
    build = bl.LegoBuild()

    # Course 1 and 3: long front and back walls, short side walls between them.
    for z in (WALL_Z[0], WALL_Z[2]):
        build.add(bl.Element("3009", WALL), (0, 0, z))  # front, x 0..6
        build.add(bl.Element("3009", WALL), (0, 3, z))  # back, x 0..6
        build.add(bl.Element("3004", WALL), (0, 1, z), rotation=bl.Rotation.DEG_90)
        build.add(bl.Element("3004", WALL), (5, 1, z), rotation=bl.Rotation.DEG_90)

    # Course 2: long side walls (two 1x2 each), short front/back walls between.
    z = WALL_Z[1]
    for y in (0, 2):
        build.add(bl.Element("3004", WALL), (0, y, z), rotation=bl.Rotation.DEG_90)
        build.add(bl.Element("3004", WALL), (5, y, z), rotation=bl.Rotation.DEG_90)
    for x in (1, 3):
        build.add(bl.Element("3004", WALL), (x, 0, z))
        build.add(bl.Element("3004", WALL), (x, 3, z))

    # Gable roof: front slope (high edge at the ridge, y = 2) and the mirrored
    # back slope, each three 2x2 curved slopes wide.
    for x in (0, 2, 4):
        build.add(bl.Element("15068", ROOF), (x, 0, ROOF_Z))
        build.add(bl.Element("15068", ROOF), (x, 2, ROOF_Z), rotation=bl.Rotation.DEG_180)

    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    check_owned(tests)
    roof_top = build.bounding_box[1].z
    tests.check("roof rises above walls", roof_top > 3.0, f"top={roof_top:.2f} bricks")
    return tests.report()
