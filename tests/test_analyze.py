import os
import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient

os.environ["DB_PATH"] = "data/test_analyze.db"

from app.main import app
from app.db import init_db


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
        mock_start.assert_called_once_with(data["id"])
