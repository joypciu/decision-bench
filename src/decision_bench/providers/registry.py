from __future__ import annotations

from decision_bench.config import Settings
from decision_bench.ports import ModelProvider, ProviderError
from decision_bench.providers.demo import DemoProvider
from decision_bench.providers.gemini import GeminiProvider
from decision_bench.providers.openrouter import OpenRouterProvider


class UnavailableProvider:
    configured = False

    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail

    def complete(self, *, model: str, messages, tools, schema):
        del model, messages, tools, schema
        raise ProviderError(self.detail)


def build_providers(settings: Settings) -> dict[str, ModelProvider]:
    providers: dict[str, ModelProvider] = {"demo": DemoProvider()}
    if settings.gemini_api_key.strip():
        providers["gemini"] = GeminiProvider(
            api_key=settings.gemini_api_key.strip(),
            base_url=settings.gemini_base_url,
            timeout_s=settings.request_timeout_s,
        )
    else:
        providers["gemini"] = UnavailableProvider("gemini", "Set GEMINI_API_KEY to call Gemini.")
    if settings.openrouter_api_key.strip():
        providers["openrouter"] = OpenRouterProvider(
            api_key=settings.openrouter_api_key.strip(),
            base_url=settings.openrouter_base_url,
            timeout_s=settings.request_timeout_s,
        )
    else:
        providers["openrouter"] = UnavailableProvider(
            "openrouter",
            "Set OPENROUTER_API_KEY to call OpenRouter.",
        )
    return providers
