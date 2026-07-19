---
applyTo: "app/llm.py,app/codex_provider.py,app/pipeline.py,app/ui.py,.env.example,tests/test_llm.py,tests/test_pipeline.py"
---

Treat `docs/engineering-invariants.md` LLM-001 through LLM-005 as acceptance
criteria. Preserve provider order, independent summary/translation models, Azure
endpoint and region configuration, per-job configuration snapshots, and the
no-translation behavior for Korean source transcripts.
