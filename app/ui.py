"""NiceGUI web UI for YouTube Script Viewer."""

import asyncio
import json
import logging
import os
import re
from nicegui import ui, app, context
import httpx
from app.db import get_analysis, list_analyses
from app.transcript import merge_transcript_entries

API_BASE = f"http://localhost:{os.getenv('APP_PORT', '8080')}"
logger = logging.getLogger(__name__)


def _bold_to_html(text: str) -> str:
    """Remove **markers** from text."""
    return re.sub(r'\*\*(.+?)\*\*', r'\1', text)


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _fmt_duration(seconds: int) -> str:
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_transcript(raw: str) -> list[dict]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [{"start": 0, "text": raw}]


def _translation_progress(item: dict) -> tuple[int, int]:
    """Return (translated_count, total_paragraphs) from transcript/transcript_ko."""
    if str(item.get("transcript_lang") or "").lower().startswith("ko"):
        # A blank translation pane is the completed state for Korean sources.
        return 0, 0
    transcript = item.get("transcript")
    transcript_ko = item.get("transcript_ko")
    if not transcript:
        return 0, 0
    try:
        entries = json.loads(transcript)
    except (json.JSONDecodeError, TypeError):
        return 0, 0
    total = len(merge_transcript_entries(entries))
    done = 0
    if transcript_ko:
        try:
            done = sum(1 for entry in json.loads(transcript_ko) if (entry.get("text") or "").strip())
        except (json.JSONDecodeError, TypeError):
            pass
    return done, total


# Step definitions for progress indicator
STEPS = [
    {"key": "fetching", "label": "Transcript 추출"},
    {"key": "summarizing", "label": "요약 생성"},
    {"key": "translating", "label": "번역"},
]


def _step_index(status: str) -> int:
    """Return which step is currently active (0-based), or len(STEPS) if completed."""
    for i, s in enumerate(STEPS):
        if s["key"] == status:
            return i
    if status == "completed":
        return len(STEPS)
    return -1  # pending


