"""Provenance tracking for data pipeline.

Every piece of data traces back to its original source through:
1. archives/manifest.csv — download log with URLs, dates, hashes
2. data/*_provenance.json — sidecar files linking CSVs to their archive sources
3. SQLite sources table — normalized provenance queryable from the web app
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SourceRecord:
    source_id: str
    source_url: str
    source_name: str
    fetch_date: str
    local_path: str
    sha256: str = ""
    document_title: str = ""
    notes: str = ""

    @classmethod
    def create(
        cls,
        source_id: str,
        source_url: str,
        source_name: str,
        local_path: str | Path,
        document_title: str = "",
        notes: str = "",
    ) -> SourceRecord:
        local_path = Path(local_path)
        sha256 = compute_sha256(local_path) if local_path.exists() else ""
        return cls(
            source_id=source_id,
            source_url=source_url,
            source_name=source_name,
            fetch_date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            local_path=str(local_path),
            sha256=sha256,
            document_title=document_title,
            notes=notes,
        )


@dataclass
class ProvenanceSidecar:
    source_id: str
    source_url: str
    source_name: str
    document_title: str
    fetch_date: str
    parse_date: str
    sha256_original: str
    parser_version: str
    notes: str = ""
    extraction_method: str = ""
    checks: dict = field(default_factory=dict)

    @classmethod
    def from_source(
        cls,
        source: SourceRecord,
        parser_version: str = "skipjack 0.2.0",
        notes: str = "",
        extraction_method: str = "",
        checks: dict | None = None,
    ) -> ProvenanceSidecar:
        return cls(
            source_id=source.source_id,
            source_url=source.source_url,
            source_name=source.source_name,
            document_title=source.document_title,
            fetch_date=source.fetch_date,
            parse_date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            sha256_original=source.sha256,
            parser_version=parser_version,
            notes=notes,
            extraction_method=extraction_method,
            checks=checks or {},
        )

    def write(self, csv_path: Path) -> Path:
        sidecar_path = csv_path.with_name(csv_path.stem + "_provenance.json")
        sidecar_path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return sidecar_path

    @classmethod
    def read(cls, sidecar_path: Path) -> ProvenanceSidecar:
        data = json.loads(sidecar_path.read_text())
        return cls(**data)


MANIFEST_FIELDS = [
    "source_id",
    "source_url",
    "source_name",
    "fetch_date",
    "local_path",
    "sha256",
    "document_title",
    "notes",
]


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(manifest_path: Path) -> list[SourceRecord]:
    if not manifest_path.exists():
        return []
    with open(manifest_path, newline="") as f:
        reader = csv.DictReader(f)
        return [SourceRecord(**row) for row in reader]


def append_to_manifest(manifest_path: Path, record: SourceRecord) -> None:
    exists = manifest_path.exists()
    with open(manifest_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(asdict(record))


def find_source(manifest_path: Path, source_id: str) -> SourceRecord | None:
    for record in read_manifest(manifest_path):
        if record.source_id == source_id:
            return record
    return None
