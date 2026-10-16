"""Tests for the results pipeline: registry, file names, coded-layout reader, checks, fetch."""

from __future__ import annotations

import json

import pytest

from skipjack.pipeline import fetch, results
from skipjack.pipeline.provenance import SourceRecord, append_to_manifest, read_manifest
from skipjack.pipeline.vrar import COUNTY_NORMALIZE

BOM = "﻿"
PRIMARY_HEAD = (
    '"Office Name","Office District","Candidate Name","Party","Winner","Early Votes",'
    '"Election Night Votes","Mail-In Ballot 1 Votes","Provisional Votes",'
    '"Mail-In Ballot 2 Votes","Total Votes" \n'
)
GENERAL_HEAD = (
    '"Office Name","Office District","Candidate Name","Party","Winner","Write-In?",'
    '"Early Votes","Early Votes Against","Election Night Votes","Election Night Votes Against",'
    '"Mail-In Ballot 1 Votes","Mail-In Ballot 1 Votes Against","Provisional Votes",'
    '"Provisional Votes Against","Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against",'
    '"Total Votes","Total Votes Against" \n'
)
PRIMARY_ROWS = (
    '"Governor / Lt. Governor","","Wes Moore and Aruna Miller","DEM","Y","10","20","30","4","5","69" \n'
    '"Governor / Lt. Governor","","Eric S. Felber and LaTrece Hawkins Lytes","DEM","","1","2","3","0","1","7" \n'
    '"U.S. Congress","06","April McClain Delaney","DEM","Y","1","1","1","1","1","5" \n'
    '"U.S. Congress","06","Someone Else","DEM","Y","0","0","0","0","0","0" \n'
)
BREAKDOWN_HEAD = (
    '"County","County Name","Office Name","Office District","Candidate Name","Party","Winner",'
    '"Congressional District 1","Congressional District 2" \n'
)


def primary_csv(rows: str = PRIMARY_ROWS) -> bytes:
    return (BOM + PRIMARY_HEAD + rows).encode()


def test_registry():
    assert set(results.RESULTS_ELECTIONS) == {"GP22", "GG22", "PP24", "PG24", "GP26", "GG26"}
    gg26 = results.RESULTS_ELECTIONS["GG26"]
    assert gg26.published is False
    assert all(e.published for c, e in results.RESULTS_ELECTIONS.items() if c != "GG26")
    assert all(e.layout == "coded" for e in results.RESULTS_ELECTIONS.values())
    assert results.RESULTS_ELECTIONS["GP26"].kind == "primary"
    assert results.RESULTS_ELECTIONS["PG24"].kind == "general"
    labels = [lab for lab, _ in results.RESULTS_ELECTIONS["PG24"].vote_types]
    assert labels[0] == "Early" and "Mail-In Ballot 2" in labels


def test_county_names_are_canonical():
    names = set(results.COUNTY_CODES.values())
    assert len(results.COUNTY_CODES) == 24
    assert names == set(COUNTY_NORMALIZE.values())
    assert results.COUNTY_CODES["19"] == "St. Mary's"
    assert results.COUNTY_CODES["03"] == "Baltimore City"


def test_urls_and_source_ids():
    gp26 = results.RESULTS_ELECTIONS["GP26"]
    files = results.county_files(gp26)
    county = [f for f in files if f.role == "county"]
    assert len(county) == 72
    first = county[0]
    assert first.url == (
        "https://elections.maryland.gov/elections/2026/election_data/GP26_01DemocraticResults.csv"
    )
    assert first.source_id == "sbe-results-GP26-county-01-dem"
    by_id = {f.source_id: f for f in files}
    assert by_id["sbe-results-GP26-county-19-non"].url.endswith("GP26_19Non-PartisanResults.csv")
    assert by_id["sbe-results-GP26-county-19-non"].county == "St. Mary's"
    assert by_id["sbe-results-GP26-check-rep"].url.endswith(
        "GP26_CongressionalBreakDownRepublican.csv"
    )
    assert by_id["sbe-results-GP26-check-non"].url.endswith(
        "GP26_CongressionalBreakDownNon-Partisian.csv"
    )
    pg24 = results.county_files(results.RESULTS_ELECTIONS["PG24"])
    assert len([f for f in pg24 if f.role == "county"]) == 24
    ids = {f.source_id: f for f in pg24}
    assert ids["sbe-results-PG24-county-24-all"].url == (
        "https://elections.maryland.gov/elections/archive/2024/election_data/PG24_24CountyResults.csv"
    )
    assert ids["sbe-results-PG24-check-all"].url.endswith("PG24_CongressionalBreakDown.csv")
    assert len(pg24) == 25


