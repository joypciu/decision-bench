from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from decision_bench.app import create_app
from decision_bench.config import Settings
from decision_bench.services import start_run


@dataclass(frozen=True)
class Risk:
    severity: str
    file: str
    reason: str


@dataclass(frozen=True)
class Review:
    run_id: str
    verdict: str
    summary: str
    risks: list[Risk]
    status: str


def clean_file(path: str) -> str:
    name = path.replace("\\", "/").strip()
    if name.startswith("a/") or name.startswith("b/"):
        return name[2:]
    return name


class Reviewer:
    def __init__(self, state, provider: str = "demo") -> None:
        self.state = state
        self.provider = provider

    @classmethod
    def open(cls, *, bench_root: Path, database: Path, provider: str = "demo") -> Reviewer:
        settings = Settings(root=bench_root, database_path=database)
        app = create_app(settings)
        return cls(app.state.work, provider=provider)

    def review(self, diff: str, bot_id: str = "change-lead") -> Review:
        run = start_run(
            self.state,
            bot_id=bot_id,
            text=diff,
            provider=self.provider,
        )
        output = run.output if isinstance(run.output, dict) else {}
        risks = []
        for item in output.get("risks") or []:
            if not isinstance(item, dict):
                continue
            risks.append(
                Risk(
                    severity=str(item.get("severity") or "low"),
                    file=clean_file(str(item.get("file") or "unknown")),
                    reason=str(item.get("reason") or ""),
                )
            )
        verdict = output.get("verdict") or output.get("severity") or run.status
        return Review(
            run_id=run.id,
            verdict=str(verdict),
            summary=str(output.get("summary") or ""),
            risks=risks,
            status=run.status,
        )
