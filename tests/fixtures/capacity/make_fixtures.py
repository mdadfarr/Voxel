"""Write tests/fixtures/capacity/*.json: the saved plans, restated with capacity fields."""
import json, sys
from pathlib import Path
OUT = Path(sys.argv[1])
G, R, K, W, A = "LIGHT_BLUISH_GRAY", "RED", "BLACK", "WHITE", "ANY"

def sec(name, said, x, y, z, w, d, h, cells, fill="solid", wall=1, parts=None):
    layers = round(h * 3)
    area = w * d - ((w - 2 * wall) * (d - 2 * wall) if fill == "shell" and 2 * wall < min(w, d) else 0)
    empty = area * layers - sum(cells.values())
    assert empty >= 0, (name, area * layers, cells)
    s = {"name": name, "plan_text": said, "size": {"width": w, "depth": d, "height": h},
         "position": {"x": x, "y": y, "z": z}, "fill": fill}
    if fill == "shell":
        s["wall"] = wall
    s["cells"] = [{"color": c, "cells": n} for c, n in cells.items()]
    s["empty"] = empty
    if parts:
        s["parts"] = [{"part": p, "color": c, "count": n} for p, c, n in parts]
    return s

def write(name, source, note, sections):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"source": source, "note": note, "sections": sections}, indent=2) + "\n")

# --- guitar -------------------------------------------------------------------
write("guitar_1c64df97", "runs/20261108-203101-make-me-a-guitar-1c64df97/plan.json",
  "Built and passed review 8 of 8. Body outline: 129 cells (from the plan's outline_rows).", [
  sec("body lower layers", "L0 red plates, L1 red plates (black at x 5..7 y 8..12 where the neck spine enters)", 0, 0, 0, 12, 14, 0.667, {R: 250}),
  sec("body top layer", "L2 red tiles except where pickguard/bridge go; pickguard 65 cells white; bridge red plate 1x4", 0, 0, 0.667, 12, 14, 0.333, {R: 64, W: 65}),
  sec("neck spine", "L0 black plates x 5..7 from y 12 to y 31 (headstock part is x 5..8 from y 24). L1 black plates from y 8 to y 31", 5, 8, 0, 3, 23, 0.667, {K: 98}),
  sec("neck and headstock top", "L2 top (24 cells) ... REDDISH_BROWN flat parts available: 3024 x18, 15573 x2, 4073 x5, 3623 x1, 48336 x6 -> exactly 42 cells; nut WHITE plate 1x3", 5, 12, 0.667, 3, 19, 0.333, {"REDDISH_BROWN": 42, W: 3},
      parts=[("15573", "REDDISH_BROWN", 2), ("4073", "REDDISH_BROWN", 5), ("48336", "REDDISH_BROWN", 6)]),
  sec("top details", "three black 1x4 tiles; two pearl silver grille tiles 1x2; knobs and tuner posts LIGHT_BLUISH_GRAY 4073 x9; selector BLACK 98138pt5; fret dots WHITE 4073 x8", 0, 0, 1, 12, 31, 0.333, {K: 13, "PEARL_SILVER": 4, G: 9, W: 8},
      parts=[("4073", G, 9), ("4073", W, 8)]),
])

