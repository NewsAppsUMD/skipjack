"""Shared fixtures, and the results tests' helpers (imported as `from conftest import ...`)."""

from __future__ import annotations

import pytest

# Not tests.client_fixture: another project's `tests` package on PYTHONPATH can shadow ours.
from client_fixture import write_downloads
from skipjack.pipeline import fetch, results
from skipjack.pipeline.provenance import SourceRecord, append_to_manifest


@pytest.fixture
def base(tmp_path):
    """The data CLI tests' miniature downloads/ folder, as a --base-url."""
    return str(write_downloads(tmp_path / "downloads"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake archive and manifest for the results pipeline, under tmp_path."""
    arch = tmp_path / "archives"
    manifest = tmp_path / "manifest.csv"
    monkeypatch.setattr(results, "ARCHIVES_DIR", arch)
    monkeypatch.setattr(results, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(fetch, "ARCHIVES_DIR", arch)
    return tmp_path


def archive(env, election, file, data: bytes):
    """Archive `data` as the results file and record it in the manifest. It lands at the file's
    own local_path (archives/sbe/results/<CODE>/ for the county and check files,
    results-precinct/ and results-questions/ for the others), which the env fixture points
    under env."""
    dest = file.local_path
    assert dest.is_relative_to(env / "archives"), "archive() needs the env fixture"
    assert file.election.code == election.code, (file.source_id, election.code)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    append_to_manifest(
        results.MANIFEST_PATH,
        SourceRecord.create(file.source_id, file.url, "SBE", dest, document_title=file.source_id),
    )


class FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def fake_client(bodies):
    """An httpx.Client stand-in whose get(url) answers bodies(url)."""

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp(bodies(url))

    return Client
