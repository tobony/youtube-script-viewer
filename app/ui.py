"""NiceGUI web UI for YouTube Script Viewer."""

import asyncio
import json
import os
import re
from nicegui import ui, app, context
import httpx
from app.db import get_analysis, list_analyses

API_BASE = f"http://localhost:{os.getenv('APP_PORT', '8080')}"


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
    transcript = item.get("transcript")
    transcript_ko = item.get("transcript_ko")
    if not transcript:
        return 0, 0
    try:
        entries = json.loads(transcript)
    except (json.JSONDecodeError, TypeError):
        return 0, 0
    total = len(_merge_into_paragraphs(entries, interval=30))
    done = 0
    if transcript_ko:
        try:
            done = sum(1 for entry in json.loads(transcript_ko) if (entry.get("text") or "").strip())
        except (json.JSONDecodeError, TypeError):
            pass
    return done, total


def _merge_into_paragraphs(entries: list[dict], interval: int = 30) -> list[dict]:
    if not entries:
        return []
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
    return paragraphs


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


def _analysis_matches_search(item: dict, query: str) -> bool:
    """Return True when an analysis should be visible for the search query."""
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return True

    searchable_fields = (
        "title",
        "channel",
        "url",
        "video_id",
        "status",
        "summary_short",
    )
    return any(
        normalized_query in str(item.get(field) or "").lower()
        for field in searchable_fields
    )


def _filter_analyses(items: list[dict], query: str) -> list[dict]:
    """Filter analyses by title, channel, URL, video ID, status, or summary."""
    return [item for item in items if _analysis_matches_search(item, query)]


def setup_ui():
    @ui.page("/")
    async def main_page():
        client = context.client
        dark = ui.dark_mode(False)
        layout_mode = {"value": "grid"}  # "list" or "grid"
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

            with ui.row().classes("w-full gap-4 items-center"):
                ui.button("분석", on_click=lambda: submit(url_input)).classes("px-8")
                url_input = ui.input(placeholder="YouTube URL을 입력하세요...").classes("flex-grow")
                search_input = ui.input(
                    placeholder="검색어를 입력하세요...",
                ).props("clearable").classes("w-72")


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

            async def search_history():
                _last_data[0] = None
                await refresh_history()

            search_input.on_value_change(lambda _: asyncio.ensure_future(search_history()))

            async def refresh_history():
                if not _is_client_alive(client):
                    history_timer.cancel()
                    return
                items = await list_analyses(limit=50)
                search_query = search_input.value or ""
                visible_items = _filter_analyses(items, search_query)

                # Only re-render if data changed
                data_key = json.dumps([
                    search_query,
                    [
                        (
                            i.get("id"),
                            i.get("status"),
                            (i.get("summary_short") or "")[:20],
                            len(i.get("transcript_ko") or ""),
                        )
                        for i in visible_items
                    ],
                ])
                if data_key == _last_data[0]:
                    return
                _last_data[0] = data_key

                history_container.clear()
                with history_container:
                    if search_query.strip() and not visible_items:
                        ui.label("검색 결과가 없습니다.").classes("w-full text-center opacity-60 py-8")
                        return
                    if layout_mode["value"] == "grid":
                        with ui.grid(columns=3).classes("w-full gap-4"):
                            for item in visible_items:
                                _render_grid_card(item)
                    else:
                        with ui.column().classes("w-full gap-3"):
                            for item in visible_items:
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

            async def load_detail():
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
                                            await c2.post(
                                                f"{API_BASE}/api/analyze",
                                                json={
                                                    "url": data["url"],
                                                    "llm_enabled": bool(llm_switch.value),
                                                    "force": True,
                                                    **_current_llm_payload(llm),
                                                },
                                            )
                                        timer.activate()
                                        await load_detail()
                                    ui.button("확인", on_click=confirm, color="primary")
                            dialog.open()
                        ui.button("다시 생성", on_click=regenerate, color="orange").props("outline size=sm")

                        # Resume translate button (when translation is incomplete)
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
                                    timer.activate()
                                    await load_detail()
                                else:
                                    ui.notify(f"오류: {r.json().get('detail', '')}", type="negative")
                            ui.button(f"🌐 번역 이어하기 ({done}/{total})", on_click=resume_translate, color="purple").props("outline size=sm")

                    # Error
                    if data.get("error_message"):
                        ui.label(f"❌ {data['error_message']}").classes("text-red")

                    # Step indicator (show when not completed)
                    if status not in ("completed", "failed"):
                        _render_steps(status, data)

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
                        _render_bilingual_transcript(
                            data.get("transcript"),
                            data.get("transcript_ko"),
                            duration,
                        )

            initial_detail_timer = ui.timer(0.1, load_detail, once=True)
            _last_status = [None]
            _last_ko_len = [0]

            async def poll():
                if not _is_client_alive(client):
                    timer.cancel()
                    return
                data = await get_analysis(analysis_id)
                if not data:
                    return
                status = data.get("status")
                ko_len = len(data.get("transcript_ko") or "")

                # Only re-render when status changes or new translation data arrives
                status_changed = status != _last_status[0]
                ko_updated = ko_len != _last_ko_len[0]

                if status_changed or ko_updated:
                    _last_status[0] = status
                    _last_ko_len[0] = ko_len
                    await load_detail()

                if status in ("completed", "failed"):
                    timer.deactivate()

            timer = ui.timer(3.0, poll)
            client.on_disconnect(initial_detail_timer.cancel)
            client.on_disconnect(timer.cancel)