# --- skull 9ccc0f34 -----------------------------------------------------------
def skull_9ccc(round2):
    nose = 8 if round2 else 6  # opening 2 wide x 4 (round 2) or 3 plates
    s = [
      sec("base", "10 x 11 x 0.333, LIGHT_BLUISH_GRAY, floor from gray plates", 0, 0, 0, 10, 11, 0.333, {G: 110}),
      sec("lower jaw", "8 x 7 x 1.667; hollow U of stacked gray plates (1-stud walls), open toward the back", 1, 4, 0.333, 8, 7, 1.667, {G: 100}, "shell", 1),
      sec("teeth", "8 gray 30069 brick 1x1 with headlight in one row", 1, 10, 2, 8, 1, 1, {G: 24}, parts=[("30069", G, 8)]),
      sec("face wall", "10 x 3 x 3.333; gray front wall, sides x=0 and x=9 gray for y 8..11; eye openings 3 wide, z 4.333..6; nose opening 2 wide", 0, 8, 3, 10, 3, 3.333, {G: 140 - 30 - nose}, "shell", 1),
      sec("socket backs", "red plates/bricks/tiles behind the eye and nose openings", 1, 9, 3, 8, 1, 3, {R: 30 + nose}),
      sec("brow ridge", "10 x 2 x 0.333 gray plates sticking out 1 stud" if round2 else "10 x 1 x 0.333 gray 1x10 plate", 0, 10, 6, 10, 2 if round2 else 1, 0.333, {G: 20 if round2 else 10}),
      sec("back and side walls", "10 x 8 x 4.333 ring (open to the front face); 1-stud red walls", 0, 0, 2, 10, 8, 4.333, {R: 312}, "shell", 1),
      sec("back wall beside the jaw", "(z 0.333..2 at back y 0..4)", 0, 0, 0.333, 10, 4, 1.667, {R: 80}, "shell", 1),
    ]
    if round2:
        s += [
          sec("cranium ceiling", "internal ceiling plate layer at z 6.333 (can be gray where hidden)", 1, 1, 6.333, 8, 9, 0.333, {A: 72}),
          sec("cranium outer ring", "outer ring z 6.333..7.333: red bricks (3010, 3004, 3005, 5152)", 0, 0, 6.333, 10, 11, 1, {R: 114}, "shell", 1),
          sec("cranium edge plates", "along all four edges: a 2-wide red plate on the ring (between the four 3x3 corner domes)", 0, 0, 7.333, 10, 11, 0.333, {R: 32}, "shell", 2),
          sec("cranium edge slopes", "then red 15068 curved slopes facing outward", 0, 0, 7.667, 10, 11, 0.667, {R: 64}, "shell", 2),
          sec("cranium center", "flat center (about 4 x 5) on a plate layer, covered with red tiles", 3, 3, 7.333, 4, 5, 0.667, {R: 40}),
        ]
    else:
        s += [
          sec("cranium ceiling ring", "a red ceiling ring/plate layer at z 6.333 (3832 2x10, 60479 1x12, 3460 1x8)", 0, 0, 6.333, 10, 11, 0.333, {R: 68}, "shell", 2),
          sec("cranium edge slopes", "red curved slopes 15068 and 93273 around all four edges stepping inward", 0, 0, 6.667, 10, 11, 0.667, {R: 136}, "shell", 2),
          sec("cranium center", "flat center of the top covered with red tiles", 2, 2, 6.667, 6, 7, 0.667, {R: 84}),
        ]
    return s

write("skull_9ccc0f34_round1", "runs/20261109-001720-make-me-a-skull-9ccc0f34/plan.round1.json",
  "Built in gray and red only, 187 parts, every check passed (snapshot 004). The cranium is the least the plan text asks for.", skull_9ccc(False))
write("skull_9ccc0f34_round2", "runs/20261109-001720-make-me-a-skull-9ccc0f34/plan.json",
  "Never finished (stopped by the user in builder call 3).", skull_9ccc(True))

# --- skull 73b41c51 -----------------------------------------------------------
BASE = lambda: sec("base", "10 x 11 x 0.333; hidden colors inside, gray where the edge shows", 0, 0, 0, 10, 11, 0.333, {G: 38, A: 72})
TEETH = lambda: sec("teeth and mouth", "8 gray 30069 headlight bricks; a BLACK 1x8 plate row set 1 stud back; gray 1x1 round plates (4073) as upper teeth", 1, 9, 2, 8, 2, 1.333, {G: 32, K: 8}, parts=[("30069", G, 8), ("4073", G, 8)])
CEILING = lambda: sec("ceiling", "10 x 11 x 0.333 full-footprint plate layer; hidden colors inside, gray at the visible edge; brow: front row sticks out to y 12", 0, 0, 6.333, 10, 12, 0.333, {G: 40, A: 80})
FACE_SAID = "10 x 2 x 3; gray front wall (2 studs thick where possible); nose x 4..6, z 3.333..4.667; eye sockets x 1..4 and x 6..9, z 4.333..6; black 1 stud behind each opening"

def jaw_face(skin):
    if not skin:
        return [
          sec("lower jaw", "8 x 8 x 1.667; 2-stud-thick U walls of wide plates; gray", 1, 3, 0.333, 8, 8, 1.667, {G: 200}, "shell", 2),
          sec("face wall", FACE_SAID, 0, 9, 3.333, 10, 2, 3, {G: 104, K: 38}),
        ]
    return [
      sec("lower jaw, outer skin", "LIGHT_BLUISH_GRAY outside", 1, 3, 0.333, 8, 8, 1.667, {G: 110}, "shell", 1),
      sec("lower jaw, inner skin", "use hidden colors on inner halves", 2, 4, 0.333, 6, 6, 1.667, {A: 80}, "shell", 1),
      sec("face wall, front row", FACE_SAID, 0, 10, 3.333, 10, 1, 3, {G: 52}),
      sec("face wall, back row", FACE_SAID, 0, 9, 3.333, 10, 1, 3, {K: 38, A: 52}),
    ]

