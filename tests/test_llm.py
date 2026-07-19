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
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "/v1beta/openai/chat/completions", "chat"),
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
    elif provider == "gemini":
        monkeypatch.setattr(llm, "GEMINI_BASE_URL", base_url)
        monkeypatch.setattr(llm, "GEMINI_API_KEY", "test-key")

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


def test_provider_registry_includes_managed_and_api_key_providers():
    assert llm.get_provider_profile("gemini").auth_type == "api_key"
    assert llm.get_provider_profile("gemini").api_style == "chat"
    assert llm.get_provider_profile("codex_subscription").auth_type == "managed_login"
    assert llm.get_provider_profile("codex_subscription").api_style == "codex_app_server"


def test_provider_display_order():
    assert list(llm.PROVIDERS) == [
        "openai",
        "azure",
        "openrouter",
        "gemini",
        "codex_subscription",
        "kiro",
    ]


@pytest.mark.parametrize(
    ("provider", "summary_attr", "translation_attr"),
    [
        ("azure", "AZURE_SUMMARY_MODEL", "AZURE_TRANSLATION_MODEL"),
        ("kiro", "KIRO_SUMMARY_MODEL", "KIRO_TRANSLATION_MODEL"),
        ("openai", "OPENAI_SUMMARY_MODEL", "OPENAI_TRANSLATION_MODEL"),
        ("openrouter", "OPENROUTER_SUMMARY_MODEL", "OPENROUTER_TRANSLATION_MODEL"),
        ("gemini", "GEMINI_SUMMARY_MODEL", "GEMINI_TRANSLATION_MODEL"),
        ("codex_subscription", "CODEX_SUMMARY_MODEL", "CODEX_TRANSLATION_MODEL"),
    ],
)
def test_providers_use_task_specific_models(monkeypatch, provider, summary_attr, translation_attr):
    monkeypatch.setattr(llm, summary_attr, "summary-model")
    monkeypatch.setattr(llm, translation_attr, "translation-model")

    assert llm.get_task_model(provider, "summary") == "summary-model"
    assert llm.get_task_model(provider, "translation") == "translation-model"


def test_setting_task_models_keeps_translation_independent(monkeypatch):
    monkeypatch.setattr(llm, "OPENAI_MODEL", "legacy-model")
    monkeypatch.setattr(llm, "OPENAI_SUMMARY_MODEL", "old-summary")
    monkeypatch.setattr(llm, "OPENAI_TRANSLATION_MODEL", "old-translation")

    llm.set_task_model("openai", "summary", "new-summary")
    llm.set_task_model("openai", "translation", "new-translation")

    assert llm.OPENAI_MODEL == "new-summary"
    assert llm.get_task_model("openai", "summary") == "new-summary"
    assert llm.get_task_model("openai", "translation") == "new-translation"


def test_azure_connection_can_be_updated_for_the_runtime(monkeypatch):
    monkeypatch.setattr(llm, "AZURE_ENDPOINT", "")
    monkeypatch.setattr(llm, "AZURE_REGION", "")

    assert llm.set_azure_connection(
        " https://example.openai.azure.com/ ", " eastus2 "
    )
    assert llm.get_azure_connection() == {
        "endpoint": "https://example.openai.azure.com",
        "region": "eastus2",
    }


def test_azure_connection_rejects_invalid_endpoint(monkeypatch):
    monkeypatch.setattr(llm, "AZURE_ENDPOINT", "original")
    monkeypatch.setattr(llm, "AZURE_REGION", "original-region")

    assert not llm.set_azure_connection("example.openai.azure.com", "eastus2")
    assert llm.get_azure_connection() == {
        "endpoint": "original",
        "region": "original-region",
    }


@pytest.mark.asyncio
async def test_codex_subscription_routes_through_app_server(monkeypatch):
    seen = {}

    class FakeCodexClient:
        async def run_prompt(self, prompt, *, system="", model=""):
            seen.update(prompt=prompt, system=system, model=model)
            return "codex result"

    monkeypatch.setattr("app.codex_provider.get_codex_client", lambda: FakeCodexClient())

    result = await llm._chat(
        "translate me",
        system="translator",
        provider="codex_subscription",
        model="codex-test-model",
    )

    assert result == "codex result"
    assert seen == {
        "prompt": "translate me",
        "system": "translator",
        "model": "codex-test-model",
    }


@pytest.mark.asyncio
async def test_managed_auth_status_reports_unavailable_client(monkeypatch):
    class MissingCodexClient:
        available = False

    monkeypatch.setattr("app.codex_provider.get_codex_client", lambda: MissingCodexClient())

    status = await llm.get_auth_status("codex_subscription")

    assert status["available"] is False
    assert status["connected"] is False