def setup_ui():
    @ui.page("/")
    async def main_page():
        client = context.client
        dark = ui.dark_mode(False)
        layout_mode = {"value": "grid"}  # "list" or "grid"
        search_state = {"query": ""}
        from app import pipeline
        from app import llm

        with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
            # Header row
            with ui.row().classes("w-full items-center"):
                ui.label("YouTube Script Viewer").classes("text-3xl font-bold flex-grow")
                with ui.row().classes("items-center gap-2"):
                    llm_switch = ui.switch("AI translate/summarize", value=pipeline.llm_enabled).props("dense")
                    llm_switch.on_value_change(lambda e: setattr(pipeline, "llm_enabled", e.value))
                    ui.button(icon="light_mode", on_click=lambda: dark.set_value(not dark.value)).props("flat round")
                    ui.toggle({"list": "☰", "grid": "▦"}, value="grid",
                              on_change=lambda e: _set_layout(e.value)).props("dense")
                    _render_llm_settings_menu(llm)

            with ui.row().classes("w-full gap-4 items-center flex-wrap sm:flex-nowrap"):
                with ui.row().classes("w-full sm:w-auto items-center gap-2 flex-[2_1_28rem] flex-nowrap"):
                    url_input = ui.input(placeholder="YouTube URL을 입력하세요...").classes("flex-grow min-w-0")
                    ui.button("분석", on_click=lambda: submit(url_input)).classes("px-8 shrink-0")
                with ui.row().classes("w-full sm:w-auto items-center gap-2 flex-[1_1_18rem] flex-nowrap"):
                    search_input = ui.input(placeholder="제목 또는 내용 검색...").props("clearable").classes(
                        "flex-grow min-w-0"
                    )
                    ui.button("검색", icon="search", on_click=lambda: apply_search()).props("outline").classes(
                        "shrink-0"
                    )

            history_container = ui.element("div").classes("w-full")

            def _set_layout(val):
                layout_mode["value"] = val
                asyncio.ensure_future(refresh_history())

            async def submit(inp):
                url = inp.value.strip()
                if not url:
                    return
                inp.value = ""
                async with httpx.AsyncClient() as c:
                    r = await c.post(
                        f"{API_BASE}/api/analyze",
                        json={
                            "url": url,
                            "llm_enabled": bool(llm_switch.value),
                            **_current_llm_payload(llm),
                        },
                    )
                if r.status_code == 200:
                    ui.notify("분석 시작!", type="positive")
                    await refresh_history()
                else:
                    ui.notify(f"오류: {r.json().get('detail', 'Unknown')}", type="negative")

            _last_data = [None]

            async def apply_search():
                search_state["query"] = (search_input.value or "").strip()
                _last_data[0] = None
                await refresh_history()

            async def clear_search():
                if search_state["query"]:
                    search_state["query"] = ""
                    _last_data[0] = None
                    await refresh_history()

            search_input.on("keydown.enter", lambda: apply_search())
            search_input.on("clear", lambda: clear_search())

            async def refresh_history():
                if not _is_client_alive(client):
                    history_timer.cancel()
                    return
                items = await list_analyses(limit=50, search=search_state["query"])

                # Only re-render if data changed
                data_key = json.dumps({
                    "layout": layout_mode["value"],
                    "query": search_state["query"],
                    "items": [
                        (i.get("id"), i.get("status"), (i.get("summary_short") or "")[:20], len(i.get("transcript_ko") or ""))
                        for i in items
                    ],
                })
                if data_key == _last_data[0]:
                    return
                _last_data[0] = data_key

                history_container.clear()
                with history_container:
                    if search_state["query"]:
                        ui.label(f'"{search_state["query"]}" 검색 결과 {len(items)}개').classes(
                            "text-sm opacity-70 mb-3"
                        )
                    if not items:
                        with ui.column().classes("w-full items-center justify-center gap-2 py-16 text-center"):
                            ui.icon("search_off").classes("text-5xl opacity-40")
                            if search_state["query"]:
                                ui.label(f'"{search_state["query"]}"와 일치하는 영상이 없습니다.').classes(
                                    "text-lg font-medium"
                                )
                                ui.label("다른 검색어를 입력하거나 검색어를 지워보세요.").classes(
                                    "text-sm opacity-60"
                                )
                            else:
                                ui.label("아직 분석한 영상이 없습니다.").classes("text-lg font-medium")
                    elif layout_mode["value"] == "grid":
                        with ui.element("div").classes(
                            "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 w-full gap-4"
                        ):
                            for item in items:
                                _render_grid_card(item)
                    else:
                        with ui.column().classes("w-full gap-3"):
                            for item in items:
                                _render_card(item)

            initial_history_timer = ui.timer(0.1, refresh_history, once=True)
            history_timer = ui.timer(3.0, refresh_history)
            client.on_disconnect(initial_history_timer.cancel)
            client.on_disconnect(history_timer.cancel)

    @ui.page("/detail/{analysis_id}")
    async def detail_page(analysis_id: str):
        client = context.client
        dark = ui.dark_mode(False)
        from app import pipeline
        from app import llm

        with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
            with ui.row().classes("w-full items-center"):
                ui.button("← 목록", on_click=lambda: ui.navigate.to("/")).props("flat size=lg")
                ui.space()
                with ui.row().classes("items-center gap-2"):
                    llm_switch = ui.switch("AI translate/summarize", value=pipeline.llm_enabled).props("dense")
                    llm_switch.on_value_change(lambda e: setattr(pipeline, "llm_enabled", e.value))
                    ui.button(icon="light_mode", on_click=lambda: dark.set_value(not dark.value)).props("flat round")
                    _render_llm_settings_menu(llm)
            content_area = ui.column().classes("w-full")
            render_state = {
                "ready": False,
                "translation": None,
                "progress_label": None,
            }

            async def load_detail(preserve_scroll: bool = False):
                scroll_y = None
                if preserve_scroll and render_state["ready"]:
                    try:
                        scroll_y = await ui.run_javascript("window.scrollY")
                    except Exception:
                        logger.debug("Could not capture detail scroll position", exc_info=True)
                content_area.clear()
                data = await get_analysis(analysis_id)
                if not data:
                    with content_area:
                        ui.label("Not found").classes("text-red")
                    return

                status = data.get("status", "pending")
                with content_area:
                    # Title
                    ui.label(data.get("title") or "처리 중...").classes("text-2xl font-bold")
                    duration = data.get("duration_seconds") or 0
                    with ui.row().classes("gap-4 text-sm opacity-70 items-center"):
                        if data.get("url"):
                            ui.button("▶ YouTube", on_click=lambda: ui.run_javascript(f'window.open("{data["url"]}", "_blank")')).props("outline color=red size=sm").classes("py-0")
                        if data.get("channel"):
                            ui.label(f"📺 {data['channel']}")
                        if duration:
                            ui.label(f"⏱ {duration // 60}분 {duration % 60}초")
                        _status_badge(status)
                        ui.space()
                        if status != "completed":
                            async def stop_analysis():
                                async with httpx.AsyncClient() as c2:
                                    await c2.post(f"{API_BASE}/api/analyses/{analysis_id}/stop")
                                ui.notify("중단됨", type="warning")
                                await load_detail()
                            ui.button("Stop", on_click=stop_analysis, color="red").props("outline size=sm")
                        async def regenerate():
                            if _is_transient_youtube_error(data.get("error_message")):
                                ui.notify(data["error_message"], type="warning")
                                return
                            with ui.dialog() as dialog, ui.card():
                                ui.label("다시 생성하시겠습니까?").classes("text-base")
                                ui.label("transcript와 번역/요약을 다시 수행합니다.").classes("text-sm opacity-70")
                                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                                    ui.button("취소", on_click=dialog.close).props("flat")
                                    async def confirm():
                                        dialog.close()
                                        async with httpx.AsyncClient() as c2:
                                            await c2.post(f"{API_BASE}/api/analyses/{analysis_id}/stop")
                                            response = await c2.post(
                                                f"{API_BASE}/api/analyze",
                                                json={
                                                    "url": data["url"],
                                                    "llm_enabled": bool(llm_switch.value),
                                                    "force": True,
                                                    **_current_llm_payload(llm),
                                                },
                                            )
                                        if response.status_code == 200:
                                            revision = response.json()
                                            ui.navigate.to(f"/detail/{revision['id']}")
                                        else:
                                            ui.notify("재생성을 시작하지 못했습니다.", type="negative")
                                    ui.button("확인", on_click=confirm, color="primary")
                            dialog.open()
                        ui.button("다시 생성", on_click=regenerate, color="orange").props("outline size=sm")

                        # Resume translate button (when translation is incomplete)
                        is_korean_source = str(data.get("transcript_lang") or "").lower().startswith("ko")
                        if not is_korean_source:
                            done, total = _translation_progress(data)
                            if total and done < total and status in ("completed", "failed", "translating"):
                                async def resume_translate():
                                    async with httpx.AsyncClient() as c2:
                                        r = await c2.post(
                                            f"{API_BASE}/api/analyses/{analysis_id}/resume-translate",
                                            json=_current_llm_payload(llm),
                                        )
                                    if r.status_code == 200:
                                        ui.notify(f"번역 이어하기 시작 ({done}/{total})", type="positive")
                                        revision_id = r.json().get("analysis_id")
                                        if revision_id:
                                            ui.navigate.to(f"/detail/{revision_id}")
                                        else:
                                            timer.activate()
                                            await load_detail()
                                    else:
                                        ui.notify(f"오류: {r.json().get('detail', '')}", type="negative")
                                ui.button(
                                    f"🌐 번역 이어하기 ({done}/{total})",
                                    on_click=resume_translate,
                                    color="purple",
                                ).props("outline size=sm")

                    # Error
                    if data.get("error_message"):
                        ui.label(f"❌ {data['error_message']}").classes("text-red")

                    # Step indicator (show when not completed)
                    if status not in ("completed", "failed"):
                        render_state["progress_label"] = _render_steps(status, data)
                    else:
                        render_state["progress_label"] = None

                    # Progressive rendering based on what's available
                    has_transcript = bool(data.get("transcript"))
                    has_summary = bool(data.get("summary_short") or data.get("summary_structured"))
                    has_ko = bool(data.get("transcript_ko"))

                    # 1. Summary (show when available)
                    if has_summary:
                        with ui.card().classes("w-full"):
                            if data.get("summary_short"):
                                ui.label(data["summary_short"]).classes("text-base leading-relaxed")
                            if data.get("summary_structured"):
                                ui.separator()
                                ui.markdown(data["summary_structured"]).classes("text-base summary-md")
                                ui.add_css("""
                                    .summary-md h1, .summary-md h2, .summary-md h3 {
                                        font-size: 1rem !important;
                                        font-weight: 700 !important;
                                        margin-top: 1em;
                                        margin-bottom: 0.3em;
                                    }
                                """)

                    # 2. Transcript (show as soon as fetching is done)
                    if has_transcript:
                        render_state["translation"] = _render_bilingual_transcript(
                            data.get("transcript"),
                            data.get("transcript_ko"),
                            duration,
                            data.get("transcript_lang"),
                        )
                    else:
                        render_state["translation"] = None

                render_state["ready"] = True
                _last_status[0] = status
                _last_ko[0] = data.get("transcript_ko") or ""
                if scroll_y is not None:
                    await ui.run_javascript(
                        f"requestAnimationFrame(() => window.scrollTo(0, {json.dumps(scroll_y)}))"
                    )

            initial_detail_timer = ui.timer(0.1, load_detail, once=True)
            _last_status = [None]
            _last_ko = [None]

            async def poll():
                if not _is_client_alive(client):
                    timer.cancel()
                    return
                data = await get_analysis(analysis_id)
                if not data:
                    return
                status = data.get("status")
                ko_raw = data.get("transcript_ko") or ""

                # Only re-render when status changes or new translation data arrives
                status_changed = status != _last_status[0]
                ko_updated = ko_raw != _last_ko[0]

                if status_changed:
                    await load_detail(preserve_scroll=True)
                elif ko_updated:
                    controls = render_state["translation"]
                    if controls:
                        _update_translation_controls(controls, ko_raw)
                        progress_label = render_state["progress_label"]
                        if progress_label and status == "translating":
                            done, total = _translation_progress(data)
                            progress_label.set_text(
                                f"번역 ({done}/{total})" if total else "번역"
                            )
                        _last_ko[0] = ko_raw
                    else:
                        await load_detail(preserve_scroll=True)

                if status in ("completed", "failed"):
                    timer.deactivate()

            timer = ui.timer(3.0, poll)
            client.on_disconnect(initial_detail_timer.cancel)
            client.on_disconnect(timer.cancel)


