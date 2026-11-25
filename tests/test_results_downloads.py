"""The results downloads: the Data page listing and the gated download route."""

from __future__ import annotations

import gzip

import pytest
from fastapi.testclient import TestClient

from skipjack.pipeline.results import RESULTS_ELECTIONS
from skipjack.web.app import create_app
from skipjack.web.downloads import download_files
from skipjack.web.routes import data as data_route
from skipjack.web.paths import DATA_DIR
from skipjack.web.routes.data import results_listing

needs_results = pytest.mark.skipif(
    not (DATA_DIR / "results" / "GP26" / "county").exists(), reason="results not parsed"
)


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_the_listing_skips_unpublished_elections(tmp_path):
    for code in ("GP26", "GG26"):
        county = tmp_path / "results" / code / "county"
        county.mkdir(parents=True)
        (county / "01-allegany-dem.csv").write_text("x")
        (county / "01-allegany-dem_provenance.json").write_text("{}")
    blocks = results_listing(tmp_path)
    assert [b["code"] for b in blocks] == ["GP26"]
    assert blocks[0]["files"] == [
        {
            "name": "01-allegany-dem",
            "path": "results/GP26/county/01-allegany-dem.csv",
            "provenance": "results/GP26/county/01-allegany-dem_provenance.json",
        }
    ]
    assert results_listing(tmp_path / "nowhere") == []


@needs_results
def test_the_data_page_lists_each_election_with_its_provenance(client):
    html = client.get("/data/").text
    assert "Election results" in html
    assert "GP26" in html and "PG20" in html
    assert "/downloads/results/GP26/county/01-allegany-dem.csv" in html
    assert "/downloads/results/GP26/county/01-allegany-dem_provenance.json" in html


@needs_results
def test_a_results_csv_and_its_sidecar_download(client):
    csv = client.get("/downloads/results/GP26/county/01-allegany-dem.csv")
    assert csv.status_code == 200 and csv.text.strip()
    sidecar = client.get("/downloads/results/GP26/county/01-allegany-dem_provenance.json")
    assert sidecar.status_code == 200 and sidecar.json()["election"] == "GP26"


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/voter_registration/source_discrepancies.csv",
        "GP26/%2e%2e/%2e%2e/voter_registration/source_discrepancies.csv",
        "GG26/county/01-allegany-dem.csv",
        "GP26/county/missing.csv",
        "GP26",
    ],
)
def test_paths_outside_the_published_results_are_404(client, path):
    assert client.get(f"/downloads/results/{path}").status_code == 404


@needs_results
def test_the_data_page_lists_the_statewide_csvs_and_the_contest_page_links_its_own(client):
    html = client.get("/data/").text
    assert "Statewide races by county" in html
    assert "/downloads/statewide/GP26-governor-dem.csv" in html
    page = client.get("/results/gp26/governor-lt-governor-dem/").text
    assert 'href="/downloads/statewide/GP26-governor-dem.csv"' in page
    # the county index is no contest: no file
    assert "/downloads/statewide/" not in client.get("/results/gp26/county/").text


@needs_results
def test_a_statewide_csv_downloads_as_a_wide_table(client):
    r = client.get("/downloads/statewide/GP26-governor-dem.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    header, first = r.text.splitlines()[:2]
    assert header.startswith("jurisdiction,") and header.endswith(",others,total")
    assert first.startswith("Allegany,")


@pytest.mark.parametrize(
    "file", ["GP26-sheriff.csv", "GP26-governor.csv", "XX99-governor.csv", "..%2Fskipjack.db"]
)
def test_an_unknown_statewide_file_is_404(client, file):
    assert client.get(f"/downloads/statewide/{file}").status_code == 404


def test_gzipped_files_are_offered_with_their_sidecars_only_when_published(tmp_path, monkeypatch):
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", False)
    for code in ("PG24", "GG26"):
        d = tmp_path / "results" / code / "precinct"
        d.mkdir(parents=True)
        (d / "15-kent-all.csv.gz").write_bytes(gzip.compress(b"x\n"))
        (d / "15-kent-all_provenance.json").write_text("{}")
        (d / "15-kent-all.csv.gz.tmp").write_bytes(b"")
    assert [p.as_posix() for p in download_files(tmp_path)] == [
        "results/PG24/precinct/15-kent-all.csv.gz",
        "results/PG24/precinct/15-kent-all_provenance.json",
    ]


def test_the_listing_offers_precinct_and_question_files(tmp_path):
    base = tmp_path / "results" / "PG24"
    for relative in ("county/15-kent-all.csv", "precinct/15-kent-all.csv.gz",
                     "questions/county/15-kent.csv", "questions/precinct/15-kent.csv.gz"):  # fmt: skip
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        (base / relative).write_text("x")
    block = results_listing(tmp_path)[0]
    assert block["precinct_files"] == [
        {
            "name": "15-kent-all",
            "path": "results/PG24/precinct/15-kent-all.csv.gz",
            "provenance": "results/PG24/precinct/15-kent-all_provenance.json",
        }
    ]
    assert block["question_files"] == [
        {
            "name": "15-kent",
            "path": "results/PG24/questions/county/15-kent.csv",
            "provenance": "results/PG24/questions/county/15-kent_provenance.json",
        },
        {
            "name": "15-kent",
            "path": "results/PG24/questions/precinct/15-kent.csv.gz",
            "provenance": "results/PG24/questions/precinct/15-kent_provenance.json",
        },
    ]


def test_a_gzipped_file_is_served_as_gzip(tmp_path, monkeypatch):
    target = tmp_path / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(gzip.compress(b"election\nPG24\n", mtime=0))
    monkeypatch.setattr(data_route, "DATA_DIR", tmp_path)
    resp = TestClient(create_app()).get("/downloads/results/PG24/precinct/15-kent-all.csv.gz")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert gzip.decompress(resp.content) == b"election\nPG24\n"


def test_the_data_page_links_the_precinct_and_question_files(tmp_path, monkeypatch):
    base = tmp_path / "results" / "PG24"
    for relative in ("county/15-kent-all.csv", "precinct/15-kent-all.csv.gz",
                     "questions/county/15-kent.csv", "questions/precinct/15-kent.csv.gz",
                     "questions/check/statewide.csv"):  # fmt: skip
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        (base / relative).write_text("x")
    monkeypatch.setattr(data_route, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_route, "statewide_listing", lambda: [])
    html = TestClient(create_app()).get("/data/").text
    assert "By precinct (1 gzipped CSVs" in html
    assert "/downloads/results/PG24/precinct/15-kent-all.csv.gz" in html
    assert "/downloads/results/PG24/precinct/15-kent-all_provenance.json" in html
    assert "Ballot questions (2 files" in html
    assert ">county/15-kent</a>" in html and ">precinct/15-kent</a>" in html
    assert "/downloads/results/PG24/questions/precinct/15-kent_provenance.json" in html
    assert "check/statewide" not in html  # only the folders the catalog lists


def test_gzipped_files_outside_results_stay_unpublished(tmp_path):
    """turnout_model's .csv.gz draw files are not downloads; only results/ offers .csv.gz."""
    d = tmp_path / "turnout_model" / "gg26"
    d.mkdir(parents=True)
    (d / "draws.csv.gz").write_bytes(gzip.compress(b"x\n"))
    (d / "forecast_county.csv").write_text("x")
    assert [p.as_posix() for p in download_files(tmp_path)] == [
        "turnout_model/gg26/forecast_county.csv"
    ]
