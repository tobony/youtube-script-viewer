"""LLM service with provider-specific API adapters."""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import APIStatusError, AsyncOpenAI
from app.transcript import merge_transcript_entries

logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# --- LLM Provider Config (from .env) ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure")

# Azure OpenAI
AZURE_API_KEY = os.getenv("AZURE_API_KEY", "")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "")
AZURE_REGION = os.getenv("AZURE_REGION", "")
AZURE_MODEL = os.getenv("AZURE_MODEL", "gpt-5.4-nano")
AZURE_SUMMARY_MODEL = os.getenv("AZURE_SUMMARY_MODEL", AZURE_MODEL)
AZURE_TRANSLATION_MODEL = os.getenv("AZURE_TRANSLATION_MODEL", AZURE_MODEL)

# kiro-gateway
KIRO_BASE_URL = os.getenv("KIRO_BASE_URL", "http://localhost:4000/v1")
KIRO_API_KEY = os.getenv("KIRO_API_KEY", "kiro-local")
KIRO_MODEL = os.getenv("KIRO_MODEL", "claude-haiku-4-5")
KIRO_SUMMARY_MODEL = os.getenv("KIRO_SUMMARY_MODEL", KIRO_MODEL)
KIRO_TRANSLATION_MODEL = os.getenv("KIRO_TRANSLATION_MODEL", KIRO_MODEL)

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", OPENAI_MODEL)
OPENAI_TRANSLATION_MODEL = os.getenv("OPENAI_TRANSLATION_MODEL", OPENAI_MODEL)

# OpenRouter
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-5.4-mini")
OPENROUTER_SUMMARY_MODEL = os.getenv("OPENROUTER_SUMMARY_MODEL", OPENROUTER_MODEL)
OPENROUTER_TRANSLATION_MODEL = os.getenv("OPENROUTER_TRANSLATION_MODEL", OPENROUTER_MODEL)

# Google Gemini (OpenAI compatibility endpoint)
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_SUMMARY_MODEL = os.getenv("GEMINI_SUMMARY_MODEL", GEMINI_MODEL)
GEMINI_TRANSLATION_MODEL = os.getenv("GEMINI_TRANSLATION_MODEL", GEMINI_MODEL)

# Codex with ChatGPT-managed subscription authentication
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.6-terra")
CODEX_SUMMARY_MODEL = os.getenv("CODEX_SUMMARY_MODEL", "gpt-5.6-terra")
CODEX_TRANSLATION_MODEL = os.getenv("CODEX_TRANSLATION_MODEL", "gpt-5.6-luna")


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    display_name: str
    api_style: str
    auth_type: str
    model_attr: str
    summary_model_attr: str
    translation_model_attr: str
    api_key_attr: str | None = None
    base_url_attr: str | None = None
    azure_endpoint: bool = False


PROVIDER_PROFILES = {
    profile.name: profile
    for profile in (
        ProviderProfile("openai", "OpenAI API", "responses", "api_key", "OPENAI_MODEL", "OPENAI_SUMMARY_MODEL", "OPENAI_TRANSLATION_MODEL", "OPENAI_API_KEY"),
        ProviderProfile("azure", "Azure OpenAI", "responses", "api_key", "AZURE_MODEL", "AZURE_SUMMARY_MODEL", "AZURE_TRANSLATION_MODEL", "AZURE_API_KEY", "AZURE_ENDPOINT", True),
        ProviderProfile("openrouter", "OpenRouter", "responses", "api_key", "OPENROUTER_MODEL", "OPENROUTER_SUMMARY_MODEL", "OPENROUTER_TRANSLATION_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"),
        ProviderProfile("gemini", "Google Gemini", "chat", "api_key", "GEMINI_MODEL", "GEMINI_SUMMARY_MODEL", "GEMINI_TRANSLATION_MODEL", "GEMINI_API_KEY", "GEMINI_BASE_URL"),
        ProviderProfile("codex_subscription", "Codex (ChatGPT subscription)", "codex_app_server", "managed_login", "CODEX_MODEL", "CODEX_SUMMARY_MODEL", "CODEX_TRANSLATION_MODEL"),
        ProviderProfile("kiro", "Kiro Gateway", "chat", "api_key", "KIRO_MODEL", "KIRO_SUMMARY_MODEL", "KIRO_TRANSLATION_MODEL", "KIRO_API_KEY", "KIRO_BASE_URL"),
    )
}
PROVIDERS = {name: profile.display_name for name, profile in PROVIDER_PROFILES.items()}


class LLMServiceError(RuntimeError):
    """User-facing LLM failure with configuration hints."""


def _get_client(provider: str | None = None, model: str | None = None, http_client: Any = None):
    provider = provider or LLM_PROVIDER
    profile = get_provider_profile(provider)
    if profile.api_style == "codex_app_server":
        raise ValueError("Codex subscription uses the app-server adapter, not AsyncOpenAI")
    kwargs: dict[str, Any] = {
        "api_key": globals().get(profile.api_key_attr or "", ""),
        "http_client": http_client,
    }
    if profile.base_url_attr:
        base_url = str(globals().get(profile.base_url_attr, ""))
        kwargs["base_url"] = _normalize_azure_base_url(base_url) if profile.azure_endpoint else _normalize_base_url(base_url)
    return AsyncOpenAI(**kwargs), model or get_model(provider), profile.api_style


