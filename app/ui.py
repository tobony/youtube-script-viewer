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
GITHUB_REPO_URL = "https://github.com/tobony/youtube-script-viewer"
logger = logging.getLogger(__name__)

GITHUB_ICON_CSS = r"""
.vpi-social-github {
    display: inline-block;
    width: 1.25rem;
    height: 1.25rem;
    flex: none;
    background-color: currentColor;
    -webkit-mask: url("data:image/svg+xml,%3Csvg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M12 .7a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2.23c-3.22.7-3.9-1.37-3.9-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.71.08-.71 1.17.08 1.78 1.2 1.78 1.2 1.04 1.78 2.72 1.27 3.39.97.1-.75.4-1.27.74-1.56-2.57-.3-5.27-1.29-5.27-5.69 0-1.26.45-2.29 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.16 1.18a10.95 10.95 0 0 1 5.75 0c2.19-1.49 3.16-1.18 3.16-1.18.63 1.59.23 2.76.11 3.05.74.8 1.19 1.83 1.19 3.09 0 4.41-2.71 5.39-5.29 5.68.42.36.79 1.06.79 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .7Z'/%3E%3C/svg%3E") no-repeat center / contain;
    mask: url("data:image/svg+xml,%3Csvg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M12 .7a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2.23c-3.22.7-3.9-1.37-3.9-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.71.08-.71 1.17.08 1.78 1.2 1.78 1.2 1.04 1.78 2.72 1.27 3.39.97.1-.75.4-1.27.74-1.56-2.57-.3-5.27-1.29-5.27-5.69 0-1.26.45-2.29 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.16 1.18a10.95 10.95 0 0 1 5.75 0c2.19-1.49 3.16-1.18 3.16-1.18.63 1.59.23 2.76.11 3.05.74.8 1.19 1.83 1.19 3.09 0 4.41-2.71 5.39-5.29 5.68.42.36.79 1.06.79 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .7Z'/%3E%3C/svg%3E") no-repeat center / contain;
}
.github-header-only { display: none !important; }
.github-menu-only { display: flex !important; }
@media (min-width: 760px) {
    .github-header-only { display: inline-flex !important; }
    .github-menu-only { display: none !important; }
}
"""


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


def _github_link_classes(placement: str) -> str:
    """Return responsive classes for the GitHub link placement."""
    if placement == "header":
        return "github-header-only no-underline text-inherit items-center justify-center w-10 h-10 rounded-full"
    if placement == "menu":
        return "no-underline text-inherit items-center gap-2 py-2"
    raise ValueError(f"Unsupported GitHub link placement: {placement}")


def _render_github_link(placement: str) -> None:
    with ui.link(target=GITHUB_REPO_URL, new_tab=True).classes(
        _github_link_classes(placement)
    ).props('aria-label="GitHub Repository" title="GitHub Repository" rel="noopener noreferrer"'):
        ui.element("span").classes("vpi-social-github").props('aria-hidden="true"')
        if placement == "menu":
            ui.label("GitHub Repository").classes("text-sm")
        else:
            ui.tooltip("GitHub Repository")


