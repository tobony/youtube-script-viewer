from app.transcript import merge_transcript_entries


def test_splits_on_sentence_boundary_inside_caption_fragment():
    entries = [
        {"start": 0, "text": "Opening without a boundary"},
        {"start": 12, "text": "continues here. A new sentence starts"},
        {"start": 23, "text": "and keeps going."},
        {"start": 32, "text": "Next topic begins here"},
    ]

    paragraphs = merge_transcript_entries(entries)

    assert len(paragraphs) == 2
    assert paragraphs[0]["text"].endswith("and keeps going.")
    assert paragraphs[1] == {"start": 32.0, "text": "Next topic begins here"}


def test_forces_split_when_punctuation_is_missing():
    entries = [
        {"start": 0, "text": "zero"},
        {"start": 15, "text": "fifteen"},
        {"start": 30, "text": "thirty"},
        {"start": 46, "text": "forty six"},
        {"start": 60, "text": "sixty"},
    ]

    paragraphs = merge_transcript_entries(entries)

    assert len(paragraphs) == 2
    assert paragraphs[0]["text"] == "zero fifteen thirty forty six"
    assert paragraphs[1] == {"start": 60.0, "text": "sixty"}


def test_forces_split_at_character_limit():
    entries = [
        {"start": 0, "text": "a" * 400},
        {"start": 5, "text": "b" * 400},
        {"start": 10, "text": "tail"},
    ]

    paragraphs = merge_transcript_entries(entries)

    assert len(paragraphs) == 2
    assert len(paragraphs[0]["text"]) == 801
    assert paragraphs[1] == {"start": 10.0, "text": "tail"}
