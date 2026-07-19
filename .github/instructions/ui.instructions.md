---
applyTo: "app/ui.py,app/transcript.py,tests/test_ui.py,tests/test_transcript.py"
---

Treat `docs/engineering-invariants.md` UI-001 through UI-005 as acceptance
criteria. Keep transcript DOM nodes stable during polling, update only changed
translation and progress controls, and preserve scroll position across unavoidable
structural renders. Use `app/transcript.py` as the only segmentation implementation.
Verify user-visible behavior in the in-app browser when available.
