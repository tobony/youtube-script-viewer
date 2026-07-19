import json

from app.ui import _translation_progress, _update_translation_controls


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
