import aiosqlite
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = "data/youtube_scripts.db"
DEFAULT_SAMPLE_DB_PATH = "sample_data/youtube_scripts.db"

DB_PATH = os.getenv("DB_PATH", DEFAULT_DB_PATH)
SAMPLE_DB_PATH = os.getenv("SAMPLE_DB_PATH", DEFAULT_SAMPLE_DB_PATH)
BACKUP_DIR = os.getenv("DB_BACKUP_DIR", str(Path(DB_PATH).parent / "backups"))

PROTECTED_CONTENT_FIELDS = {
    "url", "title", "channel", "thumbnail", "duration_seconds", "view_count",
    "like_count", "video_lang", "llm_enabled", "transcript", "transcript_lang",
    "summary_short", "summary_structured", "transcript_ko",
}


class ImmutableAnalysisError(RuntimeError):
    """Raised when completed user content would be modified in place."""


def backup_database(reason: str = "manual") -> Path | None:
    """Create and verify a consistent SQLite backup without modifying the source."""
    source_path = Path(DB_PATH)
    if not source_path.is_file() or os.getenv("PYTEST_CURRENT_TEST"):
        return None
    backup_dir = Path(BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"{source_path.stem}-{reason}-{timestamp}.db"
    with sqlite3.connect(source_path) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            target.unlink(missing_ok=True)
            raise RuntimeError("SQLite backup integrity check failed")
    return target


def backup_database_daily() -> Path | None:
    """Keep one verified startup backup per UTC day without deleting older backups."""
    source_path = Path(DB_PATH)
    if not source_path.is_file() or os.getenv("PYTEST_CURRENT_TEST"):
        return None
    backup_dir = Path(BACKUP_DIR)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if backup_dir.is_dir() and any(backup_dir.glob(f"{source_path.stem}-daily-{day}*.db")):
        return None
    return backup_database("daily")


def _database_needs_migration() -> bool:
    path = Path(DB_PATH)
    if not path.is_file():
        return False
    with sqlite3.connect(path) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analyses'"
        ).fetchone()
        if not table:
            return False
        columns = {row[1] for row in db.execute("PRAGMA table_info(analyses)")}
    return not {"revision_number", "parent_analysis_id", "is_active", "deleted_at"}.issubset(columns)


def _guard_against_missing_user_db() -> None:
    db_path = Path(DB_PATH)
    default_path = Path(DEFAULT_DB_PATH)
    marker = db_path.with_suffix(f"{db_path.suffix}.initialized")
    if db_path.resolve() == default_path.resolve() and marker.exists() and not db_path.exists():
        raise RuntimeError(
            f"User database is missing: {db_path}. Restore it from a backup instead of creating a new database."
        )


def copy_sample_db_if_missing() -> bool:
    """Copy the bundled sample DB when the default runtime DB is absent.

    Custom DB_PATH values are intentionally left empty so tests, deployments,
    and one-off databases never receive sample records unexpectedly.
    """
    db_path = Path(DB_PATH)
    default_db_path = Path(DEFAULT_DB_PATH)
    sample_db_path = Path(SAMPLE_DB_PATH)

    if db_path.resolve() != default_db_path.resolve():
        return False
    if db_path.exists() or not sample_db_path.is_file():
        return False

    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_name(f".{db_path.name}.{uuid.uuid4().hex}.tmp")

    try:
        shutil.copy2(sample_db_path, temp_path)
        try:
            # Linking the completed temporary copy is atomic and never
            # overwrites a DB another startup process may have just created.
            os.link(temp_path, db_path)
        except FileExistsError:
            return False
    finally:
        temp_path.unlink(missing_ok=True)

    return True


async def get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    _guard_against_missing_user_db()
    copy_sample_db_if_missing()
    needs_migration = _database_needs_migration()
    if needs_migration:
        backup_database("pre-migration")
    else:
        backup_database_daily()
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            channel TEXT,
            thumbnail TEXT,
            duration_seconds INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            video_lang TEXT,
            llm_enabled INTEGER DEFAULT 1,
            transcript TEXT,
            transcript_lang TEXT,
            summary_short TEXT,
            summary_structured TEXT,
            transcript_ko TEXT,
            status TEXT DEFAULT 'pending',
            error_message TEXT,
            revision_number INTEGER DEFAULT 1,
            parent_analysis_id TEXT,
            is_active INTEGER DEFAULT 1,
            deleted_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration: add columns if missing (for existing DBs)
    existing = await db.execute("PRAGMA table_info(analyses)")
    cols = {row[1] for row in await existing.fetchall()}
    migrations = {
        "summary_short": "TEXT",
        "summary_structured": "TEXT",
        "transcript_ko": "TEXT",
        "transcript_lang": "TEXT",
        "status": "TEXT DEFAULT 'pending'",
        "error_message": "TEXT",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "like_count": "INTEGER DEFAULT 0",
        "video_lang": "TEXT",
        "llm_enabled": "INTEGER DEFAULT 1",
        "revision_number": "INTEGER DEFAULT 1",
        "parent_analysis_id": "TEXT",
        "is_active": "INTEGER DEFAULT 1",
        "deleted_at": "TEXT",
    }
    for col, typedef in migrations.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typedef}")
    await db.execute("PRAGMA user_version = 1")
    await db.commit()
    await db.close()
    db_path = Path(DB_PATH)
    if db_path.resolve() == Path(DEFAULT_DB_PATH).resolve():
        db_path.with_suffix(f"{db_path.suffix}.initialized").write_text(
            "initialized\n", encoding="utf-8"
        )


