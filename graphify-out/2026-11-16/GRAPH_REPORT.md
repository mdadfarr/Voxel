# Graph Report - Voxel  (2026-11-16)

## Corpus Check
- 23 files · ~27,335 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: (none) 2)

## Summary
- 703 nodes · 1518 edges · 51 communities (23 shown, 28 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 80 edges (avg confidence: 0.87)
- Token cost: 997,119 input · 0 output

## Graph Freshness
- Built from commit: `2e8a185d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- build_until_gate
- LegoBuild
- Rebrickable Set Probe
- Build Step Sequencing
- Build Mode Workflow Rules
- load_inventory
- expert.py
- review.py
- BrickWright Instructions
- llm.py
- capacity.py
- expand.py
- Plan
- load_record
- Cost Budget
- StrictModel
- Usage
- Plate Unit Conversion
- Package Config
- Roadmap
- plan.py
- design
- design.py
- repair.py
- Inventory
- HasUsage
- add_round_usage
- repair_line_text
- Expansion

## God Nodes (most connected - your core abstractions)
1. `design()` - 50 edges
2. `Inventory` - 48 edges
3. `Plan` - 27 edges
4. `LegoBuild` - 22 edges
5. `Usage` - 20 edges
6. `call_with_retry()` - 19 edges
7. `StrictModel` - 18 edges
8. `Expansion` - 17 edges
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

## Communities (51 total, 28 thin omitted)

### Community 0 - "build_until_gate"
Cohesion: 0.12
Nodes (13): builder_retries(), retrying_complete(), book_hook_reviews(), build_until_gate(), out_of_budget(), round_config(), expert_rescues(), feedback_prompt() (+5 more)

### Community 1 - "LegoBuild"
Cohesion: 0.05
Nodes (55): alignment_box, Anchor, Ball, brick(), BrickLayer library, Color, Component factories, Connections (connector mating) (+47 more)

### Community 2 - "Rebrickable Set Probe"
Cohesion: 0.07
Nodes (22): has_ldraw(), main(), read_key(), summarize(), strip_fences(), build_inventory(), _catalog_knows(), convert_rows() (+14 more)

### Community 3 - "Build Step Sequencing"
Cohesion: 0.09
Nodes (13): order_build(), Ordered, holders(), main(), moved_parts(), paginate(), Sequence, sequence() (+5 more)

### Community 4 - "Build Mode Workflow Rules"
Cohesion: 0.09
Nodes (28): lego-builder subagent (Haiku), lego-solver subagent (Sonnet), runs/<record_id>/build_log.md, runs/<record_id>/expansion.md (description), Finish: finalize + voxel.sequence manual (build.stepped.ldr), runs/<record_id>/plan.json (checklist + sections), REBUILD request (SOLVER on/off), Review step (render + checklist pass/fail) (+20 more)

### Community 5 - "load_inventory"
Cohesion: 0.08
Nodes (10): check_build(), CheckResult, Shortfall, check_owned(), _gate_worker(), __getattr__(), color_code(), color_label() (+2 more)

### Community 6 - "expert.py"
Cohesion: 0.16
Nodes (10): context(), _gap(), _line(), owned_text(), parts_left_text(), plan_section(), plan_section_text(), used_counts() (+2 more)

### Community 7 - "review.py"
Cohesion: 0.16
Nodes (13): review_problem(), default_complete(), StrictModel, Checklist, fixed_checklist(), parse_review(), review(), Review (+5 more)

### Community 8 - "BrickWright Instructions"
Cohesion: 0.07
Nodes (32): Axis, Box, BrickWright (expert LEGO builder role), BrickWright Instructions, build.py, check_connected(expected_components), check_no_collisions(), check_no_floating() (+24 more)

### Community 9 - "llm.py"
Cohesion: 0.11
Nodes (12): _api_error_types(), call_api(), call_with_retry(), CallFailed, CutOff, finish_reason(), image_part(), is_credit_error() (+4 more)

### Community 10 - "capacity.py"
Cohesion: 0.05
Nodes (36): budget(), capacity_table(), CapacityResult, _cells(), check_capacity(), check_demands(), checker_margin(), _code() (+28 more)

### Community 11 - "expand.py"
Cohesion: 0.08
Nodes (23): collect_answers(), expansion_record(), Answer, ask_questions(), color_stock(), ColorStock, expand(), expand_prompt() (+15 more)

### Community 12 - "Plan"
Cohesion: 0.15
Nodes (5): part_kind(), part_size(), Plan, spare_set(), _user_size()

### Community 14 - "load_record"
Cohesion: 0.16
Nodes (10): new_record(), final_gate(), load_record(), _read_json(), RecordError, render_build(), Reused, save_json() (+2 more)

### Community 18 - "StrictModel"
Cohesion: 0.13
Nodes (6): bricks(), CellCount, parse_color(), PlanPart, Position, Size

### Community 23 - "Usage"
Cohesion: 0.18
Nodes (3): ReplyError, StepError, Usage

### Community 27 - "plan.py"
Cohesion: 0.24
Nodes (8): _add_usage(), make_plan(), _plan_call(), PlanResult, revise_plan(), size_warning(), _sized_plan_call(), states_size()

### Community 28 - "design"
Cohesion: 0.10
Nodes (11): answers_line(), answers_record(), design(), review_entry(), plan_version(), require_owned_check(), set_expert(), UsageLedger (+3 more)

### Community 30 - "design.py"
Cohesion: 0.22
Nodes (12): budget_line(), builder_line(), cost_line(), no_build(), DesignResult, exit_code(), load_env(), main() (+4 more)

### Community 38 - "repair.py"
Cohesion: 0.16
Nodes (6): assistant_turns(), build_sha(), _hook_usage(), repair_feedback(), review_hook(), ReviewError

### Community 42 - "Inventory"
Cohesion: 0.16
Nodes (8): build_prompt(), direct_prompt(), owned_parts_text(), Inventory, parse_plan(), parse_revision(), plan_errors(), Sizing

### Community 43 - "HasUsage"
Cohesion: 0.16
Nodes (5): HasUsage, active(), RepairContext, Reviewed, ReviewResult

### Community 48 - "Expansion"
Cohesion: 0.17
Nodes (6): Expansion, color_name(), expansion_rules(), plan_format_and_rules(), plan_prompt(), revise_prompt()

## Ambiguous Edges - Review These
- `STUCK report protocol` → `Build step 5: main session counts failures`  [AMBIGUOUS]
  CLAUDE.md · relation: conceptually_related_to

## Knowledge Gaps
- **25 isolated node(s):** `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring`, `Color`, `DoorVariant` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 262 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **28 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Inventory` connect `Inventory` to `Rebrickable Set Probe`, `load_inventory`, `expert.py`, `capacity.py`, `expand.py`, `Plan`, `load_record`, `Expansion`, `plan.py`, `design.py`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `design()` (e.g. with `.allows()` and `.estimate()`) actually correct?**
  _`design()` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `build_until_gate` be split into smaller, more focused modules?**
  _Cohesion score 0.11695906432748537 - nodes in this community are weakly interconnected._
- **Why does `design()` connect `design` to `build_until_gate`, `load_inventory`, `repair.py`, `review.py`, `Inventory`, `expand.py`, `Plan`, `HasUsage`, `load_record`, `repair_line_text`, `Cost Budget`, `Usage`, `design.py`?**
  _High betweenness centrality (0.032) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Inventory` (e.g. with `check_build()` and `owned_text()`) actually correct?**
  _`Inventory` has 5 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `STUCK report protocol` and `Build step 5: main session counts failures`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._