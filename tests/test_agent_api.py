"""Regression tests for the agent-facing API.

These cover the safety properties that make the surface safe to expose to an
agent: no body leakage in list responses, no regeneration, no deletion, and
consistent handling of Korean source transcripts (LLM-005).
"""

import json
import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DB_PATH"] = "data/test_agent_api.db"

import app.db as db_module
from app.db import create_analysis, init_db, update_analysis
from app.main import app

TEST_DB_PATH = "data/test_agent_api.db"
TOKEN = "agent-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

BODY_FIELDS = ("transcript", "transcript_ko", "summary_structured")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
async def setup_db(monkeypatch):
    previous = db_module.DB_PATH
    db_module.DB_PATH = TEST_DB_PATH
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    await init_db()
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("APP_AUTH_DISABLED", raising=False)
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    db_module.DB_PATH = previous


async def _seed(video_id="dQw4w9WgXcQ", **fields):
    row = await create_analysis(
        video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}"
    )
    if fields:
        row = await update_analysis(row["id"], **fields) or row
    return row


ENTRIES = json.dumps([{"start": 0.0, "text": "hello"}, {"start": 1.0, "text": "world"}])


# --- access control -------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_routes_are_disabled_without_token(monkeypatch):
    """Fail closed: no token configured means the whole agent surface is off."""
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    async with _client() as c:
        r = await c.get("/api/agent/v1/analyses")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "disabled"


# --- list -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_never_returns_bodies():
    await _seed(transcript=ENTRIES, transcript_ko=ENTRIES, summary_structured="# long")
    async with _client() as c:
        r = await c.get("/api/agent/v1/analyses", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    item = body["items"][0]
    for field in BODY_FIELDS:
        assert field not in item
    assert item["has_transcript"] is True
    assert item["transcript_chars"] > 0


@pytest.mark.asyncio
async def test_list_envelope_reports_total():
    for i in range(3):
        await _seed(video_id=f"vid{i}0000000")
    async with _client() as c:
        r = await c.get("/api/agent/v1/analyses?limit=2", headers=AUTH)
    body = r.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_list_total_tracks_search_filter():
    await _seed(video_id="aaa00000000", title="alpha")
    await _seed(video_id="bbb00000000", title="beta")
    async with _client() as c:
        r = await c.get("/api/agent/v1/analyses?q=alpha", headers=AUTH)
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=-1", "limit=201", "offset=-1", "order=title"],
)
async def test_list_rejects_out_of_range_parameters(query):
    """The UI's own endpoint returns 8 MB for limit=-1; the agent one must not."""
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses?{query}", headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_parameter"


# --- translation state (LLM-005) ------------------------------------------


@pytest.mark.asyncio
async def test_korean_source_reports_blank_and_hides_legacy_translation():
    row = await _seed(video_id="ko000000000", transcript_lang="ko", transcript=ENTRIES)
    await update_analysis(row["id"], transcript_ko=ENTRIES, status="completed")
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
        detail = await c.get(
            f"/api/agent/v1/analyses/{row['id']}?fields=transcript_ko", headers=AUTH
        )
    body = r.json()
    assert body["translation_state"] == "not_applicable_korean_source"
    assert body["entries"] == []
    assert body["display"] == "- BLANK -"
    # A legacy stored translation must not leak through the detail route either.
    assert detail.json()["transcript_ko"] is None


@pytest.mark.asyncio
async def test_korean_source_text_format_returns_blank_marker():
    row = await _seed(video_id="ko111111111", transcript_lang="ko", transcript=ENTRIES)
    async with _client() as c:
        r = await c.get(
            f"/api/agent/v1/analyses/{row['id']}/translation?format=text", headers=AUTH
        )
    assert r.json()["text"] == "- BLANK -"


@pytest.mark.asyncio
async def test_translated_non_korean_source_reports_translated():
    row = await _seed(video_id="en000000000", transcript_lang="en", transcript=ENTRIES)
    await update_analysis(row["id"], transcript_ko=ENTRIES, status="completed")
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
    body = r.json()
    assert body["translation_state"] == "translated"
    assert len(body["entries"]) == 2


@pytest.mark.asyncio
async def test_completed_without_translation_reports_unavailable():
    """pipeline swallows translation errors, so this is a real state."""
    row = await _seed(video_id="en111111111", transcript_lang="en", transcript=ENTRIES)
    await update_analysis(row["id"], status="completed")
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
    assert r.json()["translation_state"] == "unavailable"


@pytest.mark.asyncio
async def test_in_progress_reports_pending_then_partial():
    row = await _seed(video_id="en222222222", transcript_lang="en", transcript=ENTRIES)
    await update_analysis(row["id"], status="translating")
    async with _client() as c:
        pending = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
    assert pending.json()["translation_state"] == "pending"

    await update_analysis(row["id"], transcript_ko=ENTRIES)
    async with _client() as c:
        partial = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
    assert partial.json()["translation_state"] == "partial"