def _normalize_azure_base_url(endpoint: str) -> str:
    endpoint = _normalize_base_url(endpoint)
    if not endpoint:
        return endpoint
    if endpoint.endswith("/openai/v1"):
        return endpoint
    if "services.ai.azure.com" in endpoint:
        return f"{endpoint}/openai/v1"
    if "openai.azure.com" in endpoint and "/openai/v1" not in endpoint:
        return f"{endpoint}/openai/v1"
    return endpoint


def _normalize_base_url(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    for suffix in ("/responses", "/chat/completions"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
    return endpoint


def get_model(provider: str | None = None) -> str:
    provider = provider or LLM_PROVIDER
    profile = PROVIDER_PROFILES.get(provider)
    return str(globals().get(profile.model_attr, "")) if profile else ""


def set_model(provider: str, model: str) -> None:
    profile = PROVIDER_PROFILES.get(provider)
    if profile:
        globals()[profile.model_attr] = model


def get_task_model(provider: str | None, task: str) -> str:
    provider = provider or LLM_PROVIDER
    profile = PROVIDER_PROFILES.get(provider)
    if profile and task in {"summary", "translation"}:
        attr = profile.summary_model_attr if task == "summary" else profile.translation_model_attr
        return str(globals().get(attr, ""))
    return get_model(provider)


def set_task_model(provider: str, task: str, model: str) -> None:
    profile = PROVIDER_PROFILES.get(provider)
    if not profile or task not in {"summary", "translation"}:
        return
    attr = profile.summary_model_attr if task == "summary" else profile.translation_model_attr
    globals()[attr] = model
    if task == "summary":
        # Keep the legacy/default model aligned with the summary model for callers
        # that do not yet pass an explicit task model.
        set_model(provider, model)


def get_provider_profile(provider: str | None = None) -> ProviderProfile:
    provider = provider or LLM_PROVIDER
    try:
        return PROVIDER_PROFILES[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}") from exc


def has_api_key(provider: str | None = None) -> bool:
    provider = provider or LLM_PROVIDER
    profile = PROVIDER_PROFILES.get(provider)
    return bool(profile and profile.api_key_attr and globals().get(profile.api_key_attr))


def set_api_key(provider: str, api_key: str) -> bool:
    api_key = api_key.strip()
    if not api_key:
        return False
    profile = PROVIDER_PROFILES.get(provider)
    if not profile or not profile.api_key_attr:
        return False
    globals()[profile.api_key_attr] = api_key
    return True


def get_azure_connection() -> dict[str, str]:
    return {"endpoint": AZURE_ENDPOINT, "region": AZURE_REGION}


def set_azure_connection(endpoint: str, region: str) -> bool:
    endpoint = endpoint.strip().rstrip("/")
    region = region.strip()
    if not endpoint or not endpoint.startswith(("https://", "http://")):
        return False
    globals()["AZURE_ENDPOINT"] = endpoint
    globals()["AZURE_REGION"] = region
    return True


async def get_auth_status(provider: str | None = None) -> dict[str, Any]:
    profile = get_provider_profile(provider)
    if profile.auth_type == "api_key":
        return {"available": True, "connected": has_api_key(profile.name), "auth_mode": "api_key"}
    from app.codex_provider import CodexProviderError, get_codex_client

    client = get_codex_client()
    if not client.available:
        return {"available": False, "connected": False, "error": "Codex 실행 파일을 찾을 수 없습니다."}
    try:
        return await client.account_status()
    except CodexProviderError as exc:
        return {"available": False, "connected": False, "error": str(exc)}


async def start_managed_login(provider: str, mode: str = "chatgpt") -> dict[str, Any]:
    profile = get_provider_profile(provider)
    if profile.auth_type != "managed_login":
        raise LLMServiceError("이 provider는 관리형 로그인을 지원하지 않습니다.")
    from app.codex_provider import get_codex_client

    return await get_codex_client().start_login(mode)


async def logout_managed_provider(provider: str) -> None:
    profile = get_provider_profile(provider)
    if profile.auth_type != "managed_login":
        return
    from app.codex_provider import get_codex_client

    await get_codex_client().logout()


async def list_available_models(provider: str | None = None) -> list[dict[str, Any]]:
    profile = get_provider_profile(provider)
    if profile.api_style != "codex_app_server":
        model = get_model(profile.name)
        return [{"id": model, "display_name": model, "is_default": True}] if model else []
    from app.codex_provider import get_codex_client

    return await get_codex_client().list_models()


async def _chat(
    prompt: str,
    system: str = "",
    provider: str | None = None,
    model: str | None = None,
) -> str:
    provider = provider or LLM_PROVIDER
    resolved_model = model or get_model(provider)
    try:
        if get_provider_profile(provider).api_style == "codex_app_server":
            from app.codex_provider import get_codex_client

            return await get_codex_client().run_prompt(prompt, system=system, model=resolved_model)
        client, resolved_model, api_style = _get_client(provider=provider, model=resolved_model)
        if api_style == "responses":
            return await _call_responses(client, resolved_model, prompt, system)
        return await _call_chat_completions(client, resolved_model, prompt, system)
    except Exception as e:
        raise LLMServiceError(_format_llm_error(e, provider=provider, model=resolved_model)) from e


async def _call_responses(client: AsyncOpenAI, model: str, prompt: str, system: str = "") -> str:
    kwargs = {
        "model": model,
        "input": prompt,
        "max_output_tokens": 4096,
    }
    if system:
        kwargs["instructions"] = system
    resp = await client.responses.create(**kwargs)
    return _extract_responses_text(resp)


async def _call_chat_completions(client: AsyncOpenAI, model: str, prompt: str, system: str = "") -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_completion_tokens=4096,
    )
    return resp.choices[0].message.content or ""


def _extract_responses_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    output = getattr(response, "output", None)
    if not output and isinstance(response, dict):
        output = response.get("output")
    texts = []
    for item in output or []:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        for part in content or []:
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts)


