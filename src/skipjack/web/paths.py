"""Filesystem locations used by the web app and the static site generator."""

from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
PROJECT_ROOT = WEB_DIR.parents[2]
DATA_DIR = PROJECT_ROOT / "data"
