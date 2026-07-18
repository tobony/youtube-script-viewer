import aiosqlite
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = "data/youtube_scripts.db"
DEFAULT_SAMPLE_DB_PATH = "sample_data/youtube_scripts.db"

DB_PATH = os.getenv("DB_PATH", DEFAULT_DB_PATH)
SAMPLE_DB_PATH = os.getenv("SAMPLE_DB_PATH", DEFAULT_SAMPLE_DB_PATH)


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
    copy_sample_db_if_missing()
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
    }
    for col, typedef in migrations.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typedef}")
    await db.commit()
    await db.close()


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
        "SELECT * FROM analyses WHERE video_id = ? ORDER BY created_at DESC LIMIT 1",
        (video_id,),
    )
    result = await row.fetchone()
    await db.close()
    return dict(result) if result else None


async def list_analyses(limit: int = 20, offset: int = 0) -> list[dict]:
    db = await get_db()
    rows = await db.execute(
        "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    results = await rows.fetchall()
    await db.close()
    return [dict(r) for r in results]


async def delete_analysis(analysis_id: str) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    await db.commit()
    await db.close()
    return cursor.rowcount > 0
