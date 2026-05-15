"""Entry point: NiceGUI + FastAPI integrated app."""

import os
from dotenv import load_dotenv
from nicegui import ui
from app.main import app
from app.ui import setup_ui

load_dotenv()


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
