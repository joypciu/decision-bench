from __future__ import annotations

import re

from urllib.parse import urlparse

from decision_bench.config import Settings
from decision_bench.domain import ProviderConfig
from decision_bench.ports import RunStore
from decision_bench.providers.registry import build_providers
from decision_bench.services import AppState
from decision_bench.storage.sqlite import now

SLUG = re.compile(r"[a-z][a-z0-9-]{1,31}")
PROTECTED = {"demo", "gemini", "openrouter"}


FREE_PRESETS = (
    ("groq", "openai", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    ("cerebras", "openai", "https://api.cerebras.ai/v1", "llama3.1-8b"),
    ("mistral", "openai", "https://api.mistral.ai/v1", "open-mistral-nemo"),
    ("together", "openai", "https://api.together.xyz/v1", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"),
    ("fireworks", "openai", "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p1-8b-instruct"),
    ("deepinfra", "openai", "https://api.deepinfra.com/v1/openai", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
    ("huggingface", "openai", "https://router.huggingface.co/v1", "meta-llama/Llama-3.1-8B-Instruct"),
    ("sambanova", "openai", "https://api.sambanova.ai/v1", "Meta-Llama-3.1-8B-Instruct"),
    ("ollama", "openai", "http://127.0.0.1:11434/v1", "llama3.2"),
    ("lmstudio", "openai", "http://127.0.0.1:1234/v1", "local-model"),
    ("tavily", "search", "https://api.tavily.com", "search"),
    ("brave", "search", "https://api.search.brave.com/res/v1", "web"),
    ("exa", "search", "https://api.exa.ai", "search"),
)


def seed_provider_configs(repo: RunStore, settings: Settings) -> None:
    specs = (
        ProviderConfig(
            name="gemini",
            kind="gemini",
            base_url=settings.gemini_base_url.rstrip("/"),
            api_key=settings.gemini_api_key.strip(),
            default_model=settings.gemini_model,
            enabled=True,
            builtin=True,
            created_at=now(),
        ),
        ProviderConfig(
            name="openrouter",
            kind="openai",
            base_url=settings.openrouter_base_url.rstrip("/"),
            api_key=settings.openrouter_api_key.strip(),
            default_model=settings.openrouter_model,
            enabled=True,
            builtin=True,
            created_at=now(),
        ),
    )
    for spec in specs:
        if repo.get_provider_config(spec.name) is None:
            repo.upsert_provider_config(spec)
    for name, kind, base_url, model in FREE_PRESETS:
        if repo.get_provider_config(name) is None:
            repo.upsert_provider_config(
                ProviderConfig(
                    name=name,
                    kind=kind,
                    base_url=base_url,
                    api_key="",
                    default_model=model,
                    enabled=False,
                    builtin=False,
                    created_at=now(),
                )
            )


def reload_providers(state: AppState) -> None:
    state.providers = build_providers(state.settings, state.repo.list_provider_configs())


def save_provider(
    state: AppState,
    *,
    name: str,
    kind: str,
    base_url: str,
    api_key: str,
    default_model: str,
    enabled: bool,
) -> ProviderConfig:
    slug = name.strip().lower()
    if slug == "demo":
        raise ValueError("demo is built in and does not use an API key.")
    if not SLUG.fullmatch(slug):
        raise ValueError("Use a short name like groq or together.")
    if kind not in {"gemini", "openai", "search"}:
        raise ValueError("Choose Gemini, an OpenAI-compatible API, or a search API.")
    url = base_url.strip().rstrip("/")
    if kind == "gemini" and not url:
        url = state.settings.gemini_base_url.rstrip("/")
    if not url.startswith(("http://", "https://")) or " " in url:
        raise ValueError("Base URL must start with http:// or https://.")
    model = default_model.strip()
    if not model:
        raise ValueError("A default model id is required.")
    existing = state.repo.get_provider_config(slug)
    key = api_key.strip()
    if not key and existing is not None:
        key = existing.api_key
    local = _local_base(url)
    if not key and not local and kind != "search":
        raise ValueError("An API key is required.")
    if not key and kind == "search":
        raise ValueError("An API key is required for search providers.")
    config = ProviderConfig(
        name=slug,
        kind=kind,
        base_url=url,
        api_key=key,
        default_model=model,
        enabled=enabled,
        builtin=bool(existing.builtin) if existing else slug in {"gemini", "openrouter"},
        created_at=existing.created_at if existing else now(),
    )
    state.repo.upsert_provider_config(config)
    reload_providers(state)
    return config


def remove_provider(state: AppState, name: str) -> None:
    slug = name.strip().lower()
    if slug in PROTECTED:
        raise ValueError("Built-in providers can be disabled, not removed.")
    if state.repo.get_provider_config(slug) is None:
        raise ValueError("Provider not found.")
    state.repo.delete_provider_config(slug)
    reload_providers(state)


def _local_base(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in {"localhost", "127.0.0.1"}


def mask_key(api_key: str) -> str:
    cleaned = api_key.strip()
    if not cleaned:
        return ""
    return "••••" + cleaned[-4:]


def public_provider(state: AppState, config: ProviderConfig) -> dict:
    live = state.providers.get(config.name)
    configured = bool(live and live.configured)
    if config.kind == "search":
        configured = bool(config.enabled and config.api_key.strip())
    return {
        "name": config.name,
        "kind": config.kind,
        "base_url": config.base_url,
        "default_model": config.default_model,
        "enabled": config.enabled,
        "builtin": config.builtin,
        "configured": configured,
        "key_hint": mask_key(config.api_key),
        "detail": getattr(live, "detail", config.kind),
    }
