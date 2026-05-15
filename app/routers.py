from fastapi import APIRouter, HTTPException

from app.models import AnalyzeRequest, AnalysisResponse
from app.youtube import extract_video_id
from app.db import create_analysis, get_analysis, get_by_video_id, list_analyses, delete_analysis
from app.pipeline import start_pipeline, stop_pipeline, start_resume_translate, is_running

router = APIRouter(prefix="/api")


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(req: AnalyzeRequest):
    try:
        video_id = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Return existing if already completed or in progress
    existing = await get_by_video_id(video_id)
    if existing and existing.get("status") not in (None, "failed"):
        return existing

    row = await create_analysis(video_id=video_id, url=req.url)
    start_pipeline(row["id"])
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
async def resume_translate(analysis_id: str):
    row = await get_analysis(analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if not row.get("transcript"):
        raise HTTPException(status_code=400, detail="No transcript")
    if is_running(analysis_id):
        raise HTTPException(status_code=400, detail="Already running")
    start_resume_translate(analysis_id)
    return {"ok": True}
