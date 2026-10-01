"""Download files from Maryland SBE and record provenance."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from skipjack.pipeline.provenance import (
    SourceRecord,
    append_to_manifest,
    find_source,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARCHIVES_DIR = PROJECT_ROOT / "archives"
MANIFEST_PATH = ARCHIVES_DIR / "manifest.csv"

# results.elections.maryland.gov redirects here.
SBE_BASE = "https://elections.maryland.gov"
SOURCE_NAME = "Maryland State Board of Elections"


def vrar_url(year: int, month: int) -> str:
    if year >= 2016:
        return f"{SBE_BASE}/pdf/vrar/{year}/MSR-{year}_{month:02d}.pdf"
    return f"{SBE_BASE}/pdf/vrar/MSR-{year}_{month:02d}.pdf"


def vrar_source_id(year: int, month: int) -> str:
    return f"sbe-vrar-{year}-{month:02d}"


def vrar_local_path(year: int, month: int) -> Path:
    return ARCHIVES_DIR / "sbe" / "voter_registration" / "monthly" / f"MSR-{year}_{month:02d}.pdf"


def fetch_file(url: str, dest: Path, timeout: float = 30.0, expect_pdf: bool = False) -> bool:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            # SBE answers 200 with an HTML page for reports that do not exist yet.
            if expect_pdf and not resp.content.startswith(b"%PDF"):
                logger.info("Not published (no PDF at %s)", url)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            logger.info("Downloaded %s -> %s (%d bytes)", url, dest, len(resp.content))
            return True
    except httpx.HTTPError as e:
        logger.warning("Failed to download %s: %s", url, e)
        return False


def fetch_vrar(year: int, month: int, force: bool = False) -> SourceRecord | None:
    source_id = vrar_source_id(year, month)

    if not force:
        existing = find_source(MANIFEST_PATH, source_id)
        if existing and Path(existing.local_path).exists():
            logger.info("Already archived: %s", source_id)
            return existing

    url = vrar_url(year, month)
    dest = vrar_local_path(year, month)

    if not fetch_file(url, dest, expect_pdf=True):
        return None

    record = SourceRecord.create(
        source_id=source_id,
        source_url=url,
        source_name=SOURCE_NAME,
        local_path=dest,
        document_title=f"Voter Registration Activity Report - {year}-{month:02d}",
    )
    append_to_manifest(MANIFEST_PATH, record)
    return record


def fetch_voter_registration_xls(election: str, url: str) -> SourceRecord | None:
    source_id = f"sbe-vreg-district-{election}"
    existing = find_source(MANIFEST_PATH, source_id)
    if existing and Path(existing.local_path).exists():
        logger.info("Already archived: %s", source_id)
        return existing

    filename = url.rsplit("/", 1)[-1]
    dest = ARCHIVES_DIR / "sbe" / "voter_registration" / "by_district" / filename
    if not fetch_file(url, dest, expect_pdf=True):
        return None

    record = SourceRecord.create(
        source_id=source_id,
        source_url=url,
        source_name=SOURCE_NAME,
        local_path=dest,
        document_title=f"Voter Registration by District - {election}",
    )
    append_to_manifest(MANIFEST_PATH, record)
    return record
