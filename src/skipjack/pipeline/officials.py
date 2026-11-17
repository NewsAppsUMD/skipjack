"""The Maryland Manual's roster of officeholders, as compiled by NewsAppsUMD/maryland-manual.

The roster is a scrape of the Maryland Manual On-Line (Maryland State Archives): who holds each
state and local office, how they were selected, and when their term ends. It is a snapshot of
the officeholders in 2025-2026 (each row carries the ``page_date`` of the Manual page it came
from), so it says who the incumbents were at the 2026 elections and nothing about earlier ones:
``skipjack.web.incumbents`` uses it for GP26 and GG26 only.

The file is copied verbatim from the repository at a pinned commit, so the sha256 in its
sidecar is the file's own; ``officials-fetch --ref`` takes a newer commit.

The Manual has no members of Congress. They come from unitedstates/congress-legislators'
``legislators-current.csv`` (public domain), copied the same way at a pinned commit of its
gh-pages branch (``congress-fetch --ref``) into the ``congress_members`` table.
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


CONGRESS_NAME = "congress_legislators_current.csv"
CONGRESS_REPO = "unitedstates/congress-legislators"
CONGRESS_PATH = "legislators-current.csv"
# gh-pages "update to main @ d5af3d2" (2026-09-24): the 119th Congress's members, whose House
# terms end in January 2027
CONGRESS_REF = "577ca04282d14688cb14f6559eb27b52bdc50dfc"
CONGRESS_SOURCE_NAME = "unitedstates/congress-legislators (public domain)"
CONGRESS_COLUMNS = (
    "last_name", "first_name", "middle_name", "suffix", "nickname", "full_name", "birthday",
    "gender", "type", "state", "district", "senate_class", "party", "url", "address", "phone",
    "contact_form", "rss_url", "twitter", "twitter_id", "facebook", "youtube", "youtube_id",
    "mastodon", "bioguide_id", "thomas_id", "opensecrets_id", "lis_id", "fec_ids", "cspan_id",
    "govtrack_id", "votesmart_id", "ballotpedia_id", "washington_post_id", "icpsr_id",
    "wikipedia_id",
)  # fmt: skip


class OfficialsLoadError(RuntimeError):
    """The roster file contradicts its sidecar or has an unexpected shape."""


def source_id(ref: str) -> str:
    return f"maryland-manual-officials-{ref[:7]}"


def raw_url(ref: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{ref}/{REPO_PATH}"


def congress_source_id(ref: str) -> str:
    return f"congress-legislators-current-{ref[:7]}"


def congress_raw_url(ref: str) -> str:
    return f"https://raw.githubusercontent.com/{CONGRESS_REPO}/{ref}/{CONGRESS_PATH}"


def _copy(url: str, dest: Path, columns: tuple, record_args: dict, notes) -> Path | None:
    """Download ``url`` to ``dest`` verbatim, check its columns and write its sidecar;
    ``notes(rows)`` is the sidecar's note."""
    if not fetch_file(url, dest, expect_csv=True):
        return None
    with dest.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    if header != columns:
        raise OfficialsLoadError(f"{url}: columns changed: {header}")
    record = SourceRecord.create(source_url=url, local_path=dest, **record_args)
    sidecar = ProvenanceSidecar.from_source(
        record,
        notes=notes(rows),
        extraction_method="copy",
        checks={"rows": len(rows), "columns": "ok"},
    )
    sidecar.write(dest)
    logger.info("Wrote %s (%d rows)", dest, len(rows))
    return dest


def fetch_officials(ref: str = DEFAULT_REF, out_dir: Path = OFFICIALS_DIR) -> Path | None:
    """Download the roster at ``ref`` and write it with its provenance sidecar."""

    def notes(rows: list[dict]) -> str:
        pages = sorted(r["page_date"] for r in rows if r["page_date"])
        return (
            f"Copied verbatim from {REPO} at commit {ref}. Manual page dates "
            f"{pages[0]} to {pages[-1]}. Used for the 2026 elections' incumbents only."
        )

    return _copy(
        raw_url(ref),
        out_dir / ROSTER_NAME,
        COLUMNS,
        {
            "source_id": source_id(ref),
            "source_name": SOURCE_NAME,
            "document_title": f"Maryland Manual officeholders ({REPO}@{ref[:7]})",
        },
        notes,
    )


def fetch_congress(ref: str = CONGRESS_REF, out_dir: Path = OFFICIALS_DIR) -> Path | None:
    """Download the current members of Congress at ``ref`` (a gh-pages commit)."""
    return _copy(
        congress_raw_url(ref),
        out_dir / CONGRESS_NAME,
        CONGRESS_COLUMNS,
        {
            "source_id": congress_source_id(ref),
            "source_name": CONGRESS_SOURCE_NAME,
            "document_title": f"Current members of Congress ({CONGRESS_REPO}@{ref[:7]})",
        },
        lambda rows: (
            f"Copied verbatim from {CONGRESS_REPO} (gh-pages) at commit {ref}: {len(rows)} "
            "members. Used for the 2026 elections' congressional incumbents only."
        ),
    )


def _verified(conn: sqlite3.Connection, path: Path, columns: tuple) -> tuple[str, list[dict]]:
    """The source id and rows of a copied file, after checking it against its sidecar."""
    from skipjack.pipeline.build_db import _upsert_source, load_provenance

    prov = load_provenance(path)
    if prov is None:
        raise OfficialsLoadError(f"{path}: no provenance sidecar")
    if (sha := compute_sha256(path)) != prov["sha256_original"]:
        raise OfficialsLoadError(f"{path}: sha256 {sha} is not the sidecar's")
    _upsert_source(conn, prov)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != columns:
            raise OfficialsLoadError(f"{path}: unexpected columns")
        return prov["source_id"], list(reader)


def _insert(conn: sqlite3.Connection, table: str, columns: tuple, sid: str, rows: list[dict]):
    marks = ", ".join("?" * (len(columns) + 1))
    conn.executemany(
        f"INSERT INTO {table} (source_id, {', '.join(columns)}) VALUES ({marks})",
        [(sid, *(r[c] for c in columns)) for r in rows],
    )


def load_officials(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load the roster, the members of Congress and the hand-checked match overrides, each
    if its file is there."""
    congress = data_dir / "officials" / CONGRESS_NAME
    if congress.exists():
        sid, members = _verified(conn, congress, CONGRESS_COLUMNS)
        _insert(conn, "congress_members", CONGRESS_COLUMNS, sid, members)
        logger.info("Loaded members of Congress: %d rows", len(members))
    roster = data_dir / "officials" / ROSTER_NAME
    if not roster.exists():
        logger.warning("No officials roster at %s", roster)
        return 0
    sid, rows = _verified(conn, roster, COLUMNS)
    _insert(conn, "officials", COLUMNS, sid, rows)
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
