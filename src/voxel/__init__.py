from .check import CheckResult, Shortfall, check_build
from .inventory import Inventory, count_build, load_inventory


def __getattr__(name):
    # Loaded on demand so `python -m voxel.design` does not import design twice.
    if name == "check_owned":
        from .design import check_owned

        return check_owned
    raise AttributeError(f"module 'voxel' has no attribute {name!r}")


__all__ = [
    "CheckResult",
    "Inventory",
    "Shortfall",
    "check_build",
    "check_owned",
    "count_build",
    "load_inventory",
]
