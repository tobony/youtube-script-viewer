import os
from unittest.mock import patch

import pytest

os.environ["DB_PATH"] = "data/test_pipeline.db"

from app.db import create_analysis, get_analysis, init_db
from app.pipeline import run_pipeline


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    yield
    if os.path.exists("data/test_pipeline.db"):
        os.remove("data/test_pipeline.db")


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
