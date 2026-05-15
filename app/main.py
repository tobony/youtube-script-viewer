"""Shared NiceGUI/FastAPI app setup."""

from nicegui import app

from app.db import init_db
from app.routers import router


app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_startup
async def startup():
    await init_db()
