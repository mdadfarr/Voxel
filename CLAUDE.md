# Voxel

Voxel designs LEGO builds that use only the parts in `inventory.json`, and makes a step-by-step manual for them.

## Two kinds of requests
- **A rebuild**: `REBUILD: <old_record_id> SOLVER on` or `REBUILD: <old_record_id> SOLVER off`.
  Run `scripts/bw external init "<the old request>"` for a new record, copy runs/<old_record_id>/plan.json and expansion.md into runs/<new_record_id>/, skip steps 1 to 4, and do steps 5 to 8 with the given SOLVER setting. Do not change the plan, and do only one review round (no re-plan), so both runs build exactly the same plan.

- **A coding task** (anything that does not start with `BUILD:`): normal development work on this repo. Ignore the "Build mode" section below.
- **A build request**: a message that starts with `BUILD:`, for example `BUILD: make me a guitar`. Follow "Build mode" exactly.

## Build mode

You are the planner and the reviewer. You hand the part placing to the `lego-builder` subagent (Haiku) and hard problems to the `lego-solver` subagent (Sonnet).

### Rules for every step

- Run every BrickWright command through `scripts/bw` (it sets the inventory, the required checks and the LDraw library). Never call `brickwright` directly.
- Use only parts in `inventory.json`. Match part AND color. Ignore `inventory.unusable.json`.
- Units: x, y, width and depth are in studs; z and height are in bricks (1 brick = 3 plates). A plate-thick step is 1/3 brick (.333 / .667).
- Orientation: the front of the model is its +y side (the largest y), the side the front render shows. Put front features there.
- Look: use shaped parts (slopes, curved bricks, round parts) for outlines and curves. Cover visible surfaces with tiles for a clean look.
- Do NOT add build steps. Code computes the manual at the end (step 7).
- Keep a work folder `runs/<record_id>/` for your own files. Do not write into `records/<record_id>/` except through `scripts/bw` and the builder editing `build.py`.

### Steps

1. **Questions.** Ask me at most 3 short questions about what the request leaves open (type, colors, key features). Never ask about size, scale or how big: the size comes from my parts (step 4). Exception: if my request already says a size (words like small, big, large, tiny, tall, or a number with studs, bricks or cm), keep that size. Only offer colors I own at least 20 pieces of. Give each question a default in brackets. Wait for my answers.
2. **Description.** Write a detailed description: overall size (studs and bricks; chosen by the size rule in step 4, not from a fixed small/medium/large; my size if my request says one), realistic proportions, colors per part, and the 4 to 8 features a person would recognize. Save it as `runs/<record_id>/expansion.md` after step 3.
3. **Record.** Run `scripts/bw external init "<my request>"`. Note the `record_id` and `build_path` it prints. Run `scripts/bw external instructions > INSTRUCTIONS.md` once per session and read it.
4. **Plan.** Write `runs/<record_id>/plan.json` with:
   - `checklist`: first item "looks like <the thing>", then one yes/no item per feature;
   - `sections`: name, size, position, colors, and a short note on how to build it;
   - only parts I own, within my counts.
   - Capacity. Before planning, run `.venv/bin/python -m voxel.capacity --table` and size the model to it. The table is your budget: plan with these numbers only, and do not add up plate-cells from `inventory.json` yourself or pass `--planner-margin` / `--checker-margin` (part of what I own is held back for the builder). Every section needs: `size` as `{width, depth, height}` and `position` as `{x, y, z}` (numbers, not text), `fill` (`"solid"` or `"shell"`, with `wall` in studs for a shell), `cells` (a list of `{color, cells}` in plate-cells; color `"ANY"` for cells that cannot be seen) and `empty` (plate-cells left open). Special parts (round, cut, modified) count only if the section lists them in `parts` as `{part, color, count}`. Split a section when its wall thickness or visibility changes.
   - Size rule. Choose the overall size so that the color you use most, relative to its budget, uses 70 to 100% of its budget. If my request says a size, keep that size instead.
   - Then run `.venv/bin/python -m voxel.capacity runs/<record_id>/plan.json --request "<my request>"`. It prints the capacity result, the utilization per color, the tightest color, a `Size:` line, and `Too small` if it applies; when the plan fits it saves that as `sizing` in plan.json.
     - Exit code 1 (does not fit): change the plan (smaller, thinner walls, other colors, named special parts) and run it again. Do not go to step 5 until it fits.
     - Exit code 3 (`Too small`): scale the plan up and run it again, ONCE. If it still says so, keep the plan, go on, and tell me in the report.
     - Run it again after every plan revision in step 6. Give me the `Size:` line when the plan is done.
5. Build. The main session counts failures, not the builder.
   - Call lego-builder with: "Run validate at most 2 times, then return the full output of the last validate, passed or not."
   - If SOLVER is on and the last validate failed: call lego-solver with that output and the record_id. Then call lego-builder again with the solver's edits and the same 2-validate limit.
   - If SOLVER is off and the last validate failed: call lego-builder again with the same limit, no solver.
   - Stop after 8 builder calls in total, or when validate passes.
   - Keep a log in runs/<record_id>/build_log.md: one line per builder or solver call, with which checks failed.6. **Review.** Run `scripts/bw external render <record_id>` and look at `records/<record_id>/out/build.png`. For each checklist item, decide pass or fail, with a one-line reason. Save this as `runs/<record_id>/review.json`.
   - Any failed item: revise plan.json so the failed items can pass (change the design, not only details), and go back to step 5. The builder starts from an empty build.py. Max 2 review rounds.
7. **Finish.** Run `scripts/bw external finalize <record_id>`, then `.venv/bin/python -m voxel.sequence <record_id>`. This writes the manual as `records/<record_id>/out/build.stepped.ldr`.
8. **Report.** Tell me: the record_id, the part count, the number of manual pages, the review result per item, how many times the builder and the solver were called, and the session ID (from `/status`) so I can measure the cost.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
