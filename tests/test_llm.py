import json

import httpx
import pytest

from app import llm


def _responses_payload(text: str = "ok") -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "test-model",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
    }


def _chat_payload(text: str = "ok") -> dict:
    return {
        "id": "chat_test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://example.services.ai.azure.com", "https://example.services.ai.azure.com/openai/v1"),
        ("https://example.services.ai.azure.com/openai/v1", "https://example.services.ai.azure.com/openai/v1"),
        ("https://example.services.ai.azure.com/openai/v1/responses", "https://example.services.ai.azure.com/openai/v1"),
        ("https://example.openai.azure.com", "https://example.openai.azure.com/openai/v1"),
    ],
)
def test_normalize_azure_base_url(endpoint, expected):
    assert llm._normalize_azure_base_url(endpoint) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "base_url", "expected_path", "style"),
    [
        ("azure", "https://example.services.ai.azure.com/openai/v1", "/openai/v1/responses", "responses"),
        ("openai", None, "/v1/responses", "responses"),
        ("openrouter", "https://openrouter.ai/api/v1", "/api/v1/responses", "responses"),
        ("kiro", "http://localhost:4000/v1", "/v1/chat/completions", "chat"),
    ],
)
async def test_provider_adapter_request_paths(monkeypatch, provider, base_url, expected_path, style):
    if provider == "azure":
        monkeypatch.setattr(llm, "AZURE_ENDPOINT", base_url)
        monkeypatch.setattr(llm, "AZURE_API_KEY", "test-key")
    elif provider == "openai":
        monkeypatch.setattr(llm, "OPENAI_API_KEY", "test-key")
    elif provider == "openrouter":
        monkeypatch.setattr(llm, "OPENROUTER_BASE_URL", base_url)
        monkeypatch.setattr(llm, "OPENROUTER_API_KEY", "test-key")
    elif provider == "kiro":
        monkeypatch.setattr(llm, "KIRO_BASE_URL", base_url)
        monkeypatch.setattr(llm, "KIRO_API_KEY", "test-key")

    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content.decode() or "{}")))
        if style == "responses":
            return httpx.Response(200, json=_responses_payload("response text"))
        return httpx.Response(200, json=_chat_payload("chat text"))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client, model, api_style = llm._get_client(
        provider=provider,
        model="anthropic/claude-test" if provider == "openrouter" else "test-model",
        http_client=http_client,
    )

    try:
        if api_style == "responses":
            text = await llm._call_responses(client, model, "hello", "system")
        else:
            text = await llm._call_chat_completions(client, model, "hello", "system")
    finally:
        await client.close()

    assert api_style == style
    assert seen[0][0] == expected_path
    assert seen[0][1]["model"] == ("anthropic/claude-test" if provider == "openrouter" else "test-model")
    if style == "responses":
        assert seen[0][1]["input"] == "hello"
        assert seen[0][1]["instructions"] == "system"
        assert seen[0][1]["max_output_tokens"] == 4096
        assert text == "response text"
    else:
        assert seen[0][1]["messages"][0] == {"role": "system", "content": "system"}
        assert seen[0][1]["messages"][1] == {"role": "user", "content": "hello"}
        assert seen[0][1]["max_completion_tokens"] == 4096
        assert text == "chat text"


def test_extract_responses_text_from_dict_output():
    payload = _responses_payload("nested text")
    assert llm._extract_responses_text(payload) == "nested text"
