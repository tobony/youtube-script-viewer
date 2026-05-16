from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.models import AnalyzeRequest, AnalysisResponse, ResumeTranslateRequest
from app.youtube import extract_video_id
from app.db import create_analysis, get_analysis, get_by_video_id, list_analyses, delete_analysis, update_analysis
from app.pipeline import start_pipeline, stop_pipeline, start_resume_translate, is_running

router = APIRouter(prefix="/api")

TRANSIENT_FAILURE_RETRY_COOLDOWN = timedelta(minutes=30)


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(req: AnalyzeRequest):
    try:
        video_id = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Return existing if already completed or in progress.
    # Older runs could be marked completed even when transcript extraction failed;
    # those should be retried instead of short-circuiting immediately.
    existing = await get_by_video_id(video_id)
    if existing and not req.force and _is_transient_failure_in_cooldown(existing):
        return existing
    if (
        existing
        and not req.force
        and existing.get("status") not in (None, "failed")
        and (existing.get("status") != "completed" or existing.get("transcript"))
    ):
        return existing
    if existing:
        row = await update_analysis(
            existing["id"],
            url=req.url,
            llm_enabled=1 if req.llm_enabled else 0,
            transcript=None,
            transcript_lang=None,
            summary_short=None,
            summary_structured=None,
            transcript_ko=None,
            error_message=None,
            status="pending",
        )
        start_pipeline(row["id"], llm_provider=req.llm_provider, llm_model=req.llm_model)
        return row

    row = await create_analysis(video_id=video_id, url=req.url, llm_enabled=req.llm_enabled)
    start_pipeline(row["id"], llm_provider=req.llm_provider, llm_model=req.llm_model)
    return row


@router.get("/analyses", response_model=list[AnalysisResponse])
async def get_list(limit: int = 20, offset: int = 0):
    return await list_analyses(limit=limit, offset=offset)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
async def get_detail(analysis_id: str):
    row = await get_analysis(analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.delete("/analyses/{analysis_id}")
async def delete(analysis_id: str):
    deleted = await delete_analysis(analysis_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.post("/analyses/{analysis_id}/stop")
async def stop(analysis_id: str):
    stopped = await stop_pipeline(analysis_id)
    if not stopped:
        raise HTTPException(status_code=400, detail="Not running")
    return {"ok": True}


@router.post("/analyses/{analysis_id}/resume-translate")
async def resume_translate(analysis_id: str, req: ResumeTranslateRequest | None = None):
    row = await get_analysis(analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if not row.get("transcript"):
        raise HTTPException(status_code=400, detail="No transcript")
    if is_running(analysis_id):
        raise HTTPException(status_code=400, detail="Already running")
    await update_analysis(analysis_id, status="waiting", error_message=None)
    start_resume_translate(
        analysis_id,
        llm_provider=req.llm_provider if req else None,
        llm_model=req.llm_model if req else None,
    )
    return {"ok": True}


def _is_transient_failure_in_cooldown(row: dict) -> bool:
    if row.get("status") != "failed":
        return False
    error_message = (row.get("error_message") or "").lower()
    if "rate limited" not in error_message and "blocking transcript requests" not in error_message:
        return False
    try:
        updated_at = datetime.fromisoformat(row.get("updated_at") or "")
    except ValueError:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at < TRANSIENT_FAILURE_RETRY_COOLDOWN
