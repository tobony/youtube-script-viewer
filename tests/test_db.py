import pytest
import os

os.environ["DB_PATH"] = "data/test.db"

from app.db import init_db, create_analysis, get_analysis, update_analysis, list_analyses, delete_analysis


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    yield
    if os.path.exists("data/test.db"):
        os.remove("data/test.db")


@pytest.mark.asyncio
async def test_create_and_get():
    row = await create_analysis(video_id="abc123", url="https://youtube.com/watch?v=abc123")
    assert row["video_id"] == "abc123"
    assert row["status"] == "pending"
    fetched = await get_analysis(row["id"])
    assert fetched["url"] == "https://youtube.com/watch?v=abc123"


@pytest.mark.asyncio
async def test_update_status():
    row = await create_analysis(video_id="x1", url="u1")
    updated = await update_analysis(row["id"], status="fetching", title="Test Video")
    assert updated["status"] == "fetching"
    assert updated["title"] == "Test Video"


@pytest.mark.asyncio
async def test_list_and_delete():
    row = await create_analysis(video_id="x", url="u")
    items = await list_analyses()
    assert len(items) >= 1
    deleted = await delete_analysis(row["id"])
    assert deleted is True
