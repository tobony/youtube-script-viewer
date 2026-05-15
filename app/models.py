from pydantic import BaseModel
from typing import Optional


class AnalyzeRequest(BaseModel):
    url: str


class AnalysisResponse(BaseModel):
    id: str
    video_id: str
    url: str
    title: Optional[str] = None
    channel: Optional[str] = None
    thumbnail: Optional[str] = None
    duration_seconds: int = 0
    view_count: int = 0
    like_count: int = 0
    transcript: Optional[str] = None
    summary_short: Optional[str] = None
    summary_structured: Optional[str] = None
    transcript_ko: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
