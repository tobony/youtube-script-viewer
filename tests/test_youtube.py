import json
from unittest.mock import Mock, patch

from app.youtube import get_transcript_with_language, _detect_video_language, _parse_json3_events


def test_parse_json3_events():
    payload = {
        "events": [
            {"tStartMs": 1230, "segs": [{"utf8": "Hello"}, {"utf8": "\n"}, {"utf8": "world"}]},
            {"tStartMs": 2500},
            {"tStartMs": 3000, "segs": [{"utf8": "Next"}]},
        ]
    }

    assert _parse_json3_events(payload) == [
        {"start": 1.23, "text": "Hello world"},
        {"start": 3.0, "text": "Next"},
    ]


def test_get_transcript_falls_back_to_ytdlp_json3():
    transcript_api = Mock()
    transcript_api.fetch.side_effect = Exception("blocked")

    response = Mock()
    response.read.return_value = json.dumps(
        {"events": [{"tStartMs": 1000, "segs": [{"utf8": "Fallback caption"}]}]}
    ).encode("utf-8")

    ydl = Mock()
    ydl.extract_info.return_value = {
        "subtitles": {},
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://example.test/en.json3"}],
        },
    }
    ydl.urlopen.return_value = response
    ydl.__enter__ = Mock(return_value=ydl)
    ydl.__exit__ = Mock(return_value=False)

    with patch("app.youtube.YouTubeTranscriptApi", return_value=transcript_api), patch(
        "app.youtube.yt_dlp.YoutubeDL", return_value=ydl
    ):
        transcript, lang = get_transcript_with_language("I4zwZP80u-Y")

    assert lang == "en"
    assert json.loads(transcript) == [{"start": 1.0, "text": "Fallback caption"}]
    transcript_api.fetch.assert_called_once_with("I4zwZP80u-Y", languages=["en"])


def test_ytdlp_fallback_uses_english_only():
    transcript_api = Mock()
    transcript_api.fetch.side_effect = Exception("blocked")

    response = Mock()
    response.read.return_value = json.dumps(
        {"events": [{"tStartMs": 1000, "segs": [{"utf8": "English caption"}]}]}
    ).encode("utf-8")

    ydl = Mock()
    ydl.extract_info.return_value = {
        "subtitles": {},
        "automatic_captions": {
            "ko": [{"ext": "json3", "url": "https://example.test/ko.json3"}],
            "en": [{"ext": "json3", "url": "https://example.test/en.json3"}],
        },
    }
    ydl.urlopen.return_value = response
    ydl.__enter__ = Mock(return_value=ydl)
    ydl.__exit__ = Mock(return_value=False)

    with patch("app.youtube.YouTubeTranscriptApi", return_value=transcript_api), patch(
        "app.youtube.yt_dlp.YoutubeDL", return_value=ydl
    ):
        transcript, lang = get_transcript_with_language("I4zwZP80u-Y")

    assert lang == "en"
    assert json.loads(transcript) == [{"start": 1.0, "text": "English caption"}]
    ydl.urlopen.assert_called_once_with("https://example.test/en.json3")


def test_korean_preferred_language_uses_korean_only():
    transcript_api = Mock()
    transcript_api.fetch.side_effect = Exception("blocked")

    response = Mock()
    response.read.return_value = json.dumps(
        {"events": [{"tStartMs": 1000, "segs": [{"utf8": "한글 자막"}]}]}
    ).encode("utf-8")

    ydl = Mock()
    ydl.extract_info.return_value = {
        "subtitles": {},
        "automatic_captions": {
            "ko": [{"ext": "json3", "url": "https://example.test/ko.json3"}],
            "en": [{"ext": "json3", "url": "https://example.test/en.json3"}],
        },
    }
    ydl.urlopen.return_value = response
    ydl.__enter__ = Mock(return_value=ydl)
    ydl.__exit__ = Mock(return_value=False)

    with patch("app.youtube.YouTubeTranscriptApi", return_value=transcript_api), patch(
        "app.youtube.yt_dlp.YoutubeDL", return_value=ydl
    ):
        transcript, lang = get_transcript_with_language("korean-video", preferred_lang="ko")

    assert lang == "ko"
    assert json.loads(transcript) == [{"start": 1.0, "text": "한글 자막"}]
    transcript_api.fetch.assert_called_once_with("korean-video", languages=["ko"])
    ydl.urlopen.assert_called_once_with("https://example.test/ko.json3")


def test_detect_video_language_prefers_korean_hint():
    assert _detect_video_language({"language": "ko"}) == "ko"
    assert _detect_video_language({"original_language": "Korean"}) == "ko"
    assert _detect_video_language({"language": "en"}) == "en"
    assert _detect_video_language({}) == "en"