def _render_steps(current_status: str, data: dict = None):
    """Render step indicator with completed/active/pending states."""
    active_idx = _step_index(current_status)
    translation_progress_label = None

    with ui.row().classes("w-full gap-2 my-3 items-center"):
        for i, step in enumerate(STEPS):
            # Build label with progress for translating step
            label = step["label"]
            if step["key"] == "translating" and i == active_idx and data:
                done, total = _translation_progress(data)
                if total:
                    label = f"{step['label']} ({done}/{total})"

            if i < active_idx:
                # Completed
                with ui.row().classes("items-center gap-1 px-3 py-1 rounded-full bg-green-900"):
                    ui.label("✅").classes("text-sm")
                    ui.label(label).classes("text-sm text-green-300")
            elif i == active_idx:
                # Active
                with ui.row().classes("items-center gap-1 px-3 py-1 rounded-full bg-blue-900"):
                    ui.spinner(size="sm")
                    active_label = ui.label(label).classes("text-sm text-blue-300 font-bold")
                    if step["key"] == "translating":
                        translation_progress_label = active_label
            else:
                # Pending
                with ui.row().classes("items-center gap-1 px-3 py-1 rounded-full bg-gray-800"):
                    ui.label("⬜").classes("text-sm")
                    ui.label(label).classes("text-sm text-gray-500")

            # Arrow between steps
            if i < len(STEPS) - 1:
                ui.label("→").classes("text-gray-600")

    return translation_progress_label


