import os
import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient

os.environ["DB_PATH"] = "data/test_analyze.db"

from app.main import app
from app.db import create_analysis, init_db, update_analysis


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    yield
    if os.path.exists("data/test_analyze.db"):
        os.remove("data/test_analyze.db")


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
        mock_start.assert_called_once_with(data["id"])


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
    mock_start.assert_called_once_with(data["id"])


@pytest.mark.asyncio
async def test_analyze_retries_empty_completed_result():
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
    assert data["id"] == stale["id"]
    assert data["status"] == "pending"
    assert data["transcript"] is None
    mock_start.assert_called_once_with(stale["id"])


@pytest.mark.asyncio
async def test_analyze_retries_failed_existing_result_in_place():
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
    assert data["id"] == stale["id"]
    assert data["status"] == "pending"
    assert data["llm_enabled"] is True
    assert data["error_message"] is None
    assert data["summary_short"] is None
    mock_start.assert_called_once_with(stale["id"])


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
