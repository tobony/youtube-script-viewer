"""Shared NiceGUI/FastAPI app setup."""

from nicegui import app

from app.agent_api import router as agent_router
from app.auth import LanAuthMiddleware, enforce_startup_policy
from app.db import init_db
from app.codex_provider import close_codex_client
from app.routers import router


app.include_router(router)
# Agent surface: a separate, stable contract for scripts and agents.
app.include_router(agent_router)

# Added last so it wraps every route, including the NiceGUI websocket, and
# covers routers registered at import time.
app.add_middleware(LanAuthMiddleware)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_startup
async def startup():
    # Refuse to serve an unauthenticated app before accepting any request. Kept
    # out of import time so tests and tooling can import the app freely.
    enforce_startup_policy()
    await init_db()


@app.on_shutdown
async def shutdown():
    await close_codex_client()
