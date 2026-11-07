import bricklayer as bl
from brickwright import TestContext
from voxel import check_owned

P = bl.PLATE
C = bl.Color

# ---------------------------------------------------------------------------
# Guitar is designed lying face-up in its own frame (x = width, y = length,
# body at y=0, headstock at high y), then rotated to stand upright.
# ---------------------------------------------------------------------------

BODY_ROWS = {
    0: [(-3, 3)],
    1: [(-5, 5)],
    2: [(-6, 6)], 3: [(-6, 6)], 4: [(-6, 6)], 5: [(-6, 6)], 6: [(-6, 6)],
    7: [(-5, 5)], 8: [(-5, 5)], 9: [(-5, 5)],
    10: [(-6, 6)], 11: [(-6, 6)],
    12: [(-6, -2), (2, 6)],
    13: [(-6, -3), (3, 6)],
    14: [(-5, -3), (3, 6)],
    15: [(3, 5)],
}

RED_PLATES = {12: "60479", 8: "3460", 6: "3666", 4: "3710", 3: "3623", 2: "28653", 1: "3024"}
RED_PLATE_INV = {12: 12, 8: 9, 6: 3, 4: 6, 3: 3, 2: 27, 1: 7}
RED_TILES = {8: "4162", 4: "2431", 3: "63864", 1: "3070b"}
RED_TILE_INV = {8: 6, 4: 18, 3: 3, 1: 19}


def body_cells():
    cells = set()
    for y, spans in BODY_ROWS.items():
        for a, b in spans:
            for x in range(a, b):
                cells.add((x, y))
    return cells


def split_run(length, inv):
    """Pick piece lengths (from inventory) summing to length, fewest pieces."""
    sizes = sorted(inv, reverse=True)
    best = None

    def rec(rem, used, start):
        nonlocal best
        if rem == 0:
            if best is None or len(used) < len(best):
                best = list(used)
            return
        if best is not None and len(used) + 1 >= len(best):
            return
        for i in range(start, len(sizes)):
            s = sizes[i]
            if s <= rem and inv[s] - used.count(s) > 0:
                used.append(s)
                rec(rem - s, used, i)
                used.pop()

    rec(length, [], 0)
    if best is None:
        raise ValueError(f"cannot split run {length} with {inv}")
    for s in best:
        inv[s] -= 1
    return best


def runs(cells_line):
    """Split a sorted list of ints into contiguous runs [(start, length)]."""
    out = []
    for v in sorted(cells_line):
        if out and out[-1][0] + out[-1][1] == v:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((v, 1))
    return out


