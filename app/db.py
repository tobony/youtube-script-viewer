import aiosqlite
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "data/youtube_scripts.db")


async def get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
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
            transcript TEXT,
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
        "status": "TEXT DEFAULT 'pending'",
        "error_message": "TEXT",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "like_count": "INTEGER DEFAULT 0",
    }
    for col, typedef in migrations.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typedef}")
    await db.commit()
    await db.close()


async def create_analysis(video_id: str, url: str) -> dict:
    db = await get_db()
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO analyses (id, video_id, url, status, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?)",
        (row_id, video_id, url, now, now),
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
