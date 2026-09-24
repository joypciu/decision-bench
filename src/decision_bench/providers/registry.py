from __future__ import annotations

from urllib.parse import urlparse

from decision_bench.config import Settings
from decision_bench.domain import ProviderConfig
from decision_bench.ports import ModelProvider, ProviderError
from decision_bench.providers.demo import DemoProvider
from decision_bench.providers.gemini import GeminiProvider
from decision_bench.providers.openrouter import OpenAICompatibleProvider


class UnavailableProvider:
    configured = False
    default_model = ""

    def __init__(self, name: str, detail: str, default_model: str = "") -> None:
        self.name = name
        self.detail = detail
        self.default_model = default_model

    def complete(self, *, model: str, messages, tools, schema):
        del model, messages, tools, schema
        raise ProviderError(self.detail)


def build_providers(settings: Settings, configs: list[ProviderConfig] | None = None) -> dict[str, ModelProvider]:
    providers: dict[str, ModelProvider] = {"demo": DemoProvider()}
    for config in configs or []:
        providers[config.name] = _provider_for(settings, config)
    if "gemini" not in providers:
        providers["gemini"] = UnavailableProvider("gemini", "Add a Gemini API key in Settings.")
    if "openrouter" not in providers:
        providers["openrouter"] = UnavailableProvider("openrouter", "Add an OpenRouter API key in Settings.")
    return providers


def _provider_for(settings: Settings, config: ProviderConfig) -> ModelProvider:
    if config.kind == "search":
        return UnavailableProvider(config.name, f"{config.name} is a search provider, not a chat model.", config.default_model)
    if not config.enabled:
        return UnavailableProvider(config.name, f"{config.name} is disabled.", config.default_model)
    if config.kind == "openai":
        key = config.api_key.strip() or ("ollama" if _local(config.base_url) else "")
        if not key:
            return UnavailableProvider(config.name, f"Add an API key for {config.name} in Settings.", config.default_model)
        return OpenAICompatibleProvider(
            name=config.name,
            api_key=key,
            base_url=config.base_url,
            timeout_s=settings.request_timeout_s,
            detail="OpenAI-compatible",
            default_model=config.default_model,
        )
    if not config.api_key.strip():
        return UnavailableProvider(config.name, f"Add an API key for {config.name} in Settings.", config.default_model)
    if config.kind == "gemini":
        provider = GeminiProvider(
            api_key=config.api_key.strip(),
            base_url=config.base_url or settings.gemini_base_url,
            timeout_s=settings.request_timeout_s,
            detail="Google Gemini",
        )
        provider.default_model = config.default_model
        return provider
    return UnavailableProvider(config.name, f"Unknown provider kind {config.kind}.", config.default_model)


def _local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in {"localhost", "127.0.0.1"}
