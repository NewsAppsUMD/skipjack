"""Which data files are offered for download.

Only aggregated, publishable files under these folders are ever served or copied into the
static site. Raw archives and the SQLite database are never included by default.
"""

from __future__ import annotations

from pathlib import Path

DOWNLOAD_ROOTS = ("voter_registration", "voter_file")
DOWNLOAD_SUFFIXES = {".csv", ".json"}
SQLITE_NAME = "skipjack.db"


def download_files(data_dir: Path) -> list[Path]:
    """Paths relative to ``data_dir`` of every downloadable file, sorted."""
    found = []
    for root in DOWNLOAD_ROOTS:
        base = data_dir / root
        if base.exists():
            found += [
                p.relative_to(data_dir)
                for p in base.rglob("*")
                if p.is_file() and p.suffix in DOWNLOAD_SUFFIXES
            ]
    return sorted(found)
