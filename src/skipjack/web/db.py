"""SQLite connection management for the web app."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "skipjack.db"


@contextmanager
def get_db(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
