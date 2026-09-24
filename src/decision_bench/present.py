from __future__ import annotations


def decision_of(output: dict | None) -> str | None:
    if not isinstance(output, dict):
        return None
    for key in ("verdict", "severity", "risk_level", "answer"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def summary_of(output: dict | None) -> str | None:
    if not isinstance(output, dict):
        return None
    for key in ("summary", "rationale", "notes"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def provider_hint(error: str | None) -> str | None:
    if not error:
        return None
    lowered = error.lower()
    if any(token in lowered for token in ("429", "503", "quota", "high demand", "rate limit")):
        return "This provider is busy or out of free quota. Run the same case on another configured provider."
    return None