def setup_ui():
    ui.add_head_html(f"<style>{GITHUB_ICON_CSS}</style>", shared=True)

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
                    _render_github_link("header")

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

            history_state = {
                "structure_key": None,
                "item_structure_keys": {},
                "content_key": None,
                "rendered": False,
                "cards": {},
                "layout": None,
                "query": None,
            }
            history_refresh_lock = asyncio.Lock()

            async def apply_search():
                search_state["query"] = (search_input.value or "").strip()
                await refresh_history()

            async def clear_search():
                if search_state["query"]:
                    search_state["query"] = ""
                    await refresh_history()

            search_input.on("keydown.enter", lambda: apply_search())
            search_input.on("clear", lambda: clear_search())

            async def _capture_history_anchor():
                """Capture the visible card and scroll position before a structural update."""
                try:
                    return await ui.run_javascript(
                        """(() => {
                            const cards = Array.from(document.querySelectorAll('[data-analysis-id]'));
                            const visible = cards.find((card) => {
                                const rect = card.getBoundingClientRect();
                                return rect.bottom > 0 && rect.top < window.innerHeight;
                            });
                            return {
                                scrollY: window.scrollY,
                                id: visible?.getAttribute('data-analysis-id') ?? null,
                                top: visible?.getBoundingClientRect().top ?? null,
                            };
                        })()"""
                    )
                except Exception:
                    logger.debug("Could not capture history scroll position", exc_info=True)
                    return None

            async def _restore_history_anchor(anchor):
                if not isinstance(anchor, dict):
                    return
                scroll_y = anchor.get("scrollY")
                if not isinstance(scroll_y, (int, float)):
                    return
                await ui.run_javascript(
                    f"""requestAnimationFrame(() => {{
                        const oldY = {json.dumps(scroll_y)};
                        const oldTop = {json.dumps(anchor.get("top"))};
                        const anchorId = {json.dumps(anchor.get("id"))};
                        let targetY = oldY;
                        if (anchorId !== null && Number.isFinite(oldTop)) {{
                            const card = Array.from(document.querySelectorAll('[data-analysis-id]'))
                                .find((element) => element.getAttribute('data-analysis-id') === anchorId);
                            if (card) targetY += card.getBoundingClientRect().top - oldTop;
                        }}
                        window.scrollTo(0, Math.max(0, targetY));
                    }})"""
                )

            async def refresh_history():
                if not _is_client_alive(client):
                    history_timer.cancel()
                    return

                # A timer tick and a user-triggered refresh can overlap. Serializing
                # them prevents two structural renders from racing each other.
                async with history_refresh_lock:
                    items = await list_analyses(limit=50, search=search_state["query"])
                    structure_key = _history_structure_key(
                        layout_mode["value"], search_state["query"], items
                    )
                    content_key = _history_content_key(items)

                    if structure_key == history_state["structure_key"]:
                        if content_key == history_state["content_key"]:
                            return
                        # Progress, status, and summary changes only update the
                        # controls already attached to each card. The card DOM is
                        # deliberately kept stable while a job is running.
                        anchor = await _capture_history_anchor()
                        for item in items:
                            controls = history_state["cards"].get(str(item.get("id")))
                            if controls:
                                _update_history_card(controls, item)
                        history_state["content_key"] = content_key
                        if anchor:
                            await _restore_history_anchor(anchor)
                        return

                    # Metadata such as a thumbnail, duration, or view count
                    # can arrive after the card was first rendered.  The item
                    # set and ordering are unchanged in that case, so rebuild
                    # only the affected card contents instead of clearing the
                    # entire history container (which flashes the whole page).
                    current_item_ids = [str(item.get("id")) for item in items]
                    rendered_item_ids = list(history_state["cards"].keys())
                    same_items = (
                        history_state["rendered"]
                        and history_state["layout"] == layout_mode["value"]
                        and history_state["query"] == search_state["query"]
                        and rendered_item_ids == current_item_ids
                        and all(
                            history_state["cards"].get(item_id)
                            for item_id in current_item_ids
                        )
                    )
                    if same_items:
                        anchor = await _capture_history_anchor()
                        next_item_structure_keys = {}
                        for item in items:
                            item_id = str(item.get("id"))
                            next_item_structure_keys[item_id] = _history_item_structure_key(
                                layout_mode["value"], item
                            )
                            controls = history_state["cards"][item_id]
                            if (
                                next_item_structure_keys[item_id]
                                != history_state["item_structure_keys"].get(item_id)
                            ):
                                _rebuild_history_card(
                                    controls, item, layout_mode["value"]
                                )
                            _update_history_card(controls, item)
                        history_state.update(
                            {
                                "structure_key": structure_key,
                                "item_structure_keys": next_item_structure_keys,
                                "content_key": content_key,
                            }
                        )
                        if anchor:
                            await _restore_history_anchor(anchor)
                        return

                    preserve_anchor = (
                        history_state["rendered"]
                        and history_state["layout"] == layout_mode["value"]
                        and history_state["query"] == search_state["query"]
                    )
                    anchor = await _capture_history_anchor() if preserve_anchor else None

                    history_container.clear()
                    cards = {}
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
                                    cards[str(item.get("id"))] = _render_grid_card(item)
                        else:
                            with ui.column().classes("w-full gap-3"):
                                for item in items:
                                    cards[str(item.get("id"))] = _render_card(item)

                    history_state.update({
                        "structure_key": structure_key,
                        "item_structure_keys": {
                            str(item.get("id")): _history_item_structure_key(
                                layout_mode["value"], item
                            )
                            for item in items
                        },
                        "content_key": content_key,
                        "rendered": True,
                        "cards": cards,
                        "layout": layout_mode["value"],
                        "query": search_state["query"],
                    })
                    if anchor:
                        await _restore_history_anchor(anchor)

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
                    _render_github_link("header")
            content_area = ui.column().classes("w-full")
            render_state = {
                "ready": False,
                "data": None,
                "translation": None,
                "progress_label": None,
                "progress_container": None,
                "status_badge": None,
                "stop_button": None,
                "title_label": None,
                "channel_label": None,
                "duration_label": None,
                "error_label": None,
                "summary_container": None,
                "summary_key": None,
                "transcript_container": None,
                "transcript_key": None,
                "resume_button": None,
            }

            async def load_detail(preserve_scroll: bool = False):
                """Render the detail page for the initial load or an explicit action.

                Polling uses ``_update_detail_in_place`` below instead.  Keeping
                this full render for navigation and user actions makes the
                initial page construction straightforward while ensuring that a
                background status transition never clears the whole page.
                """
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
                duration = data.get("duration_seconds") or 0
                render_state["data"] = data
                with content_area:
                    # Title
                    title_label = ui.label(data.get("title") or "처리 중...").classes(
                        "text-2xl font-bold"
                    )
                    with ui.row().classes("gap-4 text-sm opacity-70 items-center"):
                        if data.get("url"):
                            ui.button("▶ YouTube", on_click=lambda: ui.run_javascript(f'window.open("{data["url"]}", "_blank")')).props("outline color=red size=sm").classes("py-0")
                        channel_label = ui.label("")
                        channel_label.set_visibility(False)
                        duration_label = ui.label("")
                        duration_label.set_visibility(False)
                        status_badge = _status_badge(status)
                        ui.space()

                        async def stop_analysis():
                            async with httpx.AsyncClient() as c2:
                                await c2.post(f"{API_BASE}/api/analyses/{analysis_id}/stop")
                            ui.notify("중단됨", type="warning")
                            await load_detail()

                        stop_button = ui.button(
                            "Stop", on_click=stop_analysis, color="red"
                        ).props("outline size=sm")

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

                        async def resume_translate():
                            current_data = render_state.get("data") or data
                            done, total = _translation_progress(current_data)
                            async with httpx.AsyncClient() as c2:
                                r = await c2.post(
                                    f"{API_BASE}/api/analyses/{analysis_id}/resume-translate",
                                    json=_current_llm_payload(llm),
                                )
                            if r.status_code == 200:
                                ui.notify(
                                    f"번역 이어하기 시작 ({done}/{total})", type="positive"
                                )
                                revision_id = r.json().get("analysis_id")
                                if revision_id:
                                    ui.navigate.to(f"/detail/{revision_id}")
                                else:
                                    timer.activate()
                                    await load_detail()
                            else:
                                ui.notify(
                                    f"오류: {r.json().get('detail', '')}", type="negative"
                                )

                        resume_button = ui.button(
                            "🌐 번역 이어하기",
                            on_click=resume_translate,
                            color="purple",
                        ).props("outline size=sm")

                    # Error
                    error_label = ui.label("").classes("text-red")

                    # Step indicator (show when not completed)
                    with ui.element("div") as progress_container:
                        if status not in ("completed", "failed"):
                            render_state["progress_label"] = _render_steps(status, data)
                        else:
                            render_state["progress_label"] = None

                    # Keep stable containers for progressive summary/transcript
                    # updates.  Only these small regions are cleared when their
                    # own content changes.
                    summary_container = ui.column().classes("w-full")
                    _render_summary_content(summary_container, data)
                    transcript_container = ui.column().classes("w-full")
                    render_state["translation"] = _render_transcript_content(
                        transcript_container, data
                    )

                render_state.update(
                    {
                        "title_label": title_label,
                        "channel_label": channel_label,
                        "duration_label": duration_label,
                        "status_badge": status_badge,
                        "stop_button": stop_button,
                        "resume_button": resume_button,
                        "error_label": error_label,
                        "progress_container": progress_container,
                        "summary_container": summary_container,
                        "summary_key": _detail_summary_key(data),
                        "transcript_container": transcript_container,
                        "transcript_key": _detail_transcript_key(data),
                    }
                )
                channel = data.get("channel") or ""
                channel_label.set_text(f"📺 {channel}" if channel else "")
                channel_label.set_visibility(bool(channel))
                duration_label.set_text(
                    f"⏱ {duration // 60}분 {duration % 60}초" if duration else ""
                )
                duration_label.set_visibility(bool(duration))
                stop_button.set_visibility(status != "completed")
                error_message = data.get("error_message") or ""
                error_label.set_text(f"❌ {error_message}" if error_message else "")
                error_label.set_visibility(bool(error_message))
                progress_container.set_visibility(status not in ("completed", "failed"))
                _update_resume_button_visibility(data, resume_button)

                render_state["ready"] = True
                _last_status[0] = status
                _last_ko[0] = data.get("transcript_ko") or ""
                if scroll_y is not None:
                    await ui.run_javascript(
                        f"requestAnimationFrame(() => window.scrollTo(0, {json.dumps(scroll_y)}))"
                    )

            async def _update_detail_in_place(data: dict):
                """Apply polled changes without replacing the detail page DOM."""
                if not render_state["ready"]:
                    await load_detail(preserve_scroll=True)
                    return

                status = data.get("status", "pending")
                ko_raw = data.get("transcript_ko") or ""
                status_changed = status != _last_status[0]
                ko_updated = ko_raw != _last_ko[0]
                summary_key = _detail_summary_key(data)
                transcript_key = _detail_transcript_key(data)
                summary_changed = summary_key != render_state["summary_key"]
                transcript_changed = transcript_key != render_state["transcript_key"]

                if not any(
                    (status_changed, ko_updated, summary_changed, transcript_changed)
                ):
                    return

                try:
                    scroll_y = await ui.run_javascript("window.scrollY")
                except Exception:
                    scroll_y = None

                render_state["data"] = data
                title = data.get("title") or "처리 중..."
                render_state["title_label"].set_text(title)

                channel = data.get("channel") or ""
                render_state["channel_label"].set_text(f"📺 {channel}" if channel else "")
                render_state["channel_label"].set_visibility(bool(channel))

                duration = data.get("duration_seconds") or 0
                render_state["duration_label"].set_text(
                    f"⏱ {duration // 60}분 {duration % 60}초" if duration else ""
                )
                render_state["duration_label"].set_visibility(bool(duration))

                status_badge = render_state["status_badge"]
                status_badge.set_text(status)
                status_badge.props(f"color={_status_color(status)}")
                render_state["stop_button"].set_visibility(status != "completed")

                error_message = data.get("error_message") or ""
                render_state["error_label"].set_text(
                    f"❌ {error_message}" if error_message else ""
                )
                render_state["error_label"].set_visibility(bool(error_message))

                # Rebuild the small progress region only when the workflow step
                # changes.  Translation-count changes update its existing label
                # so even that region does not flash for every paragraph.
                if status_changed:
                    progress_container = render_state["progress_container"]
                    if status in ("completed", "failed"):
                        progress_container.clear()
                        progress_container.set_visibility(False)
                        render_state["progress_label"] = None
                    else:
                        progress_container.clear()
                        with progress_container:
                            render_state["progress_label"] = _render_steps(status, data)
                        progress_container.set_visibility(True)
                elif ko_updated:
                    progress_label = render_state["progress_label"]
                    if progress_label and status == "translating":
                        done, total = _translation_progress(data)
                        progress_label.set_text(
                            f"번역 ({done}/{total})" if total else "번역"
                        )

                if summary_changed:
                    _render_summary_content(render_state["summary_container"], data)
                    render_state["summary_key"] = summary_key

                if transcript_changed:
                    render_state["translation"] = _render_transcript_content(
                        render_state["transcript_container"], data
                    )
                    render_state["transcript_key"] = transcript_key
                elif ko_updated and render_state["translation"]:
                    _update_translation_controls(render_state["translation"], ko_raw)

                _update_resume_button_visibility(data, render_state["resume_button"])
                _last_status[0] = status
                _last_ko[0] = ko_raw

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

                # Polling must update existing controls in place.  In
                # particular, a status transition (summary/translation
                # completion) must not call content_area.clear().
                await _update_detail_in_place(data)

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


