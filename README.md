# Voxel

Design LEGO builds from only the pieces you own.

An AI agent (BrickWright) designs the build with BrickLayer. Voxel adds the
rule the agent has to follow: the build may only use parts from your sets.

## Status

Step 1 of the plan: the inventory check. It takes a finished build and an
inventory file and reports which parts are over what you own. No LLM and no API
key needed.

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

1. Inventory check (this commit)
2. Rebrickable fetch: enter set numbers instead of a parts list
3. Agent loop with Claude Haiku, with the check wired into validation
4. "Closest build you can make" mode and a missing parts report
5. Step-by-step handout (PDF)
6. Web app
