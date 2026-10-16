"""The results downloads: the Data page listing and the gated download route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skipjack.web.app import create_app
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
