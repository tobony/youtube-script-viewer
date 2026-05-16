"""LLM service with provider-specific API adapters."""

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import APIStatusError, AsyncOpenAI

logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# --- LLM Provider Config (from .env) ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure")

# Azure OpenAI
AZURE_API_KEY = os.getenv("AZURE_API_KEY", "")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "")
AZURE_MODEL = os.getenv("AZURE_MODEL", "gpt-5.4-nano")

# kiro-gateway
KIRO_BASE_URL = os.getenv("KIRO_BASE_URL", "http://localhost:4000/v1")
KIRO_API_KEY = os.getenv("KIRO_API_KEY", "kiro-local")
KIRO_MODEL = os.getenv("KIRO_MODEL", "claude-haiku-4-5")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

# OpenRouter
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-5.4-mini")

PROVIDERS = {
    "azure": "Azure OpenAI",
    "kiro": "Kiro Gateway",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}


class LLMServiceError(RuntimeError):
    """User-facing LLM failure with configuration hints."""


def _get_client(provider: str | None = None, model: str | None = None, http_client: Any = None):
    provider = provider or LLM_PROVIDER
    if provider == "azure":
        return AsyncOpenAI(
            base_url=_normalize_azure_base_url(AZURE_ENDPOINT),
            api_key=AZURE_API_KEY,
            http_client=http_client,
        ), model or AZURE_MODEL, "responses"
    if provider == "kiro":
        return AsyncOpenAI(
            base_url=_normalize_base_url(KIRO_BASE_URL),
            api_key=KIRO_API_KEY,
            http_client=http_client,
        ), model or KIRO_MODEL, "chat"
    if provider == "openai":
        return AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client), model or OPENAI_MODEL, "responses"
    if provider == "openrouter":
        return AsyncOpenAI(
            base_url=_normalize_base_url(OPENROUTER_BASE_URL),
            api_key=OPENROUTER_API_KEY,
            http_client=http_client,
        ), model or OPENROUTER_MODEL, "responses"
    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


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
    if provider == "azure":
        return AZURE_MODEL
    if provider == "kiro":
        return KIRO_MODEL
    if provider == "openai":
        return OPENAI_MODEL
    if provider == "openrouter":
        return OPENROUTER_MODEL
    return ""


def set_model(provider: str, model: str) -> None:
    global AZURE_MODEL, KIRO_MODEL, OPENAI_MODEL, OPENROUTER_MODEL
    if provider == "azure":
        AZURE_MODEL = model
    elif provider == "kiro":
        KIRO_MODEL = model
    elif provider == "openai":
        OPENAI_MODEL = model
    elif provider == "openrouter":
        OPENROUTER_MODEL = model


def has_api_key(provider: str | None = None) -> bool:
    provider = provider or LLM_PROVIDER
    if provider == "azure":
        return bool(AZURE_API_KEY)
    if provider == "kiro":
        return bool(KIRO_API_KEY)
    if provider == "openai":
        return bool(OPENAI_API_KEY)
    if provider == "openrouter":
        return bool(OPENROUTER_API_KEY)
    return False


def set_api_key(provider: str, api_key: str) -> bool:
    global AZURE_API_KEY, KIRO_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY
    api_key = api_key.strip()
    if not api_key:
        return False
    if provider == "azure":
        AZURE_API_KEY = api_key
    elif provider == "kiro":
        KIRO_API_KEY = api_key
    elif provider == "openai":
        OPENAI_API_KEY = api_key
    elif provider == "openrouter":
        OPENROUTER_API_KEY = api_key
    else:
        return False
    return True


async def _chat(
    prompt: str,
    system: str = "",
    provider: str | None = None,
    model: str | None = None,
) -> str:
    provider = provider or LLM_PROVIDER
    client, resolved_model, api_style = _get_client(provider=provider, model=model)
    try:
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
    try:
        entries = json.loads(transcript_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    paragraphs = _merge(entries, 30)

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


def _merge(entries: list[dict], interval: int) -> list[dict]:
    if not entries:
        return []
    paragraphs = []
    current_start = entries[0]["start"]
    current_texts = []
    for entry in entries:
        if (entry["start"] - current_start >= interval
                and current_texts
                and current_texts[-1].rstrip()[-1:] in ".?!"):
            paragraphs.append({"start": current_start, "text": " ".join(current_texts)})
            current_start = entry["start"]
            current_texts = []
        current_texts.append(entry["text"])
    if current_texts:
        paragraphs.append({"start": current_start, "text": " ".join(current_texts)})
    return paragraphs
