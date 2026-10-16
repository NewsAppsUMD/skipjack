"""Build skipjack.db from cleaned CSVs in data/."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import polars as pl
from polars.exceptions import PolarsError

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.results import COUNTY_CODES, RESULTS_ELECTIONS, county_slug

logger = logging.getLogger(__name__)


class TurnoutLoadError(RuntimeError):
    """A turnout snapshot contradicts itself or has no provenance sidecar."""


class ResultsLoadError(RuntimeError):
    """A county results CSV contradicts its sidecar or the registry, or has no sidecar."""


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


def _source_fields(prov: dict) -> tuple:
    """The provenance every source row shares, after its key columns."""
    return (
        prov["source_url"],
        prov["source_name"],
        prov["document_title"],
        prov["fetch_date"],
        prov.get("sha256_original", ""),
        prov.get("extraction_method", ""),
        json.dumps(prov.get("checks", {})),
        prov.get("notes", ""),
    )


def _upsert_source(conn: sqlite3.Connection, prov: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sources
           (source_id, source_url, source_name, document_title, fetch_date, sha256,
            extraction_method, checks_json, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (prov["source_id"], *_source_fields(prov)),
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
    "reg_window", "reg_week", "race",
]  # fmt: skip
VOTER_FILE_COLUMNS = ["snapshot_date", "metric", *VOTER_FILE_DIMS, "count", "eligible", "voters"]


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
                    "voters": None,
                    **row,
                }
                for row in payload["rows"]
            )
            conn.executemany(sql, rows)
            total += len(payload["rows"])
        logger.info("Loaded voter file snapshot %s", meta["snapshot_date"])
    return total


def _upsert_turnout_source(conn: sqlite3.Connection, prov: dict, as_of: str) -> None:
    """The as-of date comes from the CSV's file name; the sidecar carries the rest."""
    conn.execute(
        """INSERT OR REPLACE INTO turnout_sources
           (source_id, election, family, geography, as_of, source_url, source_name,
            document_title, fetch_date, sha256, extraction_method, checks_json, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prov["source_id"],
            prov["election"],
            prov["family"],
            prov["geography"],
            as_of,
            *_source_fields(prov),
        ),
    )


def _check_turnout_snapshot(df: pl.DataFrame, prov: dict, csv_path: Path) -> None:
    """Every snapshot must agree with its file name and its provenance sidecar."""
    as_of = csv_path.stem
    bad_as_of = df.filter(pl.col("as_of").is_null() | (pl.col("as_of") != as_of))
    if bad_as_of.height:
        found = bad_as_of["as_of"].unique().to_list()
        raise TurnoutLoadError(
            f"{csv_path}: rows carry as_of {found} but the file name says {as_of!r}"
        )
    for col in ("election", "family", "geography"):
        values = df[col].unique().to_list()
        expected = prov.get(col, "")
        if len(values) != 1 or values[0] != expected:
            raise TurnoutLoadError(
                f"{csv_path}: {col} column holds {values} but the sidecar says {expected!r}"
            )


def load_turnout(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> int:
    """Load every data/turnout/<election>/<family>/<geography>/<as-of>.csv snapshot."""
    root = data_dir / "turnout"
    if not root.exists():
        return 0
    columns = [
        "as_of",
        "election",
        "family",
        "geography",
        "county",
        "district",
        "category",
        "party",
        "measure",
        "value",
    ]
    overrides = {c: pl.String for c in columns if c != "value"}
    overrides["value"] = pl.Int64
    sql = (
        f"INSERT INTO turnout_counts (source_id, {', '.join(columns)}) "
        f"VALUES (?, {', '.join('?' * len(columns))})"
    )
    total = 0
    for csv_path in sorted(root.glob("*/*/*/*.csv")):
        prov = load_provenance(csv_path)
        if not prov:
            raise TurnoutLoadError(
                f"{csv_path}: no provenance sidecar; refusing to load an unattributable snapshot"
            )
        try:
            df = pl.read_csv(csv_path, schema_overrides=overrides).select(columns)
        except PolarsError as e:
            raise TurnoutLoadError(f"{csv_path}: {e}") from e
        _check_turnout_snapshot(df, prov, csv_path)
        _upsert_turnout_source(conn, prov, csv_path.stem)
        conn.executemany(sql, ((prov["source_id"], *row) for row in df.iter_rows()))
        total += len(df)
    logger.info("Loaded turnout: %d rows", total)
    return total


_RESULTS_TEXT = ["election", "county", "party_file", "office", "district", "candidate", "party"]


def _normalize_district(district: str) -> str:
    """A purely numeric district drops its leading zeros ("01" -> "1"); "1A" and "" stay.

    PP20's Dorchester file prints "01" where the other counties print "1".
    """
    return str(int(district)) if district.isdigit() else district


def load_results(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> int:
    """Load every data/results/<CODE>/county/*.csv, melting the vote-type columns.

    Returns the number of results_county rows loaded.
    """
    root = data_dir / "results"
    if not root.exists():
        return 0
    next_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM results_county").fetchone()[0] + 1
    total = 0
    for csv_path in sorted(root.glob("*/county/*.csv")):
        prov = load_provenance(csv_path)
        if not prov:
            raise ResultsLoadError(
                f"{csv_path}: no provenance sidecar; refusing to load an unattributable file"
            )
        code = csv_path.parent.parent.name
        election = RESULTS_ELECTIONS.get(code)
        if election is None:
            raise ResultsLoadError(f"{csv_path}: unknown election code {code!r}")
        stems = [stem for _, stem in election.vote_types]
        against = election.kind == "general"
        overrides = {c: pl.String for c in _RESULTS_TEXT}
        overrides |= {"winner": pl.Int64, "write_in": pl.Int64, "total_votes": pl.Int64}
        overrides |= {f"{stem}_votes": pl.Int64 for stem in stems}
        if against:
            overrides |= {f"{stem}_against": pl.Int64 for stem in stems}
            overrides["total_against"] = pl.Int64
        try:
            df = pl.read_csv(
                csv_path, schema_overrides=overrides, missing_utf8_is_empty_string=True
            )
        except PolarsError as e:
            raise ResultsLoadError(f"{csv_path}: {e}") from e
        needed = [*overrides]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise ResultsLoadError(f"{csv_path}: missing columns {missing} for {code}")
        if df.height == 0:
            raise ResultsLoadError(f"{csv_path}: no rows")
        _check_results_file(df, prov, csv_path, code)
        df = df.with_columns(
            pl.col("district").map_elements(_normalize_district, return_dtype=pl.String)
        )
        _upsert_source(conn, prov)

        county_rows, vote_rows = [], []
        for row in df.iter_rows(named=True):
            vote_sum = 0
            for stem in stems:
                votes = row[f"{stem}_votes"]
                if votes is None:
                    raise ResultsLoadError(f"{csv_path}: blank {stem}_votes for {row['candidate']}")
                vote_sum += votes
                vote_rows.append(
                    (next_id, stem, votes, row[f"{stem}_against"] if against else None)
                )
            if vote_sum != row["total_votes"]:
                raise ResultsLoadError(
                    f"{csv_path}: {row['candidate']}: total votes {row['total_votes']} "
                    f"!= sum of vote types {vote_sum}"
                )
            county_rows.append(
                (
                    next_id,
                    prov["source_id"],
                    *(row[c] for c in _RESULTS_TEXT),
                    row["winner"],
                    row["write_in"],
                    row["total_votes"],
                    row["total_against"] if against else None,
                )
            )
            next_id += 1
        conn.executemany(
            """INSERT INTO results_county
               (id, source_id, election, county, party_file, office, district, candidate, party,
                winner, write_in, total_votes, total_against)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            county_rows,
        )
        conn.executemany(
            "INSERT INTO results_votes (result_id, vote_type, votes, votes_against) "
            "VALUES (?, ?, ?, ?)",
            vote_rows,
        )
        total += len(county_rows)
    logger.info("Loaded results: %d rows", total)
    return total


def _check_results_file(df: pl.DataFrame, prov: dict, csv_path: Path, code: str) -> None:
    """The CSV must agree with its sidecar, its directory and its file name."""
    elections = df["election"].unique().to_list()
    if elections != [prov.get("election", "")] or elections != [code]:
        raise ResultsLoadError(
            f"{csv_path}: election column holds {elections} but the sidecar says "
            f"{prov.get('election', '')!r} and the directory says {code!r}"
        )
    number, _, rest = csv_path.stem.partition("-")
    county = COUNTY_CODES.get(number)
    if county is None or not rest.startswith(county_slug(county) + "-"):
        raise ResultsLoadError(f"{csv_path}: file name does not match a registry county")
    counties = df["county"].unique().to_list()
    if counties != [county]:
        raise ResultsLoadError(
            f"{csv_path}: county column holds {counties} but the file is {county!r}"
        )
    party_file = rest[len(county_slug(county)) + 1 :]
    party_files = df["party_file"].unique().to_list()
    if party_files != [party_file]:
        raise ResultsLoadError(
            f"{csv_path}: party_file column holds {party_files} but the file name says {party_file!r}"
        )


def build(db_path: Path = DB_PATH, data_dir: Path = DATA_DIR) -> Path:
    if db_path.exists():
        db_path.unlink()
        logger.info("Removed existing %s", db_path)

    conn = create_db(db_path)
    counts = {subdir: load_table(conn, subdir, data_dir) for subdir in TABLES}
    counts["voter_file"] = load_voter_file(conn, data_dir)
    counts["turnout"] = load_turnout(conn, data_dir)
    counts["results"] = load_results(conn, data_dir)
    conn.commit()
    conn.close()

    logger.info("Built %s: %s", db_path, counts)
    return db_path