def _render_bilingual_transcript(
    raw: str, raw_ko: str | None, duration: int, transcript_lang: str | None = None
):
    entries = _parse_transcript(raw)
    if not entries:
        return

    paragraphs = merge_transcript_entries(entries)
    is_korean_source = bool(transcript_lang and transcript_lang.lower().startswith("ko"))

    # Parse Korean (already paragraph-level from translate_paragraphs)
    ko_paragraphs = []
    if raw_ko and not is_korean_source:
        try:
            ko_paragraphs = json.loads(raw_ko)
        except (json.JSONDecodeError, TypeError):
            pass

    # Full text for copy buttons
    full_en = "\n\n".join(p["text"] for p in paragraphs)
    full_ko = "\n\n".join(p["text"] for p in ko_paragraphs) if ko_paragraphs else ""

    # Info bar + copy all buttons
    last_ts = entries[-1]["start"] if entries else 0
    with ui.row().classes("w-full gap-4 text-sm opacity-70 mb-2 mt-4 items-center"):
        ui.label(f"⏱ 영상: {_fmt_time(duration)}")
        ui.label(f"📍 자막: {_fmt_time(entries[0]['start'])} ~ {_fmt_time(last_ts)}")
        ui.label(f"📝 {len(paragraphs)}개 단락")

    translation_controls = {
        "paragraphs": [],
        "copy_all_state": {"text": full_ko},
        "copy_all_button": None,
    }

    # Column headers with copy buttons
    with ui.grid(columns=2).classes("w-full gap-4"):
        with ui.row().classes("items-center gap-2"):
            ui.label("Original").classes("text-sm font-bold opacity-70")
            source_copy_label = "📋 한국어 전체 복사" if is_korean_source else "📋 원문 전체 복사"
            ui.button(source_copy_label, on_click=lambda: _copy(full_en)).props("flat dense size=sm")
        with ui.row().classes("items-center gap-2"):
            ui.label("한국어").classes("text-sm font-bold opacity-70")
            copy_all_button = ui.button(
                "📋 한글 전체 복사",
                on_click=lambda: _copy(translation_controls["copy_all_state"]["text"]),
            ).props("flat dense size=sm")
            copy_all_button.set_visibility(bool(full_ko))
            translation_controls["copy_all_button"] = copy_all_button

    ui.separator()

    # Side-by-side grid
    with ui.grid(columns=2).classes("w-full gap-x-6 gap-y-4"):
        for i, para in enumerate(paragraphs):
            ko_text = ko_paragraphs[i]["text"] if i < len(ko_paragraphs) else ""

            # Left: original + copy button
            with ui.column().classes("gap-1 group"):
                with ui.row().classes("items-center w-full pr-4"):
                    ui.label(_fmt_time(para["start"])).classes(
                        "text-xs text-blue-400 font-mono select-none"
                    )
                    ui.space()
                    ui.button(icon="content_copy", on_click=lambda t=para["text"]: _copy(t)).props(
                        "flat dense round size=xs"
                    ).classes("opacity-0 group-hover:opacity-100")
                ui.label(para["text"]).classes("text-base leading-relaxed")

            # Right: translation + copy button
            with ui.column().classes("gap-1 group"):
                if is_korean_source:
                    ui.label("- BLANK -").classes("text-sm opacity-30 italic")
                else:
                    text_state = {"text": ko_text}
                    with ui.row().classes("items-center w-full pr-4"):
                        ui.space()
                        copy_button = ui.button(
                            icon="content_copy",
                            on_click=lambda state=text_state: _copy(state["text"]),
                        ).props(
                            "flat dense round size=xs"
                        ).classes("opacity-0 group-hover:opacity-100")
                        copy_button.set_visibility(bool(ko_text))
                    ko_label = ui.label(
                        _bold_to_html(ko_text) if ko_text else "번역 대기중"
                    ).classes(
                        "text-base leading-relaxed" if ko_text else "text-sm opacity-30 italic"
                    )
                    translation_controls["paragraphs"].append(
                        {"state": text_state, "label": ko_label, "copy_button": copy_button}
                    )

    return translation_controls


