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

## UI development with the in-app browser

For substantial user-facing features or improvements, use the in-app browser as
part of the implementation loop when the Browser capability is available.

A change is user-facing when it affects pages, components, layout, styling,
navigation, interactions, loading/error states, or backend behavior that is
directly visible in the UI. Pure database, test, documentation, or internal
refactoring work does not require opening the browser unless the user asks.

At the beginning of a user-facing task:

1. Reuse an existing healthy development server when possible; do not start a
   duplicate server on the same port.
2. Otherwise start the app with `uv run main.py` on `http://localhost:8080`.
3. Verify both the listening port and a successful HTTP response before opening
   the browser.
4. Open or reuse an in-app browser tab for `http://localhost:8080` and make it
   visible so the user can follow the implementation.

After each meaningful UI change, reload the local page and inspect the rendered
result. Check the relevant interaction and visible success, loading, empty, and
error states when applicable. Keep the final working app tab open for the user.

If the Browser capability is unavailable, continue with code and automated
tests, and state that visual browser verification could not be performed.
