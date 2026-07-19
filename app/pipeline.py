"""Async pipeline: Queue-based sequential execution."""

import asyncio
import json
import logging
from app.db import activate_analysis, update_analysis, get_analysis
from app.youtube import TranscriptFetchError, get_metadata, get_transcript_with_language
from app.transcript import merge_transcript_entries

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
        job = await _queue.get()
        if len(job) == 2:
            job_type, analysis_id = job
            llm_provider = None
            summary_model = None
            translation_model = None
        elif len(job) == 4:
            job_type, analysis_id, llm_provider, llm_model = job
            summary_model = llm_model
            translation_model = llm_model
        else:
            job_type, analysis_id, llm_provider, summary_model, translation_model = job
        global _current_id, _current_task
        _current_id = analysis_id
        try:
            if job_type == "full":
                _current_task = asyncio.current_task()
                await run_pipeline(
                    analysis_id,
                    llm_provider=llm_provider,
                    summary_model=summary_model,
                    translation_model=translation_model,
                )
            elif job_type == "resume_translate":
                _current_task = asyncio.current_task()
                await _resume_translate(
                    analysis_id,
                    llm_provider=llm_provider,
                    translation_model=translation_model,
                )
        except asyncio.CancelledError:
            await update_analysis(analysis_id, status="failed", error_message="Stopped by user")
        except Exception as e:
            logger.error(f"Worker error for {analysis_id}: {e}")
        finally:
            _current_id = None
            _current_task = None
            _queue.task_done()


def start_pipeline(
    analysis_id: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    summary_model: str | None = None,
    translation_model: str | None = None,
):
    _ensure_worker()
    asyncio.ensure_future(
        _enqueue(
            "full",
            analysis_id,
            llm_provider,
            summary_model or llm_model,
            translation_model or llm_model,
        )
    )


def start_resume_translate(
    analysis_id: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    translation_model: str | None = None,
):
    _ensure_worker()
    asyncio.ensure_future(
        _enqueue("resume_translate", analysis_id, llm_provider, None, translation_model or llm_model)
    )


async def _enqueue(
    job_type: str,
    analysis_id: str,
    llm_provider: str | None = None,
    summary_model: str | None = None,
    translation_model: str | None = None,
):
    await update_analysis(analysis_id, status="waiting")
    await _queue.put((job_type, analysis_id, llm_provider, summary_model, translation_model))


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


async def run_pipeline(
    analysis_id: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    summary_model: str | None = None,
    translation_model: str | None = None,
):
    summary_model = summary_model or llm_model
    translation_model = translation_model or llm_model
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
            from app.llm import LLMServiceError, summarize, translate_paragraphs

            try:
                await update_analysis(analysis_id, status="summarizing")
                short, structured = await summarize(transcript, provider=llm_provider, model=summary_model)
                await update_analysis(analysis_id, summary_short=short, summary_structured=structured)
            except LLMServiceError as e:
                await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])
                return
            except Exception as e:
                logger.warning(f"Summarize failed for {analysis_id}: {e}")

            if row.get("transcript_lang") == "ko":
                # Korean source text does not need a second translated copy.
                await update_analysis(analysis_id, transcript_ko=None)
            else:
                try:
                    await update_analysis(analysis_id, status="translating")

                    async def on_translate_progress(ko_json: str):
                        await update_analysis(analysis_id, transcript_ko=ko_json)

                    transcript_ko = await translate_paragraphs(
                        transcript,
                        on_progress=on_translate_progress,
                        provider=llm_provider,
                        model=translation_model,
                    )
                    await update_analysis(analysis_id, transcript_ko=transcript_ko)
                except LLMServiceError as e:
                    await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])
                    return
                except Exception as e:
                    logger.warning(f"Translate failed for {analysis_id}: {e}")

        await update_analysis(analysis_id, status="completed")
        await activate_analysis(analysis_id)
        logger.info(f"Pipeline completed: {analysis_id}")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Pipeline failed for {analysis_id}: {e}")
        await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])


async def _resume_translate(
    analysis_id: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    translation_model: str | None = None,
):
    translation_model = translation_model or llm_model
    from app.llm import LLMServiceError, translate_paragraphs

    try:
        row = await get_analysis(analysis_id)
        transcript = row.get("transcript", "")
        transcript_ko = row.get("transcript_ko", "")
        if not transcript:
            return

        if row.get("transcript_lang") == "ko":
            await update_analysis(analysis_id, transcript_ko=None, status="completed")
            await activate_analysis(analysis_id)
            return

        existing_ko = []
        if transcript_ko:
            try:
                existing_ko = _leading_completed_translations(json.loads(transcript_ko))
            except (json.JSONDecodeError, TypeError):
                pass

        skip_count = len(existing_ko)
        await update_analysis(analysis_id, status="translating")

        async def on_progress(ko_json: str):
            await update_analysis(analysis_id, transcript_ko=ko_json)

        result = await translate_paragraphs(
            transcript,
            on_progress=on_progress,
            skip_count=skip_count,
            existing_ko=existing_ko,
            provider=llm_provider,
            model=translation_model,
        )
        await update_analysis(analysis_id, transcript_ko=result, status="completed")
        await activate_analysis(analysis_id)
    except asyncio.CancelledError:
        raise
    except LLMServiceError as e:
        await update_analysis(analysis_id, status="failed", error_message=str(e)[:1000])
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
    return json.dumps(merge_transcript_entries(entries), ensure_ascii=False)


def _leading_completed_translations(entries: list[dict]) -> list[dict]:
    completed = []
    for entry in entries:
        if not (entry.get("text") or "").strip():
            break
        completed.append(entry)
    return completed