def _detail_summary_key(data: dict) -> tuple[str, str]:
    return (
        data.get("summary_short") or "",
        data.get("summary_structured") or "",
    )


def _detail_transcript_key(data: dict) -> tuple[str, str, int]:
    return (
        data.get("transcript") or "",
        data.get("transcript_lang") or "",
        data.get("duration_seconds") or 0,
    )


def _update_resume_button_visibility(data: dict, button) -> None:
    """Show the resume action only when a non-Korean translation is partial."""
    if button is None:
        return
    is_korean_source = str(data.get("transcript_lang") or "").lower().startswith("ko")
    done, total = _translation_progress(data)
    visible = (
        not is_korean_source
        and bool(total)
        and done < total
        and data.get("status") in ("completed", "failed", "translating")
    )
    button.set_visibility(visible)


def _render_summary_content(container, data: dict) -> bool:
    """Render only the summary region, leaving the detail page DOM intact."""
    container.clear()
    has_summary = bool(data.get("summary_short") or data.get("summary_structured"))
    container.set_visibility(has_summary)
    if not has_summary:
        return False

    with container:
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
    return True


def _render_transcript_content(container, data: dict):
    """Render only the transcript region and return its translation controls."""
    container.clear()
    if not data.get("transcript"):
        container.set_visibility(False)
        return None

    container.set_visibility(True)
    with container:
        return _render_bilingual_transcript(
            data.get("transcript"),
            data.get("transcript_ko"),
            data.get("duration_seconds") or 0,
            data.get("transcript_lang"),
        )


