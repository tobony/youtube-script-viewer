# AGENTS.md

## Project map

- `app/db.py`: SQLite schema, backups, revisions, and persistence rules.
- `app/pipeline.py`: asynchronous fetch, summary, and translation workflow.
- `app/llm.py` and `app/codex_provider.py`: provider configuration and calls.
- `app/transcript.py`: the shared transcript segmentation implementation.
- `app/ui.py`: NiceGUI pages and incremental UI updates.
- `tests/`: isolated automated tests. Tests must never use the production DB.

Before changing database, pipeline, transcript, LLM-provider, or UI behavior,
read `docs/engineering-invariants.md`. Its rules are required, not suggestions.

## Python execution

Do not run `python`, `pip`, or `pytest` directly. Use the current repository's
`.venv` through `uv`.

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv'
uv run python -m pytest -q
```

- Run Python: `uv run python ...`
- Add dependencies: `uv add <package>`
- Add development dependencies: `uv add --dev <package>`
- Sync dependencies: `uv sync`

Prefer `uv run python -m pytest` over the generated `pytest.exe` wrapper on
Windows because the wrapper can retain a stale interpreter path.

## User-data safety

- Never run tests against `data/youtube_scripts.db`.
- Never overwrite completed transcript, summary, or translation content in place.
- Regeneration and translation resume must create an inactive revision.
- Activate a revision only after it completes successfully; failures must leave
  the previous active result visible.
- Delete analyses with soft deletion. Do not physically delete user records.
- Use additive migrations. Back up the live SQLite DB and verify integrity before
  changing its schema or data representation.
- Never replace a missing initialized user DB with sample data.
- Do not edit, remove, copy over, or seed the production DB during tests.

## UI behavior

- Polling and progressive translation must update existing controls in place.
- Do not clear and rebuild a long detail page for a progress-only change.
- Preserve stable transcript DOM nodes and the user's scroll position.
- Keep transcript segmentation centralized in `app/transcript.py`.

For substantial user-facing changes, use the in-app browser when available:

1. Reuse a healthy server, or start `uv run main.py` on `http://localhost:8080`.
2. Verify both the listening port and an HTTP 200 response.
3. Reload and inspect the affected page after meaningful changes.
4. Check relevant success, progress, empty, and error states plus browser console errors.
5. Keep the final verified app tab open for the user.

If browser control is unavailable, continue with automated tests and report that
visual verification was not performed.

## LLM and transcript conventions

- Provider display order is OpenAI API, Azure OpenAI, OpenRouter, Gemini, Codex, Kiro.
- Every provider has independent summary and translation models.
- Azure configuration includes API key, endpoint, region, and deployment/model.
- Snapshot provider and task-model settings when a job starts.
- Do not translate Korean source transcripts; show `- BLANK -` in the translation pane.

## Definition of done

- Add or update regression tests for changed behavior.
- Run focused tests, then the complete test suite.
- Confirm tests use an isolated DB path and the production DB row count and
  integrity remain unchanged when DB-related work is involved.
- For UI changes, verify the rendered behavior in the in-app browser.
- Review the diff for unrelated changes and preserve the user's dirty worktree.
- Never use broad staging such as `git add .`; do not commit `.env`, databases,
  backups, runtime logs, or temporary screenshots.

For the repeatable safety workflow, use the repository skill
`$safe-youtube-viewer-maintenance`.
