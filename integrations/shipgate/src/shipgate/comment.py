from __future__ import annotations

from shipgate.review import Review


def format_comment(review: Review) -> str:
    lines = [f"**{review.verdict}**", "", review.summary.strip() or "No summary.", ""]
    if review.risks:
        for risk in review.risks:
            lines.append(f"- **{risk.severity}** `{risk.file}` — {risk.reason}")
    else:
        lines.append("No file-level risks.")
    lines.append("")
    lines.append(f"Decision Bench run `{review.run_id}`.")
    return "\n".join(lines)


def status_for(review: Review) -> tuple[str, str]:
    if review.status == "succeeded" and review.verdict == "ship":
        state = "success"
    elif review.verdict in {"revise", "block"}:
        state = "failure"
    else:
        state = "error"
    detail = review.verdict
    if review.risks:
        detail = f"{review.verdict}: {review.risks[0].file}"
    return state, detail[:140]


def review_event(verdict: str) -> str:
    if verdict == "ship":
        return "APPROVE"
    if verdict == "block":
        return "REQUEST_CHANGES"
    return "COMMENT"
