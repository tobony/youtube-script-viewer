import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ["DB_PATH"] = "data/test_pipeline.db"

import app.db as db_module
from app.db import create_analysis, get_analysis, init_db, update_analysis
from app.pipeline import _resume_translate, run_pipeline

TEST_DB_PATH = "data/test_pipeline.db"


@pytest.fixture(autouse=True)
async def setup_db():
    previous_path = db_module.DB_PATH
    db_module.DB_PATH = TEST_DB_PATH
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    await init_db()
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    db_module.DB_PATH = previous_path


@pytest.mark.asyncio
async def test_run_pipeline_fails_when_transcript_is_empty():
    row = await create_analysis(
        video_id="I4zwZP80u-Y",
        url="https://www.youtube.com/watch?v=I4zwZP80u-Y",
    )

    fetched = {
        **row,
        "title": "How To Use Code Interpreter To Process Excel Documents",
        "error_message": None,
        "transcript": "",
        "transcript_lang": None,
    }
    with patch("app.pipeline._stage_fetch", return_value=fetched):
        await run_pipeline(row["id"])

    updated = await get_analysis(row["id"])
    assert updated["status"] == "failed"
    assert updated["error_message"] == "Transcript unavailable or could not be fetched"


@pytest.mark.asyncio
async def test_run_pipeline_skips_llm_when_disabled_for_job():
    row = await create_analysis(
        video_id="x1",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        llm_enabled=False,
    )
    transcript = '[{"start": 0, "text": "Hello world."}]'
    fetched = {
        **row,
        "title": "Video",
        "error_message": None,
        "transcript": transcript,
        "transcript_lang": "en",
        "llm_enabled": 0,
    }

    with patch("app.pipeline._stage_fetch", return_value=fetched), patch("app.llm.summarize") as summarize:
        await run_pipeline(row["id"])

    updated = await get_analysis(row["id"])
    assert updated["status"] == "completed"
    assert updated["summary_short"] is None
    assert updated["transcript_ko"] is None
    summarize.assert_not_called()


@pytest.mark.asyncio
async def test_run_pipeline_uses_job_llm_snapshot():
    row = await create_analysis(
        video_id="x1",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        llm_enabled=True,
    )
    transcript = '[{"start": 0, "text": "Hello world."}]'
    fetched = {
        **row,
        "title": "Video",
        "error_message": None,
        "transcript": transcript,
        "transcript_lang": "en",
        "llm_enabled": 1,
    }

    summarize = AsyncMock(return_value=("short", "structured"))
    translate = AsyncMock(return_value='[{"start": 0, "text": "안녕"}]')
    with (
        patch("app.pipeline._stage_fetch", return_value=fetched),
        patch("app.llm.summarize", summarize),
        patch("app.llm.translate_paragraphs", translate),
    ):
        await run_pipeline(
            row["id"],
            llm_provider="openai",
            summary_model="gpt-summary",
            translation_model="gpt-translation",
        )

    summarize.assert_awaited_once_with(transcript, provider="openai", model="gpt-summary")
    translate.assert_awaited_once()
    assert translate.await_args.kwargs["provider"] == "openai"
    assert translate.await_args.kwargs["model"] == "gpt-translation"


@pytest.mark.asyncio
async def test_run_pipeline_leaves_translation_blank_for_korean_source():
    row = await create_analysis(
        video_id="ko-video",
        url="https://www.youtube.com/watch?v=ko-video",
        llm_enabled=True,
    )
    transcript = '[{"start": 0, "text": "한국어 원문입니다."}]'
    fetched = {
        **row,
        "title": "한국어 영상",
        "error_message": None,
        "transcript": transcript,
        "transcript_lang": "ko",
        "llm_enabled": 1,
    }
    summarize = AsyncMock(return_value=("요약", "구조화 요약"))
    translate = AsyncMock()

    with (
        patch("app.pipeline._stage_fetch", return_value=fetched),
        patch("app.llm.summarize", summarize),
        patch("app.llm.translate_paragraphs", translate),
    ):
        await run_pipeline(row["id"])

    translate.assert_not_awaited()
    updated = await get_analysis(row["id"])
    assert updated["transcript_ko"] is None
    assert updated["status"] == "completed"


@pytest.mark.asyncio
async def test_resume_translate_retries_from_first_blank_translation():
    row = await create_analysis(
        video_id="x1",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        llm_enabled=True,
    )
    transcript = (
        "["
        '{"start": 0, "text": "First sentence."},'
        '{"start": 31, "text": "Second sentence."}'
        "]"
    )
    existing_ko = (
        "["
        '{"start": 0, "text": "첫 문장."},'
        '{"start": 31, "text": ""}'
        "]"
    )
    await update_analysis(row["id"], status="failed", transcript=transcript, transcript_ko=existing_ko)

    translate = AsyncMock(return_value='[{"start": 0, "text": "첫 문장."}, {"start": 31, "text": "둘째 문장."}]')
    with patch("app.llm.translate_paragraphs", translate):
        await _resume_translate(row["id"], llm_provider="openai", llm_model="gpt-test")

    translate.assert_awaited_once()
    assert translate.await_args.kwargs["skip_count"] == 1
    assert translate.await_args.kwargs["existing_ko"] == [{"start": 0, "text": "첫 문장."}]
    updated = await get_analysis(row["id"])
    assert updated["status"] == "completed"
