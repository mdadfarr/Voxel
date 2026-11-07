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

Put your Anthropic key in `.env` (ignored by git):

```
ANTHROPIC_API_KEY=your_key_here
```

Then:

```powershell
python -m voxel.design "a small red tower" --inventory inventory.json --max-cost 0.5
```

Before the builder runs, one planning call turns the request into a plan: a
checklist, the sections of the object (size, position, colors, parts) and the
owned parts it needs. The plan is saved as `plan.json` in the record folder.
The builder gets the plan, the planned parts and a spare set (owned basic
bricks and plates in the plan's colors) instead of the full inventory. Retry
rounds get all of that again, plus the gate report. The inventory checks still
use the full inventory. The summary shows the total cost, split into plan and
build.

Flags:

- `--plan-model`: model for the planning call (default `claude-opus-5-5`).
- `--build-model`: model for the builder agent (default `claude-haiku-5-5`).
  `--model` still works as another name for it.
- `--max-cost`: USD cap per build round (default 0.5).
- `--max-retries`: builder rounds after a failed gate (default 2).
- `--inventory`: inventory file (default `inventory.json`).

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
