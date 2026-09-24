import json

import httpx

from decision_bench.domain import Message, ToolCall, ToolSpec
from decision_bench.providers.gemini import GeminiProvider, to_gemini_contents, to_gemini_schema
from decision_bench.providers.http import provider_error_message
from decision_bench.providers.openrouter import OpenRouterProvider


def test_provider_error_message_uses_the_api_message():
    response = httpx.Response(429, json={"error": {"code": 429, "message": "Quota exceeded."}})
    assert provider_error_message(response) == "HTTP 429: Quota exceeded."


def test_gemini_retries_a_temporary_503():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {},
            },
        )

    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://example.test/v1beta",
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )
    provider.complete(
        model="gemini-3.6-flash",
        messages=[Message(role="user", content="case")],
        tools=[ToolSpec("finish", "done", {"type": "object"})],
        schema={},
    )
    assert calls["count"] == 2


def test_gemini_schema_uses_api_types():
    converted = to_gemini_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string", "enum": ["ship"]}},
        }
    )
    assert converted["type"] == "OBJECT"
    assert converted["properties"]["verdict"]["type"] == "STRING"
    assert "additionalProperties" not in converted


def test_gemini_replays_thought_signatures():
    contents = to_gemini_contents(
        [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall("call_0", "read_case", {}, thought_signature="sig-1"),
                ],
            )
        ]
    )
    part = contents[0]["parts"][0]
    assert part["functionCall"]["name"] == "read_case"
    assert part["thoughtSignature"] == "sig-1"


def test_gemini_parses_a_function_call():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "finish"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"name": "finish", "args": {"answer": "ok"}}}]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4},
            },
        )

    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://example.test/v1beta",
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )
    completion = provider.complete(
        model="gemini-3.6-flash",
        messages=[Message(role="user", content="case")],
        tools=[ToolSpec("finish", "done", {"type": "object"})],
        schema={},
    )
    assert completion.tool_calls[0].arguments == {"answer": "ok"}
    assert completion.prompt_tokens == 11


def test_openrouter_parses_a_tool_call():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.path == "/api/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "finish",
                                        "arguments": json.dumps({"answer": "ok"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
            },
        )

    provider = OpenRouterProvider(
        api_key="test-key",
        base_url="https://example.test/api/v1",
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )
    completion = provider.complete(
        model="example/free",
        messages=[Message(role="user", content="case")],
        tools=[ToolSpec("finish", "done", {"type": "object", "properties": {}})],
        schema={},
    )
    assert completion.tool_calls[0].name == "finish"
    assert completion.tool_calls[0].arguments == {"answer": "ok"}