def _update_translation_controls(controls: dict, raw_ko: str | None) -> None:
    """Update translated paragraph nodes in place without replacing the transcript DOM."""
    ko_paragraphs = _parse_transcript(raw_ko or "")
    full_ko = "\n\n".join(
        paragraph.get("text", "") for paragraph in ko_paragraphs if paragraph.get("text")
    )
    controls["copy_all_state"]["text"] = full_ko
    controls["copy_all_button"].set_visibility(bool(full_ko))

    for index, control in enumerate(controls["paragraphs"]):
        ko_text = (
            ko_paragraphs[index].get("text", "")
            if index < len(ko_paragraphs)
            else ""
        )
        control["state"]["text"] = ko_text
        control["copy_button"].set_visibility(bool(ko_text))
        if ko_text:
            control["label"].set_text(_bold_to_html(ko_text))
            control["label"].classes(
                remove="text-sm opacity-30 italic",
                add="text-base leading-relaxed",
            )
        else:
            control["label"].set_text("번역 대기중")
            control["label"].classes(
                remove="text-base leading-relaxed",
                add="text-sm opacity-30 italic",
            )


def _copy(text: str):
    ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(text)})')
    ui.notify("복사됨", type="positive", position="bottom", timeout=1000)


def _render_card(item: dict):
    with ui.card().classes("w-full cursor-pointer").on("click", lambda i=item: ui.navigate.to(f"/detail/{i['id']}")):
        with ui.row().classes("w-full gap-4 items-center"):
            if item.get("thumbnail"):
                ui.image(item["thumbnail"]).classes("w-32 h-20 object-cover rounded")
            with ui.column().classes("flex-grow"):
                ui.label(item.get("title") or item["url"]).classes("font-bold text-base")
                with ui.row().classes("gap-2 text-sm opacity-70"):
                    if item.get("channel"):
                        ui.label(item["channel"])
                    if item.get("duration_seconds"):
                        ui.label(f"{item['duration_seconds'] // 60}분")
            _status_badge(item.get("status", "pending"))


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _render_grid_card(item: dict):
    with ui.card().classes("w-full relative group"):
        # Delete button (top-right, visible on hover)
        async def delete_card():
            with ui.dialog() as dialog, ui.card():
                ui.label("삭제하시겠습니까?").classes("text-base")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("취소", on_click=dialog.close).props("flat")
                    async def confirm_delete():
                        dialog.close()
                        async with httpx.AsyncClient() as c:
                            await c.delete(f"{API_BASE}/api/analyses/{item['id']}")
                        ui.notify("삭제됨", type="info")
                    ui.button("삭제", on_click=confirm_delete, color="red")
            dialog.open()
        ui.button(icon="close", on_click=delete_card).props("flat round size=sm").classes(
            "absolute top-1 right-1 z-10 opacity-100 sm:opacity-0 "
            "sm:group-hover:opacity-100 bg-white/80"
        )
        # Thumbnail with overlays (clickable link)
        if item.get("thumbnail"):
            with ui.link(target=f"/detail/{item['id']}").classes("relative w-full block"):
                ui.image(item["thumbnail"]).classes("w-full h-48 sm:h-40 object-cover rounded")
                # Like count (top-right)
                if item.get("like_count"):
                    with ui.element("div").classes("absolute top-2 right-2 bg-black/70 text-white text-xs px-2 py-0.5 rounded-full flex items-center gap-1"):
                        ui.label(f"👍 {_fmt_count(item['like_count'])}")
                # View count (bottom-left)
                if item.get("view_count"):
                    with ui.element("div").classes("absolute bottom-2 left-2 bg-black/70 text-white text-xs px-2 py-0.5 rounded-full flex items-center gap-1"):
                        ui.label(f"👁 {_fmt_count(item['view_count'])}")
                # Duration (bottom-right)
                if item.get("duration_seconds"):
                    dur = item["duration_seconds"]
                    with ui.element("div").classes("absolute bottom-2 right-2 bg-black/70 text-white text-xs px-2 py-0.5 rounded-full"):
                        ui.label(f"⏱ {_fmt_duration(dur)}")
        with ui.column().classes("p-2 gap-1"):
            ui.link(item.get("title") or item["url"], target=f"/detail/{item['id']}").classes("font-bold text-sm line-clamp-2 no-underline text-inherit")
            with ui.row().classes("gap-2 text-xs opacity-70"):
                if item.get("channel"):
                    ui.label(item["channel"])
            with ui.row().classes("items-center gap-2"):
                if item.get("url"):
                    ui.button("▶ YouTube", on_click=lambda e, u=item["url"]: ui.run_javascript(f'window.open("{u}", "_blank")') or e.stop_propagation()).props("outline color=red size=sm").classes("py-0")
                _status_badge(item.get("status", "pending"))
                # Show workflow step when in progress
                status = item.get("status", "pending")
                if status not in ("completed", "failed"):
                    step_labels = {"pending": "대기중", "waiting": "⏳ 대기중", "fetching": "📥 추출중", "summarizing": "📝 요약중", "translating": "🌐 번역중"}
                    label = step_labels.get(status, "")
                    if status == "translating":
                        done, total = _translation_progress(item)
                        if total:
                            label = f"🌐 번역 {done}/{total}"
                    ui.label(label).classes("text-xs opacity-70")
            # Summary preview (4 lines max with tooltip)
            if item.get("summary_short"):
                summary = item["summary_short"]
                with ui.label(summary).classes("text-sm opacity-70 line-clamp-4 mt-1"):
                    ui.tooltip(summary).props('max-width="400px"').classes("text-sm")


