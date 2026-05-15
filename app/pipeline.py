"""Async pipeline: Queue-based sequential execution."""

import asyncio
import json
import logging
from app.db import update_analysis, get_analysis
from app.youtube import TranscriptFetchError, get_metadata, get_transcript_with_language

logger = logging.getLogger(__name__)

# Runtime toggle (controlled via UI)
llm_enabled: bool = True

# Queue + worker
_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None
_current_id: str | None = None
_current_task: asyncio.Task | None = None


def _ensure_worker():
    global _queue, _worker_task
    if _queue is None:
        _queue = asyncio.Queue()
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


async def _worker():
    while True:
        job_type, analysis_id = await _queue.get()
        global _current_id, _current_task
        _current_id = analysis_id
        try:
            if job_type == "full":
                _current_task = asyncio.current_task()
                await run_pipeline(analysis_id)
            elif job_type == "resume_translate":
                _current_task = asyncio.current_task()
                await _resume_translate(analysis_id)
        except asyncio.CancelledError:
            await update_analysis(analysis_id, status="failed", error_message="Stopped by user")
        except Exception as e:
            logger.error(f"Worker error for {analysis_id}: {e}")
        finally:
            _current_id = None
            _current_task = None
            _queue.task_done()


def start_pipeline(analysis_id: str):
    _ensure_worker()
    asyncio.ensure_future(_enqueue("full", analysis_id))


def start_resume_translate(analysis_id: str):
    _ensure_worker()
    asyncio.ensure_future(_enqueue("resume_translate", analysis_id))


async def _enqueue(job_type: str, analysis_id: str):
    await update_analysis(analysis_id, status="waiting")
    await _queue.put((job_type, analysis_id))


async def stop_pipeline(analysis_id: str) -> bool:
    # If currently running, cancel
    if _current_id == analysis_id and _current_task and not _current_task.done():
        _current_task.cancel()
        await update_analysis(analysis_id, status="failed", error_message="Stopped by user")
        return True
    # If in queue, remove it
    if _queue:
        new_items = []
        while not _queue.empty():
            item = _queue.get_nowait()
            if item[1] != analysis_id:
                new_items.append(item)
            else:
                _queue.task_done()
                asyncio.ensure_future(
                    update_analysis(analysis_id, status="failed", error_message="Stopped by user")
                )
        for item in new_items:
            await _queue.put(item)
        if analysis_id != _current_id:
            return True
    return False


def is_running(analysis_id: str) -> bool:
    return _current_id == analysis_id


async def run_pipeline(analysis_id: str):
    try:
        await update_analysis(analysis_id, status="fetching")
        row = await _stage_fetch(analysis_id)

        transcript = row.get("transcript", "")
        if not transcript:
            await update_analysis(
                analysis_id,
                status="failed",
                error_message=row.get("error_message") or "Transcript unavailable or could not be fetched",
            )
            return

        if bool(row.get("llm_enabled", True)) and transcript:
            from app.llm import summarize, translate_paragraphs

            try:
                await update_analysis(analysis_id, status="summarizing")
                short, structured = await summarize(transcript)
                await update_analysis(analysis_id, summary_short=short, summary_structured=structured)
            except Exception as e:
                logger.warning(f"Summarize failed for {analysis_id}: {e}")

            if row.get("transcript_lang") == "ko":
                await update_analysis(analysis_id, transcript_ko=_copy_transcript_as_paragraphs(transcript))
            else:
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

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Pipeline failed for {analysis_id}: {e}")
        await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])


async def _resume_translate(analysis_id: str):
    from app.llm import translate_paragraphs

    try:
        row = await get_analysis(analysis_id)
        transcript = row.get("transcript", "")
        transcript_ko = row.get("transcript_ko", "")
        if not transcript:
            return

        existing_ko = []
        if transcript_ko:
            try:
                existing_ko = json.loads(transcript_ko)
            except (json.JSONDecodeError, TypeError):
                pass

        skip_count = len(existing_ko)
        await update_analysis(analysis_id, status="translating")

        async def on_progress(ko_json: str):
            await update_analysis(analysis_id, transcript_ko=ko_json)

        result = await translate_paragraphs(
            transcript, on_progress=on_progress,
            skip_count=skip_count, existing_ko=existing_ko,
        )
        await update_analysis(analysis_id, transcript_ko=result, status="completed")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Resume translate failed for {analysis_id}: {e}")
        await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])


async def _stage_fetch(analysis_id: str) -> dict:
    row = await get_analysis(analysis_id)
    video_id = row["video_id"]

    metadata = await asyncio.to_thread(get_metadata, video_id)
    error_message = None
    try:
        transcript, transcript_lang = await asyncio.to_thread(
            get_transcript_with_language,
            video_id,
            metadata.get("video_lang") or "en",
        )
    except TranscriptFetchError as e:
        transcript = ""
        transcript_lang = None
        error_message = str(e)

    row = await update_analysis(
        analysis_id,
        title=metadata["title"],
        channel=metadata["channel"],
        thumbnail=metadata["thumbnail"],
        duration_seconds=metadata["duration_seconds"],
        view_count=metadata["view_count"],
        like_count=metadata["like_count"],
        video_lang=metadata.get("video_lang"),
        transcript=transcript,
        transcript_lang=transcript_lang,
        error_message=error_message,
    )
    return row


def _copy_transcript_as_paragraphs(transcript_json: str, interval: int = 30) -> str:
    try:
        entries = json.loads(transcript_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not entries:
        return ""

    paragraphs = []
    current_start = entries[0]["start"]
    current_texts = []
    for entry in entries:
        if (entry["start"] - current_start >= interval
                and current_texts
                and current_texts[-1].rstrip()[-1:] in ".?!"):
            paragraphs.append({"start": current_start, "text": " ".join(current_texts)})
            current_start = entry["start"]
            current_texts = []
        current_texts.append(entry["text"])
    if current_texts:
        paragraphs.append({"start": current_start, "text": " ".join(current_texts)})
    return json.dumps(paragraphs, ensure_ascii=False)
