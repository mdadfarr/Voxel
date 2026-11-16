# Graph Report - Voxel  (2026-11-15)

## Corpus Check
- 24 files · ~33,300 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 3 file(s) not represented in the graph (top: (none) 3)

## Summary
- 657 nodes · 1399 edges · 47 communities (21 shown, 26 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 74 edges (avg confidence: 0.87)
- Token cost: 997,119 input · 0 output

## Graph Freshness
- Built from commit: `2361ce1a`
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
- Inventory
- build_prompt
- order_build
- Path
- Cost Budget
- plan.py
- add_round_usage
- Plate Unit Conversion
- Package Config
- Roadmap
- design.py
- design.py
- StrictModel
- Expansion
- parse_plan
- Review
- run_usage
- builder_retries

## God Nodes (most connected - your core abstractions)
1. `Inventory` - 46 edges
2. `design()` - 34 edges
3. `Plan` - 27 edges
4. `LegoBuild` - 22 edges
5. `call_with_retry()` - 19 edges
6. `Expansion` - 17 edges
7. `StrictModel` - 17 edges
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
- `check_build()` --uses--> `Inventory`  [INFERRED]
  src/voxel/check.py → src/voxel/inventory.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Joint motion annotation and sampling** — instructions_joint, instructions_motionrange, instructions_legobuild_check_motion, instructions_motionreport, instructions_legobuild_posed, instructions_tests_check_motion [EXTRACTED 1.00]
- **LegoBuild placement methods** — instructions_legobuild_add, instructions_legobuild_stack, instructions_legobuild_connect, instructions_legobuild_add_raw, instructions_legobuild_extend [EXTRACTED 1.00]
- **Required build validity checks in run_tests** — instructions_run_tests, instructions_check_no_floating, instructions_check_no_collisions, instructions_check_connected, instructions_testcontext [EXTRACTED 1.00]
- **Build mode planner/builder/solver/reviewer pipeline** — claude_voxel_build_mode, _claude_agents_lego_builder_lego_builder, _claude_agents_lego_solver_lego_solver, claude_build_loop, claude_review_step, claude_finish_sequence [EXTRACTED 1.00]
- **voxel.design Expand -> Plan -> Build -> Review pipeline** — readme_voxel_design, readme_expand_step, readme_planning_call, readme_build_sequencing, readme_ask_expert, readme_review_call [EXTRACTED 1.00]

## Communities (47 total, 26 thin omitted)

### Community 0 - "design"
Cohesion: 0.16
Nodes (10): design(), build_until_gate(), out_of_budget(), round_config(), expert_rescues(), feedback_prompt(), gate_problem(), require_owned_check() (+2 more)

### Community 1 - "BrickLayer Part Library"
Cohesion: 0.05
Nodes (55): alignment_box, Anchor, Ball, brick(), BrickLayer library, Color, Component factories, Connections (connector mating) (+47 more)

### Community 2 - "Rebrickable Set Probe"
Cohesion: 0.06
Nodes (22): has_ldraw(), main(), read_key(), summarize(), strip_fences(), build_inventory(), _catalog_knows(), convert_rows() (+14 more)

### Community 3 - "Build Step Sequencing"
Cohesion: 0.10
Nodes (10): holders(), main(), moved_parts(), paginate(), Sequence, sequence(), walk(), check_steps() (+2 more)

### Community 4 - "Build Mode Workflow Rules"
Cohesion: 0.09
Nodes (28): lego-builder subagent (Haiku), lego-solver subagent (Sonnet), runs/<record_id>/build_log.md, runs/<record_id>/expansion.md (description), Finish: finalize + voxel.sequence manual (build.stepped.ldr), runs/<record_id>/plan.json (checklist + sections), REBUILD request (SOLVER on/off), Review step (render + checklist pass/fail) (+20 more)

### Community 5 - "check.py"
Cohesion: 0.11
Nodes (7): check_build(), CheckResult, Shortfall, check_owned(), __getattr__(), color_code(), count_build()

### Community 6 - "expand.py"
Cohesion: 0.06
Nodes (35): capacity_table(), ask_questions(), color_stock(), ColorStock, expand(), expand_prompt(), ExpansionResult, inventory_summary() (+27 more)

### Community 7 - "field_validator"
Cohesion: 0.18
Nodes (5): bricks(), parse_color(), PlanPart, Position, Size

### Community 8 - "BrickWright Validation Harness"
Cohesion: 0.07
Nodes (32): Axis, Box, BrickWright (expert LEGO builder role), BrickWright Instructions, build.py, check_connected(expected_components), check_no_collisions(), check_no_floating() (+24 more)

### Community 9 - "llm.py"
Cohesion: 0.06
Nodes (22): _api_error_types(), call_api(), call_with_retry(), CallFailed, CutOff, default_complete(), finish_reason(), image_part() (+14 more)

### Community 10 - "capacity.py"
Cohesion: 0.05
Nodes (35): budget(), CapacityResult, _cells(), check_capacity(), check_demands(), checker_margin(), _code(), _credit_special() (+27 more)

### Community 11 - "Inventory"
Cohesion: 0.19
Nodes (4): part_size(), Plan, plan_errors(), spare_set()

### Community 13 - "order_build"
Cohesion: 0.20
Nodes (5): final_gate(), _gate_worker(), order_build(), Ordered, stepped_build()

### Community 14 - "Path"
Cohesion: 0.16
Nodes (10): new_record(), load_env(), load_record(), _read_json(), RecordError, render_build(), Reused, save_json() (+2 more)

### Community 18 - "plan.py"
Cohesion: 0.22
Nodes (9): _add_usage(), make_plan(), _plan_call(), plan_prompt(), PlanResult, revise_plan(), size_warning(), _sized_plan_call() (+1 more)

### Community 28 - "design.py"
Cohesion: 0.12
Nodes (7): collect_answers(), expansion_record(), plan_version(), set_expert(), Answer, ExpandError, PlanError

### Community 30 - "design.py"
Cohesion: 0.27
Nodes (11): budget_line(), builder_line(), cost_line(), no_build(), DesignResult, exit_code(), main(), models_line() (+3 more)

### Community 35 - "StrictModel"
Cohesion: 0.17
Nodes (5): ModelSize, Questions, CellCount, StrictModel, Verdict

### Community 37 - "Expansion"
Cohesion: 0.25
Nodes (3): Expansion, expansion_rules(), revise_prompt()

### Community 39 - "parse_plan"
Cohesion: 0.29
Nodes (4): parse_plan(), parse_revision(), Sizing, _user_size()

### Community 40 - "Review"
Cohesion: 0.53
Nodes (3): review_problem(), Review, review_report()

## Ambiguous Edges - Review These
- `STUCK report protocol` → `Build step 5: main session counts failures`  [AMBIGUOUS]
  CLAUDE.md · relation: conceptually_related_to

## Knowledge Gaps
- **25 isolated node(s):** `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring`, `Color`, `DoorVariant` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 242 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **26 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Inventory` connect `expand.py` to `Rebrickable Set Probe`, `check.py`, `Expansion`, `parse_plan`, `capacity.py`, `Inventory`, `build_prompt`, `Path`, `plan.py`, `design.py`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Inventory` (e.g. with `check_build()` and `owned_text()`) actually correct?**
  _`Inventory` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `BrickLayer Part Library` be split into smaller, more focused modules?**
  _Cohesion score 0.05136612021857923 - nodes in this community are weakly interconnected._
- **Why does `Plan` connect `Inventory` to `StrictModel`, `Expansion`, `expand.py`, `parse_plan`, `field_validator`, `llm.py`, `build_prompt`, `Path`, `plan.py`, `design.py`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `design()` (e.g. with `.allows()` and `.require()`) actually correct?**
  _`design()` has 6 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `STUCK report protocol` and `Build step 5: main session counts failures`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._