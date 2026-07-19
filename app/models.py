from pydantic import BaseModel
from typing import Optional


class AnalyzeRequest(BaseModel):
    url: str
    llm_enabled: bool = True
    force: bool = False
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    summary_model: Optional[str] = None
    translation_model: Optional[str] = None


class ResumeTranslateRequest(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    summary_model: Optional[str] = None
    translation_model: Optional[str] = None


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
    video_lang: Optional[str] = None
    llm_enabled: bool = True
    transcript: Optional[str] = None
    transcript_lang: Optional[str] = None
    summary_short: Optional[str] = None
    summary_structured: Optional[str] = None
    transcript_ko: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    revision_number: int = 1
    parent_analysis_id: Optional[str] = None
    is_active: bool = True
    deleted_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
