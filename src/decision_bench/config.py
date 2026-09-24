from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_root() -> Path:
    env = os.environ.get("DECISION_BENCH_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], Path.cwd()):
        if (candidate / "packs").is_dir():
            return candidate
    return Path.cwd()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    root: Path = Field(
        default_factory=default_root,
        validation_alias=AliasChoices("DECISION_BENCH_ROOT", "ROOT"),
    )
    database_path: Path = Field(default=Path("data/decision_bench.sqlite"))
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    request_timeout_s: float = 45.0

    def resolved_database_path(self) -> Path:
        path = self.database_path
        if not path.is_absolute():
            path = self.root / path
        return path
