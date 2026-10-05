"""Compare stored case outcomes only when the frozen evaluation context matches."""
from collections import Counter

from .domain import EvalRun


def compare_evaluations(first: EvalRun, second: EvalRun) -> dict:
    left = {r["case_id"]: r for r in first.results}
    right = {r["case_id"]: r for r in second.results}
    rows = []
    duplicate = {key for side in (first.results, second.results)
                 for key, count in Counter(r["case_id"] for r in side).items() if count > 1}
    for case_id in dict.fromkeys([*left, *right]):
        before, after = left.get(case_id), right.get(case_id)
        if case_id in duplicate:
            state = "ambiguous"
        elif before is None:
            state = "added"
        elif after is None:
            state = "removed"
        elif first.pack_id != second.pack_id:
            state = "different_pack"
        elif not before.get("case_fingerprint") or not after.get("case_fingerprint"):
            state = "unknown_context"
        elif before["case_fingerprint"] != after["case_fingerprint"]:
            state = "case_changed"
        elif before["passed"] and not after["passed"]:
            state = "regressed"
        elif not before["passed"] and after["passed"]:
            state = "improved"
        else:
            state = "unchanged"
        rows.append({"case_id": case_id, "title": (after or before).get("title", case_id),
                     "before": before, "after": after, "state": state})
    counts = Counter(row["state"] for row in rows)
    return {"rows": rows, "counts": dict(counts),
            "comparable": sum(counts[key] for key in ("regressed", "improved", "unchanged"))}