def skull_73b4_r1(skin):
    said = "10 x 9 ring, z 0.333..6.333; LIGHT_BLUISH_GRAY outside, hidden colors inside; closed walls at every height (beside the jaw only the back, y 0..3)"
    dome = "level 1 (z 6.667..7.667) full 10x11 ring 2 studs thick; level 2 (z 7.667..8.333) inset 1 stud (8x9); level 3 (z 8.333..8.667) inset again (6x7); LIGHT_BLUISH_GRAY"
    s = [BASE(), *jaw_face(skin), TEETH()]
    if not skin:
        s += [
          sec("side and back walls", said, 0, 0, 0.333, 10, 9, 6, {G: 744}, "shell", 2),
          sec("cranium level 1", dome, 0, 0, 6.667, 10, 11, 1, {G: 204}, "shell", 2),
          sec("cranium level 2", dome, 1, 1, 7.667, 8, 9, 0.667, {G: 104}, "shell", 2),
        ]
    else:
        s += [
          sec("side and back walls, outer skin", said, 0, 0, 0.333, 10, 9, 6, {G: 408}, "shell", 1),
          sec("side and back walls, inner skin", said, 1, 1, 0.333, 8, 7, 6, {A: 310}, "shell", 1),
          sec("cranium level 1, outer skin", dome, 0, 0, 6.667, 10, 11, 1, {G: 114}, "shell", 1),
          sec("cranium level 1, inner skin", dome, 1, 1, 6.667, 8, 9, 1, {A: 90}, "shell", 1),
          sec("cranium level 2, outer skin", dome, 1, 1, 7.667, 8, 9, 0.667, {G: 60}, "shell", 1),
          sec("cranium level 2, inner skin", dome, 2, 2, 7.667, 6, 7, 0.667, {A: 44}, "shell", 1),
        ]
    return s + [CEILING(), sec("cranium level 3", dome, 2, 2, 8.333, 6, 7, 0.333, {G: 42})]

def skull_73b4_r2(skin):
    said = "10 x 9 ring, z 0.333..6.333; RED above the jaw (z 2..6.667), gray beside the jaw (z 0.333..2); closed walls at every height"
    s = [BASE(), *jaw_face(skin), TEETH()]
    if not skin:
        s += [
          sec("side and back walls above the jaw", said, 0, 0, 2, 10, 9, 4.333, {R: 624}, "shell", 2),
          sec("back wall beside the jaw", said, 0, 0, 0.333, 10, 9, 1.667, {G: 120}, "shell", 2),
        ]
    else:
        s += [
          sec("side and back walls above the jaw, outer skin", said, 0, 0, 2, 10, 9, 4.333, {R: 338}, "shell", 1),
          sec("side and back walls above the jaw, inner skin", said, 1, 1, 2, 8, 7, 4.333, {A: 260}, "shell", 1),
          sec("back wall beside the jaw, outer skin", said, 0, 0, 0.333, 10, 9, 1.667, {G: 70}, "shell", 1),
          sec("back wall beside the jaw, inner skin", said, 1, 1, 0.333, 8, 7, 1.667, {A: 50}, "shell", 1),
        ]
    return s + [
      CEILING(),
      sec("cranium outer ring", "outer ring z 6.667..7.667 red (bricks 3004/3010/3005/5152, or plates)", 0, 0, 6.667, 10, 11, 1, {R: 114}, "shell", 1),
      sec("cranium edge plates", "edges between corners: ... a 2-wide red plate ring", 0, 0, 7.667, 10, 11, 0.333, {R: 32}, "shell", 2),
      sec("cranium edge slopes", "red 15068 curved slopes facing outward", 0, 0, 8, 10, 11, 0.667, {R: 64}, "shell", 2),
      sec("cranium center", "flat center plates + red tiles", 2, 2, 7.667, 6, 7, 0.667, {R: 84}),
    ]

WIDE = "Reading 'wide': each 2-stud wall is one color, made of parts up to 2 wide (the plan: 'build walls 2 studs thick from wide plates')."
SKIN = "Reading 'skin': each 2-stud wall is a visible outer skin 1 stud thick plus a hidden inner skin in any color (the plan: 'use hidden colors on inner halves')."
R1 = "Built: 321 parts, every check passed, but gray ran out and visible cells fell back to white, tan and red; the review failed on it. "
R2 = "Never built. "
write("skull_73b41c51_round1_wide", "runs/20261109-005715-make-me-a-skull-73b41c51/plan.round1.json", R1 + WIDE, skull_73b4_r1(False))
write("skull_73b41c51_round1_skin", "runs/20261109-005715-make-me-a-skull-73b41c51/plan.round1.json", R1 + SKIN, skull_73b4_r1(True))
write("skull_73b41c51_round2_wide", "runs/20261109-005715-make-me-a-skull-73b41c51/plan.json", R2 + WIDE, skull_73b4_r2(False))
write("skull_73b41c51_round2_skin", "runs/20261109-005715-make-me-a-skull-73b41c51/plan.json", R2 + SKIN, skull_73b4_r2(True))
