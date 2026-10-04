"""Locate the Decision Bench checkout in the consolidated repository."""
import os
from pathlib import Path


def bench_root() -> Path:
    configured = os.environ.get("DECISION_BENCH_ROOT")
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        if (parent / "packs").is_dir() and (parent / "src" / "decision_bench").is_dir():
            return parent
    raise RuntimeError("Set DECISION_BENCH_ROOT to the Decision Bench checkout containing packs/.")
