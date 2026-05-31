from app.ui import _filter_analyses


def test_filter_analyses_returns_all_for_empty_query():
    items = [
        {"title": "Python Tips", "channel": "Dev Channel"},
        {"title": "Cooking Basics", "channel": "Food Channel"},
    ]

    assert _filter_analyses(items, "") == items
    assert _filter_analyses(items, "   ") == items


def test_filter_analyses_matches_visible_metadata_case_insensitively():
    items = [
        {
            "title": "Python Tips",
            "channel": "Dev Channel",
            "url": "https://youtube.com/watch?v=abc123",
            "video_id": "abc123",
            "status": "completed",
            "summary_short": "Async programming examples",
        },
        {
            "title": "Cooking Basics",
            "channel": "Food Channel",
            "url": "https://youtube.com/watch?v=def456",
            "video_id": "def456",
            "status": "failed",
            "summary_short": "Knife skills",
        },
    ]

    assert _filter_analyses(items, "python") == [items[0]]
    assert _filter_analyses(items, "FOOD") == [items[1]]
    assert _filter_analyses(items, "async") == [items[0]]
    assert _filter_analyses(items, "failed") == [items[1]]
    assert _filter_analyses(items, "missing") == []
