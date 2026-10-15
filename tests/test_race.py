"""Tests for estimated race: the slim input file and the aggregation of predictions.

All data is synthetic: fake names, six made-up voters, made-up probabilities. wru itself (R)
is not run here; its output is stood in for by a small predictions.csv.
"""

from __future__ import annotations

import json

import polars as pl
import pytest

from skipjack.pipeline import race as race_mod
from skipjack.pipeline import voterfile as vf
from skipjack.pipeline.voterfile import COUNTY_FIPS, FIXED_COLUMNS, VoterFileError

ELECTION = "11/05/2024-PG"
SNAPSHOT = "2026-08-12"

# id, county, party, surname, zip, (white, black, hispanic, asian, other)
VOTERS = [
    (1, "Allegany", "DEM", "ALPHA", "21502", (0.90, 0.05, 0.02, 0.02, 0.01)),
    (2, "Allegany", "DEM", "BRAVO", "21502-1234", (0.20, 0.70, 0.05, 0.03, 0.02)),
    (3, "Allegany", "REP", "CHARLIE", "", (0.95, 0.02, 0.01, 0.01, 0.01)),
    (4, "Saint Mary's", "REP", "DELTA", "20650", (0.60, 0.30, 0.05, 0.03, 0.02)),
    (5, "Saint Mary's", "UNA", "ECHO", "20650", (0.10, 0.10, 0.30, 0.45, 0.05)),
    (6, "Saint Mary's", "DEM", "FOXTROT", "20650", (0.00, 0.60, 0.40, 0.00, 0.00)),
]
RACE_COLUMNS = list(vf.RACES)


def line(vtr, county, party, surname, zip_code):
    fields = dict.fromkeys(FIXED_COLUMNS, "")
    fields.update(
        VTR_ID=str(vtr), LastName=surname, FirstName="TESTFIRST", MiddleName="M",
        ResidentialZip=zip_code, StatusCode="A", Party=party, Gender="Female",
        Congressional="06", Legislative="01A", BirthDate="03/15/1980",
        StateRegistrationDate="01/01/2010", CountyRegistrationDate="01/01/2010",
    )  # fmt: skip
    return "\t".join([*fields.values(), "history", county, ""])


