"""Smoke tests for the web routes against the built skipjack.db."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from skipjack.web.app import app
from skipjack.web.db import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="skipjack.db not built")
client = TestClient(app)


def embedded_data(html: str) -> dict:
    m = re.search(r'<script id="county-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_statewide_county_page():
    r = client.get("/voters/county")
    assert r.status_code == 200
    data = embedded_data(r.text)
    names = [s["name"] for s in data["series"]]
    assert names[:3] == ["Democratic", "Republican", "Unaffiliated"]
    i = data["dates"].index("2026-08")
    assert data["totals"][i] == 4_322_671
    # UNAF (through 2024) and UNA (2025 on) are one continuous series.
    una = data["series"][2]["values"]
    assert una[data["dates"].index("2024-12")] and una[i]


def test_county_with_apostrophe():
    r = client.get("/voters/county/prince-georges/")
    assert r.status_code == 200
    assert "<h1>Prince George&#39;s" in r.text or "<h1>Prince George's" in r.text


def test_unknown_county_is_not_found():
    assert client.get("/voters/county/atlantis/").status_code == 404
    assert client.get("/voters/month/1999-01/").status_code == 404


def test_old_query_string_links_redirect():
    r = client.get("/voters/county", params={"county": "Kent"}, follow_redirects=True)
    assert r.status_code == 200 and r.url.path == "/voters/county/kent/"
    r = client.get("/voters/", params={"report_date": "2019-06"}, follow_redirects=True)
    assert r.status_code == 200 and r.url.path == "/voters/month/2019-06/"


def test_month_pages_show_that_month():
    html = client.get("/voters/month/2019-06/").text
    assert "as of 2019-06" in html
    assert 'value="/voters/month/2019-06/" selected' in html
    assert "as of 2026-08" in client.get("/voters/").text


def test_voters_table_links_to_county_pages():
    r = client.get("/voters/")
    assert 'href="/voters/county/montgomery/"' in r.text
    assert "data-sortable" in r.text


def test_overall_series():
    state = embedded_data(client.get("/voters/county").text)["overall"]
    i = embedded_data(client.get("/voters/county").text)["dates"].index("2026-08")
    assert state["inactive"][i] == 288_388
    assert state["party_switches"][i] == 11_573
    assert state["new_registrations"][i] == 41_106
    assert state["removals"][i] == 40_037

    r = client.get("/voters/county/kent/")
    county = embedded_data(r.text)["overall"]
    assert county["new_registrations"] is None  # only reported statewide
    assert "only reported statewide" in r.text
    assert county["inactive"][i] == 820
