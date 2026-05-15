"""Entry point: NiceGUI + FastAPI integrated app."""

import os
from dotenv import load_dotenv
from nicegui import app, ui
from app.db import init_db
from app.routers import router
from app.ui import setup_ui

load_dotenv()

app.include_router(router)


@app.on_startup
async def startup():
    await init_db()


setup_ui()

ui.run(
    host="0.0.0.0",
    port=int(os.getenv("APP_PORT", "8080")),
    title="YouTube Script Viewer",
    favicon="🎬",
    dark=True,
    reload=os.getenv("APP_ENV") != "docker",
    show=False,
)