async def create_analysis(video_id: str, url: str, llm_enabled: bool = True) -> dict:
    db = await get_db()
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO analyses (id, video_id, url, llm_enabled, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (row_id, video_id, url, 1 if llm_enabled else 0, now, now),
    )
    await db.commit()
    row = await db.execute("SELECT * FROM analyses WHERE id = ?", (row_id,))
    result = await row.fetchone()
    await db.close()
    return dict(result)


async def update_analysis(analysis_id: str, **kwargs) -> Optional[dict]:
    db = await get_db()
    current_cursor = await db.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    current = await current_cursor.fetchone()
    if not current:
        await db.close()
        return None
    protected_changes = PROTECTED_CONTENT_FIELDS.intersection(kwargs)
    if current["status"] == "completed" and any(
        current[field] != kwargs[field] for field in protected_changes
    ):
        await db.close()
        raise ImmutableAnalysisError(
            "Completed analysis content is immutable; create a new revision instead."
        )
    kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [analysis_id]
    await db.execute(f"UPDATE analyses SET {sets} WHERE id = ?", vals)
    await db.commit()
    row = await db.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    result = await row.fetchone()
    await db.close()
    return dict(result) if result else None


async def get_analysis(analysis_id: str) -> Optional[dict]:
    db = await get_db()
    row = await db.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    result = await row.fetchone()
    await db.close()
    return dict(result) if result else None


async def get_by_video_id(video_id: str) -> Optional[dict]:
    db = await get_db()
    row = await db.execute(
        """SELECT * FROM analyses
           WHERE video_id = ? AND is_active = 1 AND deleted_at IS NULL
           ORDER BY revision_number DESC, created_at DESC LIMIT 1""",
        (video_id,),
    )
    result = await row.fetchone()
    await db.close()
    return dict(result) if result else None


