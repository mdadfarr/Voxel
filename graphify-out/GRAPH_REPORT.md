# Graph Report - Voxel  (2026-11-16)

## Corpus Check
- 24 files · ~28,786 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: (none) 2)

## Summary
- 735 nodes · 1590 edges · 53 communities (26 shown, 27 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 81 edges (avg confidence: 0.87)
- Token cost: 997,119 input · 0 output

## Graph Freshness
- Built from commit: `2e8a185d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- build_until_gate
- LegoBuild.connect
- Rebrickable Set Probe
- Build Step Sequencing
- Build Mode Workflow Rules
- load_inventory
- display.py
- review.py
- BrickWright Instructions
- llm.py
- capacity.py
- expand.py
- PartShape
- load_record
- Cost Budget
- plan.py
- Usage
- Plate Unit Conversion
- Package Config
- Roadmap
- Component factories
- design
- design.py
- repair.py
- LegoBuild
- HasUsage
- add_round_usage
- BrickLayer library
- Answer
- MotionReport
- door

## God Nodes (most connected - your core abstractions)
1. `design()` - 51 edges
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
- `review_problem()` --uses--> `Review`  [INFERRED]
  src/voxel/design.py → src/voxel/review.py
- `retrying_complete()` --calls--> `call_api()`  [EXTRACTED]
  src/voxel/design.py → src/voxel/llm.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Joint motion annotation and sampling** — instructions_joint, instructions_motionrange, instructions_legobuild_check_motion, instructions_motionreport, instructions_legobuild_posed, instructions_tests_check_motion [EXTRACTED 1.00]
- **LegoBuild placement methods** — instructions_legobuild_add, instructions_legobuild_stack, instructions_legobuild_connect, instructions_legobuild_add_raw, instructions_legobuild_extend [EXTRACTED 1.00]
- **Required build validity checks in run_tests** — instructions_run_tests, instructions_check_no_floating, instructions_check_no_collisions, instructions_check_connected, instructions_testcontext [EXTRACTED 1.00]
- **Build mode planner/builder/solver/reviewer pipeline** — claude_voxel_build_mode, _claude_agents_lego_builder_lego_builder, _claude_agents_lego_solver_lego_solver, claude_build_loop, claude_review_step, claude_finish_sequence [EXTRACTED 1.00]
- **voxel.design Expand -> Plan -> Build -> Review pipeline** — readme_voxel_design, readme_expand_step, readme_planning_call, readme_build_sequencing, readme_ask_expert, readme_review_call [EXTRACTED 1.00]

## Communities (53 total, 27 thin omitted)

### Community 0 - "build_until_gate"
Cohesion: 0.12
Nodes (13): builder_retries(), retrying_complete(), book_hook_reviews(), build_until_gate(), out_of_budget(), round_config(), expert_rescues(), feedback_prompt() (+5 more)

### Community 1 - "LegoBuild.connect"
Cohesion: 0.15
Nodes (18): Ball, Connections (connector mating), Connector, ConnectorType, Cylindrical, Direction, Fixed, Joint (+10 more)

### Community 2 - "Rebrickable Set Probe"
Cohesion: 0.07
Nodes (21): has_ldraw(), main(), read_key(), summarize(), build_inventory(), _catalog_knows(), convert_rows(), Converted (+13 more)

### Community 3 - "Build Step Sequencing"
Cohesion: 0.09
Nodes (13): order_build(), Ordered, holders(), main(), moved_parts(), paginate(), Sequence, sequence() (+5 more)

### Community 4 - "Build Mode Workflow Rules"
Cohesion: 0.09
Nodes (28): lego-builder subagent (Haiku), lego-solver subagent (Sonnet), runs/<record_id>/build_log.md, runs/<record_id>/expansion.md (description), Finish: finalize + voxel.sequence manual (build.stepped.ldr), runs/<record_id>/plan.json (checklist + sections), REBUILD request (SOLVER on/off), Review step (render + checklist pass/fail) (+20 more)

### Community 5 - "load_inventory"
Cohesion: 0.07
Nodes (17): special_errors(), check_build(), CheckResult, Shortfall, build_prompt(), check_owned(), direct_prompt(), _gate_worker() (+9 more)

### Community 6 - "display.py"
Cohesion: 0.11
Nodes (14): display_image(), ldraw_rotation(), _normal(), _number(), parse_pose(), Pose, pose_note(), read_pose() (+6 more)

### Community 7 - "review.py"
Cohesion: 0.18
Nodes (11): default_complete(), StrictModel, Checklist, fixed_checklist(), parse_review(), review(), Review, review_errors() (+3 more)

### Community 8 - "BrickWright Instructions"
Cohesion: 0.19
Nodes (13): BrickWright (expert LEGO builder role), BrickWright Instructions, build.py, check_connected(expected_components), check_no_collisions(), check_no_floating(), create_build(), Validation harness (+5 more)

### Community 9 - "llm.py"
Cohesion: 0.11
Nodes (11): _api_error_types(), call_api(), call_with_retry(), CutOff, finish_reason(), image_part(), is_credit_error(), response_cost() (+3 more)

### Community 10 - "capacity.py"
Cohesion: 0.06
Nodes (34): budget(), capacity_table(), CapacityResult, _cells(), check_capacity(), check_demands(), checker_margin(), _code() (+26 more)

### Community 11 - "expand.py"
Cohesion: 0.06
Nodes (33): ask_questions(), color_stock(), ColorStock, expand(), expand_prompt(), ExpansionResult, inventory_summary(), is_size_question() (+25 more)

### Community 12 - "PartShape"
Cohesion: 0.12
Nodes (13): Axis, Box, Chirality, Disc, Ell, Footprint, Length, mirror() (+5 more)

### Community 14 - "load_record"
Cohesion: 0.16
Nodes (10): new_record(), final_gate(), load_record(), _read_json(), RecordError, render_build(), Reused, save_json() (+2 more)

### Community 18 - "plan.py"
Cohesion: 0.05
Nodes (26): Expansion, part_kind(), part_size(), _add_usage(), bricks(), CellCount, expansion_rules(), make_plan() (+18 more)

### Community 23 - "Usage"
Cohesion: 0.15
Nodes (5): CallFailed, ReplyError, StepError, Usage, _hook_usage()

### Community 27 - "Component factories"
Cohesion: 0.21
Nodes (15): brick(), Color, Component factories, Element, fill_inverted_slope(), fill_slope(), inverted_slope(), minifig() (+7 more)

### Community 28 - "design"
Cohesion: 0.08
Nodes (13): design(), review_entry(), plan_version(), repair_line_text(), require_owned_check(), review_problem(), set_expert(), UsageLedger (+5 more)

### Community 30 - "design.py"
Cohesion: 0.22
Nodes (12): budget_line(), builder_line(), cost_line(), no_build(), DesignResult, exit_code(), load_env(), main() (+4 more)

### Community 38 - "repair.py"
Cohesion: 0.18
Nodes (5): assistant_turns(), build_sha(), repair_feedback(), review_hook(), ReviewError

### Community 42 - "LegoBuild"
Cohesion: 0.23
Nodes (9): alignment_box, Anchor, LegoBuild, LegoBuild.add(), LegoBuild.extend(), LegoBuild.stack(), LegoGeometry, Local frames and alignment-box anchoring (+1 more)

### Community 43 - "HasUsage"
Cohesion: 0.20
Nodes (4): HasUsage, RepairContext, Reviewed, ReviewResult

### Community 46 - "BrickLayer library"
Cohesion: 0.22
Nodes (8): BrickLayer library, BrickLayer coordinate system (+x left, +y back, +z up; studs/bricks), LegoBuild.add_raw(), Mat3, Pose, Position, Rotation, Vec3

### Community 48 - "Answer"
Cohesion: 0.22
Nodes (5): answers_line(), answers_record(), collect_answers(), expansion_record(), Answer

### Community 49 - "MotionReport"
Cohesion: 0.33
Nodes (6): LegoBuild.check_motion(), MotionCollision, MotionDisconnection, MotionFloating, MotionReport, tests.check_motion(samples)

### Community 50 - "door"
Cohesion: 0.40
Nodes (5): door(), DoorVariant, FrameVariant, PaneVariant, window()

## Ambiguous Edges - Review These
- `STUCK report protocol` → `Build step 5: main session counts failures`  [AMBIGUOUS]
  CLAUDE.md · relation: conceptually_related_to

## Knowledge Gaps
- **25 isolated node(s):** `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring`, `Color`, `DoorVariant` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 273 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **27 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Inventory` connect `load_inventory` to `Rebrickable Set Probe`, `capacity.py`, `expand.py`, `load_record`, `plan.py`, `design.py`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `design()` (e.g. with `.allows()` and `.estimate()`) actually correct?**
  _`design()` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `voxel`, `Jumper half-stud offset`, `Local frames and alignment-box anchoring` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `build_until_gate` be split into smaller, more focused modules?**
  _Cohesion score 0.11695906432748537 - nodes in this community are weakly interconnected._
- **Why does `design()` connect `design` to `build_until_gate`, `load_inventory`, `display.py`, `repair.py`, `review.py`, `HasUsage`, `load_record`, `Answer`, `Cost Budget`, `plan.py`, `Usage`, `design.py`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Inventory` (e.g. with `check_build()` and `owned_text()`) actually correct?**
  _`Inventory` has 5 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `STUCK report protocol` and `Build step 5: main session counts failures`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._