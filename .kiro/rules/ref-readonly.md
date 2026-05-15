# Rule: /ref/ directory is READ-ONLY reference

The `/ref/` directory contains the original source code of the YouTube content analysis platform (NexusSkill). It serves as a **read-only reference** for building the new application.

## Rules

1. **NEVER modify, delete, or move any files inside `/ref/`.**
2. **NEVER write new files into `/ref/`.**
3. Use `/ref/` only to **read and understand** the existing architecture, patterns, and logic.
4. All new code must be written **outside** of `/ref/` (in the project root or new directories).
5. When referencing patterns from `/ref/`, adapt them to the new project — do not copy files verbatim.
