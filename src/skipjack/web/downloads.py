"""Which data files are offered for download.

Only aggregated, publishable files under these folders are ever served or copied into the
static site. Raw archives and the SQLite database are never included by default.

voter_registration, voter_file and turnout_model (the forecast's aggregate inputs and
outputs) are mounted and served wholesale; the turnout files
are gated per family instead, so a certified election does not publish the board's
unofficial early-voting tallies (see skipjack.pipeline.turnout.may_publish). The results files
are gated per election: only elections the registry marks published are offered. The
unofficial results (results_unofficial) are offered only while the count runs, and only the
newest snapshot: once the certified results are published they are no longer offered.
"""

from __future__ import annotations

from pathlib import Path

from skipjack.pipeline.results import RESULTS_ELECTIONS
from skipjack.pipeline.results_unofficial import is_live
from skipjack.pipeline.turnout import may_publish

DOWNLOAD_ROOTS = (
    "voter_registration",
    "voter_file",
    "turnout",
    "results",
    "results_unofficial",
    "turnout_model",
)
MOUNTED_ROOTS = ("voter_registration", "voter_file", "turnout_model")
DOWNLOAD_SUFFIXES = {".csv", ".json"}
SQLITE_NAME = "skipjack.db"


def results_published(code: str) -> bool:
    """An election's results files are offered only when the registry marks it published."""
    election = RESULTS_ELECTIONS.get(code)
    return bool(election and election.published)


def unofficial_snapshot(data_dir: Path, code: str) -> str | None:
    """The newest parsed snapshot of a live unofficial count, or None once the certified
    results are published (or when nothing is parsed)."""
    if not is_live(code):
        return None
    root = data_dir / "results_unofficial" / code
    if not root.is_dir():
        return None
    found = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    return found[-1] if found else None


def unofficial_offered(data_dir: Path, parts: tuple[str, ...]) -> bool:
    """Whether a path under results_unofficial/ (``parts`` start at the election code) is
    offered: it sits in a live election's newest snapshot."""
    return len(parts) >= 3 and unofficial_snapshot(data_dir, parts[0]) == parts[1]


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
            if root == "results_unofficial" and not unofficial_offered(data_dir, parts[1:]):
                continue
            found.append(relative)
    return sorted(found)
