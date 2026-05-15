import re
import json
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(url: str) -> str:
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    raise ValueError(f"Invalid YouTube URL: {url}")


def get_metadata(video_id: str) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title", ""),
        "channel": info.get("channel", "") or info.get("uploader", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration_seconds": info.get("duration", 0) or 0,
        "view_count": info.get("view_count", 0) or 0,
        "like_count": info.get("like_count", 0) or 0,
    }


def get_transcript_with_language(video_id: str) -> tuple[str, str | None]:
    """Returns (transcript_json, language_code)."""
    ytt = YouTubeTranscriptApi()
    for lang in ["ko", "en"]:
        try:
            result = ytt.fetch(video_id, languages=[lang])
            entries = [{"start": s.start, "text": s.text} for s in result.snippets]
            return json.dumps(entries, ensure_ascii=False), lang
        except Exception:
            continue
    try:
        result = ytt.fetch(video_id)
        entries = [{"start": s.start, "text": s.text} for s in result.snippets]
        language_code = getattr(result, "language_code", None)
        return json.dumps(entries, ensure_ascii=False), language_code
    except Exception:
        return "", None


def get_transcript(video_id: str) -> str:
    """Returns JSON string: [{"start": float, "text": str}, ...]"""
    transcript, _language = get_transcript_with_language(video_id)
    return transcript
