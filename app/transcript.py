"""Shared transcript paragraph segmentation."""

import re


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_SENTENCE_END_RE = re.compile(r"[.!?。！？]+$")


def merge_transcript_entries(
    entries: list[dict],
    *,
    min_seconds: int = 20,
    target_seconds: int = 30,
    max_seconds: int = 45,
    max_chars: int = 700,
) -> list[dict]:
    """Merge caption fragments into readable, timestamped paragraphs.

    Prefer a sentence boundary after ``min_seconds`` once the paragraph reaches
    ``target_seconds``. If captions have no usable punctuation, enforce a hard
    time or character limit so an entire video cannot become one paragraph.
    """
    units = _sentence_units(entries)
    if not units:
        return []

    paragraphs: list[dict] = []
    current: list[dict] = []

    def flush(count: int | None = None) -> None:
        nonlocal current
        selected = current if count is None else current[:count]
        if selected:
            paragraphs.append({
                "start": selected[0]["start"],
                "text": " ".join(unit["text"] for unit in selected).strip(),
            })
        current = [] if count is None else current[count:]

    for unit in units:
        current.append(unit)
        paragraph_start = current[0]["start"]
        elapsed = unit["start"] - paragraph_start
        char_count = sum(len(item["text"]) + 1 for item in current)

        candidates = [
            index + 1
            for index, item in enumerate(current)
            if item["sentence_end"] and item["start"] - paragraph_start >= min_seconds
        ]
        if elapsed >= target_seconds and candidates:
            flush(candidates[-1])
        elif elapsed >= max_seconds or char_count >= max_chars:
            flush(candidates[-1] if candidates else None)

    flush()
    return paragraphs


def _sentence_units(entries: list[dict]) -> list[dict]:
    units: list[dict] = []
    for entry in entries:
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        start = float(entry.get("start") or 0)
        for part in _SENTENCE_SPLIT_RE.split(text):
            part = part.strip()
            if part:
                units.append({
                    "start": start,
                    "text": part,
                    "sentence_end": bool(_SENTENCE_END_RE.search(part)),
                })
    return units
