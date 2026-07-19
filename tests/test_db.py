import pytest
import os

os.environ["DB_PATH"] = "data/test.db"

import app.db as db_module
from app.db import (
    ImmutableAnalysisError,
    activate_analysis,
    create_analysis,
    create_analysis_revision,
    delete_analysis,
    get_analysis,
    get_by_video_id,
    init_db,
    list_analyses,
    update_analysis,
)

TEST_DB_PATH = "data/test.db"


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
async def test_create_and_get():
    row = await create_analysis(video_id="abc123", url="https://youtube.com/watch?v=abc123")
    assert row["video_id"] == "abc123"
    assert row["status"] == "pending"
    assert row["llm_enabled"] == 1
    fetched = await get_analysis(row["id"])
    assert fetched["url"] == "https://youtube.com/watch?v=abc123"


@pytest.mark.asyncio
async def test_create_with_llm_disabled():
    row = await create_analysis(video_id="abc123", url="https://youtube.com/watch?v=abc123", llm_enabled=False)
    assert row["llm_enabled"] == 0


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
    assert row["id"] not in {item["id"] for item in await list_analyses()}
    archived = await get_analysis(row["id"])
    assert archived["url"] == "u"
    assert archived["deleted_at"] is not None


@pytest.mark.asyncio
async def test_completed_content_cannot_be_changed_in_place():
    row = await create_analysis(video_id="immutable", url="original-url")
    await update_analysis(
        row["id"],
        transcript='[{"text": "original transcript"}]',
        summary_short="original summary",
        status="completed",
    )

    with pytest.raises(ImmutableAnalysisError):
        await update_analysis(row["id"], summary_short="replacement summary")

    unchanged = await get_analysis(row["id"])
    assert unchanged["summary_short"] == "original summary"
    assert unchanged["transcript"] == '[{"text": "original transcript"}]'


@pytest.mark.asyncio
async def test_new_revision_only_becomes_active_after_completion():
    source = await create_analysis(video_id="revision", url="original-url")
    await update_analysis(
        source["id"],
        transcript='[{"text": "original transcript"}]',
        summary_short="original summary",
        status="completed",
    )
    revision = await create_analysis_revision(source["id"], mode="full")

    assert revision["id"] != source["id"]
    assert revision["parent_analysis_id"] == source["id"]
    assert revision["revision_number"] == 2
    assert revision["is_active"] == 0
    assert (await get_by_video_id("revision"))["id"] == source["id"]

    await update_analysis(
        revision["id"],
        transcript='[{"text": "new transcript"}]',
        summary_short="new summary",
        status="completed",
    )
    await activate_analysis(revision["id"])

    assert (await get_by_video_id("revision"))["id"] == revision["id"]
    preserved = await get_analysis(source["id"])
    assert preserved["transcript"] == '[{"text": "original transcript"}]'
    assert preserved["summary_short"] == "original summary"
    assert preserved["is_active"] == 0


@pytest.mark.asyncio
async def test_searches_title_and_content_fields():
    title_row = await create_analysis(video_id="title", url="title-url")
    transcript_row = await create_analysis(video_id="transcript", url="transcript-url")
    translated_row = await create_analysis(video_id="translated", url="translated-url")
    summary_row = await create_analysis(video_id="summary", url="summary-url")
    structured_row = await create_analysis(video_id="structured", url="structured-url")

    await update_analysis(title_row["id"], title="Windows File Locking")
    await update_analysis(transcript_row["id"], transcript='[{"text": "original needle"}]')
    await update_analysis(translated_row["id"], transcript_ko='[{"text": "번역 검색어"}]')
    await update_analysis(summary_row["id"], summary_short="short keyword summary")
    await update_analysis(structured_row["id"], summary_structured="structured discovery")

    assert [row["id"] for row in await list_analyses(search="WINDOWS")] == [title_row["id"]]
    assert [row["id"] for row in await list_analyses(search="original needle")] == [transcript_row["id"]]
    assert [row["id"] for row in await list_analyses(search="번역 검색어")] == [translated_row["id"]]
    assert [row["id"] for row in await list_analyses(search="keyword summary")] == [summary_row["id"]]
    assert [row["id"] for row in await list_analyses(search="structured discovery")] == [structured_row["id"]]


@pytest.mark.asyncio
async def test_search_applies_before_limit_and_treats_wildcards_literally():
    target = await create_analysis(video_id="target", url="target-url")
    await update_analysis(target["id"], title="Literal 100%_match")
    for index in range(3):
        row = await create_analysis(video_id=f"newer-{index}", url=f"newer-{index}-url")
        await update_analysis(row["id"], title="Unrelated newer item")

    results = await list_analyses(limit=1, search="100%_match")

    assert [row["id"] for row in results] == [target["id"]]


@pytest.mark.asyncio
async def test_blank_search_returns_unfiltered_results():
    row = await create_analysis(video_id="blank", url="blank-url")

    results = await list_analyses(search="   ")

    assert row["id"] in {item["id"] for item in results}
