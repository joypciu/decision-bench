from __future__ import annotations

import re

from shipgate.review import Review


def changed_lines(diff: str) -> dict[str, int]:
    """First added line number in the new file, keyed by path."""
    path = ""
    new_line = 0
    found: dict[str, int] = {}
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            if name.startswith("b/"):
                name = name[2:]
            path = "" if name == "/dev/null" else name
            continue
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
        if match:
            new_line = int(match.group(1))
            continue
        if not path:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if path not in found:
                found[path] = new_line
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            new_line += 1
    return found


def diff_stats(diff: str) -> dict[str, int]:
    files = added = removed = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"files": files, "added": added, "removed": removed}


def inline_comments(review: Review, diff: str) -> list[dict]:
    lines = changed_lines(diff)
    comments = []
    used = set()
    for risk in review.risks:
        path = _match_path(risk.file, lines)
        if path is None or path in used:
            continue
        used.add(path)
        comments.append(
            {
                "path": path,
                "line": lines[path],
                "side": "RIGHT",
                "body": f"**{risk.severity}** {risk.reason}",
            }
        )
    return comments


def _match_path(file_name: str, lines: dict[str, int]) -> str | None:
    if file_name in lines:
        return file_name
    matches = [path for path in lines if path.endswith("/" + file_name)]
    if len(matches) == 1:
        return matches[0]
    return None
