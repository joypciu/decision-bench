from __future__ import annotations

import os
import sys
from pathlib import Path

from shipgate.comment import format_comment
from shipgate.review import Reviewer
from shipgate.paths import bench_root


def review_path(path: str) -> int:
    reviewer = Reviewer.open(
        bench_root=bench_root(),
        database=Path(os.environ.get("SHIPGATE_DATA", Path(__file__).resolve().parents[2] / "data")) / "cli.sqlite",
        provider=os.environ.get("SHIPGATE_PROVIDER", "demo"),
    )
    review = reviewer.review(Path(path).read_text(encoding="utf-8"))
    print(format_comment(review))
    return 0 if review.verdict == "ship" else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "review":
        if len(args) != 2:
            print("Usage: python -m shipgate review <diff-file>", file=sys.stderr)
            return 2
        return review_path(args[1])
    from shipgate.main import serve

    serve()
    return 0
