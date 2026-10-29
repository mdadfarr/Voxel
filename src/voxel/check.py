"""The inventory check: does a finished build only use parts the user owns?

This is plain Python with no LLM. Later it plugs into BrickWright's validation
loop so a build that uses parts you do not own is rejected and the agent
revises it.
"""

from __future__ import annotations

from dataclasses import dataclass

import bricklayer as bl

from .inventory import Inventory, PartKey, count_build, key_label


@dataclass(frozen=True)
class Shortfall:
    key: PartKey
    used: int
    owned: int

    @property
    def over(self) -> int:
        return self.used - self.owned

    def message(self) -> str:
        return f"{key_label(self.key)}: used {self.used}, owned {self.owned} ({self.over} over)"


@dataclass(frozen=True)
class CheckResult:
    shortfalls: list[Shortfall]
    leftover: dict[PartKey, int]

    @property
    def ok(self) -> bool:
        return not self.shortfalls

    def report(self) -> str:
        """Text meant to be read by a person, and fed back to the agent on failure."""
        if self.ok:
            return "Inventory check passed: the build only uses parts you own."
        lines = ["Inventory check failed:"]
        lines += [f"  - {s.message()}" for s in self.shortfalls]
        return "\n".join(lines)


def check_build(build: bl.LegoBuild, inventory: Inventory) -> CheckResult:
    """Compare the parts a build uses against the parts owned."""
    used = count_build(build)
    shortfalls = [
        Shortfall(key, n, inventory.owned(key))
        for key, n in sorted(used.items())
        if n > inventory.owned(key)
    ]
    leftover = {
        key: owned - used.get(key, 0)
        for key, owned in sorted(inventory.counts.items())
        if owned - used.get(key, 0) > 0
    }
    return CheckResult(shortfalls, leftover)