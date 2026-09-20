"""Agent-facing read/register API.

Namespace: /api/agent/v1/*

The routes under /api/* are the UI's own internal contract: the UI calls them
over HTTP from the server side, and they are free to change. Agents need a
stable contract instead, so this module adds a separate, smaller surface that
returns compact metadata by default and sends large bodies only when explicitly
requested.

Safety rules enforced here:

* Registration never regenerates. ``force`` is rejected, and an existing row
  that needs a retry is reported as ``needs_regeneration`` instead of silently
  starting another paid run. Agents therefore cannot multiply revisions.
* Deletion is not exposed at all.
* The list response never carries transcript, translation, or structured
  summary bodies; derived flags tell an agent what is available.
* Korean source transcripts follow the UI rule (LLM-005): not translated, the
  translation pane renders ``- BLANK -``, and any legacy stored translation is
  not exposed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.auth import auth_token
from app.db import (
    count_analyses,
    create_analysis,
    get_analysis,
    get_by_video_id,
    list_analyses_summary,
)
from app.pipeline import queued_count, start_pipeline
from app.routers import _is_transient_failure_in_cooldown
from app.youtube import extract_video_id

router = APIRouter(prefix="/api/agent/v1", tags=["agent"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 20
DEFAULT_MAX_QUEUED = 5

DETAIL_BODY_FIELDS = frozenset(
    {"summary_short", "summary_structured", "transcript", "transcript_ko"}
)
DETAIL_DEFAULT_FIELDS = ("summary_short", "summary_structured")

ORDER_COLUMNS = {"created_at": "created_at", "updated_at": "updated_at"}

IN_PROGRESS_STATUSES = frozenset(
    {"pending", "waiting", "fetching", "summarizing", "translating"}
)

# Rows an agent may not silently retry: they need a human-triggered regeneration
# from the UI, which creates a new inactive revision.
RETRY_REQUIRED_STATUSES = frozenset({"failed"})


def _error(
    status_code: int, code: str, message: str, detail: Any | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "detail": detail or {}}},
    )


def _guard() -> JSONResponse | None:
    """Agent routes are unavailable when the app was deliberately left open."""
    if auth_token() is None:
        return _error(
            503,
            "disabled",
            "Agent API is disabled because no APP_AUTH_TOKEN is configured.",
        )
    return None


def _parse_entries(raw: Any) -> list[dict] | str | None:
    """Return parsed entries, None when absent, or the string "parse_error"."""
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "parse_error"
    if not isinstance(data, list):
        return "parse_error"
    return data


def _is_korean_source(row: dict) -> bool:
    return str(row.get("transcript_lang") or "").lower().startswith("ko")


def translation_state(row: dict, *, parsed: list[dict] | str | None = None) -> str:
    """Classify the translation pane.

    ``parsed`` lets a single-item endpoint pass an already parsed body so that a
    corrupt payload is reported as ``parse_error``. List rows cannot afford to
    parse every body; they rely on the stored character count instead. That
    asymmetry is documented in docs/agent-api.md.
    """
    if _is_korean_source(row):
        # LLM-005: Korean source is never translated, even when a legacy
        # translation body is still stored on the row.
        return "not_applicable_korean_source"

    status = str(row.get("status") or "")
    if status == "failed":
        return "failed"

    if parsed is not None:
        if parsed == "parse_error":
            return "parse_error"
        has_entries = bool(parsed)
    else:
        has_entries = bool(_chars(row, "transcript_ko", "translation_chars"))

    if status in IN_PROGRESS_STATUSES:
        return "partial" if has_entries else "pending"
    if status == "completed":
        return "translated" if has_entries else "unavailable"
    return "unknown"


def _flag(row: dict, flag: str, source_field: str) -> bool:
    """Read a derived flag, falling back to the body when it is not selected."""
    if flag in row:
        return bool(row[flag])
    return bool(row.get(source_field))


def _chars(row: dict, source_field: str, char_key: str) -> int:
    if char_key in row:
        return int(row[char_key] or 0)
    return len(row.get(source_field) or "")


def _summary(row: dict) -> dict:
    """Compact metadata: never includes transcript or summary bodies."""
    return {
        "id": row.get("id"),
        "video_id": row.get("video_id"),
        "url": row.get("url"),
        "title": row.get("title"),
        "channel": row.get("channel"),
        "thumbnail": row.get("thumbnail"),
        "duration_seconds": row.get("duration_seconds") or 0,
        "view_count": row.get("view_count") or 0,
        "like_count": row.get("like_count") or 0,
        "video_lang": row.get("video_lang"),
        "transcript_lang": row.get("transcript_lang"),
        "llm_enabled": bool(row.get("llm_enabled")),
        "status": row.get("status"),
        "error_message": row.get("error_message"),
        "revision_number": row.get("revision_number") or 1,
        "is_active": bool(row.get("is_active")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # Derived flags so an agent can decide what to fetch next without
        # downloading any body.
        "has_transcript": _flag(row, "has_transcript", "transcript"),
        "has_summary": _flag(row, "has_summary", "summary_short"),
        "transcript_chars": _chars(row, "transcript", "transcript_chars"),
        "translation_chars": _chars(row, "transcript_ko", "translation_chars"),
        "translation_state": translation_state(row),
    }


def _max_queued() -> int:
    raw = (os.getenv("AGENT_MAX_QUEUED") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_QUEUED
    return value if value >= 0 else DEFAULT_MAX_QUEUED


async def _resolve_active(analysis_id: str) -> dict | None:
    row = await get_analysis(analysis_id)
    if not row or row.get("deleted_at") is not None:
        return None
    return row


@router.get("/health")
async def agent_health():
    blocked = _guard()
    if blocked:
        return blocked
    return {"status": "ok", "version": "1", "total_active": await count_analyses()}


@router.get("/analyses")
async def agent_list(
    limit: int = Query(DEFAULT_LIMIT),
    offset: int = Query(0),
    q: str | None = Query(None),
    order: str = Query("created_at"),
):
    blocked = _guard()
    if blocked:
        return blocked
    if limit < 1 or limit > MAX_LIMIT:
        return _error(
            400,
            "invalid_parameter",
            f"limit must be between 1 and {MAX_LIMIT}.",
            {"limit": limit},
        )
    if offset < 0:
        return _error(400, "invalid_parameter", "offset must be >= 0.", {"offset": offset})
    if order not in ORDER_COLUMNS:
        return _error(
            400,
            "invalid_parameter",
            f"order must be one of: {', '.join(sorted(ORDER_COLUMNS))}.",
            {"order": order},
        )

    search = (q or "").strip() or None
    rows = await list_analyses_summary(
        limit=limit, offset=offset, search=search, order_by=ORDER_COLUMNS[order]
    )
    total = await count_analyses(search=search)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_summary(row) for row in rows],
    }


@router.get("/analyses/{analysis_id}")
async def agent_detail(analysis_id: str, fields: str | None = Query(None)):
    blocked = _guard()
    if blocked:
        return blocked

    requested = DETAIL_DEFAULT_FIELDS
    if fields is not None:
        requested = tuple(part.strip() for part in fields.split(",") if part.strip())
        unknown = [name for name in requested if name not in DETAIL_BODY_FIELDS]
        if unknown:
            return _error(
                400,
                "invalid_parameter",
                f"Unknown fields: {', '.join(sorted(unknown))}.",
                {"allowed": sorted(DETAIL_BODY_FIELDS)},
            )

    row = await _resolve_active(analysis_id)
    if row is None:
        return _error(404, "not_found", "Analysis not found.", {"id": analysis_id})

    korean = _is_korean_source(row)
    # Parse translation once: it feeds both the state and the field payload.
    parsed_ko = None if korean else _parse_entries(row.get("transcript_ko"))

    payload = _summary(row)
    payload["translation_state"] = translation_state(row, parsed=parsed_ko)
    payload["display"] = "- BLANK -" if korean else None
    payload["fields"] = list(requested)

    for name in requested:
        if name == "transcript_ko":
            if korean:
                # Legacy Korean-source translation bodies stay hidden.
                payload[name] = None
            elif parsed_ko == "parse_error":
                payload[name] = None
            else:
                payload[name] = parsed_ko
        elif name == "transcript":
            payload[name] = row.get("transcript")
        else:
            payload[name] = row.get(name)
    return payload


@router.get("/videos/{video_id}")
async def agent_by_video(video_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    row = await get_by_video_id(video_id)
    if not row:
        return _error(
            404, "not_found", "No active analysis for this video.", {"video_id": video_id}
        )
    payload = _summary(row)
    payload["translation_state"] = translation_state(
        row, parsed=None if _is_korean_source(row) else _parse_entries(row.get("transcript_ko"))
    )
    payload["summary_short"] = row.get("summary_short")
    payload["summary_structured"] = row.get("summary_structured")
    return payload


@router.get("/analyses/{analysis_id}/status")
async def agent_status(analysis_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    row = await _resolve_active(analysis_id)
    if row is None:
        return _error(404, "not_found", "Analysis not found.", {"id": analysis_id})
    return {
        "id": row.get("id"),
        "video_id": row.get("video_id"),
        "status": row.get("status"),
        "translation_state": translation_state(
            row,
            parsed=None if _is_korean_source(row) else _parse_entries(row.get("transcript_ko")),
        ),
        "revision_number": row.get("revision_number") or 1,
        "updated_at": row.get("updated_at"),
    }


@router.get("/analyses/{analysis_id}/summary")
async def agent_summary(analysis_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    row = await _resolve_active(analysis_id)
    if row is None:
        return _error(404, "not_found", "Analysis not found.", {"id": analysis_id})
    return {
        "id": row.get("id"),
        "video_id": row.get("video_id"),
        "title": row.get("title"),
        "summary_short": row.get("summary_short"),
        "summary_structured": row.get("summary_structured"),
    }


def _rendered_text(entries: list[dict]) -> str:
    return "\n\n".join(str(entry.get("text") or "") for entry in entries)


async def _body_response(analysis_id: str, field: str, fmt: str):
    blocked = _guard()
    if blocked:
        return blocked
    if fmt not in ("json", "text"):
        return _error(400, "invalid_parameter", "format must be json or text.", {"format": fmt})

    row = await _resolve_active(analysis_id)
    if row is None:
        return _error(404, "not_found", "Analysis not found.", {"id": analysis_id})

    korean = _is_korean_source(row)
    raw = row.get(field) or ""

    if field == "transcript_ko" and korean:
        return {
            "id": row.get("id"),
            "video_id": row.get("video_id"),
            "transcript_lang": row.get("transcript_lang"),
            "translation_state": "not_applicable_korean_source",
            "display": "- BLANK -",
            "entries": [] if fmt == "json" else None,
            "text": "- BLANK -" if fmt == "text" else None,
            "note": "Korean source transcripts are not translated.",
        }

    parsed = _parse_entries(raw)
    if parsed == "parse_error":
        return _error(
            422,
            "parse_error",
            "Stored body is not valid JSON.",
            {"id": analysis_id, "field": field},
        )

    entries = parsed or []
    state = translation_state(row, parsed=parsed) if field == "transcript_ko" else None
    if fmt == "json":
        return {
            "id": row.get("id"),
            "video_id": row.get("video_id"),
            "transcript_lang": row.get("transcript_lang"),
            "translation_state": state,
            "field": field,
            "chars": len(raw),
            "entries": entries,
        }
    return {
        "id": row.get("id"),
        "video_id": row.get("video_id"),
        "transcript_lang": row.get("transcript_lang"),
        "translation_state": state,
        "field": field,
        "chars": len(raw),
        "text": _rendered_text(entries),
    }


@router.get("/analyses/{analysis_id}/translation")
async def agent_translation(analysis_id: str, format: str = Query("json")):
    return await _body_response(analysis_id, "transcript_ko", format)


@router.get("/analyses/{analysis_id}/transcript")
async def agent_transcript(analysis_id: str, format: str = Query("json")):
    return await _body_response(analysis_id, "transcript", format)


@router.post("/analyses")
async def agent_register(request: Request):
    """Register a YouTube URL for analysis without ever regenerating."""
    blocked = _guard()
    if blocked:
        return blocked

    try:
        payload = await request.json()
    except Exception:
        return _error(400, "invalid_parameter", "Request body must be JSON.")
    if not isinstance(payload, dict):
        return _error(400, "invalid_parameter", "Request body must be a JSON object.")

    # Regeneration is intentionally unavailable to agents: it spends money and
    # hides the previous active revision. Reject rather than ignore.
    rejected = sorted(set(payload) - {"url", "llm_enabled"})
    if rejected:
        return _error(
            400,
            "invalid_parameter",
            f"Unsupported field(s): {', '.join(rejected)}.",
            {"allowed": ["url", "llm_enabled"]},
        )

    url = str(payload.get("url") or "").strip()
    if not url:
        return _error(400, "invalid_url", "url is required.")
    try:
        video_id = extract_video_id(url)
    except ValueError as exc:
        return _error(400, "invalid_url", str(exc), {"url": url})

    llm_enabled = payload.get("llm_enabled")
    llm_enabled = True if llm_enabled is None else bool(llm_enabled)

    existing = await get_by_video_id(video_id)
    if existing:
        if _is_transient_failure_in_cooldown(existing):
            return JSONResponse(
                status_code=200,
                content={
                    "result": "cooldown",
                    "reason": "recent transient failure",
                    **_summary(existing),
                },
            )
        settled = existing.get("status") not in (None, *RETRY_REQUIRED_STATUSES)
        if settled and (existing.get("status") != "completed" or existing.get("transcript")):
            # Already queued, running, or completed: starting another run would
            # spend money for no new content.
            return JSONResponse(
                status_code=200, content={"result": "existing", **_summary(existing)}
            )
        # Failed, or completed without a transcript. A retry creates a new
        # revision, which this endpoint deliberately does not do: report it and
        # let a human regenerate from the UI.
        return JSONResponse(
            status_code=200,
            content={
                "result": "needs_regeneration",
                "reason": "Existing row has no usable result; regenerate from the UI.",
                **_summary(existing),
            },
        )

    cap = _max_queued()
    if cap and queued_count() >= cap:
        return _error(
            429,
            "queue_full",
            "Too many analyses are already queued.",
            {"queued": queued_count(), "max_queued": cap},
        )

    row = await create_analysis(video_id=video_id, url=url, llm_enabled=llm_enabled)
    start_pipeline(row["id"])
    return JSONResponse(status_code=202, content={"result": "queued", **_summary(row)})
