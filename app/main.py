"""Shared NiceGUI/FastAPI app setup."""

from nicegui import app

from app.db import init_db
from app.codex_provider import close_codex_client
from app.routers import router


app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_startup
async def startup():
    await init_db()


@app.on_shutdown
async def shutdown():
    await close_codex_client()
