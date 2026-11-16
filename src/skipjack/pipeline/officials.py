"""The Maryland Manual's roster of officeholders, as compiled by NewsAppsUMD/maryland-manual.

The roster is a scrape of the Maryland Manual On-Line (Maryland State Archives): who holds each
state and local office, how they were selected, and when their term ends. It is a snapshot of
the officeholders in 2025-2026 (each row carries the ``page_date`` of the Manual page it came
from), so it says who the incumbents were at the 2026 elections and nothing about earlier ones:
``skipjack.web.incumbents`` uses it for GP26 and GG26 only.

The file is copied verbatim from the repository at a pinned commit, so the sha256 in its
sidecar is the file's own; ``officials-fetch --ref`` takes a newer commit.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path

from skipjack.pipeline.fetch import fetch_file
from skipjack.pipeline.provenance import ProvenanceSidecar, SourceRecord, compute_sha256

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OFFICIALS_DIR = PROJECT_ROOT / "data" / "officials"
ROSTER_NAME = "maryland_manual_officials.csv"
OVERRIDES_NAME = "incumbent_overrides.csv"
REPO = "NewsAppsUMD/maryland-manual"
REPO_PATH = "data/officials.csv"
# The commit "Add officials dataset: state and local office-holders from the Manual"
# (2026-11-16); pinned so a re-fetch is the same file unless asked for a newer one.
DEFAULT_REF = "36ddbeaeef4aecda7c3ad646847c2a2b241c97d9"
SOURCE_NAME = "Maryland State Archives, Maryland Manual On-Line (compiled by NewsAppsUMD)"

COLUMNS = (
    "id", "person_id", "person_id_method", "name_full", "name_first", "name_middle",
    "name_last", "name_suffix", "nickname", "office_title", "role", "role_note", "body_name",
    "parent_body", "jurisdiction_level", "jurisdiction", "jurisdiction_id", "branch",
    "record_type", "selection_method", "appointing_authority", "selection_text", "party",
    "party_source", "district", "counties", "term_length_years", "term_start",
    "term_start_title", "term_end_year", "is_vacancy", "is_ex_officio", "note", "bio_url",
    "source_url", "evidence", "page_date",
)  # fmt: skip
OVERRIDE_COLUMNS = ("person_id", "election", "candidate", "decision", "note")
DECISIONS = ("not_a_match", "unconfirmed")


class OfficialsLoadError(RuntimeError):
    """The roster file contradicts its sidecar or has an unexpected shape."""


def source_id(ref: str) -> str:
    return f"maryland-manual-officials-{ref[:7]}"


def raw_url(ref: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{ref}/{REPO_PATH}"


def fetch_officials(ref: str = DEFAULT_REF, out_dir: Path = OFFICIALS_DIR) -> Path | None:
    """Download the roster at ``ref`` and write it with its provenance sidecar."""
    dest = out_dir / ROSTER_NAME
    url = raw_url(ref)
    if not fetch_file(url, dest, expect_csv=True):
        return None
    with dest.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    if header != COLUMNS:
        raise OfficialsLoadError(f"{url}: columns changed: {header}")
    record = SourceRecord.create(
        source_id=source_id(ref),
        source_url=url,
        source_name=SOURCE_NAME,
        local_path=dest,
        document_title=f"Maryland Manual officeholders ({REPO}@{ref[:7]})",
    )
    pages = sorted(r["page_date"] for r in rows if r["page_date"])
    sidecar = ProvenanceSidecar.from_source(
        record,
        notes=(
            f"Copied verbatim from {REPO} at commit {ref}. Manual page dates "
            f"{pages[0]} to {pages[-1]}. Used for the 2026 elections' incumbents only."
        ),
        extraction_method="copy",
        checks={"rows": len(rows), "columns": "ok"},
    )
    sidecar.write(dest)
    logger.info("Wrote %s (%d rows)", dest, len(rows))
    return dest


def load_officials(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load the roster and the hand-checked match overrides, if the roster is there."""
    from skipjack.pipeline.build_db import _upsert_source, load_provenance

    roster = data_dir / "officials" / ROSTER_NAME
    if not roster.exists():
        logger.warning("No officials roster at %s", roster)
        return 0
    prov = load_provenance(roster)
    if prov is None:
        raise OfficialsLoadError(f"{roster}: no provenance sidecar")
    if (sha := compute_sha256(roster)) != prov["sha256_original"]:
        raise OfficialsLoadError(f"{roster}: sha256 {sha} is not the sidecar's")
    _upsert_source(conn, prov)
    with roster.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise OfficialsLoadError(f"{roster}: unexpected columns")
        rows = [(prov["source_id"], *(r[c] for c in COLUMNS)) for r in reader]
    marks = ", ".join("?" * (len(COLUMNS) + 1))
    conn.executemany(
        f"INSERT INTO officials (source_id, {', '.join(COLUMNS)}) VALUES ({marks})", rows
    )
    overrides = data_dir / "officials" / OVERRIDES_NAME
    if overrides.exists():
        with overrides.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or ()) != OVERRIDE_COLUMNS:
                raise OfficialsLoadError(f"{overrides}: unexpected columns")
            found = list(reader)
        for r in found:
            if r["decision"] not in DECISIONS:
                raise OfficialsLoadError(f"{overrides}: unknown decision {r['decision']!r}")
        conn.executemany(
            "INSERT INTO official_overrides (person_id, election, candidate, decision, note)"
            " VALUES (?, ?, ?, ?, ?)",
            [tuple(r[c] for c in OVERRIDE_COLUMNS) for r in found],
        )
    logger.info("Loaded officials: %d rows", len(rows))
    return len(rows)
