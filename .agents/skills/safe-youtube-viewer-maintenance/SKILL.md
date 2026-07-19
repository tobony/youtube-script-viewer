---
name: safe-youtube-viewer-maintenance
description: Safely implement and verify changes to the YouTube Script Viewer while preserving user DB content, immutable analysis revisions, isolated tests, progressive UI scroll stability, transcript segmentation, and per-provider summary/translation settings. Use for changes to app/db.py, app/pipeline.py, app/routers.py, app/models.py, app/ui.py, app/transcript.py, app/llm.py, provider integrations, database migrations, regeneration, translation resume, or release verification.
---

# Safe YouTube Viewer Maintenance

## Prepare

1. Read `AGENTS.md` and `docs/engineering-invariants.md` completely.
2. Inspect the working tree and preserve unrelated user changes.
3. Classify the change as data, pipeline, transcript, LLM provider, UI, or a combination.
4. Treat `data/youtube_scripts.db`, its backups, `.env`, and runtime credentials as user data.

## Protect data

For DB, persistence, regeneration, or migration work:

1. Confirm every test uses a dedicated test `DB_PATH`.
2. Record the live DB row count and integrity with read-only queries when the task
   could affect persistence.
3. Create and integrity-check a SQLite online backup before an authorized live migration.
4. Keep migrations additive and completed content immutable.
5. Create inactive revisions for regeneration or translation resume and activate
   only after success.
6. Keep deletion soft and preserve previous revisions.

Do not mutate the production DB merely to test a code path. Use a temporary DB and
separate port for realistic browser testing, then remove only those temporary artifacts.

## Implement narrowly

- Reuse `app/transcript.py::merge_transcript_entries` for segmentation.
- Snapshot provider and summary/translation model settings at job creation.
- Preserve the configured provider order and Azure connection fields.
- Keep Korean-source translation blank.
- Update progressive UI controls in place. Do not clear the detail page for a
  progress-only change.
- Preserve stable original paragraph nodes and scroll position.

## Verify

Run focused tests first, then the complete suite using the repository `.venv`:

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv'
uv run python -m pytest -q
```

For UI changes, use the in-app browser when available. Verify the affected success,
progress, empty, and error states and check browser console errors. For progressive
translation, record `window.scrollY` and an original paragraph DOM ID before and
after a translation update; both must remain stable.

For DB-related work, re-check the production DB row count and
`PRAGMA integrity_check`. Any unexpected difference is a failed verification; stop
and investigate rather than normalizing or deleting data.

## Finish

Review the diff for unrelated files, temporary artifacts, secrets, databases,
backups, logs, and screenshots. Report tests, browser checks, DB checks, backup
locations, and any limitation. Do not stage or commit unless the user requests it.
