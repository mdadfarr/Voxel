# Graph Report - Voxel  (2026-11-14)

## Corpus Check
- Corpus is ~26,141 words - fits in a single context window. You may not need a graph.

## Summary
- 551 nodes · 1169 edges · 28 communities (20 shown, 8 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 120 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Design Pipeline Orchestration
- BrickLayer Part Library
- Rebrickable Set Probe
- Build Step Sequencing
- Build Mode Workflow Rules
- Inventory Checking
- Request Expansion Questions
- Expert Context Helpers
- BrickWright Validation Harness
- LLM API Calls
- Geometry Primitives
- Plan Parsing and Reuse
- Call Errors and Usage
- Part Kind Classification
- Expansion Model
- Review Entry Point
- Review Parsing
- Cost Budget
- Size and Position Models
- Plan Generation
- LLM Retry and Cost
- Plan Part Colors
- Builder Prompt
- Plate Unit Conversion
- Package Config
- Roadmap

## God Nodes (most connected - your core abstractions)
1. `design()` - 38 edges
2. `Inventory` - 38 edges
3. `Plan` - 27 edges
4. `LegoBuild` - 22 edges
5. `Expansion` - 20 edges
6. `call_with_retry()` - 19 edges
7. `Usage` - 16 edges
8. `StrictModel` - 15 edges
9. `revise_plan()` - 14 edges
10. `load_inventory()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `ask_expert tool (Sonnet edits, Opus rescue)` --semantically_similar_to--> `lego-solver subagent (Sonnet)`  [INFERRED] [semantically similar]
  README.md → .claude/agents/lego-solver.md
- `Build step 5: main session counts failures` --conceptually_related_to--> `STUCK report protocol`  [AMBIGUOUS]
  CLAUDE.md → .claude/agents/lego-builder.md
- `__getattr__()` --indirect_call--> `check_owned()`  [INFERRED]
  src/voxel/__init__.py → src/voxel/design.py
- `final_gate()` --uses--> `CheckResult`  [INFERRED]
  src/voxel/design.py → src/voxel/check.py
- `check_build()` --uses--> `Inventory`  [INFERRED]
  src/voxel/check.py → src/voxel/inventory.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Build mode planner/builder/solver/reviewer pipeline** — claude_voxel_build_mode, _claude_agents_lego_builder_lego_builder, _claude_agents_lego_solver_lego_solver, claude_build_loop, claude_review_step, claude_finish_sequence [EXTRACTED 1.00]
- **Required build validity checks in run_tests** — instructions_run_tests, instructions_check_no_floating, instructions_check_no_collisions, instructions_check_connected, instructions_testcontext [EXTRACTED 1.00]
- **LegoBuild placement methods** — instructions_legobuild_add, instructions_legobuild_stack, instructions_legobuild_connect, instructions_legobuild_add_raw, instructions_legobuild_extend [EXTRACTED 1.00]
- **Joint motion annotation and sampling** — instructions_joint, instructions_motionrange, instructions_legobuild_check_motion, instructions_motionreport, instructions_legobuild_posed, instructions_tests_check_motion [EXTRACTED 1.00]
- **voxel.design Expand -> Plan -> Build -> Review pipeline** — readme_voxel_design, readme_expand_step, readme_planning_call, readme_build_sequencing, readme_ask_expert, readme_review_call [EXTRACTED 1.00]

## Communities (28 total, 8 thin omitted)

### Community 0 - "Design Pipeline Orchestration"
Cohesion: 0.05
Nodes (41): add_round_usage(), budget_line(), builder_retries(), retrying_complete(), cost_line(), design(), build_until_gate(), new_record() (+33 more)

### Community 1 - "BrickLayer Part Library"
Cohesion: 0.05
Nodes (55): alignment_box, Anchor, Ball, brick(), BrickLayer library, Color, Component factories, Connections (connector mating) (+47 more)

### Community 2 - "Rebrickable Set Probe"
Cohesion: 0.07
Nodes (21): has_ldraw(), main(), read_key(), summarize(), build_inventory(), _catalog_knows(), convert_rows(), Converted (+13 more)

### Community 3 - "Build Step Sequencing"
Cohesion: 0.09
Nodes (13): order_build(), Ordered, holders(), main(), moved_parts(), paginate(), Sequence, sequence() (+5 more)

### Community 4 - "Build Mode Workflow Rules"
Cohesion: 0.09
Nodes (28): lego-builder subagent (Haiku), lego-solver subagent (Sonnet), runs/<record_id>/build_log.md, runs/<record_id>/expansion.md (description), Finish: finalize + voxel.sequence manual (build.stepped.ldr), runs/<record_id>/plan.json (checklist + sections), REBUILD request (SOLVER on/off), Review step (render + checklist pass/fail) (+20 more)

### Community 5 - "Inventory Checking"
Cohesion: 0.10
Nodes (9): check_build(), CheckResult, Shortfall, check_owned(), __getattr__(), color_code(), color_label(), count_build() (+1 more)

### Community 6 - "Request Expansion Questions"
Cohesion: 0.13
Nodes (18): collect_answers(), Answer, ask_questions(), ColorStock, expand(), expand_prompt(), ExpansionResult, inventory_summary() (+10 more)

### Community 7 - "Expert Context Helpers"
Cohesion: 0.12
Nodes (10): context(), _gap(), _line(), owned_text(), parts_left_text(), plan_section(), plan_section_text(), used_counts() (+2 more)

### Community 8 - "BrickWright Validation Harness"
Cohesion: 0.13
Nodes (19): BrickWright (expert LEGO builder role), BrickWright Instructions, build.py, check_connected(expected_components), check_no_collisions(), check_no_floating(), create_build(), Validation harness (+11 more)

### Community 9 - "LLM API Calls"
Cohesion: 0.15
Nodes (6): _api_error_types(), call_api(), CutOff, image_part(), is_credit_error(), _short()

### Community 10 - "Geometry Primitives"
Cohesion: 0.12
Nodes (13): Axis, Box, Chirality, Disc, Ell, Footprint, Length, mirror() (+5 more)

### Community 11 - "Plan Parsing and Reuse"
Cohesion: 0.18
Nodes (6): load_record(), Reused, parse_plan(), parse_revision(), Plan, plan_errors()

### Community 12 - "Call Errors and Usage"
Cohesion: 0.14
Nodes (4): CallFailed, StepError, Usage, ReviewError

### Community 13 - "Part Kind Classification"
Cohesion: 0.20
Nodes (6): color_stock(), part_kind(), part_name(), part_size(), shape_kind(), spare_set()

### Community 14 - "Expansion Model"
Cohesion: 0.21
Nodes (5): Expansion, expansion_rules(), plan_format_and_rules(), plan_prompt(), revise_prompt()

### Community 15 - "Review Entry Point"
Cohesion: 0.21
Nodes (5): default_complete(), HasUsage, review(), review_prompt(), ReviewResult

### Community 16 - "Review Parsing"
Cohesion: 0.21
Nodes (6): review_problem(), parse_review(), Review, review_errors(), review_report(), Verdict

### Community 18 - "Size and Position Models"
Cohesion: 0.18
Nodes (6): ModelSize, Questions, bricks(), Position, Size, StrictModel

### Community 19 - "Plan Generation"
Cohesion: 0.27
Nodes (5): ReplyError, make_plan(), _plan_call(), PlanResult, revise_plan()

### Community 20 - "LLM Retry and Cost"
Cohesion: 0.27
Nodes (5): call_with_retry(), finish_reason(), response_cost(), response_text(), _tokens()

## Ambiguous Edges - Review These
- `STUCK report protocol` → `Build step 5: main session counts failures`  [AMBIGUOUS]
  CLAUDE.md · relation: conceptually_related_to

## Knowledge Gaps
- **25 isolated node(s):** `voxel`, `runs/<record_id>/build_log.md`, `Units: studs (x,y) and bricks (z), 1 brick = 3 plates`, `tests.check(name, ok, detail)`, `Local frames and alignment-box anchoring` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 192 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Inventory` connect `Request Expansion Questions` to `Design Pipeline Orchestration`, `Rebrickable Set Probe`, `Inventory Checking`, `Expert Context Helpers`, `Plan Parsing and Reuse`, `Part Kind Classification`, `Expansion Model`, `Plan Generation`, `Builder Prompt`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `design()` (e.g. with `.allows()` and `.require()`) actually correct?**
  _`design()` has 10 INFERRED edges - model-reasoned connections that need verification._
- **What connects `voxel`, `runs/<record_id>/build_log.md`, `Units: studs (x,y) and bricks (z), 1 brick = 3 plates` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Design Pipeline Orchestration` be split into smaller, more focused modules?**
  _Cohesion score 0.05136986301369863 - nodes in this community are weakly interconnected._
- **Why does `design()` connect `Design Pipeline Orchestration` to `Request Expansion Questions`, `Expert Context Helpers`, `Plan Parsing and Reuse`, `Call Errors and Usage`, `Part Kind Classification`, `Expansion Model`, `Review Parsing`, `Cost Budget`, `Plan Generation`, `Builder Prompt`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Are the 25 inferred relationships involving `Inventory` (e.g. with `check_build()` and `build_prompt()`) actually correct?**
  _`Inventory` has 25 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `STUCK report protocol` and `Build step 5: main session counts failures`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._