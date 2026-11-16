# Graph Report - Voxel  (2026-11-15)

## Corpus Check
- 24 files · ~33,778 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 3 file(s) not represented in the graph (top: (none) 3)

## Summary
- 665 nodes · 1434 edges · 47 communities (22 shown, 25 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 74 edges (avg confidence: 0.87)
- Token cost: 997,119 input · 0 output

## Graph Freshness
- Built from commit: `2458cacf`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- design
- BrickLayer Part Library
- Rebrickable Set Probe
- Build Step Sequencing
- Build Mode Workflow Rules
- check.py
- expand.py
- field_validator
- BrickWright Validation Harness
- llm.py
- capacity.py
- plan.py
- inventory.py
- json
- Path
- Cost Budget
- review.py
- Usage
- Plate Unit Conversion
- Package Config
- Roadmap
- load_inventory
- design.py
- design.py
- load_record
- main
- builder_retries

## God Nodes (most connected - your core abstractions)
1. `Inventory` - 48 edges
2. `design()` - 37 edges
3. `Plan` - 27 edges
4. `LegoBuild` - 22 edges
5. `Expansion` - 19 edges
6. `call_with_retry()` - 19 edges
7. `StrictModel` - 18 edges
8. `Usage` - 15 edges
9. `load_inventory()` - 15 edges
10. `revise_plan()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `ask_expert tool (Sonnet edits, Opus rescue)` --semantically_similar_to--> `lego-solver subagent (Sonnet)`  [INFERRED] [semantically similar]
  README.md → .claude/agents/lego-solver.md
- `Build step 5: main session counts failures` --conceptually_related_to--> `STUCK report protocol`  [AMBIGUOUS]
  CLAUDE.md → .claude/agents/lego-builder.md
- `__getattr__()` --indirect_call--> `check_owned()`  [INFERRED]
  src/voxel/__init__.py → src/voxel/design.py
- `retrying_complete()` --calls--> `call_api()`  [EXTRACTED]
  src/voxel/design.py → src/voxel/llm.py
- `design()` --uses--> `ReviewError`  [INFERRED]
  src/voxel/design.py → src/voxel/review.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Joint motion annotation and sampling** — instructions_joint, instructions_motionrange, instructions_legobuild_check_motion, instructions_motionreport, instructions_legobuild_posed, instructions_tests_check_motion [EXTRACTED 1.00]
- **LegoBuild placement methods** — instructions_legobuild_add, instructions_legobuild_stack, instructions_legobuild_connect, instructions_legobuild_add_raw, instructions_legobuild_extend [EXTRACTED 1.00]
- **Required build validity checks in run_tests** — instructions_run_tests, instructions_check_no_floating, instructions_check_no_collisions, instructions_check_connected, instructions_testcontext [EXTRACTED 1.00]
- **Build mode planner/builder/solver/reviewer pipeline** — claude_voxel_build_mode, _claude_agents_lego_builder_lego_builder, _claude_agents_lego_solver_lego_solver, claude_build_loop, claude_review_step, claude_finish_sequence [EXTRACTED 1.00]
- **voxel.design Expand -> Plan -> Build -> Review pipeline** — readme_voxel_design, readme_expand_step, readme_planning_call, readme_build_sequencing, readme_ask_expert, readme_review_call [EXTRACTED 1.00]

## Communities (47 total, 25 thin omitted)

### Community 0 - "design"
Cohesion: 0.18
Nodes (9): design(), build_until_gate(), out_of_budget(), round_config(), expert_rescues(), feedback_prompt(), plan_version(), UsageLedger (+1 more)

### Community 1 - "BrickLayer Part Library"
Cohesion: 0.05
Nodes (55): alignment_box, Anchor, Ball, brick(), BrickLayer library, Color, Component factories, Connections (connector mating) (+47 more)

### Community 2 - "Rebrickable Set Probe"
Cohesion: 0.11
Nodes (15): build_inventory(), _catalog_knows(), convert_rows(), Converted, fetch_set_rows(), inventory_entries(), InventoryBuild, main() (+7 more)

### Community 3 - "Build Step Sequencing"
Cohesion: 0.09
Nodes (13): order_build(), Ordered, holders(), main(), moved_parts(), paginate(), Sequence, sequence() (+5 more)

### Community 4 - "Build Mode Workflow Rules"
Cohesion: 0.09
Nodes (28): lego-builder subagent (Haiku), lego-solver subagent (Sonnet), runs/<record_id>/build_log.md, runs/<record_id>/expansion.md (description), Finish: finalize + voxel.sequence manual (build.stepped.ldr), runs/<record_id>/plan.json (checklist + sections), REBUILD request (SOLVER on/off), Review step (render + checklist pass/fail) (+20 more)

### Community 5 - "check.py"
Cohesion: 0.10
Nodes (8): check_build(), CheckResult, Shortfall, build_prompt(), check_owned(), __getattr__(), color_code(), count_build()

### Community 6 - "expand.py"
Cohesion: 0.09
Nodes (24): collect_answers(), Answer, ask_questions(), color_stock(), ColorStock, expand(), expand_prompt(), ExpansionResult (+16 more)

### Community 7 - "field_validator"
Cohesion: 0.10
Nodes (7): bricks(), CellCount, parse_color(), PlanPart, Position, Size, spare_set()

### Community 8 - "BrickWright Validation Harness"
Cohesion: 0.07
Nodes (32): Axis, Box, BrickWright (expert LEGO builder role), BrickWright Instructions, build.py, check_connected(expected_components), check_no_collisions(), check_no_floating() (+24 more)

### Community 9 - "llm.py"
Cohesion: 0.07
Nodes (15): _api_error_types(), call_api(), call_with_retry(), CallFailed, CutOff, finish_reason(), image_part(), is_credit_error() (+7 more)

### Community 10 - "capacity.py"
Cohesion: 0.05
Nodes (36): budget(), capacity_table(), CapacityResult, _cells(), check_capacity(), check_demands(), checker_margin(), _code() (+28 more)

### Community 11 - "plan.py"
Cohesion: 0.15
Nodes (8): _gap(), plan_section(), color_name(), part_kind(), part_name(), part_size(), plan_format_and_rules(), Section

### Community 12 - "inventory.py"
Cohesion: 0.27
Nodes (3): owned_parts_text(), color_label(), key_label()

### Community 13 - "json"
Cohesion: 0.17
Nodes (6): has_ldraw(), main(), read_key(), summarize(), http_get_json(), RebrickableError

### Community 14 - "Path"
Cohesion: 0.22
Nodes (7): new_record(), final_gate(), _gate_worker(), render_build(), save_json(), save_plan(), save_reviews()

### Community 18 - "review.py"
Cohesion: 0.06
Nodes (31): direct_checklist(), direct_prompt(), review_problem(), Expansion, load_json(), _add_usage(), expansion_rules(), make_plan() (+23 more)

### Community 23 - "Usage"
Cohesion: 0.20
Nodes (5): add_round_usage(), expert_usage(), interrupted_usage(), run_usage(), Usage

### Community 27 - "load_inventory"
Cohesion: 0.18
Nodes (7): context(), _line(), owned_text(), parts_left_text(), plan_section_text(), used_counts(), load_inventory()

### Community 28 - "design.py"
Cohesion: 0.12
Nodes (7): expansion_record(), gate_problem(), require_owned_check(), set_expert(), ExpandError, PlanError, ReviewError

### Community 30 - "design.py"
Cohesion: 0.38
Nodes (9): budget_line(), builder_line(), cost_line(), no_build(), DesignResult, models_line(), print_summary(), review_line() (+1 more)

### Community 37 - "load_record"
Cohesion: 0.33
Nodes (4): load_record(), _read_json(), RecordError, Reused

### Community 38 - "main"
Cohesion: 0.40
Nodes (3): exit_code(), load_env(), main()

## Ambiguous Edges - Review These
- `STUCK report protocol` → `Build step 5: main session counts failures`  [AMBIGUOUS]
  CLAUDE.md · relation: conceptually_related_to

## Knowledge Gaps
- **25 isolated node(s):** `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring`, `Color`, `DoorVariant` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 246 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Inventory` connect `expand.py` to `Rebrickable Set Probe`, `check.py`, `load_record`, `field_validator`, `capacity.py`, `plan.py`, `inventory.py`, `review.py`, `load_inventory`, `design.py`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Inventory` (e.g. with `check_build()` and `owned_text()`) actually correct?**
  _`Inventory` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `BrickLayer Part Library` be split into smaller, more focused modules?**
  _Cohesion score 0.05136612021857923 - nodes in this community are weakly interconnected._
- **Why does `design()` connect `design` to `check.py`, `expand.py`, `load_record`, `field_validator`, `main`, `Path`, `Cost Budget`, `review.py`, `Usage`, `load_inventory`, `design.py`, `design.py`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `design()` (e.g. with `.allows()` and `.require()`) actually correct?**
  _`design()` has 7 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `STUCK report protocol` and `Build step 5: main session counts failures`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._