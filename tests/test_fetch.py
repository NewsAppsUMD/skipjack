"""Tests for fetching files the machine did not fetch the first time.

manifest.csv records absolute paths from whatever machine fetched each file, so a clone
elsewhere sees paths that do not exist. The same file still sits in archives/ under the
project root, and fetch should say "already archived" instead of downloading it again.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from skipjack import cli
from skipjack.pipeline import fetch
from skipjack.pipeline.provenance import (
    SourceRecord,
    append_to_manifest,
    read_manifest,
)

# Another machine's home directory, which does not exist here.
ELSEWHERE = "/Users/someone-else/code/skipjack"


@pytest.fixture
def archives(tmp_path, monkeypatch):
    """A fake archives/ tree and the manifest that goes with it."""
    root = tmp_path / "archives"
    monkeypatch.setattr(fetch, "ARCHIVES_DIR", root)
    monkeypatch.setattr(fetch, "MANIFEST_PATH", tmp_path / "manifest.csv")
    return root


def stale_row(source_id: str, source_url: str, title: str, tail: str) -> SourceRecord:
    """A manifest row as another machine wrote it: absolute path, dead here."""
    tail = tail.lstrip("/")
    return SourceRecord(
        source_id=source_id,
        source_url=source_url,
        source_name="Maryland State Board of Elections",
        fetch_date="2026-06-24T15:53:29+00:00",
        local_path=f"{ELSEWHERE}/{tail}",
        sha256="0" * 64,
        document_title=title,
    )


@pytest.fixture
def forbid_downloads(monkeypatch):
    def forbid():
        def boom(url, dest, timeout=30.0, expect_pdf=False):
            raise AssertionError(f"downloaded despite the archive: {url}")

        monkeypatch.setattr(fetch, "fetch_file", boom)

    return forbid


def test_resolve_archived_finds_the_file_on_this_machine(archives):
    pdf = archives / "sbe/voter_registration/monthly/MSR-2026_09.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    record = stale_row(
        "sbe-vrar-2026-09",
        "https://elections.maryland.gov/pdf/vrar/2026/MSR-2026_09.pdf",
        "Voter Registration Activity Report - 2026-09",
        "/archives/sbe/voter_registration/monthly/MSR-2026_09.pdf",
    )
    assert fetch.resolve_archived(record) == pdf


def test_resolve_archived_returns_a_live_absolute_path_unchanged(archives, tmp_path):
    already_here = tmp_path / "on" / "this" / "machine.pdf"
    already_here.parent.mkdir(parents=True)
    already_here.write_bytes(b"%PDF")
    record = SourceRecord(
        source_id="x", source_url="", source_name="", fetch_date="", local_path=str(already_here)
    )
    assert fetch.resolve_archived(record) == already_here


def test_resolve_archived_keeps_the_stored_path_when_the_file_is_nowhere(archives):
    record = stale_row(
        "sbe-vrar-2026-09",
        "https://elections.maryland.gov/pdf/vrar/2026/MSR-2026_09.pdf",
        "Voter Registration Activity Report - 2026-09",
        "/archives/sbe/voter_registration/monthly/MSR-2026_09.pdf",
    )
    # No PDF here, so the caller keeps the stored path and its own missing-file check fires.
    assert fetch.resolve_archived(record) == Path(record.local_path)


def test_resolve_archived_leaves_paths_outside_the_repositories_alone(archives):
    # Voter file rows point outside the repo on purpose; there is nothing to re-base.
    somewhere = "/Volumes/Scratch/voter files/export.txt"
    record = SourceRecord(
        source_id="sbe-voterfile-2024-09-11-part-01",
        source_url="",
        source_name="",
        fetch_date="",
        local_path=somewhere,
    )
    assert fetch.resolve_archived(record) == Path(somewhere)


def test_vrar_from_another_machine_is_not_downloaded_again(archives, forbid_downloads):
    pdf = archives / "sbe/voter_registration/monthly/MSR-2026_09.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    append_to_manifest(
        fetch.MANIFEST_PATH,
        stale_row(
            "sbe-vrar-2026-09",
            "https://elections.maryland.gov/pdf/vrar/2026/MSR-2026_09.pdf",
            "Voter Registration Activity Report - 2026-09",
            "/archives/sbe/voter_registration/monthly/MSR-2026_09.pdf",
        ),
    )
    forbid_downloads()

    record = fetch.fetch_vrar(2026, 9)

    assert record is not None and record.source_id == "sbe-vrar-2026-09"
    assert len(read_manifest(fetch.MANIFEST_PATH)) == 1  # no second row appended


def test_district_file_from_another_machine_is_not_downloaded_again(archives, forbid_downloads):
    url = "https://elections.maryland.gov/pdf/vreg/2022/VR-2022-Governor.pdf"
    pdf = archives / "sbe/voter_registration/by_district/VR-2022-Governor.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    append_to_manifest(
        fetch.MANIFEST_PATH,
        stale_row(
            "sbe-vreg-district-2022-Governor",
            url,
            "Voter Registration by District - 2022-Governor",
            "/archives/sbe/voter_registration/by_district/VR-2022-Governor.pdf",
        ),
    )
    forbid_downloads()

    record = fetch.fetch_voter_registration_xls("2022-Governor", url)

    assert record is not None and record.source_id == "sbe-vreg-district-2022-Governor"
    assert len(read_manifest(fetch.MANIFEST_PATH)) == 1


@pytest.mark.skipif(
    not fetch.vrar_local_path(2026, 9).exists(), reason="the 2026-09 PDF is not archived"
)
def test_parse_reads_a_manifest_written_by_another_machine(tmp_path, monkeypatch):
    """cmd_parse finds the PDFs relative to this project root."""
    monkeypatch.chdir(tmp_path)
    stale = stale_row(
        "sbe-vrar-2026-09",
        "https://elections.maryland.gov/pdf/vrar/2026/MSR-2026_09.pdf",
        "Voter Registration Activity Report - 2026-09",
        "/archives/sbe/voter_registration/monthly/MSR-2026_09.pdf",
    )
    monkeypatch.setattr(fetch, "MANIFEST_PATH", tmp_path / "manifest.csv")
    append_to_manifest(fetch.MANIFEST_PATH, stale)

    cli.cmd_parse(argparse.Namespace(month=["2026-09"], force=False))

    for stem in ("monthly", "activity", "summary"):
        path = tmp_path / "data" / "voter_registration" / stem / "2026-09.csv"
        assert path.exists(), f"missing {path}"
        assert len(path.read_text()) > 100
    assert "2026-09" in Path("data/voter_registration/monthly/2026-09.csv").read_text()