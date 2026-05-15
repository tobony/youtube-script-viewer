"""LLM service — Azure OpenAI (default) or kiro-gateway."""

import asyncio
import json
import logging
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI, AsyncAzureOpenAI

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


def _get_client():
    if LLM_PROVIDER == "azure":
        return AsyncOpenAI(
            base_url=AZURE_ENDPOINT,
            api_key=AZURE_API_KEY,
        ), AZURE_MODEL
    else:
        return AsyncOpenAI(
            base_url=KIRO_BASE_URL,
            api_key=KIRO_API_KEY,
        ), KIRO_MODEL


async def _chat(prompt: str, system: str = "") -> str:
    client, model = _get_client()
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


async def translate_paragraphs(transcript_json: str, on_progress=None) -> str:
    """Translate transcript JSON entries, return same JSON format with Korean text.
    on_progress(ko_paragraphs_json) is called after each paragraph is translated.
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

    ko_paragraphs = []
    for para in paragraphs:
        try:
            ko_text = await _chat(para["text"], system=system)
            ko_paragraphs.append({"start": para["start"], "text": ko_text})
        except Exception as e:
            logger.warning(f"Translation failed: {e}")
            ko_paragraphs.append({"start": para["start"], "text": ""})

        # Progressive update after each paragraph
        if on_progress:
            await on_progress(json.dumps(ko_paragraphs, ensure_ascii=False))

    return json.dumps(ko_paragraphs, ensure_ascii=False)


async def summarize(transcript_json: str) -> tuple[str, str]:
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
        result = await _chat(prompt)
    except Exception as e:
        logger.warning(f"Summarize failed: {e}")
        return "", ""

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