def _escape_like(value: str) -> str:
    """Escape user input so SQLite LIKE treats %, _, and backslashes as literals."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_analyses(limit: int = 20, offset: int = 0, search: str | None = None) -> list[dict]:
    db = await get_db()
    query = "SELECT * FROM analyses WHERE is_active = 1 AND deleted_at IS NULL"
    params: list[object] = []
    search = (search or "").strip()
    if search:
        pattern = f"%{_escape_like(search)}%"
        searchable_columns = (
            "title",
            "transcript",
            "transcript_ko",
            "summary_short",
            "summary_structured",
        )
        query += " AND (" + " OR ".join(
            f"LOWER(COALESCE({column}, '')) LIKE LOWER(?) ESCAPE '\\'"
            for column in searchable_columns
        ) + ")"
        params.extend([pattern] * len(searchable_columns))
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute(query, params)
    results = await rows.fetchall()
    await db.close()
    return [dict(r) for r in results]


async def delete_analysis(analysis_id: str) -> bool:
    db = await get_db()
    row = await db.execute("SELECT video_id FROM analyses WHERE id = ?", (analysis_id,))
    existing = await row.fetchone()
    if not existing:
        await db.close()
        return False
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "UPDATE analyses SET deleted_at = ?, is_active = 0, updated_at = ? WHERE video_id = ?",
        (now, now, existing["video_id"]),
    )
    await db.commit()
    await db.close()
    return cursor.rowcount > 0


async def create_analysis_revision(
    source_id: str,
    *,
    mode: str,
    llm_enabled: bool | None = None,
) -> Optional[dict]:
    """Create an inactive working revision while preserving the source row."""
    if mode not in {"full", "translation"}:
        raise ValueError(f"Unsupported revision mode: {mode}")
    db = await get_db()
    # Serialize revision-number allocation so rapid duplicate requests cannot
    # produce competing revisions with the same number.
    await db.execute("BEGIN IMMEDIATE")
    source_cursor = await db.execute("SELECT * FROM analyses WHERE id = ?", (source_id,))
    source = await source_cursor.fetchone()
    if not source:
        await db.rollback()
        await db.close()
        return None
    revision_cursor = await db.execute(
        "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM analyses WHERE video_id = ?",
        (source["video_id"],),
    )
    revision_number = (await revision_cursor.fetchone())[0]
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    copy_results = mode == "translation"
    await db.execute(
        """INSERT INTO analyses (
            id, video_id, url, title, channel, thumbnail, duration_seconds,
            view_count, like_count, video_lang, llm_enabled, transcript,
            transcript_lang, summary_short, summary_structured, transcript_ko,
            status, error_message, revision_number, parent_analysis_id,
            is_active, deleted_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL, ?, ?)""",
        (
            row_id, source["video_id"], source["url"], source["title"], source["channel"],
            source["thumbnail"], source["duration_seconds"], source["view_count"],
            source["like_count"], source["video_lang"],
            source["llm_enabled"] if llm_enabled is None else (1 if llm_enabled else 0),
            source["transcript"] if copy_results else None,
            source["transcript_lang"] if copy_results else None,
            source["summary_short"] if copy_results else None,
            source["summary_structured"] if copy_results else None,
            source["transcript_ko"] if copy_results else None,
            "waiting" if copy_results else "pending",
            revision_number, source_id, now, now,
        ),
    )
    await db.commit()
    row = await db.execute("SELECT * FROM analyses WHERE id = ?", (row_id,))
    result = await row.fetchone()
    await db.close()
    return dict(result)


async def activate_analysis(analysis_id: str) -> Optional[dict]:
    """Atomically publish a completed revision without deleting older revisions."""
    db = await get_db()
    await db.execute("BEGIN IMMEDIATE")
    row = await db.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    target = await row.fetchone()
    if not target:
        await db.rollback()
        await db.close()
        return None
    if target["status"] != "completed" or target["deleted_at"] is not None:
        await db.rollback()
        await db.close()
        raise ValueError("Only completed, non-deleted revisions can be activated")
    await db.execute(
        "UPDATE analyses SET is_active = 0 WHERE video_id = ? AND deleted_at IS NULL",
        (target["video_id"],),
    )
    await db.execute("UPDATE analyses SET is_active = 1 WHERE id = ?", (analysis_id,))
    await db.commit()
    row = await db.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    result = await row.fetchone()
    await db.close()
    return dict(result)


# ---------------------------------------------------------------------------
# Agent-facing read helpers.
#
# These mirror the WHERE clause of list_analyses exactly. They never return
# transcript, translation, or structured-summary bodies, so an agent can page
# through the archive without downloading every stored document. Bodies stay
# available through get_analysis for explicit single-item requests.
# ---------------------------------------------------------------------------

SUMMARY_COLUMNS = (
    "id, video_id, url, title, channel, thumbnail, duration_seconds, "
    "view_count, like_count, video_lang, llm_enabled, transcript_lang, "
    "status, error_message, revision_number, is_active, created_at, updated_at, "
    "(transcript IS NOT NULL AND transcript != '') AS has_transcript, "
    "(summary_short IS NOT NULL AND summary_short != '') AS has_summary, "
    "LENGTH(COALESCE(transcript, '')) AS transcript_chars, "
    "LENGTH(COALESCE(transcript_ko, '')) AS translation_chars"
)

SEARCHABLE_COLUMNS = (
    "title",
    "transcript",
    "transcript_ko",
    "summary_short",
    "summary_structured",
)

SORTABLE_COLUMNS = frozenset({"created_at", "updated_at"})


def _filter_clause(search: str | None) -> tuple[str, list[object]]:
    """Build the shared active/search predicate used by list and count."""
    clause = " WHERE is_active = 1 AND deleted_at IS NULL"
    params: list[object] = []
    search = (search or "").strip()
    if search:
        pattern = f"%{_escape_like(search)}%"
        clause += " AND (" + " OR ".join(
            f"LOWER(COALESCE({column}, '')) LIKE LOWER(?) ESCAPE '\\'"
            for column in SEARCHABLE_COLUMNS
        ) + ")"
        params.extend([pattern] * len(SEARCHABLE_COLUMNS))
    return clause, params


async def count_analyses(search: str | None = None) -> int:
    """Count active, non-deleted rows using the same predicate as list_analyses."""
    where, params = _filter_clause(search)
    db = await get_db()
    cursor = await db.execute(f"SELECT COUNT(*) FROM analyses{where}", params)
    row = await cursor.fetchone()
    await db.close()
    return int(row[0]) if row else 0


async def list_analyses_summary(
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
    order_by: str = "created_at",
) -> list[dict]:
    """List active rows without any large body column."""
    if order_by not in SORTABLE_COLUMNS:
        raise ValueError(f"Unsupported order column: {order_by}")
    where, params = _filter_clause(search)
    db = await get_db()
    cursor = await db.execute(
        f"SELECT {SUMMARY_COLUMNS} FROM analyses{where} "
        f"ORDER BY {order_by} DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()
    await db.close()
    return [dict(row) for row in rows]
