import os
import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient

os.environ["DB_PATH"] = "data/test_analyze.db"

from app.main import app
import app.db as db_module
from app.db import create_analysis, get_analysis, init_db, update_analysis

TEST_DB_PATH = "data/test_analyze.db"


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
async def test_analyze_invalid_url():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/analyze", json={"url": "not-a-url"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_analyze_starts_pipeline():
    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        assert r.status_code == 200
        data = r.json()
        assert data["video_id"] == "dQw4w9WgXcQ"
        assert data["status"] == "pending"
        assert data["llm_enabled"] is True
        mock_start.assert_called_once_with(
            data["id"], llm_provider=None, llm_model=None, summary_model=None, translation_model=None
        )


@pytest.mark.asyncio
async def test_analyze_stores_llm_enabled_for_job():
    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/analyze",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "llm_enabled": False},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["llm_enabled"] is False
    mock_start.assert_called_once_with(
        data["id"], llm_provider=None, llm_model=None, summary_model=None, translation_model=None
    )


@pytest.mark.asyncio
async def test_analyze_passes_llm_snapshot_to_job():
    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/analyze",
                json={
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "llm_provider": "openai",
                    "llm_model": "gpt-test",
                    "summary_model": "gpt-summary",
                    "translation_model": "gpt-translation",
                },
            )

    assert r.status_code == 200
    data = r.json()
    mock_start.assert_called_once_with(
        data["id"],
        llm_provider="openai",
        llm_model="gpt-test",
        summary_model="gpt-summary",
        translation_model="gpt-translation",
    )


@pytest.mark.asyncio
async def test_analyze_retries_empty_completed_result_as_new_revision():
    stale = await create_analysis(
        video_id="I4zwZP80u-Y",
        url="https://www.youtube.com/watch?v=I4zwZP80u-Y",
    )
    await update_analysis(stale["id"], status="completed", transcript="")

    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=I4zwZP80u-Y"})

    assert r.status_code == 200
    data = r.json()
    assert data["id"] != stale["id"]
    assert data["parent_analysis_id"] == stale["id"]
    assert data["revision_number"] == 2
    assert data["is_active"] is False
    assert data["status"] == "pending"
    assert data["transcript"] is None
    preserved = await get_analysis(stale["id"])
    assert preserved["status"] == "completed"
    assert preserved["transcript"] == ""
    mock_start.assert_called_once_with(
        data["id"], llm_provider=None, llm_model=None, summary_model=None, translation_model=None
    )


@pytest.mark.asyncio
async def test_analyze_retries_failed_existing_result_as_new_revision():
    stale = await create_analysis(
        video_id="I4zwZP80u-Y",
        url="https://www.youtube.com/watch?v=I4zwZP80u-Y",
        llm_enabled=False,
    )
    await update_analysis(
        stale["id"],
        status="failed",
        error_message="Transcript unavailable or could not be fetched",
        summary_short="old summary",
    )

    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/analyze",
                json={"url": "https://www.youtube.com/watch?v=I4zwZP80u-Y", "llm_enabled": True},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["id"] != stale["id"]
    assert data["parent_analysis_id"] == stale["id"]
    assert data["is_active"] is False
    assert data["status"] == "pending"
    assert data["llm_enabled"] is True
    assert data["error_message"] is None
    assert data["summary_short"] is None
    preserved = await get_analysis(stale["id"])
    assert preserved["status"] == "failed"
    assert preserved["summary_short"] == "old summary"
    mock_start.assert_called_once_with(
        data["id"], llm_provider=None, llm_model=None, summary_model=None, translation_model=None
    )


@pytest.mark.asyncio
async def test_analyze_does_not_retry_transient_youtube_failure_immediately():
    stale = await create_analysis(
        video_id="I4zwZP80u-Y",
        url="https://www.youtube.com/watch?v=I4zwZP80u-Y",
    )
    await update_analysis(
        stale["id"],
        status="failed",
        error_message="YouTube rate limited transcript downloads. Wait before retrying.",
    )

    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=I4zwZP80u-Y"})

    assert r.status_code == 200
    data = r.json()
    assert data["id"] == stale["id"]
    assert data["status"] == "failed"
    mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_force_creates_revision_and_preserves_completed_result():
    stale = await create_analysis(
        video_id="I4zwZP80u-Y",
        url="https://www.youtube.com/watch?v=I4zwZP80u-Y",
    )
    await update_analysis(
        stale["id"],
        status="completed",
        transcript='[{"start": 0, "text": "old transcript"}]',
        summary_short="old summary",
        transcript_ko='[{"start": 0, "text": "old ko"}]',
    )

    with patch("app.routers.start_pipeline") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/analyze",
                json={"url": "https://www.youtube.com/watch?v=I4zwZP80u-Y", "force": True},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["id"] != stale["id"]
    assert data["parent_analysis_id"] == stale["id"]
    assert data["is_active"] is False
    assert data["status"] == "pending"
    assert data["transcript"] is None
    assert data["summary_short"] is None
    assert data["transcript_ko"] is None
    preserved = await get_analysis(stale["id"])
    assert preserved["transcript"] == '[{"start": 0, "text": "old transcript"}]'
    assert preserved["summary_short"] == "old summary"
    assert preserved["transcript_ko"] == '[{"start": 0, "text": "old ko"}]'
    assert preserved["is_active"] == 1
    mock_start.assert_called_once_with(
        data["id"], llm_provider=None, llm_model=None, summary_model=None, translation_model=None
    )


@pytest.mark.asyncio
async def test_resume_translate_passes_llm_snapshot_to_job():
    row = await create_analysis(
        video_id="I4zwZP80u-Y",
        url="https://www.youtube.com/watch?v=I4zwZP80u-Y",
    )
    await update_analysis(row["id"], status="failed", transcript='[{"start": 0, "text": "hello"}]')

    with patch("app.routers.start_resume_translate") as mock_start:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/analyses/{row['id']}/resume-translate",
                json={"llm_provider": "azure", "llm_model": "deployment-a"},
            )

    assert r.status_code == 200
    revision_id = r.json()["analysis_id"]
    assert revision_id != row["id"]
    data = await get_analysis(revision_id)
    assert data["parent_analysis_id"] == row["id"]
    assert data["status"] == "waiting"
    assert data["transcript"] == '[{"start": 0, "text": "hello"}]'
    preserved = await get_analysis(row["id"])
    assert preserved["status"] == "failed"
    mock_start.assert_called_once_with(
        revision_id, llm_provider="azure", llm_model="deployment-a", translation_model=None
    )


@pytest.mark.asyncio
async def test_get_analyses_filters_by_query():
    matching = await create_analysis(video_id="search-match", url="search-match-url")
    other = await create_analysis(video_id="search-other", url="search-other-url")
    await update_analysis(matching["id"], title="Searchable Windows title")
    await update_analysis(other["id"], title="Unrelated title")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/analyses", params={"q": "windows"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [matching["id"]]
