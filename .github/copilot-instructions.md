# YouTube Script Viewer repository instructions

Read `AGENTS.md` and `docs/engineering-invariants.md` before modifying database,
pipeline, transcript, LLM-provider, or UI behavior.

- Use the repository `.venv` through `uv`. Run tests with
  `$env:UV_PROJECT_ENVIRONMENT='.venv'; uv run python -m pytest -q` on Windows.
- Never run tests against `data/youtube_scripts.db` or seed it with fixtures.
- Completed transcript, summary, translation, and metadata are immutable.
- Regeneration and translation resume create inactive revisions; publish only
  after success and preserve the previous active revision on failure.
- Delete user analyses softly and use additive, backup-first migrations.
- Progressive translation must update existing labels and progress controls in
  place. Do not clear and rebuild the detail page or disturb scroll position.
- Keep transcript segmentation in `app/transcript.py`.
- Keep provider order and independent summary/translation model settings stable.
- Do not translate Korean source transcripts; render `- BLANK -` instead.
- Add regression tests, run focused tests and the full suite, and use the in-app
  browser for user-visible changes when available.
- Preserve unrelated working-tree changes. Never stage `.env`, DBs, backups,
  runtime logs, screenshots, or all files indiscriminately.

Use `.agents/skills/safe-youtube-viewer-maintenance/SKILL.md` for the repeatable
safety and verification workflow.