@pytest.mark.asyncio
async def test_failed_status_reports_failed():
    row = await _seed(video_id="en333333333", transcript_lang="en", transcript=ENTRIES)
    await update_analysis(row["id"], status="failed", error_message="boom")
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
    assert r.json()["translation_state"] == "failed"


@pytest.mark.asyncio
async def test_corrupt_translation_body_reports_parse_error():
    row = await _seed(video_id="en444444444", transcript_lang="en", transcript=ENTRIES)
    await update_analysis(row["id"], transcript_ko="{not json", status="completed")
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses/{row['id']}/translation", headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "parse_error"


# --- registration ---------------------------------------------------------


@pytest.mark.asyncio
async def test_register_queues_new_url():
    with patch("app.agent_api.start_pipeline") as mock_start:
        async with _client() as c:
            r = await c.post(
                "/api/agent/v1/analyses",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH,
            )
    assert r.status_code == 202
    assert r.json()["result"] == "queued"
    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_register_duplicate_does_not_start_pipeline():
    """A retrying agent must not multiply paid LLM calls."""
    await _seed(video_id="dup00000000", status="completed", transcript=ENTRIES)
    with patch("app.agent_api.start_pipeline") as mock_start:
        async with _client() as c:
            r = await c.post(
                "/api/agent/v1/analyses",
                json={"url": "https://www.youtube.com/watch?v=dup00000000"},
                headers=AUTH,
            )
    assert r.status_code == 200
    assert r.json()["result"] == "existing"
    mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_register_failed_row_does_not_silently_regenerate():
    """Regeneration creates a revision, which stays a human-only action."""
    await _seed(video_id="fail0000000", status="failed", error_message="permanent")
    with patch("app.agent_api.start_pipeline") as mock_start:
        async with _client() as c:
            r = await c.post(
                "/api/agent/v1/analyses",
                json={"url": "https://www.youtube.com/watch?v=fail0000000"},
                headers=AUTH,
            )
    assert r.status_code == 200
    assert r.json()["result"] == "needs_regeneration"
    mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_register_rejects_force():
    async with _client() as c:
        r = await c.post(
            "/api/agent/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "force": True},
            headers=AUTH,
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_parameter"


@pytest.mark.asyncio
async def test_register_rejects_invalid_url():
    async with _client() as c:
        r = await c.post("/api/agent/v1/analyses", json={"url": "nope"}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_url"


@pytest.mark.asyncio
async def test_register_respects_queue_cap(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_QUEUED", "2")
    with patch("app.agent_api.queued_count", return_value=2):
        async with _client() as c:
            r = await c.post(
                "/api/agent/v1/analyses",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers=AUTH,
            )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "queue_full"


# --- deletion is not exposed ----------------------------------------------


@pytest.mark.asyncio
async def test_delete_is_not_exposed_on_agent_surface():
    row = await _seed(video_id="del00000000")
    async with _client() as c:
        r = await c.delete(f"/api/agent/v1/analyses/{row['id']}", headers=AUTH)
    assert r.status_code == 405


# --- lookup by stable identifier ------------------------------------------


@pytest.mark.asyncio
async def test_video_id_lookup_resolves_active_revision():
    """id changes per revision; video_id is the stable key for agents."""
    async with _client() as c:
        r = await c.get("/api/agent/v1/videos/dQw4w9WgXcQ", headers=AUTH)
    assert r.status_code == 404

    await _seed(video_id="active00000", summary_short="short")
    async with _client() as c:
        r = await c.get("/api/agent/v1/videos/active00000", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["summary_short"] == "short"


@pytest.mark.asyncio
async def test_unknown_id_returns_envelope_404():
    async with _client() as c:
        r = await c.get("/api/agent/v1/analyses/nope", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_status_route_is_compact():
    row = await _seed(video_id="stat0000000", status="translating", transcript=ENTRIES)
    async with _client() as c:
        r = await c.get(f"/api/agent/v1/analyses/{row['id']}/status", headers=AUTH)
    body = r.json()
    assert body["status"] == "translating"
    assert body["translation_state"] == "pending"
    for field in BODY_FIELDS:
        assert field not in body


# --- the UI's own contract is unchanged -----------------------------------


@pytest.mark.asyncio
async def test_existing_ui_routes_keep_their_shape():
    """Regression: agents were added alongside the UI, not in place of it."""
    await _seed(video_id="ui000000000", transcript=ENTRIES)
    async with _client() as c:
        r = await c.get("/api/analyses", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # The UI still receives full bodies from its own endpoint.
    assert "transcript" in body[0]