def _status_badge(status: str):
    colors = {
        "waiting": "grey",
        "pending": "grey",
        "fetching": "blue",
        "summarizing": "orange",
        "translating": "purple",
        "completed": "green",
        "failed": "red",
    }
    ui.badge(status, color=colors.get(status, "grey"))


def _is_transient_youtube_error(error_message: str | None) -> bool:
    if not error_message:
        return False
    lowered = error_message.lower()
    return "rate limited" in lowered or "blocking transcript requests" in lowered


def _current_llm_payload(llm) -> dict:
    provider = llm.LLM_PROVIDER
    return {
        "llm_provider": provider,
        "llm_model": llm.get_model(provider),
        "summary_model": llm.get_task_model(provider, "summary"),
        "translation_model": llm.get_task_model(provider, "translation"),
    }


def _render_llm_settings_menu(llm):
    with ui.button(icon="menu").props("flat round"):
        with ui.menu().classes("p-4 max-h-[calc(100vh-2rem)] overflow-y-auto"):
            with ui.column().classes("gap-3 w-80"):
                ui.label("Settings").classes("text-base font-bold")
                ui.select(
                    llm.PROVIDERS,
                    label="Provider",
                    value=llm.LLM_PROVIDER,
                    on_change=lambda e: update_provider(e.value),
                ).props("dense outlined").classes("w-full")

                model_container = ui.column().classes("w-full gap-2")
                auth_container = ui.column().classes("w-full gap-2")

                def render_model_controls(provider: str):
                    model_container.clear()
                    with model_container:
                        if provider != "codex_subscription":
                            ui.input(
                                "요약 모델",
                                value=llm.get_task_model(provider, "summary"),
                                on_change=lambda e: llm.set_task_model(provider, "summary", e.value),
                            ).props("dense outlined").classes("w-full")
                            ui.input(
                                "번역 모델",
                                value=llm.get_task_model(provider, "translation"),
                                on_change=lambda e: llm.set_task_model(provider, "translation", e.value),
                            ).props("dense outlined").classes("w-full")
                            return

                        fallback_options = {
                            "gpt-5.6-terra": "GPT-5.6-Terra",
                            "gpt-5.6-luna": "GPT-5.6-Luna",
                            "gpt-5.6-sol": "GPT-5.6-Sol",
                        }
                        summary_select = ui.select(
                            fallback_options,
                            label="요약 모델",
                            value=llm.get_task_model(provider, "summary"),
                            on_change=lambda e: llm.set_task_model(provider, "summary", e.value),
                        ).props("dense outlined options-dense").classes("w-full")
                        translation_select = ui.select(
                            fallback_options,
                            label="번역 모델",
                            value=llm.get_task_model(provider, "translation"),
                            on_change=lambda e: llm.set_task_model(provider, "translation", e.value),
                        ).props("dense outlined options-dense").classes("w-full")
                        model_status = ui.label("계정 모델 목록 확인 중...").classes(
                            "text-xs opacity-70 leading-snug"
                        )

                        async def refresh_models():
                            if llm.LLM_PROVIDER != provider:
                                return
                            refresh_models_button.disable()
                            model_status.set_text("계정 모델 목록 확인 중...")
                            try:
                                models = await llm.list_available_models(provider)
                                options = {
                                    item["id"]: item.get("display_name") or item["id"]
                                    for item in models
                                }
                                for selected in (summary_select.value, translation_select.value):
                                    if selected and selected not in options:
                                        options[selected] = selected
                                summary_select.set_options(options, value=summary_select.value)
                                translation_select.set_options(options, value=translation_select.value)
                                names = ", ".join(options.values())
                                model_status.set_text(f"계정에서 사용 가능 ({len(models)}): {names}")
                            except Exception as exc:
                                logger.exception("Codex model list refresh failed")
                                model_status.set_text(f"모델 목록을 확인하지 못했습니다: {exc}")
                            finally:
                                refresh_models_button.enable()

                        refresh_models_button = ui.button(
                            "사용 가능 모델 확인",
                            icon="refresh",
                            on_click=refresh_models,
                        ).props("outline size=sm")
                        ui.timer(0.15, refresh_models, once=True)

                def render_auth_controls(provider: str):
                    auth_container.clear()
                    profile = llm.get_provider_profile(provider)
                    with auth_container:
                        if profile.auth_type == "managed_login":
                            _render_codex_auth_controls(llm, provider)
                            return

                        if provider == "azure":
                            azure_connection = llm.get_azure_connection()
                            endpoint_input = ui.input(
                                "Endpoint",
                                value=azure_connection["endpoint"],
                                placeholder="https://<resource>.openai.azure.com",
                            ).props("dense outlined").classes("w-full")
                            region_input = ui.input(
                                "Region",
                                value=azure_connection["region"],
                                placeholder="e.g. eastus2",
                            ).props("dense outlined").classes("w-full")
                            connection_status = ui.label(
                                "Azure connection loaded"
                                if azure_connection["endpoint"]
                                else "Azure endpoint missing"
                            ).classes("text-xs opacity-70")

                            def apply_azure_connection():
                                if llm.set_azure_connection(
                                    endpoint_input.value or "", region_input.value or ""
                                ):
                                    connection_status.set_text("Runtime Azure connection applied")
                                    ui.notify("Azure endpoint and region applied for this session", type="positive")
                                else:
                                    ui.notify("Enter a valid http(s) Azure endpoint", type="warning")

                            ui.button(
                                "Apply Azure settings", on_click=apply_azure_connection
                            ).props("outline size=sm")

                        key_status = ui.label(
                            "API key loaded" if llm.has_api_key(provider) else "API key missing"
                        ).classes("text-xs opacity-70")
                        api_key_input = ui.input("API key").props(
                            "dense outlined type=password autocomplete=off"
                        ).classes("w-full")

                        def apply_api_key():
                            if llm.set_api_key(provider, api_key_input.value or ""):
                                api_key_input.set_value("")
                                key_status.set_text("Runtime API key applied")
                                ui.notify("API key applied for this session", type="positive")
                            else:
                                ui.notify("Enter a non-empty API key", type="warning")

                        ui.button("Apply API key", on_click=apply_api_key).props("outline size=sm")
                        ui.label("Runtime API keys are not saved. Restarting the app uses .env again.").classes(
                            "text-xs opacity-70"
                        )

                def update_provider(provider: str):
                    llm.LLM_PROVIDER = provider
                    render_model_controls(provider)
                    render_auth_controls(provider)

                render_model_controls(llm.LLM_PROVIDER)
                render_auth_controls(llm.LLM_PROVIDER)


