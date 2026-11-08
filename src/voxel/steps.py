"""The step check: can the build be put together in its own step order?

Voxel computes the step order itself (voxel.sequence); this check is the safety
check on that computed order, not a rule for the builder.

For every build-step boundary, the parts placed so far must stand on their own:
nothing floats. Each step may add at most MAX_STEP_PARTS parts. Collisions are
not checked per step: a collision in a partial build is also in the full build,
which BrickWright already checks.
"""

from __future__ import annotations

import bricklayer as bl

STEPS_CHECK_NAME = "buildable_steps"
MAX_STEP_PARTS = 12


def describe_leaf(leaf: bl.Placement) -> str:
    element = leaf.content
    lo = leaf.bounding_box[0]
    return f"{element.color.display_name} {element.ldraw_id} at ({lo.x:g},{lo.y:g},{lo.z:g})"


def step_problems(build: bl.LegoBuild) -> list[str]:
    """One line per problem; an empty list means every step is buildable."""
    markers = list(build.step_markers)
    if not markers:
        return ["build has no step markers; call build.add_step() after each step"]
    if markers[-1] < len(build.placements):
        markers.append(len(build.placements))  # trailing parts form one more step

    ground = build._ground
    problems: list[str] = []
    reported: set[int] = set()  # leaf indexes already reported as floating
    previous_marker, previous_parts = 0, 0
    for number, marker in enumerate(markers, start=1):
        if marker == previous_marker:
            continue  # empty step
        partial = bl.LegoBuild(build.placements[:marker])
        # Depends on BrickLayer's private cached `_ground`: judge floating against
        # the full build's ground, not the partial build's own lowest point.
        # test_top_first_stack_fails_at_step_1 guards this.
        partial.__dict__["_ground"] = ground
        leaves = partial.flattened_placements
        added = len(leaves) - previous_parts
        if added > MAX_STEP_PARTS:
            problems.append(f"step {number}: {added} parts (max {MAX_STEP_PARTS})")
        floating = {id(leaf) for leaf in partial.floating_placements}
        for index, leaf in enumerate(leaves):
            if id(leaf) in floating and index not in reported:
                reported.add(index)
                problems.append(f"step {number}: {describe_leaf(leaf)} floats")
        previous_marker, previous_parts = marker, len(leaves)
    return problems


def check_steps(tests) -> None:
    """Call this inside run_tests(). Fails a test when a build step cannot stand."""
    problems = step_problems(tests.build)
    tests.check(STEPS_CHECK_NAME, not problems, None if not problems else "\n".join(problems))
