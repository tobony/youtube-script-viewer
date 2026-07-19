"""Global pytest safety barrier for the user's production database."""

import os
from pathlib import Path

import pytest


PRODUCTION_DB_PATH = Path("data/youtube_scripts.db").resolve()
SESSION_TEST_DB_PATH = Path("data/test-suite.db")

# Loaded before test-module collection so app.db cannot capture the production
# default merely because tests were collected in a different order.
os.environ["DB_PATH"] = str(SESSION_TEST_DB_PATH)
os.environ["DB_BACKUP_DIR"] = "data/test-backups"


def _assert_not_production_db() -> None:
    import app.db as db_module

    effective_path = Path(db_module.DB_PATH).resolve()
    assert effective_path != PRODUCTION_DB_PATH, (
        "Tests must never use data/youtube_scripts.db. "
        f"Effective DB_PATH was {effective_path}."
    )


@pytest.fixture(autouse=True)
def protect_production_db():
    _assert_not_production_db()
    yield
    _assert_not_production_db()


def pytest_sessionfinish(session, exitstatus):
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{SESSION_TEST_DB_PATH}{suffix}").unlink(missing_ok=True)