def _render_steps(current_status: str, data: dict = None):
    """Render step indicator with completed/active/pending states."""
    active_idx = _step_index(current_status)

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
                    ui.label(label).classes("text-sm text-blue-300 font-bold")
            else:
                # Pending
                with ui.row().classes("items-center gap-1 px-3 py-1 rounded-full bg-gray-800"):
                    ui.label("⬜").classes("text-sm")
                    ui.label(label).classes("text-sm text-gray-500")

            # Arrow between steps
            if i < len(STEPS) - 1:
                ui.label("→").classes("text-gray-600")


def _render_bilingual_transcript(raw: str, raw_ko: str, duration: int):
    entries = _parse_transcript(raw)
    if not entries:
        return

    paragraphs = _merge_into_paragraphs(entries, interval=30)

    # Parse Korean (already paragraph-level from translate_paragraphs)
    ko_paragraphs = []
    if raw_ko:
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

    # Column headers with copy buttons
    with ui.grid(columns=2).classes("w-full gap-4"):
        with ui.row().classes("items-center gap-2"):
            ui.label("Original").classes("text-sm font-bold opacity-70")
            ui.button("📋 영어 전체 복사", on_click=lambda: _copy(full_en)).props("flat dense size=sm")
        with ui.row().classes("items-center gap-2"):
            ui.label("한국어").classes("text-sm font-bold opacity-70")
            if full_ko:
                ui.button("📋 한글 전체 복사", on_click=lambda: _copy(full_ko)).props("flat dense size=sm")

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
                if ko_text:
                    with ui.row().classes("items-center w-full pr-4"):
                        ui.space()
                        ui.button(icon="content_copy", on_click=lambda t=ko_text: _copy(t)).props(
                            "flat dense round size=xs"
                        ).classes("opacity-0 group-hover:opacity-100")
                    ui.label(_bold_to_html(ko_text)).classes("text-base leading-relaxed")
                else:
                    ui.label("번역 대기중").classes("text-sm opacity-30 italic")


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
            "absolute top-1 right-1 z-10 opacity-0 group-hover:opacity-100 bg-white/80"
        )
        # Thumbnail with overlays (clickable link)
        if item.get("thumbnail"):
            with ui.link(target=f"/detail/{item['id']}").classes("relative w-full block"):
                ui.image(item["thumbnail"]).classes("w-full h-40 object-cover rounded")
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
    return {
        "llm_provider": llm.LLM_PROVIDER,
        "llm_model": llm.get_model(llm.LLM_PROVIDER),
    }


def _render_llm_settings_menu(llm):
    with ui.button(icon="menu").props("flat round"):
        with ui.menu().classes("p-4"):
            with ui.column().classes("gap-3 w-72"):
                ui.label("Settings").classes("text-base font-bold")
                model_input = ui.input(
                    "Model",
                    value=llm.get_model(llm.LLM_PROVIDER),
                ).props("dense outlined").classes("w-full")

                def update_provider(e):
                    llm.LLM_PROVIDER = e.value
                    model_input.set_value(llm.get_model(e.value))
                    key_status.set_text(
                        "API key loaded" if llm.has_api_key(e.value) else "API key missing"
                    )
                    api_key_input.set_value("")

                ui.select(
                    llm.PROVIDERS,
                    label="Provider",
                    value=llm.LLM_PROVIDER,
                    on_change=update_provider,
                ).props("dense outlined").classes("w-full")

                model_input.on_value_change(
                    lambda e: llm.set_model(llm.LLM_PROVIDER, e.value)
                )

                key_status = ui.label(
                    "API key loaded" if llm.has_api_key(llm.LLM_PROVIDER) else "API key missing"
                ).classes("text-xs opacity-70")
                api_key_input = ui.input("API key").props(
                    "dense outlined type=password autocomplete=off"
                ).classes("w-full")

                def apply_api_key():
                    if llm.set_api_key(llm.LLM_PROVIDER, api_key_input.value or ""):
                        api_key_input.set_value("")
                        key_status.set_text("Runtime API key applied")
                        ui.notify("API key applied for this session", type="positive")
                    else:
                        ui.notify("Enter a non-empty API key", type="warning")

                ui.button("Apply API key", on_click=apply_api_key).props("outline size=sm")
                ui.label("Runtime API keys are not saved. Restarting the app uses .env again.").classes("text-xs opacity-70")


def _is_client_alive(client) -> bool:
    return client.id in client.instances and not getattr(client, "_deleted", False)