def _format_llm_error(error: Exception, provider: str | None = None, model: str | None = None) -> str:
    provider = provider or LLM_PROVIDER
    model = model or get_model(provider)
    if isinstance(error, APIStatusError) and error.status_code == 404:
        if provider == "azure":
            return (
                "Azure OpenAI resource not found. Check AZURE_ENDPOINT and make sure the model is the "
                f"deployment name. Current endpoint: {_normalize_azure_base_url(AZURE_ENDPOINT)}. "
                f"Current model: {model}"
            )
        return (
            f"{PROVIDERS.get(provider, provider)} resource not found. "
            f"Check the selected model name. Current model: {model}"
        )
    return str(error)


async def translate_paragraphs(
    transcript_json: str,
    on_progress=None,
    skip_count: int = 0,
    existing_ko: list | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Translate transcript JSON entries, return same JSON format with Korean text.
    on_progress(ko_paragraphs_json) is called after each paragraph is translated.
    skip_count: number of already-translated paragraphs to skip.
    existing_ko: previously translated paragraphs to prepend.
    """
    provider = provider or LLM_PROVIDER
    model = model or get_task_model(provider, "translation")
    try:
        entries = json.loads(transcript_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    paragraphs = merge_transcript_entries(entries)

    system = (
        "You are a translator. Translate the given English text to natural Korean. "
        "Keep technical terms in original with Korean explanation in parentheses. "
        "Output ONLY the translation, nothing else."
    )

    ko_paragraphs = list(existing_ko) if existing_ko else []
    for para in paragraphs[skip_count:]:
        try:
            ko_text = await _chat(para["text"], system=system, provider=provider, model=model)
            ko_paragraphs.append({"start": para["start"], "text": ko_text})
        except LLMServiceError as e:
            logger.warning("Translation failed: %s", e)
            raise
        except Exception as e:
            message = _format_llm_error(e, provider=provider, model=model)
            logger.warning("Translation failed: %s", message)
            raise LLMServiceError(message) from e

        if on_progress:
            await on_progress(json.dumps(ko_paragraphs, ensure_ascii=False))

    return json.dumps(ko_paragraphs, ensure_ascii=False)


async def summarize(transcript_json: str, provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    """Generate Korean summary from transcript."""
    provider = provider or LLM_PROVIDER
    model = model or get_task_model(provider, "summary")
    try:
        entries = json.loads(transcript_json)
    except (json.JSONDecodeError, TypeError):
        return "", ""

    text = " ".join(e["text"] for e in entries)[:8000]

    prompt = (
        "다음 YouTube 영상 스크립트를 분석해서 두 가지 요약을 한국어로 만들어줘.\n\n"
        "1. **짧은 요약**: 3-5문장으로 핵심 내용 정리\n"
        "2. **구조화 요약**: 주제별로 bullet point 형태로 정리\n\n"
        "구조화 요약 규칙:\n"
        "- 마크다운 헤딩(#, ##, ###)은 절대 사용하지 마\n"
        "- 주제 제목은 **굵은 글씨**로 표시 (예: **주제명**)\n"
        "- 하위 내용은 bullet point(- 또는 •)로 나열\n\n"
        "반드시 아래 형식으로 응답해:\n"
        "---SHORT---\n(짧은 요약)\n---STRUCTURED---\n(구조화 요약)\n\n"
        f"스크립트:\n{text}"
    )

    try:
        result = await _chat(prompt, provider=provider, model=model)
    except LLMServiceError as e:
        logger.warning("Summarize failed: %s", e)
        raise
    except Exception as e:
        message = _format_llm_error(e, provider=provider, model=model)
        logger.warning("Summarize failed: %s", message)
        raise LLMServiceError(message) from e

    short = ""
    structured = ""
    if "---SHORT---" in result and "---STRUCTURED---" in result:
        parts = result.split("---STRUCTURED---")
        short = parts[0].split("---SHORT---")[-1].strip()
        structured = parts[1].strip()
    else:
        short = result[:500]

    return short, structured
