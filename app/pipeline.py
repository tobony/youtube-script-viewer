"""Async pipeline: fetch → summarize → translate."""

import asyncio
import logging
from app.db import update_analysis
from app.youtube import get_metadata, get_transcript

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}

# Runtime toggle (controlled via UI)
llm_enabled: bool = True


def start_pipeline(analysis_id: str):
    task = asyncio.create_task(run_pipeline(analysis_id))
    _tasks[analysis_id] = task
    task.add_done_callback(lambda t: _tasks.pop(analysis_id, None))


async def run_pipeline(analysis_id: str):
    try:
        await update_analysis(analysis_id, status="fetching")
        row = await _stage_fetch(analysis_id)

        transcript = row.get("transcript", "")

        if llm_enabled and transcript:
            from app.llm import summarize, translate_paragraphs

            try:
                await update_analysis(analysis_id, status="summarizing")
                short, structured = await summarize(transcript)
                await update_analysis(analysis_id, summary_short=short, summary_structured=structured)
            except Exception as e:
                logger.warning(f"Summarize failed for {analysis_id}: {e}")

            try:
                await update_analysis(analysis_id, status="translating")

                async def on_translate_progress(ko_json: str):
                    await update_analysis(analysis_id, transcript_ko=ko_json)

                transcript_ko = await translate_paragraphs(transcript, on_progress=on_translate_progress)
                await update_analysis(analysis_id, transcript_ko=transcript_ko)
            except Exception as e:
                logger.warning(f"Translate failed for {analysis_id}: {e}")

        await update_analysis(analysis_id, status="completed")
        logger.info(f"Pipeline completed: {analysis_id}")

    except Exception as e:
        logger.error(f"Pipeline failed for {analysis_id}: {e}")
        await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])


async def _stage_fetch(analysis_id: str) -> dict:
    from app.db import get_analysis
    row = await get_analysis(analysis_id)
    video_id = row["video_id"]

    metadata = await asyncio.to_thread(get_metadata, video_id)
    try:
        transcript = await asyncio.to_thread(get_transcript, video_id)
    except Exception:
        transcript = ""

    row = await update_analysis(
        analysis_id,
        title=metadata["title"],
        channel=metadata["channel"],
        thumbnail=metadata["thumbnail"],
        duration_seconds=metadata["duration_seconds"],
        view_count=metadata["view_count"],
        like_count=metadata["like_count"],
        transcript=transcript,
    )
    return row
