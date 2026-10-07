"""Build skipjack.db from cleaned CSVs in data/."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import polars as pl

from skipjack.models import SCHEMA_SQL

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = PROJECT_ROOT / "skipjack.db"

# subdirectory of data/voter_registration -> (table, columns)
TABLES = {
    "monthly": (
        "voter_registration",
        ["report_date", "county", "party", "party_name", "active_voters"],
    ),
    "activity": (
        "registration_activity",
        ["report_date", "county", "measure", "party", "value"],
    ),
    "summary": (
        "registration_summary",
        ["report_date", "section", "category", "party", "value"],
    ),
}


def create_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    return conn


def load_provenance(csv_path: Path) -> dict | None:
    sidecar = csv_path.with_name(csv_path.stem + "_provenance.json")
    if sidecar.exists():
        return json.loads(sidecar.read_text())
    return None


def _upsert_source(conn: sqlite3.Connection, prov: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sources
           (source_id, source_url, source_name, document_title, fetch_date, sha256,
            extraction_method, checks_json, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prov["source_id"],
            prov["source_url"],
            prov["source_name"],
            prov["document_title"],
            prov["fetch_date"],
            prov.get("sha256_original", ""),
            prov.get("extraction_method", ""),
            json.dumps(prov.get("checks", {})),
            prov.get("notes", ""),
        ),
    )


def load_table(conn: sqlite3.Connection, subdir: str, data_dir: Path = DATA_DIR) -> int:
    table, columns = TABLES[subdir]
    src_dir = data_dir / "voter_registration" / subdir
    if not src_dir.exists():
        logger.warning("No data at %s", src_dir)
        return 0

    placeholders = ", ".join("?" for _ in range(len(columns) + 1))
    sql = f"INSERT INTO {table} (source_id, {', '.join(columns)}) VALUES ({placeholders})"
    total = 0
    for csv_path in sorted(src_dir.glob("*.csv")):
        prov = load_provenance(csv_path)
        source_id = prov["source_id"] if prov else csv_path.stem
        if prov:
            _upsert_source(conn, prov)
        schema = {c: pl.String for c in columns if c not in ("active_voters", "value")}
        df = pl.read_csv(csv_path, schema_overrides=schema)
        conn.executemany(sql, ((source_id, *row) for row in df.select(columns).iter_rows()))
        total += len(df)
    logger.info("Loaded %s: %d rows", table, total)
    return total


VOTER_FILE_DIMS = [
    "county", "party_group", "party", "status", "election", "age_band", "generation",
    "reg_year", "district_type", "district", "gender", "voter_type", "eligible_elections",
    "reg_window", "reg_week",
]  # fmt: skip
VOTER_FILE_COLUMNS = ["snapshot_date", "metric", *VOTER_FILE_DIMS, "count", "eligible"]


def load_voter_file(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> int:
    """Load every data/voter_file/<snapshot>/ directory. Returns the number of cells loaded."""
    root = data_dir / "voter_file"
    if not root.exists():
        return 0

    sql = (
        f"INSERT INTO voter_file_counts ({', '.join(VOTER_FILE_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in VOTER_FILE_COLUMNS)})"
    )
    total = 0
    for snapshot_dir in sorted(p for p in root.iterdir() if (p / "snapshot.json").exists()):
        meta_path = snapshot_dir / "snapshot.json"
        meta = json.loads(meta_path.read_text())
        prov = load_provenance(meta_path)
        if prov:
            _upsert_source(conn, prov)
        conn.execute(
            "INSERT INTO voter_file_snapshots (snapshot_date, source_id, meta_json) VALUES (?, ?, ?)",
            (meta["snapshot_date"], prov["source_id"] if prov else None, json.dumps(meta)),
        )
        for metric_path in sorted(snapshot_dir.glob("*.json")):
            if metric_path.name.startswith("snapshot"):
                continue
            payload = json.loads(metric_path.read_text())
            rows = (
                {
                    "snapshot_date": payload["snapshot_date"],
                    "metric": payload["metric"],
                    **dict.fromkeys(VOTER_FILE_DIMS),
                    "count": None,
                    "eligible": None,
                    **row,
                }
                for row in payload["rows"]
            )
            conn.executemany(sql, rows)
            total += len(payload["rows"])
        logger.info("Loaded voter file snapshot %s", meta["snapshot_date"])
    return total


def build(db_path: Path = DB_PATH, data_dir: Path = DATA_DIR) -> Path:
    if db_path.exists():
        db_path.unlink()
        logger.info("Removed existing %s", db_path)

    conn = create_db(db_path)
    counts = {subdir: load_table(conn, subdir, data_dir) for subdir in TABLES}
    counts["voter_file"] = load_voter_file(conn, data_dir)
    conn.commit()
    conn.close()

    logger.info("Built %s: %s", db_path, counts)
    return db_path
