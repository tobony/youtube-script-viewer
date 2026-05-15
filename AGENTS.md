# AGENTS.md

## Python execution

Do not run `python`, `pip`, or `pytest` directly in this project.

Always run Python-related commands through `uv`.

- Run Python: `uv run python ...`
- Run tests: `uv run pytest`
- Add dependencies: `uv add <package>`
- Add development dependencies: `uv add --dev <package>`
- Sync dependencies: `uv sync`

Examples:

- Do not use: `python main.py`
- Use: `uv run python main.py`

- Do not use: `pip install requests`
- Use: `uv add requests`

- Do not use: `pytest`
- Use: `uv run pytest`