def test_read_coded_primary():
    frame = results._read_coded(primary_csv(), results.RESULTS_ELECTIONS["GP26"], "Allegany", "dem")
    assert frame.columns[:9] == [
        "election", "county", "party_file", "office", "district", "candidate", "party",
        "winner", "write_in",
    ]  # fmt: skip
    assert "early_votes" in frame.columns and "mail_in_2_votes" in frame.columns
    assert not any(c.endswith("_against") for c in frame.columns)
    assert frame.columns[-1] == "total_votes"
    assert frame.height == 4
    moore = frame.row(0, named=True)
    assert moore["election"] == "GP26" and moore["county"] == "Allegany"
    assert moore["district"] == "" and moore["winner"] == 1 and moore["write_in"] == 0
    assert moore["total_votes"] == 69 and moore["election_night_votes"] == 20
    # several winners in one contest are kept as given
    assert frame.filter(frame["office"] == "U.S. Congress")["winner"].to_list() == [1, 1]
    assert frame["district"].to_list()[2] == "06"


def test_read_coded_general_has_against_and_write_in():
    row = (
        '"Judge","","Jane Roe","","","","3","1","3","1","3","1","0","0","1","0","10","3" \n'
        '"Governor","","Write-In","","","Y","1","","1","","1","","0","","0","","3","" \n'
    )
    data = (BOM + GENERAL_HEAD + row).encode()
    frame = results._read_coded(data, results.RESULTS_ELECTIONS["PG24"], "Kent", "all")
    assert frame["write_in"].to_list() == [0, 1]
    assert frame["early_against"].to_list() == [1, None]
    assert frame["total_against"].to_list() == [3, None]
    assert frame.columns[-2:] == ["total_votes", "total_against"]


def test_row_total_check_fails_on_bad_row():
    bad = PRIMARY_ROWS.replace('"4","5","69"', '"4","5","70"')
    with pytest.raises(results.ResultsParseError, match="Wes Moore"):
        results._read_coded(primary_csv(bad), results.RESULTS_ELECTIONS["GP26"], "Kent", "dem")


def test_row_total_check_against():
    row = '"Judge","","Jane Roe","","","","3","1","3","1","3","1","0","0","1","0","10","4" \n'
    with pytest.raises(results.ResultsParseError, match="against"):
        results._read_coded(
            (BOM + GENERAL_HEAD + row).encode(),
            results.RESULTS_ELECTIONS["PG24"],
            "Kent",
            "all",
        )


def _frames(election, rows_by_county, party_file="dem"):
    return {
        c: results._read_coded(primary_csv(r), election, c, party_file)
        for c, r in rows_by_county.items()
    }


def breakdown(rows: list[str], general=False) -> bytes:
    head = BREAKDOWN_HEAD
    if general:
        head = '"Vote Type (FOR=1,AGAINST=2)",' + head.replace('"Winner",', '"Winner","Write-In?",')
    return (BOM + head + "".join(rows)).encode()


def test_statewide_check_passes_and_fails():
    e = results.RESULTS_ELECTIONS["GP26"]
    frames = _frames(e, {"Allegany": PRIMARY_ROWS, "Kent": PRIMARY_ROWS})
    good = breakdown(
        [
            '"00","State of Maryland","Governor / Lt. Governor","","Wes Moore and Aruna Miller","DEM","Y","100","38" \n',
            '"00","State of Maryland","Governor / Lt. Governor","","Eric S. Felber and LaTrece Hawkins Lytes","DEM","","10","4" \n',
        ]
    )
    n = results._check_statewide(e, "dem", list(frames.values()), good)
    assert n == 2
    bad = good.replace(b'"100"', b'"101"')
    with pytest.raises(results.ResultsParseError, match="Wes Moore"):
        results._check_statewide(e, "dem", list(frames.values()), bad)
    missing = breakdown(
        [
            '"00","State of Maryland","Governor / Lt. Governor","","Wes Moore and Aruna Miller","DEM","Y","100","38" \n'
        ]
    )
    with pytest.raises(results.ResultsParseError, match="no statewide row"):
        results._check_statewide(e, "dem", list(frames.values()), missing)


def test_statewide_check_general_uses_for_rows_only():
    e = results.RESULTS_ELECTIONS["PG24"]
    row = '"President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","1","","2","","3","","1","","3","","10","" \n'
    frames = [
        results._read_coded((BOM + GENERAL_HEAD + row).encode(), e, c, "all")
        for c in ("Allegany", "Kent")
    ]
    good = breakdown(
        [
            '"1","00","State of Maryland","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","15","5" \n',
            '"2","00","State of Maryland","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","999","1" \n',
        ],
        general=True,
    )
    assert results._check_statewide(e, "all", frames, good) == 1


