---
applyTo: "app/db.py,app/models.py,app/pipeline.py,app/routers.py,tests/**/*.py"
---

Treat `docs/engineering-invariants.md` DATA-001 through DATA-010 as acceptance
criteria. Do not replace immutable revisions with in-place updates or physical
deletion. Any migration must be additive, backup-first, integrity-checked, and
covered by preservation tests. Tests must set and verify a dedicated DB path;
they must never open `data/youtube_scripts.db`.
