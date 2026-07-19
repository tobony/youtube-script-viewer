# Contributing

Thanks for taking the time to improve YouTube Script Viewer.

This project is a local, single-user app that stores accumulated transcripts,
summaries, translations, and settings in SQLite. Changes must protect that user
data first.

## Development setup

1. Install Python 3.12 or later and uv.
2. Copy `.env.example` to `.env` and configure only the providers you need.
3. Install dependencies:

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv'
uv sync --extra dev
```

4. Run the app locally:

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv'
uv run main.py
```

The local development server uses <http://localhost:8080> by default.

## Tests

Run tests through the repository virtual environment:

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv'
uv run python -m pytest -q
```

Tests must never use `data/youtube_scripts.db`. The test suite sets a dedicated
`DB_PATH`; keep that protection in place when adding tests.

## Data safety rules

- Do not overwrite completed transcript, summary, translation, or metadata in
  place.
- Regeneration and translation resume must create inactive revisions and publish
  them only after success.
- Preserve the previous active revision on failure.
- Delete analyses softly instead of physically deleting user records.
- Keep migrations additive and backup-first.
- Keep transcript segmentation centralized in `app/transcript.py`.
- For UI progress updates, update existing controls in place and preserve scroll
  position.

Read `AGENTS.md` and `docs/engineering-invariants.md` before changing database,
pipeline, transcript, LLM-provider, or UI behavior.

## Pull requests

- Keep changes focused and explain the user-visible behavior change.
- Add or update regression tests for changed behavior.
- Do not commit `.env`, runtime logs, local databases, backups, temporary
  screenshots, or personal editor settings.
- If a change touches generated sample data, use synthetic content unless the
  redistribution rights are explicit.