# ---- parse and fetch on a fake archive -----------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    arch = tmp_path / "archives"
    manifest = tmp_path / "manifest.csv"
    monkeypatch.setattr(results, "ARCHIVES_DIR", arch)
    monkeypatch.setattr(results, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(fetch, "ARCHIVES_DIR", arch)
    return tmp_path


def archive(env, election, file, data: bytes):
    dest = env / "archives" / "sbe" / "results" / election.code / file.url.rsplit("/", 1)[-1]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    append_to_manifest(
        results.MANIFEST_PATH,
        SourceRecord.create(file.source_id, file.url, "SBE", dest, document_title=file.source_id),
    )


GOOD_BREAKDOWN = breakdown(
    [
        '"00","State of Maryland","Governor / Lt. Governor","","Wes Moore and Aruna Miller","DEM","Y","1656","0" \n',
        '"00","State of Maryland","Governor / Lt. Governor","","Eric S. Felber and LaTrece Hawkins Lytes","DEM","","168","0" \n',
    ]
)


def fill(env, election, skip=(), breakdown_data=GOOD_BREAKDOWN):
    for f in results.county_files(election):
        if f.party_file == "non" or f.source_id in skip:
            continue
        if f.role == "county" and f.party_file == "dem":
            archive(env, election, f, primary_csv())
        elif f.role == "check" and f.party_file == "dem":
            archive(env, election, f, breakdown_data)
        elif f.party_file == "rep":
            archive(
                env,
                election,
                f,
                primary_csv('"U.S. Senator","","X","REP","Y","0","0","0","0","0","0" \n')
                if f.role == "county"
                else breakdown(
                    ['"00","State of Maryland","U.S. Senator","","X","REP","Y","0","0" \n']
                ),
            )


def test_parse_writes_everything_when_checks_pass(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e)
    written, failed = results.results_parse("GP26", data_dir=env / "data")
    assert failed == []
    assert written == 48  # 24 counties x dem, rep; the missing non files are skipped
    out = env / "data" / "results" / "GP26" / "county" / "19-st-marys-dem.csv"
    assert out.exists()
    side = json.loads(out.with_name("19-st-marys-dem_provenance.json").read_text())
    assert side["election"] == "GP26" and side["family"] == "results"
    assert side["geography"] == "county"
    assert side["source_id"] == "sbe-results-GP26-county-19-dem"
    assert side["checks"]["row_totals"] == "ok"
    assert "statewide_check" in side["checks"]


def test_parse_is_all_or_nothing_on_checksum_mismatch(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e, breakdown_data=GOOD_BREAKDOWN.replace(b'"1656"', b'"1657"'))
    written, failed = results.results_parse("GP26", data_dir=env / "data")
    assert written == 0 and failed and "Wes Moore" in failed[0]
    assert not (env / "data" / "results" / "GP26").exists()


def test_parse_missing_required_file_fails_but_missing_non_is_fine(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e, skip={"sbe-results-GP26-county-05-dem"})
    written, failed = results.results_parse("GP26", data_dir=env / "data")
    assert written == 0
    assert len(failed) == 1 and "GP26_05DemocraticResults.csv" in failed[0]
    assert not (env / "data" / "results" / "GP26").exists()


def test_parse_skips_unpublished_and_skips_existing(env):
    assert results.results_parse("GG26", data_dir=env / "data") == (0, [])
    fill(env, results.RESULTS_ELECTIONS["GP26"])
    assert results.results_parse("GP26", data_dir=env / "data")[0] == 48
    assert results.results_parse("GP26", data_dir=env / "data")[0] == 0
    assert results.results_parse("GP26", force=True, data_dir=env / "data")[0] == 48


class FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def fake_client(bodies):
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


def test_fetch_file_expect_csv_rejects_html(tmp_path, monkeypatch):
    html = b"\xef\xbb\xbf<!DOCTYPE html><html></html>"
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: html))
    dest = tmp_path / "x.csv"
    assert fetch.fetch_file("http://x/y.csv", dest, expect_csv=True) is False
    assert not dest.exists()
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: primary_csv()))
    assert fetch.fetch_file("http://x/y.csv", dest, expect_csv=True) is True
    assert dest.exists()


def test_results_fetch(env, monkeypatch):
    def body(url):
        # a non-partisan file for one county does not exist: the SBE answers HTML
        if "Non-Partisan" in url or "Non-Partisian" in url:
            return b"<html>nope</html>"
        return primary_csv()

    monkeypatch.setattr(fetch.httpx, "Client", fake_client(body))
    records = results.results_fetch("GP26")
    # 24 x (dem, rep) + 2 breakdown files; the non files are not published
    assert len(records) == 50
    assert len(read_manifest(results.MANIFEST_PATH)) == 50
    assert (env / "archives/sbe/results/GP26/GP26_01DemocraticResults.csv").exists()
    # a second run downloads nothing

    def only_non(url):  # absent non-partisan files are probed again; nothing else is
        assert "Non-Parti" in url, url
        return b"<html></html>"

    monkeypatch.setattr(fetch.httpx, "Client", fake_client(only_non))
    assert results.results_fetch("GP26") == []


def test_fetch_unpublished_election_fetches_nothing(env, monkeypatch):
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda u: pytest.fail(u)))
    assert results.results_fetch("GG26") == []