@pytest.fixture
def voter_dir(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    header = "\t".join([*FIXED_COLUMNS, ELECTION, "County", ""])
    rows = [line(v, c, p, s, z) for v, c, p, s, z, _ in VOTERS]
    (d / "List_Part_1-2026-08-12.txt").write_text("\n".join([header, *rows]) + "\n")
    (d / "List_Part_1-2026-08-12Readme.txt").write_text(
        f"Total Records :\t{len(VOTERS)}\n\nDate          :\t08/12/2026\n"
    )
    return d


def write_predictions(directory, voters=VOTERS, total=1.0):
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["vtr_id,pred_whi,pred_bla,pred_his,pred_asi,pred_oth,geo_level"]
    for vtr, *_, probs in voters:
        probs = [*probs[:-1], probs[-1] + (total - sum(probs))]
        lines.append(f"{vtr}," + ",".join(f"{p:.6f}" for p in probs) + ",zcta")
    (directory / "predictions.csv").write_text("\n".join(lines) + "\n")
    (directory / "run.json").write_text(
        json.dumps(
            {
                "method": "BISG (test)",
                "names_used": "surname, first, middle",
                "conditioned_on": "ZIP code tabulation area",
                "census_year": "2020",
                "wru_version": "0.0-test",
                "share_zcta": 0.83,
                "share_county_fallback": 0.17,
                "share_surname_on_2010_list": 1.0,
            }
        )  # fmt: skip
    )
    return directory


@pytest.fixture
def census_csv(tmp_path):
    rows = ["county_fips,county,vap_total,vap_white,vap_black,vap_hispanic,vap_asian,vap_other"]
    rows += [f"{code},{name},1000,500,300,100,50,50" for name, code in COUNTY_FIPS.items()]
    path = tmp_path / "census" / "md_county_vap_2020.csv"
    path.parent.mkdir()
    path.write_text("\n".join(rows) + "\n")
    return path


def run_race(voter_dir, tmp_path, predictions, census_csv, below=2, **kw):
    return vf.run(
        voter_dir,
        tmp_path / "out",
        suppress_below=below,
        manifest_path=tmp_path / "manifest.csv",
        data_dir=tmp_path / "data",
        race_predictions=predictions,
        census_csv=census_csv,
        **kw,
    )


def rows_of(tmp_path, metric):
    return json.loads((tmp_path / "out" / SNAPSHOT / f"{metric}.json").read_text())["rows"]


def cell(rows, **where):
    found = [r for r in rows if all(r[k] == v for k, v in where.items())]
    assert len(found) == 1, (where, found)
    return found[0]


# --- the slim input file -------------------------------------------------------------


def test_input_has_only_the_estimate_fields(voter_dir, tmp_path):
    summary = race_mod.build_input(voter_dir, tmp_path / "race")
    df = pl.read_csv(summary["input_path"], infer_schema=False)
    assert df.columns == race_mod.INPUT_COLUMNS
    assert df.height == len(VOTERS) == summary["rows_written"]
    by_id = {r["vtr_id"]: r for r in df.iter_rows(named=True)}
    assert by_id["1"]["county"] == "001" and by_id["4"]["county"] == "037"  # Saint Mary's fixed
    assert by_id["1"]["state"] == "MD" and by_id["1"]["surname"] == "ALPHA"
    assert by_id["2"]["zcta"] == "21502"  # ZIP+4 reduced to five digits
    assert by_id["3"]["zcta"] == ""
    assert summary["blank_zcta"] == 1


def test_input_is_refused_inside_the_repository_or_the_voter_file(voter_dir):
    with pytest.raises(VoterFileError, match="repository"):
        race_mod.build_input(voter_dir, vf.PROJECT_ROOT / "race_scratch")
    with pytest.raises(VoterFileError, match="voter file directory"):
        race_mod.build_input(voter_dir, voter_dir / "race")


# --- predictions into aggregates -----------------------------------------------------


def test_expected_counts_are_summed_before_rounding(voter_dir, tmp_path, census_csv):
    run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred"), census_csv, below=1)
    rows = rows_of(tmp_path, "race_by_party")
    allegany = cell(rows, county="Allegany", party_group="ALL", race="white")
    assert (allegany["count"], allegany["voters"]) == (2, 3)  # 2.05 expected of 3
    # Asian: 0.06 in Allegany and 0.48 in St. Mary's round to 0, but statewide is 0.54 -> 1.
    assert cell(rows, county="Allegany", party_group="ALL", race="asian")["count"] == 0
    assert cell(rows, county="St. Mary's", party_group="ALL", race="asian")["count"] == 0
    assert cell(rows, county="Maryland", party_group="ALL", race="asian")["count"] == 1
    # The all-party row sums the parties before rounding too.
    dem = cell(rows, county="Maryland", party_group="DEM", race="black")
    assert dem["count"] == 1 and dem["voters"] == 3  # 0.05 + 0.70 + 0.60 = 1.35


def test_race_metrics_cover_age_and_district(voter_dir, tmp_path, census_csv):
    run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred"), census_csv)
    age = rows_of(tmp_path, "race_by_age")
    assert (
        cell(age, county="Maryland", party_group="ALL", age_band="45-54", race="white")["voters"]
        == 6
    )
    districts = rows_of(tmp_path, "race_by_district")
    assert cell(districts, district_type="congressional", district="06", race="black")["count"] == 2


def test_each_race_row_of_a_group_shares_one_denominator(voter_dir, tmp_path, census_csv):
    run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred"), census_csv, below=1)
    rows = rows_of(tmp_path, "race_by_party")
    group = [r for r in rows if r["county"] == "Maryland" and r["party_group"] == "ALL"]
    assert {r["voters"] for r in group} == {6}
    assert abs(sum(r["count"] for r in group) - 6) <= 2  # rounding only


def test_small_race_cells_are_hidden_at_the_default_threshold(voter_dir, tmp_path, census_csv):
    run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred"), census_csv, below=10)
    for metric in ("race_by_party", "race_by_age", "race_by_district"):
        for r in rows_of(tmp_path, metric):
            for value in ("count", "voters"):
                assert r[value] is None or r[value] == 0 or r[value] >= 10, (metric, r)


def test_outputs_carry_no_identifiers_or_probabilities(voter_dir, tmp_path, census_csv):
    run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred"), census_csv)
    out = tmp_path / "out" / SNAPSHOT
    text = "".join(p.read_text() for p in out.glob("*.json"))
    for forbidden in (
        "vtr_id",
        "pred_",
        "ALPHA",
        "TESTFIRST",
        "21502",
        "VTR_ID",
        "predictions.csv",
    ):
        assert forbidden not in text, forbidden
    for metric in ("race_by_party", "race_by_age", "race_by_district"):
        payload = json.loads((out / f"{metric}.json").read_text())
        allowed = set(payload["dims"]) | set(payload["values"])
        assert all(set(r) <= allowed for r in payload["rows"])


def test_snapshot_records_the_method_and_calibration(voter_dir, tmp_path, census_csv):
    run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred"), census_csv)
    out = tmp_path / "out" / SNAPSHOT
    race = json.loads((out / "snapshot.json").read_text())["race"]
    assert race["wru_version"] == "0.0-test" and race["unmatched_share"] == 0
    assert race["limitations"] and "estimated" in race["limitations"][0].lower()
    state = race["calibration"]["Maryland"]
    assert state["white"]["census_vap_pct"] == 50.0
    assert state["white"]["estimated_pct"] == pytest.approx(100 * 2.75 / 6, abs=0.1)
    assert set(race["calibration"]["counties"]) == {"Allegany", "St. Mary's"}
    # Provenance names the predictions' hash and the Census source, not a home directory.
    sidecar = (out / "snapshot_provenance.json").read_text()
    assert race["predictions_sha256"] in sidecar and "/Users/" not in sidecar
    assert "census-pl94171-2020-md-county-vap" in (tmp_path / "manifest.csv").read_text()


def test_a_run_without_predictions_has_no_race_metrics(voter_dir, tmp_path):
    vf.run(
        voter_dir, tmp_path / "out", suppress_below=2,
        manifest_path=tmp_path / "manifest.csv", data_dir=tmp_path / "data",
    )  # fmt: skip
    out = tmp_path / "out" / SNAPSHOT
    assert not list(out.glob("race_*.json"))
    assert "race" not in json.loads((out / "snapshot.json").read_text())


def test_predictions_for_another_file_are_refused(voter_dir, tmp_path, census_csv):
    other = [(900 + v[0], *v[1:]) for v in VOTERS]
    with pytest.raises(VoterFileError, match="no race prediction"):
        run_race(voter_dir, tmp_path, write_predictions(tmp_path / "pred", other), census_csv)


def test_predictions_that_do_not_sum_to_one_are_refused(tmp_path):
    with pytest.raises(VoterFileError, match="sum to 1"):
        vf.read_race_predictions(write_predictions(tmp_path / "pred", total=1.2))


def test_missing_run_record_is_refused(tmp_path):
    d = write_predictions(tmp_path / "pred")
    (d / "run.json").unlink()
    with pytest.raises(VoterFileError, match="run.json"):
        vf.read_race_predictions(d)


# --- the page ------------------------------------------------------------------------


@pytest.fixture
def race_db(voter_dir, tmp_path, census_csv, monkeypatch):
    """An in-memory database holding the synthetic snapshot with race estimates."""
    import sqlite3
    from contextlib import contextmanager

    from skipjack.models import SCHEMA_SQL
    from skipjack.pipeline.build_db import load_voter_file
    from skipjack.web.routes import race as race_route

    vf.run(
        voter_dir,
        tmp_path / "data" / "voter_file",
        suppress_below=2,
        manifest_path=tmp_path / "manifest.csv",
        data_dir=tmp_path / "data",
        race_predictions=write_predictions(tmp_path / "pred"),
        census_csv=census_csv,
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    load_voter_file(conn, tmp_path / "data")

    @contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(race_route, "get_db", fake_get_db)
    return conn


def test_the_race_page_says_estimated_and_hides_small_cells(race_db):
    from fastapi.testclient import TestClient

    from skipjack.web.app import app

    html = TestClient(app).get("/voters/race/").text
    assert "Estimated race of Maryland voters" in html
    assert "estimates" in html and "never a fact about a person" in html
    assert "Other or multiracial" in html
    assert "Race is estimated, not recorded" in html  # the limitations come from the snapshot
    assert "&lt;2" in html  # a hidden cell is drawn as "<2", not as a number


def test_build_race_reads_shares_and_calibration(race_db):
    from skipjack.web.race import build_race, snapshots_with_race

    assert snapshots_with_race(race_db) == [SNAPSHOT]
    data = build_race(race_db)
    assert data["statewide"]["voters"] == 6
    white = next(c for c in data["calibration"] if c["race"] == "white")
    assert white["census"] == 50.0 and white["difference"] == pytest.approx(45.8 - 50.0, abs=0.1)
    assert [c["county"] for c in data["counties"]] == ["Allegany", "St. Mary's", "Maryland"]


def test_without_race_the_page_is_absent(tmp_path):
    import sqlite3

    from skipjack.models import SCHEMA_SQL
    from skipjack.web.race import build_race

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    assert build_race(conn) is None
