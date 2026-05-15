# Rule: Use `uv` for Python operations

Always use `uv` instead of `pip` or `python` directly for Python-related tasks:

- **Run scripts/tests**: `uv run pytest ...` instead of `python -m pytest ...`
- **Install dependencies**: `uv pip install ...` instead of `pip install ...`
- **Run Python**: `uv run python ...` instead of `python ...`
