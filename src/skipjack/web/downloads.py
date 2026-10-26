"""Which data files are offered for download.

Only aggregated, publishable files under these folders are ever served or copied into the
static site. Raw archives and the SQLite database are never included by default.

voter_registration and voter_file are mounted and served wholesale; the turnout files
are gated per family instead, so a certified election does not publish the board's
unofficial early-voting tallies (see skipjack.pipeline.turnout.may_publish). The results files
are gated per election: only elections the registry marks published are offered.
"""

from __future__ import annotations

from pathlib import Path

from skipjack.pipeline.results import RESULTS_ELECTIONS
from skipjack.pipeline.turnout import may_publish

DOWNLOAD_ROOTS = ("voter_registration", "voter_file", "turnout", "results")
MOUNTED_ROOTS = ("voter_registration", "voter_file")
DOWNLOAD_SUFFIXES = {".csv", ".json"}
SQLITE_NAME = "skipjack.db"


def results_published(code: str) -> bool:
    """An election's results files are offered only when the registry marks it published."""
    election = RESULTS_ELECTIONS.get(code)
    return bool(election and election.published)


def download_files(data_dir: Path) -> list[Path]:
    """Paths relative to ``data_dir`` of every downloadable file, sorted."""
    found = []
    for root in DOWNLOAD_ROOTS:
        base = data_dir / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not (p.is_file() and p.suffix in DOWNLOAD_SUFFIXES):
                continue
            relative = p.relative_to(data_dir)
            parts = relative.parts
            if root == "turnout" and len(parts) >= 3 and not may_publish(parts[1], parts[2]):
                continue
            if root == "results" and not (len(parts) >= 2 and results_published(parts[1])):
                continue
            found.append(relative)
    return sorted(found)