def _render_codex_auth_controls(llm, provider: str):
    status_label = ui.label("Codex 로그인 상태 확인 중...").classes("text-xs opacity-70")
    with ui.row().classes("w-full gap-2 flex-wrap"):
        browser_login_button = ui.button("ChatGPT로 로그인", icon="login").props("outline size=sm")
        device_login_button = ui.button("기기 코드", icon="devices").props("outline size=sm")
        refresh_button = ui.button(icon="refresh").props("flat round size=sm")
        logout_button = ui.button("로그아웃").props("flat color=negative size=sm")
        logout_button.set_enabled(False)

    ui.label("Codex app-server가 로그인 정보와 토큰 갱신을 관리합니다.").classes("text-xs opacity-60")

    with ui.dialog() as login_dialog, ui.card().classes("w-96 max-w-full"):
        login_dialog_content = ui.column().classes("w-full gap-3")

    with ui.dialog() as logout_dialog, ui.card().classes("w-80 max-w-full"):
        ui.label("Codex에서 로그아웃할까요?").classes("text-base font-bold")
        ui.label("이 컴퓨터의 Codex 앱과 CLI가 공유하는 로그인도 해제됩니다.").classes("text-sm opacity-70")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("취소", on_click=logout_dialog.close).props("flat")

            async def confirm_logout():
                try:
                    await llm.logout_managed_provider(provider)
                    logout_dialog.close()
                    ui.notify("Codex에서 로그아웃했습니다.", type="positive")
                    await refresh_status()
                except Exception as exc:
                    ui.notify(str(exc), type="negative")

            ui.button("로그아웃", on_click=confirm_logout).props("color=negative")

    async def refresh_status():
        status_label.set_text("Codex 로그인 상태 확인 중...")
        refresh_button.disable()
        try:
            status = await llm.get_auth_status(provider)
            connected = bool(status.get("connected"))
            if connected:
                plan = str(status.get("plan_type") or "subscription").replace("_", " ").title()
                status_label.set_text(f"연결됨 · ChatGPT {plan}")
            elif not status.get("available", True):
                status_label.set_text(status.get("error") or "Codex app-server를 사용할 수 없습니다.")
            else:
                status_label.set_text("ChatGPT 로그인이 필요합니다.")
            logout_button.set_enabled(connected)
            browser_login_button.set_enabled(not connected and status.get("available", True))
            device_login_button.set_enabled(not connected and status.get("available", True))
        except Exception as exc:
            logger.exception("Codex auth status refresh failed")
            status_label.set_text(str(exc).strip() or "Codex 로그인 상태를 확인하지 못했습니다.")
            logout_button.set_enabled(False)
        finally:
            refresh_button.enable()

    async def start_login(mode: str):
        browser_login_button.disable()
        device_login_button.disable()
        try:
            result = await llm.start_managed_login(provider, mode)
            login_dialog_content.clear()
            with login_dialog_content:
                if mode == "device":
                    ui.label("기기 코드로 ChatGPT 로그인").classes("text-lg font-bold")
                    ui.label("아래 코드를 로그인 페이지에 입력하세요.").classes("text-sm opacity-70")
                    ui.label(result.get("userCode") or "-").classes(
                        "text-2xl font-mono font-bold tracking-widest"
                    )
                    ui.link("ChatGPT 기기 로그인 열기", result.get("verificationUrl") or "#", new_tab=True)
                else:
                    ui.label("ChatGPT 로그인").classes("text-lg font-bold")
                    ui.label("로그인 페이지에서 완료한 뒤 이 메뉴의 새로고침 버튼을 누르세요.").classes(
                        "text-sm opacity-70"
                    )
                    ui.link("ChatGPT 로그인 페이지 열기", result.get("authUrl") or "#", new_tab=True)
                with ui.row().classes("w-full justify-end"):
                    ui.button("닫기", on_click=login_dialog.close).props("flat")
            login_dialog.open()
        except Exception as exc:
            ui.notify(str(exc), type="negative")
            await refresh_status()
        finally:
            browser_login_button.enable()
            device_login_button.enable()

    browser_login_button.on_click(lambda: start_login("chatgpt"))
    device_login_button.on_click(lambda: start_login("device"))
    refresh_button.on_click(refresh_status)
    logout_button.on_click(logout_dialog.open)
    ui.timer(0.1, refresh_status, once=True)


def _is_client_alive(client) -> bool:
    return client.id in client.instances and not getattr(client, "_deleted", False)