def _copy(text: str):
    ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(text)})')
    ui.notify("복사됨", type="positive", position="bottom", timeout=1000)


def _history_structure_key(layout: str, query: str, items: list[dict]) -> str:
    """Return the parts of a history render that require new DOM nodes.

    Status, summary, and progressive translation are intentionally excluded.
    Those values are updated through the controls returned by the card renderers.
    A change in thumbnail/overlay presence is structural because it changes the
    card layout, so it is allowed to trigger one anchored structural render.
    """
    return json.dumps(
        {
            "layout": layout,
            "query": query,
            "items": [_history_item_structure_key(layout, item) for item in items],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _history_item_structure_key(layout: str, item: dict) -> str:
    """Return the card-shape key for one history item."""
    return json.dumps(
        {
            "id": str(item.get("id")),
            "thumbnail": bool(item.get("thumbnail")),
            "like_count": bool(item.get("like_count")) if layout == "grid" else False,
            "view_count": bool(item.get("view_count")) if layout == "grid" else False,
            "duration": bool(item.get("duration_seconds")) if layout == "grid" else False,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _history_content_key(items: list[dict]) -> str:
    """Return card values that can be changed without changing card structure."""
    return json.dumps(
        [
            {
                "id": str(item.get("id")),
                "status": item.get("status", "pending"),
                "summary": item.get("summary_short") or "",
                "title": item.get("title") or item.get("url") or "",
                "channel": item.get("channel") or "",
                "duration": item.get("duration_seconds") or 0,
                "translation_progress": _translation_progress(item),
            }
            for item in items
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _history_step_label(item: dict) -> str:
    status = item.get("status", "pending")
    if status in ("completed", "failed"):
        return ""
    labels = {
        "pending": "대기중",
        "waiting": "⏳ 대기중",
        "fetching": "📥 추출중",
        "summarizing": "📝 요약중",
        "translating": "🌐 번역중",
    }
    label = labels.get(status, "")
    if status == "translating":
        done, total = _translation_progress(item)
        if total:
            label = f"🌐 번역 {done}/{total}"
    return label


def _status_color(status: str) -> str:
    return {
        "waiting": "grey",
        "pending": "grey",
        "fetching": "blue",
        "summarizing": "orange",
        "translating": "purple",
        "completed": "green",
        "failed": "red",
    }.get(status, "grey")


def _update_history_card(controls: dict, item: dict) -> None:
    """Update a history card without replacing its DOM node."""
    status = item.get("status", "pending")
    badge = controls.get("status_badge")
    if badge:
        badge.set_text(status)
        badge.props(f"color={_status_color(status)}")

    workflow_label = controls.get("workflow_label")
    if workflow_label:
        step_label = _history_step_label(item)
        workflow_label.set_text(step_label)
        workflow_label.set_visibility(bool(step_label))

    summary_label = controls.get("summary_label")
    if summary_label:
        summary = item.get("summary_short") or ""
        summary_label.set_text(summary)
        summary_label.set_visibility(bool(summary))

    title_label = controls.get("title_label")
    if title_label:
        title_label.set_text(item.get("title") or item.get("url") or "")

    channel_label = controls.get("channel_label")
    if channel_label:
        channel = item.get("channel") or ""
        channel_label.set_text(channel)
        channel_label.set_visibility(bool(channel))

    duration_label = controls.get("duration_label")
    if duration_label:
        duration = item.get("duration_seconds") or 0
        duration_label.set_text(f"{duration // 60}분" if duration else "")
        duration_label.set_visibility(bool(duration))


def _populate_list_card(card, item: dict):
    with card:
        with ui.row().classes("w-full gap-4 items-center"):
            if item.get("thumbnail"):
                ui.image(item["thumbnail"]).classes("w-32 h-20 object-cover rounded")
            with ui.column().classes("flex-grow"):
                title_label = ui.label(item.get("title") or item.get("url") or "").classes(
                    "font-bold text-base"
                )
                with ui.row().classes("gap-2 text-sm opacity-70"):
                    channel_label = ui.label(item.get("channel") or "")
                    channel_label.set_visibility(bool(item.get("channel")))
                    duration = item.get("duration_seconds") or 0
                    duration_label = ui.label(f"{duration // 60}분" if duration else "")
                    duration_label.set_visibility(bool(duration))
            status_badge = _status_badge(item.get("status", "pending"))
    return {
        "root": card,
        "status_badge": status_badge,
        "title_label": title_label,
        "channel_label": channel_label,
        "duration_label": duration_label,
    }


def _render_card(item: dict):
    card = ui.card()
    card.classes("w-full cursor-pointer")
    card.props(f"data-analysis-id={json.dumps(str(item.get('id')))}")
    card.on("click", lambda i=item: ui.navigate.to(f"/detail/{i['id']}"))
    return _populate_list_card(card, item)


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _populate_grid_card(card, item: dict):
    with card:
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
            title_label = ui.link(item.get("title") or item.get("url") or "", target=f"/detail/{item['id']}").classes("font-bold text-sm line-clamp-2 no-underline text-inherit")
            with ui.row().classes("gap-2 text-xs opacity-70"):
                channel_label = ui.label(item.get("channel") or "")
                channel_label.set_visibility(bool(item.get("channel")))
            with ui.row().classes("items-center gap-2"):
                if item.get("url"):
                    ui.button("▶ YouTube", on_click=lambda e, u=item["url"]: ui.run_javascript(f'window.open("{u}", "_blank")') or e.stop_propagation()).props("outline color=red size=sm").classes("py-0")
                status_badge = _status_badge(item.get("status", "pending"))
                # Show workflow step when in progress
                workflow_label = ui.label(_history_step_label(item)).classes("text-xs opacity-70")
                workflow_label.set_visibility(bool(_history_step_label(item)))
            # Summary preview (4 lines max with tooltip)
            summary = item.get("summary_short") or ""
            with ui.label(summary).classes("text-sm opacity-70 line-clamp-4 mt-1") as summary_label:
                summary_label.set_visibility(bool(summary))
                if summary:
                    ui.tooltip(summary).props('max-width="400px"').classes("text-sm")
    return {
        "root": card,
        "status_badge": status_badge,
        "workflow_label": workflow_label,
        "summary_label": summary_label,
        "title_label": title_label,
        "channel_label": channel_label,
    }


def _render_grid_card(item: dict):
    card = ui.card()
    card.classes("w-full relative group")
    card.props(f"data-analysis-id={json.dumps(str(item.get('id')))}")
    return _populate_grid_card(card, item)


def _rebuild_history_card(controls: dict, item: dict, layout: str) -> None:
    """Rebuild one card in place when its optional shape changes."""
    card = controls["root"]
    card.clear()
    updated = (
        _populate_grid_card(card, item)
        if layout == "grid"
        else _populate_list_card(card, item)
    )
    controls.clear()
    controls.update(updated)


def _status_badge(status: str):
    return ui.badge(status, color=_status_color(status))


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
    with ui.button(icon="settings").props(
        'flat round aria-label="LLM Settings" title="LLM Settings"'
    ):
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
                with ui.column().classes("github-menu-only w-full gap-1"):
                    ui.separator()
                    _render_github_link("menu")


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
