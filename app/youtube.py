import re
import json
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi


class TranscriptFetchError(Exception):
    """Raised when transcript fetching fails with a user-actionable reason."""


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
        "video_lang": _detect_video_language(info),
    }


def get_transcript_with_language(video_id: str, preferred_lang: str = "en") -> tuple[str, str | None]:
    """Returns (transcript_json, language_code)."""
    preferred_lang = _normalize_preferred_language(preferred_lang)
    ytt = YouTubeTranscriptApi()
    try:
        result = ytt.fetch(video_id, languages=[preferred_lang])
        entries = [{"start": s.start, "text": s.text} for s in result.snippets]
        return json.dumps(entries, ensure_ascii=False), preferred_lang
    except Exception as transcript_api_error:
        try:
            return get_transcript_with_language_ytdlp(video_id, preferred_lang)
        except TranscriptFetchError as ytdlp_error:
            raise ytdlp_error from transcript_api_error
        except Exception as ytdlp_error:
            raise TranscriptFetchError(_describe_transcript_error(ytdlp_error)) from transcript_api_error


def get_transcript_with_language_ytdlp(video_id: str, preferred_lang: str = "en") -> tuple[str, str | None]:
    """Fetch auto captions via yt-dlp as a fallback when youtube-transcript-api is blocked."""
    preferred_lang = _normalize_preferred_language(preferred_lang)
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            captions = info.get("subtitles") or {}
            auto_captions = info.get("automatic_captions") or {}

            caption = _find_json3_caption(captions.get(preferred_lang, [])) or _find_json3_caption(
                auto_captions.get(preferred_lang, [])
            )
            if not caption:
                raise TranscriptFetchError(f"{preferred_lang} transcript is unavailable for this video")
            entries = _fetch_json3_caption(ydl, caption["url"])
            if entries:
                return json.dumps(entries, ensure_ascii=False), preferred_lang
    except TranscriptFetchError:
        raise
    except Exception as e:
        raise TranscriptFetchError(_describe_transcript_error(e)) from e
    raise TranscriptFetchError("Transcript unavailable or could not be fetched")


def _find_json3_caption(captions: list[dict]) -> dict | None:
    for caption in captions:
        if caption.get("ext") == "json3" and caption.get("url"):
            return caption
    return None


def _fetch_json3_caption(ydl: yt_dlp.YoutubeDL, url: str) -> list[dict]:
    response = ydl.urlopen(url)
    raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    return _parse_json3_events(payload)


def _parse_json3_events(payload: dict) -> list[dict]:
    entries = []
    for event in payload.get("events") or []:
        segs = event.get("segs") or []
        text = "".join(seg.get("utf8", "") for seg in segs).replace("\n", " ").strip()
        if text:
            entries.append({"start": (event.get("tStartMs") or 0) / 1000, "text": text})
    return entries


def _detect_video_language(info: dict) -> str:
    for key in ("language", "original_language", "spoken_language"):
        value = info.get(key)
        if value:
            value = str(value).lower()
            if value.startswith("ko") or "korean" in value:
                return "ko"
            if value.startswith("en") or "english" in value:
                return "en"
    return "en"


def _normalize_preferred_language(language: str | None) -> str:
    if language and str(language).lower().startswith("ko"):
        return "ko"
    return "en"


def _describe_transcript_error(error: Exception) -> str:
    error_name = type(error).__name__
    text = str(error)
    combined = f"{error_name}: {text}"
    if "IpBlocked" in combined or "RequestBlocked" in combined:
        return "YouTube is blocking transcript requests from this IP. Wait before retrying or use cookies/proxy."
    if "429" in combined or "Too Many Requests" in combined:
        return "YouTube rate limited transcript downloads. Wait before retrying."
    if "NoTranscriptFound" in combined:
        return "Requested transcript is unavailable for this video."
    return "Transcript unavailable or could not be fetched"


def get_transcript(video_id: str) -> str:
    """Returns JSON string: [{"start": float, "text": str}, ...]"""
    transcript, _language = get_transcript_with_language(video_id)
    return transcript
