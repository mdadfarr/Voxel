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
python -m voxel.design "a small red tower" --inventory inventory.json --max-cost 1.5
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

The builder does not split the build into steps; Voxel does that in code once
the inventory check passes. It starts with the parts on the ground, then keeps
adding parts that are clipped to or rest on a part already placed (in the order
`build.py` wrote them), in pages of at most 12 parts. Parts on the same part
below share a page when they fit. The result is `out/build.ldr` with a `0 STEP`
after each page; the builder's own order is kept as `out/build.unordered.ldr`.
The step check then runs on the computed order as a safety check. Parts that
nothing holds up are sent back to the builder as a normal gate retry.

When the builder's validate fails, it asks an expert instead of guessing:
BrickWright's `ask_expert` tool (Voxel turns it on; `--no-expert` turns it off).
The builder sends only its record ID and the failed check's name. Code builds a
small packet: the check's detail, the problem parts (for `connected`, the parts
outside the largest group, nearest first, at most 3), the nearest parts around
them (10% of the build, at least 8) plus every part in the column below, down to
the ground, the parts you have left, and the plan section the problem is in.
Sonnet answers with exact edits ("remove ... at (x,y,z); add Red 3023 at
(x,y,z)"), as structured output checked in code. Each problem gets its own
conversation; the start of the prompt (instructions and your full parts list) is
the same every time and cached. If the same check still fails after the edits,
Sonnet is asked again in the same conversation (5 tries), then Opus takes over
(at most 3 Opus rescues per run, then the builder is told to stop and report).
Expert calls count in `--max-cost` (the tool refuses when too little is left)
and have their own rows on the Models line and an `expert` part in the Cost
line. Every call is saved in `expert.json` in the record. An expert call that
still fails after the retries is answered with "The expert is unavailable right
now; fix this yourself." and the build goes on; a credit error stops the run.

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
`Cost: $X (expand $a + plan $b + build $c + expert $e + review $d)`. Expand is the cost of
the questions and description calls ($0.000 with `--no-expand`). A second
line splits it by model, with tokens:
`Models: claude-opus-5-5 12,400 in / 3,100 out $0.310 | claude-haiku-5-5 990,000 in / 54,000 out $0.140`.
The same numbers, plus which steps used each model, are saved as `usage.json`
in the final record.

`--max-cost` is one budget for the whole run: questions, description, plan,
plan revisions, every build round and every review. Before each step Voxel
checks what is left (at least a small floor per step, or the most that step
has cost so far in this run), and each build round gets the rest as
BrickWright's own cap. When the budget runs out, the run stops and keeps the
last build that passed the gate; the summary says where:
`Budget: $1.12 of $1.50 used; ran out before review round 2, kept <record_id>`.
BrickWright checks its cap between turns, so the total can go over by one
builder turn.

Every prompt uses one definition of front: the front of the model is its +y
side (the largest y), the side BrickWright's front render shows; the side
render shows the -x side. All JSON calls (questions, description, plan,
revision, review) use structured output, so the reply is JSON in the expected
shape; a reply cut off at the token limit or rejected by the checks is retried
once with the reason.

API errors do not crash the run. Every call (questions, description, plan,
revision, review, and each of the builder's calls) is retried up to 2 times,
after 5 s and then 15 s, when it times out, hits a rate limit, cannot connect,
or gets a server error. A call times out after 180 s (360 s for the plan and
revision calls, which write long replies). If a call still fails after the
retries, or the API says the credit balance is too low (not retried), the run
stops cleanly: it keeps the last build that passed the gate, prints one line
with the reason and the normal summary, and exits with code 5:
`Stopped: the review call timed out 3 times (180 s each), in review round 1; kept <record_id>.`

To compare builder models on the exact same plan, reuse an earlier record:

```powershell
python -m voxel.design --from-record <record_id> --build-model claude-sonnet-5-5
```

This skips the questions, the description and the planning call (and their
cost), and takes that record's `expansion.json` and the latest plan version
from its `plans.json`. The request is taken from the record unless you give
one. Build, gate, review and re-planning run as normal, in new records. The
plan is checked against your current inventory first. Records made before
`plans.json` existed have their plan in plates and cannot be reused.

Exit codes: 0 when the gate passed and the review passed or was skipped, 2 when
the gate failed, 3 when the gate passed but the review still failed after 2
rounds, 4 when the budget ran out before any build passed the gate, 5 when an
API call failed for good (after its retries, or no credit), with or without a
kept build.

Flags:

- `--plan-model`: model for the planning call (default `claude-opus-5-5`).
- `--build-model`: model for the builder agent (default `claude-haiku-5-5`).
  `--model` still works as another name for it.
- `--max-cost`: USD cap for the whole run (default 1.5).
- `--max-retries`: builder rounds after a failed gate (default 2).
- `--inventory`: inventory file (default `inventory.json`).
- `--no-questions`: do not ask; use every question's default answer. The
  questions call still runs, to get the defaults.
- `--no-expand`: skip the questions and the detailed description; plan from
  the short request. With `--from-record`, run without the saved expansion.
- `--from-record <record_id>`: reuse that record's expansion and latest plan
  instead of asking, expanding and planning again.
- `--no-expert`: do not offer the builder the `ask_expert` tool.

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
