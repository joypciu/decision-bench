from __future__ import annotations

import json
from typing import Any

import jsonschema

from decision_bench.domain import Check, GoldCase


def schema_check(output: Any, schema: dict) -> Check:
    if not isinstance(output, dict):
        return Check("schema", False, "Output must be a JSON object.")
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as exc:
        return Check("schema", False, exc.message)
    return Check("schema", True, "Output matches the schema.")


def score_output(
    output: Any,
    schema: dict,
    case: GoldCase | None,
    child_count: int,
    *,
    enforce_children: bool,
) -> list[Check]:
    checks = [schema_check(output, schema)]
    if case is None:
        return checks
    for key, expected in case.expected.items():
        if key == "must_mention":
            blob = json.dumps(output).lower()
            missing = [item for item in expected if str(item).lower() not in blob]
            if missing:
                checks.append(Check("evidence", False, "Missing evidence: " + ", ".join(missing)))
            else:
                checks.append(Check("evidence", True, "Required evidence is present."))
            continue
        actual = output.get(key) if isinstance(output, dict) else None
        checks.append(
            Check(
                key,
                actual == expected,
                f"Expected {key}={expected!r}, got {actual!r}.",
            )
        )
    if enforce_children and case.expect_min_children:
        enough = child_count >= case.expect_min_children
        checks.append(
            Check(
                "delegation",
                enough,
                f"{child_count} child run(s); need at least {case.expect_min_children}.",
            )
        )
    return checks


def summarize(checks: list[Check]) -> tuple[bool, int]:
    if not checks:
        return False, 0
    passed = all(check.passed for check in checks)
    score = round(100 * sum(check.passed for check in checks) / len(checks))
    return passed, score
