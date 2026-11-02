"""Tests for the turnout forecast page, read from the committed model outputs in
data/turnout_model/gg26. The checks are consistency checks against those CSVs, so a new
model run does not break them."""

from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

from skipjack.web import forecast
from skipjack.web.app import create_app

needs_model = pytest.mark.skipif(not forecast.available(), reason="no turnout model outputs")


def _statewide(scenario: str, measure: str) -> float:
    with (forecast.MODEL_DIR / "forecast_statewide.csv").open(newline="") as f:
        row = next(
            r for r in csv.DictReader(f) if (r["scenario"], r["measure"]) == (scenario, measure)
        )
    return float(row["mean"])


@needs_model
def test_the_forecast_reads_the_three_scenarios_in_order():
    f = forecast.build_forecast()
    assert [s["key"] for s in f["scenarios"]] == ["low", "central", "high"]
    assert f["central"]["ballots"] == _statewide("central", "ballots")
    low, central, high = (s["ballots"] for s in f["scenarios"])
    assert low <= central <= high
    assert f["range"]["low"] <= low and high <= f["range"]["high"]


@needs_model
def test_the_forecast_has_every_county_and_party():
    f = forecast.build_forecast()
    assert len(f["counties"]) == 24
    assert all(c["rate_central"] is not None and c["share_mail"] is not None for c in f["counties"])
    assert sum(c["ballots_central"] for c in f["counties"]) == pytest.approx(
        f["central"]["ballots"], abs=24
    )
    assert sum(p["share"]["central"] for p in f["parties"]) == pytest.approx(100, abs=0.01)


@needs_model
def test_the_banked_ballots_add_up():
    e = forecast.build_forecast()["early"]
    assert e["banked"] == e["early"] + e["mail_projected"]
    assert e["mail_projected"] >= e["mail_received"]


@needs_model
def test_the_forecast_page_and_report_render():
    client = TestClient(create_app())
    r = client.get("/turnout/forecast/")
    assert r.status_code == 200
    assert f"{round(_statewide('central', 'ballots')):,}" in r.text
    assert "/downloads/turnout_model/gg26/forecast_county.csv" in r.text
    assert client.get("/downloads/turnout_model/gg26/forecast_county.csv").status_code == 200
    if forecast.report_available():
        assert client.get("/turnout/forecast/report/").status_code == 200


@needs_model
def test_the_nav_offers_the_forecast_under_turnout():
    text = TestClient(create_app()).get("/about").text
    turnout = text[text.index("<summary>Turnout</summary>") :]
    turnout = turnout[: turnout.index("</details>")]
    assert 'href="/turnout/"' in turnout and 'href="/turnout/forecast/"' in turnout


def test_the_forecast_is_not_read_as_an_election_slug(monkeypatch):
    monkeypatch.setattr(forecast, "available", lambda: False)
    assert TestClient(create_app()).get("/turnout/forecast/").status_code == 404
