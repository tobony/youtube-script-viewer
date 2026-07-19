# Engineering invariants

These rules protect accumulated video URLs, transcripts, summaries, translations,
and the reading experience. A refactor is not allowed to weaken them.

## Data safety

- **DATA-001 — Test isolation:** tests must use a dedicated test DB and must fail
  before touching `data/youtube_scripts.db`.
- **DATA-002 — Completed content is immutable:** completed URL metadata,
  transcripts, summaries, and translations are never overwritten in place.
- **DATA-003 — Revision creation:** regeneration and translation resume create a
  new inactive revision linked to the source revision.
- **DATA-004 — Atomic publication:** a new revision becomes active only after a
  successful completion transaction.
- **DATA-005 — Failure preservation:** pending or failed revisions never hide or
  alter the previous active result.
- **DATA-006 — Soft deletion:** user deletion sets deletion metadata and keeps the
  stored record recoverable.
- **DATA-007 — Additive migrations:** schema changes add columns/tables/indexes.
  Destructive rewrites require explicit user approval, a verified backup, and a
  migration-specific recovery plan.
- **DATA-008 — Backup first:** create a consistent SQLite online backup and run
  `PRAGMA integrity_check` before migrating a live DB.
- **DATA-009 — Missing DB guard:** after the default DB has been initialized, a
  missing DB is an error to restore from; it is not replaced with sample data.
- **DATA-010 — No hidden retention:** the app never automatically deletes backups.

## UI stability

- **UI-001 — Stable DOM:** progress polling must not clear and rebuild the detail
  page or transcript grid.
- **UI-002 — Incremental translation:** update only the changed translation labels,
  copy controls, and progress text.
- **UI-003 — Reading position:** keep original paragraph nodes stable. When a full
  structural render is unavoidable, capture and restore the scroll position.
- **UI-004 — Shared segmentation:** both pipeline and UI use
  `app/transcript.py::merge_transcript_entries`; do not duplicate segmentation.
- **UI-005 — Browser verification:** user-facing work is complete only after the
  affected state is inspected in the in-app browser when available.

## LLM providers

- **LLM-001 — Stable order:** OpenAI API, Azure OpenAI, OpenRouter, Gemini, Codex,
  then Kiro.
- **LLM-002 — Task models:** each provider independently configures summary and
  translation models; the legacy/default model is only a fallback.
- **LLM-003 — Azure connection:** Azure requires endpoint and region in addition
  to credentials and deployment/model names.
- **LLM-004 — Job snapshot:** a queued job uses the provider and task models
  captured when the job starts, not mutable global settings changed later.
- **LLM-005 — Korean source:** a Korean transcript is not translated again and its
  translation column renders `- BLANK -`.

## Verification contract

Use the repository `.venv`:

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv'
uv run python -m pytest -q
```

For DB-related changes, verify all of the following:

1. The effective test `DB_PATH` is not the production DB.
2. Completed content cannot be modified in place.
3. failed regeneration leaves the prior revision active.
4. activation switches revisions atomically without deleting history.
5. deletion is soft.
6. the live DB row count and integrity are unchanged unless the user explicitly
   requested a data operation.

For progressive UI changes, verify that a translation update changes the expected
paragraph and progress text while `window.scrollY` and a previously rendered
original paragraph DOM ID remain stable.

## Architectural reasons

The application is a long-lived personal archive, not a disposable processing
cache. Generated output therefore uses immutable revisions. Progressive rendering
keeps the user's reading context stable. Tests are isolated because even a passing
test suite is unacceptable if it writes fixtures into the user's archive.
