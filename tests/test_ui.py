import json

import pytest

from app.ui import (
    GITHUB_REPO_URL,
    _detail_summary_key,
    _detail_transcript_key,
    _github_link_classes,
    _history_structure_key,
    _translation_progress,
    _update_resume_button_visibility,
    _update_translation_controls,
)


class FakeElement:
    def __init__(self):
        self.text = None
        self.visible = None
        self.added_classes = None
        self.removed_classes = None

    def set_text(self, text):
        self.text = text

    def set_visibility(self, visible):
        self.visible = visible

    def classes(self, add=None, *, remove=None):
        self.added_classes = add
        self.removed_classes = remove


def test_github_link_uses_repository_url_and_responsive_placements():
    assert GITHUB_REPO_URL == "https://github.com/tobony/youtube-script-viewer"
    assert "github-header-only" in _github_link_classes("header")
    assert "github-menu-only" not in _github_link_classes("menu")
    with pytest.raises(ValueError):
        _github_link_classes("footer")


def test_korean_source_has_no_translation_progress():
    item = {
        "transcript_lang": "ko",
        "transcript": json.dumps([{"start": 0, "text": "한국어 원문"}]),
        "transcript_ko": None,
    }

    assert _translation_progress(item) == (0, 0)


def test_non_korean_source_reports_translation_progress():
    item = {
        "transcript_lang": "en",
        "transcript": json.dumps([{"start": 0, "text": "English source."}]),
        "transcript_ko": json.dumps([{"start": 0, "text": "한국어 번역."}]),
    }

    assert _translation_progress(item) == (1, 1)


def test_history_structure_key_ignores_progress_only_changes():
    base = {
        "id": "analysis-1",
        "status": "translating",
        "summary_short": "요약",
        "transcript_ko": "[첫 문단]",
        "thumbnail": "https://example.test/thumb.jpg",
    }
    progressed = {
        **base,
        "status": "completed",
        "summary_short": "요약이 갱신됨",
        "transcript_ko": "[첫 문단, 둘째 문단]",
    }

    assert _history_structure_key("grid", "", [base]) == _history_structure_key(
        "grid", "", [progressed]
    )


def test_history_structure_key_changes_when_card_shape_changes():
    without_thumbnail = {"id": "analysis-1", "status": "fetching"}
    with_thumbnail = {**without_thumbnail, "thumbnail": "https://example.test/thumb.jpg"}

    assert _history_structure_key("grid", "", [without_thumbnail]) != _history_structure_key(
        "grid", "", [with_thumbnail]
    )


def test_translation_controls_update_existing_nodes_in_place():
    copy_all_button = FakeElement()
    paragraphs = []
    for _ in range(3):
        paragraphs.append({
            "state": {"text": ""},
            "label": FakeElement(),
            "copy_button": FakeElement(),
        })
    controls = {
        "paragraphs": paragraphs,
        "copy_all_state": {"text": ""},
        "copy_all_button": copy_all_button,
    }

    _update_translation_controls(
        controls,
        json.dumps([
            {"start": 0, "text": "첫 번째 번역"},
            {"start": 30, "text": "두 번째 번역"},
        ], ensure_ascii=False),
    )

    assert controls["copy_all_state"]["text"] == "첫 번째 번역\n\n두 번째 번역"
    assert copy_all_button.visible is True
    assert paragraphs[0]["label"].text == "첫 번째 번역"
    assert paragraphs[0]["copy_button"].visible is True
    assert paragraphs[1]["label"].text == "두 번째 번역"
    assert paragraphs[2]["label"].text == "번역 대기중"
    assert paragraphs[2]["copy_button"].visible is False


def test_detail_progress_keys_exclude_incremental_translation_data():
    base = {
        "summary_short": "초기 요약",
        "summary_structured": "",
        "transcript": json.dumps([{"start": 0, "text": "English"}]),
        "transcript_lang": "en",
        "duration_seconds": 60,
        "transcript_ko": json.dumps([{"start": 0, "text": "첫 번역"}], ensure_ascii=False),
    }
    progressed = {
        **base,
        "transcript_ko": json.dumps(
            [{"start": 0, "text": "갱신된 번역"}], ensure_ascii=False
        ),
    }

    assert _detail_summary_key(base) == _detail_summary_key(progressed)
    assert _detail_transcript_key(base) == _detail_transcript_key(progressed)


def test_resume_translation_action_is_visible_only_for_partial_non_korean_data():
    button = FakeElement()
    partial = {
        "status": "completed",
        "transcript_lang": "en",
        "transcript": json.dumps([{"start": 0, "text": "English"}]),
        "transcript_ko": None,
    }
    _update_resume_button_visibility(partial, button)
    assert button.visible is True

    complete = {
        **partial,
        "transcript_ko": json.dumps([{"start": 0, "text": "번역"}], ensure_ascii=False),
    }
    _update_resume_button_visibility(complete, button)
    assert button.visible is False

    korean_source = {**partial, "transcript_lang": "ko"}
    _update_resume_button_visibility(korean_source, button)
    assert button.visible is False