def build_guitar():
    g = bl.LegoBuild()
    cells = body_cells()
    plate_inv = dict(RED_PLATE_INV)

    # ---- body layer 1: rows along x --------------------------------------
    g.add(bl.Element("3832", C.RED), (-5, 7, 0))  # 2x10 covering rows 7-8
    for y in sorted(BODY_ROWS):
        if y in (7, 8):
            continue
        for a, b in BODY_ROWS[y]:
            x = a
            for s in split_run(b - a, plate_inv):
                g.add(bl.Element(RED_PLATES[s], C.RED), (x, y, 0))
                x += s

    # ---- body layer 2: columns along y -----------------------------------
    cols = {}
    for (x, y) in cells:
        cols.setdefault(x, []).append(y)
    col_runs = []
    for x, ys in cols.items():
        for start, ln in runs(ys):
            col_runs.append((ln, x, start))
    col_runs.sort(reverse=True)
    for ln, x, start in col_runs:
        y = start
        pieces = sorted(split_run(ln, plate_inv), reverse=True)
        if len(pieces) >= 3:
            pieces = [pieces[0]] + sorted(pieces[2:]) + [pieces[1]]
        for s in pieces:
            g.add(bl.Element(RED_PLATES[s], C.RED), (x, y, P), bl.Rotation.DEG_90)
            y += s

    # ---- body top (layer 3): pickguard, pickups, bridge, knobs -----------
    special = set()
    z3 = 2 * P
    # pickguard (white) x[-3,3) y[3,10) with three black single-coil pickups
    for y in (3, 5, 7, 9):
        g.add(bl.Element("2431", C.WHITE), (-2, y, z3))
    for y in (4, 6, 8):
        g.add(bl.Element("2431", C.BLACK), (-2, y, z3))
    g.add(bl.Element("2431", C.WHITE), (-3, 3, z3), bl.Rotation.DEG_90)
    for y in (7, 8, 9):
        g.add(bl.Element("3070b", C.WHITE), (-3, y, z3))
    g.add(bl.Element("2431", C.WHITE), (2, 6, z3), bl.Rotation.DEG_90)
    for y in (3, 4, 5):
        g.add(bl.Element("3070b", C.WHITE), (2, y, z3))
    for x in range(-3, 3):
        for y in range(3, 10):
            special.add((x, y))
    # bridge
    g.add(bl.Element("2431", C.LIGHT_BLUISH_GRAY), (-2, 2, z3))
    for x in range(-2, 2):
        special.add((x, 2))
    # volume/tone knobs (two-high white round plates) and selector switch
    for (x, y) in [(-4, 3), (-4, 5), (-4, 7)]:
        g.add(bl.Element("4073", C.WHITE), (x, y, z3))
        g.add(bl.Element("4073", C.WHITE), (x, y, z3 + P))
    g.add(bl.Element("4073", C.LIGHT_BLUISH_GRAY), (-4, 9, z3))  # selector

    # remaining body surface: red tiles (rows R, columns V), round corners
    V = bl.Rotation.DEG_90
    red_tiles = [
        # (length, x, y, vertical)
        (4, -2, 0, False), (8, -4, 1, False),
        (4, -6, 3, True), (8, -5, 2, True), (8, 3, 2, True), (8, 4, 2, True),
        (4, 5, 3, True),
        (4, -6, 10, False), (4, 2, 10, False),
        (4, -6, 11, False), (4, 2, 11, False),
        (4, -6, 12, False),
        (4, 3, 12, True), (4, 4, 12, True), (3, 5, 12, True),
        (3, -6, 13, False),
    ]
    for ln, x, y, vert in red_tiles:
        g.add(bl.Element(RED_TILES[ln], C.RED), (x, y, z3), V if vert else bl.Rotation.DEG_0)
    singles = [(-4, 2), (-4, 4), (-4, 6), (-4, 8), (-6, 2),
               (-2, 10), (1, 10), (-2, 11), (1, 11), (-4, 14), (2, 12)]
    for x, y in singles:
        g.add(bl.Element("3070b", C.RED), (x, y, z3))
    for x, y in [(-3, 0), (2, 0), (-5, 1), (4, 1), (5, 2), (-5, 14)]:
        g.add(bl.Element("98138", C.RED), (x, y, z3))

    # ---- neck ------------------------------------------------------------
    g.add(bl.Element("91988", C.TAN), (-1, 10, z3), bl.Rotation.DEG_90, name="neck")
    for x in (-1, 0):
        for y in (10, 11):
            g.add(bl.Element("3070b", C.TAN), (x, y, 3 * P))
    # fretboard (dark) with frets and nut
    g.add(bl.Element("2445", C.BLACK), (-1, 12, 3 * P), bl.Rotation.DEG_90, name="fretboard")
    zf = 4 * P
    g.add(bl.Element("3069b", C.BLACK), (-1, 12, zf))
    g.add(bl.Element("3069b", C.BLACK), (-1, 13, zf))
    for y in (14, 17, 20):
        g.add(bl.Element("3070b", C.LIGHT_BLUISH_GRAY), (-1, y, zf))
        g.add(bl.Element("3070b", C.LIGHT_BLUISH_GRAY), (0, y, zf))
    for y in (15, 18, 21):
        g.add(bl.Element("3068b", C.BLACK), (-1, y, zf))
    for x in (-1, 0):
        g.add(bl.Element("3070b", C.TAN), (x, 23, zf))  # nut

    # ---- headstock -------------------------------------------------------
    for x in range(-1, 3):
        g.add(bl.Element("3460", C.TAN), (x, 21, P), bl.Rotation.DEG_90)
    for x in (1, 2):
        g.add(bl.Element("3623", C.TAN), (x, 21, z3), bl.Rotation.DEG_90)
    for y in range(24, 29):
        g.add(bl.Element("3710", C.TAN), (-1, y, z3))
    # six-in-line tuners with keys sticking out sideways
    for y in range(23, 29):
        g.add(bl.Element("32828", C.LIGHT_BLUISH_GRAY), (2, y, 3 * P))
    return g


def create_build():
    build = bl.LegoBuild()
    # ---- stand -----------------------------------------------------------
    for x in (-2, 0):
        build.add(bl.Element("3020", C.BLACK), (x, -2, 0), bl.Rotation.DEG_90)
        build.add(bl.Element("3068b", C.DARK_BLUISH_GRAY), (x, -2, P))
    build.add(bl.Element("28653", C.BLACK), (-1, 0, P))
    bracket = build.add(bl.Element("99207", C.BLACK), (-1, 0, 2 * P), name="bracket")

    guitar = build_guitar()
    src = guitar.find_connector("antistud", toward=bl.Position(0.5, 0.5, 0))
    dst = bracket.find_connector("open_stud", toward=bl.Position(0.5, -1, 0))
    build.connect(
        guitar, src, to=dst,
        target_rotation=bl.rotation_about(bl.Direction.LEFT.value, 90),
        name="guitar",
    )
    # turn the whole display so the guitar's face looks toward the viewer
    outer = bl.LegoBuild()
    outer.add(build, (0, 0, 0), bl.Rotation.DEG_180, name="display")
    return outer


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    check_owned(tests)
    lo, hi = build.bounding_box
    tests.check("guitar stands tall", hi.z - lo.z > 20, f"height {hi.z - lo.z:.1f} bricks")
    return tests.report()
