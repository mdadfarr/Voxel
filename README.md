# Voxel

Design LEGO builds from only the pieces you own.

An AI agent (BrickWright) designs the build with BrickLayer. Voxel adds the
rule the agent has to follow: the build may only use parts from your sets.

## Status

Done so far:

1. Inventory check: takes a finished build and an inventory file and reports
   which parts are over what you own. No LLM and no API key needed.
2. Rebrickable fetch: type set numbers, get an inventory file. Spare parts
   count as owned. Parts the design engine cannot use are listed in
   `inventory.unusable.json`. Needs a free Rebrickable API key.

3. Design agent: BrickWright (Claude Haiku) designs the build. The owned-parts
   rule is checked inside the agent's tests and again by an independent gate;
   if the gate fails, the agent is run again with the report (max 2 retries).

## Design a build

Put your Anthropic key and the path to your LDraw parts library in `.env`
(ignored by git):

```
ANTHROPIC_API_KEY=your_key_here
LDRAW_LIBRARY_PATH=/path/to/ldraw
```

The review step needs an LDraw library to render the build. `LDRAW_LIBRARY_PATH`
points to it; without it, a few standard places (like `~/ldraw`) are searched.
If no library is found, the build is still made and checked, but the review is
skipped with a warning. (`scripts/bw` sets the path for you;
`python -m voxel.design` reads it from `.env` or your shell.)

Then:

```powershell
python -m voxel.design "a small red tower" --inventory inventory.json --max-cost 0.5
```

The run goes Expand -> Plan -> Build -> Review.

First, Voxel asks you at most 3 questions about what the request leaves open
(type, colors, size, key features). Each shows its default in brackets; press
Enter to take it. Color questions only offer colors you own at least 20 pieces
of. Then one call writes a detailed description: overall size (width and depth
in studs, height in bricks, 1 brick = 3 plates), proportions between the parts,
colors per part, the key features a person would recognize, and shaped parts
for outlines and curves. The planner gets this description instead of the
short request and turns each key feature into a checklist item. The questions,
answers, description and features are saved as `expansion.json` in the record
folder. Both calls see a short inventory summary (pieces and shaped parts per
color), not the full part list.

Then one planning call turns the request into a plan: a
checklist, the sections of the object (size, position, colors, parts) and the
owned parts it needs. The plan uses BrickLayer's units: x, y, width and depth
in studs; z and height in bricks (1 brick = 3 plates, so a plate step is 1/3
brick). The plan is saved as `plan.json` in the record folder. The builder gets
the plan, the planned parts and a spare set instead of the full inventory. The
spare set holds owned plain bricks and plates, slopes, curved and round parts
and tiles in the plan's colors that the plan does not already use: at most 40
rows, smallest parts first, shared out over the plan's colors (most-used color
first). Retry rounds after a failed gate continue from the previous build and
get all of that again, plus the gate report. The inventory checks still use the
full inventory.

After the build passes the gate, it is rendered and one review call (Opus,
with the render attached) answers pass/fail with a short reason for each
checklist item in the plan. The first item ("looks like ...") is judged from
the image alone. If any item fails, the plan is revised: one more Opus call sees
the old plan, the failed items with their reasons and the render, and returns a
new plan with the same checklist, checked like the first one. The builder then
starts from an empty build with the new plan and the review's reasons (max 2
reviews). If that rebuild cannot pass the gate, the earlier build is kept. The
reviews are saved as `review.json` in the record folder, one entry per round.
Each record has its plan version in `plan.json`; the final record also has
`plans.json`, every plan version with why it was made. Plan revisions count
under plan in the cost line.

The summary shows the review result and the total cost:
`Cost: $X (expand $a + plan $b + build $c + review $d)`. Expand is the cost of
the questions and description calls ($0.000 with `--no-expand`).

Exit codes: 0 when the gate passed and the review passed or was skipped, 2 when
the gate failed, 3 when the gate passed but the review still failed after 2
rounds.

Flags:

- `--plan-model`: model for the planning call (default `claude-opus-5-5`).
- `--build-model`: model for the builder agent (default `claude-haiku-5-5`).
  `--model` still works as another name for it.
- `--max-cost`: USD cap per build round (default 0.5).
- `--max-retries`: builder rounds after a failed gate (default 2).
- `--inventory`: inventory file (default `inventory.json`).
- `--no-questions`: do not ask; use every question's default answer. The
  questions call still runs, to get the defaults.
- `--no-expand`: skip the questions and the detailed description; plan from
  the short request.

## Get an inventory from set numbers

Put your key in a `.env` file (it is ignored by git):

```
REBRICKABLE_API_KEY=your_key_here
```

Then:

```powershell
python -m voxel.rebrickable 10242 75869
```

Typing the same set twice means you own two copies.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Inventory file

A list of parts. `part` is the LDraw part id, `color` is a BrickLayer color
name (like `RED`) or an LDraw color code. See `inventory.example.json`.

## Roadmap

1. Inventory check (done)
2. Rebrickable fetch (done)
3. Agent loop with Claude Haiku, with the check wired into validation (done)
4. "Closest build you can make" mode and a missing parts report
5. Step-by-step handout (PDF)
6. Web app
