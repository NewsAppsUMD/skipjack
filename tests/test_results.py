"""Tests for the results pipeline: registry, file names, coded-layout reader, checks, fetch."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
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


def read_coded(*args):
    """The frame a coded-layout file reads to (the reader also returns its row-total
    exceptions, which only the exception tests look at)."""
    return results._read_coded(*args).frame


def read_named(*args):
    return results._read_named(*args).frame


def test_registry():
    assert set(results.RESULTS_ELECTIONS) == {
        "GP22", "GG22", "PP24", "PG24", "GP26", "GG26", "PG20", "PP20",
        "GP10", "GG10", "PP12", "PG12", "GP14", "GG14", "PP16", "PG16", "GP18", "GG18",
    }  # fmt: skip
    gg26 = results.RESULTS_ELECTIONS["GG26"]
    assert gg26.published is False
    assert all(e.published for c, e in results.RESULTS_ELECTIONS.items() if c != "GG26")
    named = {c for c, e in results.RESULTS_ELECTIONS.items() if e.layout == "named"}
    assert named == {c for c, e in results.RESULTS_ELECTIONS.items() if e.year <= 2020}
    assert results.RESULTS_ELECTIONS["GP22"].layout == "coded"
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
    frame = read_coded(primary_csv(), results.RESULTS_ELECTIONS["GP26"], "Allegany", "dem")
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
    frame = read_coded(data, results.RESULTS_ELECTIONS["PG24"], "Kent", "all")
    assert frame["write_in"].to_list() == [0, 1]
    assert frame["early_against"].to_list() == [1, None]
    assert frame["total_against"].to_list() == [3, None]
    assert frame.columns[-2:] == ["total_votes", "total_against"]


def test_row_total_check_fails_on_bad_row():
    bad = PRIMARY_ROWS.replace('"4","5","69"', '"4","5","70"')
    with pytest.raises(results.ResultsParseError, match="Wes Moore"):
        read_coded(primary_csv(bad), results.RESULTS_ELECTIONS["GP26"], "Kent", "dem")


def test_row_total_check_against():
    row = '"Judge","","Jane Roe","","","","3","1","3","1","3","1","0","0","1","0","10","4" \n'
    with pytest.raises(results.ResultsParseError, match="against"):
        read_coded(
            (BOM + GENERAL_HEAD + row).encode(),
            results.RESULTS_ELECTIONS["PG24"],
            "Kent",
            "all",
        )


def _frames(election, rows_by_county, party_file="dem"):
    return {
        c: read_coded(primary_csv(r), election, c, party_file) for c, r in rows_by_county.items()
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
    assert n == (2, 0, [])
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
        read_coded((BOM + GENERAL_HEAD + row).encode(), e, c, "all") for c in ("Allegany", "Kent")
    ]
    good = breakdown(
        [
            '"1","00","State of Maryland","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","15","5" \n',
            '"2","00","State of Maryland","Judge Elsewhere","","Some Judge","BOT","Y","","999","1" \n',
        ],
        general=True,
    )
    assert results._check_statewide(e, "all", frames, good) == (1, 0, [])


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


# ---- the named layout: 2020 -----------------------------------------------------------------------

G20_HEAD = (
    '"Candidate Name","Party","Office Name","Office District","Winner","Write-In?",'
    '"Early Voting Votes","Early Voting Votes Against","Election Day Votes",'
    '"Election Day Votes Against","By Mail Votes","By Mail Votes Against","Prov. Votes",'
    '"Prov. Votes Against","By Mail 2 Votes","By Mail 2 Votes Against","Total Votes",'
    '"Total Votes Against"\n'
)
P20_HEAD = (
    "Candidate Name,Party,Office Name,Office District,Winner,Vote By Mail Votes,"
    "In Person Voting Votes,Provisional Votes,Total Votes\n"
)
G20_ROWS = (
    '"Donald J. Trump","REP","President - Vice Pres","","","","10","","20","","30","","4","","6","","70",""\n'
    '"Joe Biden","DEM","President - Vice Pres","","Y","","1","","2","","3","","0","","1","","7",""\n'
    '"Other Write-Ins","","President - Vice Pres","","","Y","0","","1","","0","","0","","0","","1",""\n'
)
ALL_STATE_HEAD = (
    '"County","Candidate Name","Party","Office Name","Office District","Winner","Write-In?",'
    '"Early Voting Votes","Early Voting Votes Against","Election Day Votes",'
    '"Election Day Votes Against","By Mail Votes","By Mail Votes Against","Prov. Votes",'
    '"Prov. Votes Against","By Mail 2 Votes","By Mail 2 Votes Against","Total Votes",'
    '"Total Votes Against"\n'
)
PG20 = results.RESULTS_ELECTIONS["PG20"]
PP20 = results.RESULTS_ELECTIONS["PP20"]


def g20_csv(rows: str = G20_ROWS) -> bytes:
    return (G20_HEAD + rows).encode()


def p20_csv(rows: str = "Joe Biden,DEM,President - Vice Pres,,Y,3,2,1,6\n") -> bytes:
    return (P20_HEAD + rows).encode()


def all_state(rows: list[str]) -> bytes:
    return (ALL_STATE_HEAD + "".join(rows)).encode()


GOOD_ALL_STATE = all_state(
    [
        '"00","Donald J. Trump","REP","President - Vice Pres","","","","20","","40","","60","","8","","12","","140",""\n',
        '"00","Joe Biden","DEM","President - Vice Pres","","Y","","2","","4","","6","","0","","2","","14",""\n',
        '"00","Other Write-Ins","","President - Vice Pres","","","Y","0","","2","","0","","0","","0","","2",""\n',
        # a county's own rows in the all-state file are not statewide rows
        '"03","Joe Biden","DEM","President - Vice Pres","","Y","","1","","1","","1","","0","","0","","3",""\n',
    ]
)


def test_registry_2020_vote_types_and_urls():
    assert [s for _, s in PG20.vote_types] == [
        "early_voting", "election_day", "by_mail", "prov", "by_mail_2",
    ]  # fmt: skip
    assert [lab for lab, _ in PG20.vote_types][3] == "Prov."
    assert [lab for lab, _ in PP20.vote_types] == [
        "Vote By Mail", "In Person Voting", "Provisional",
    ]  # fmt: skip
    assert PG20.base_url == "https://elections.maryland.gov/elections/archive/2020/election_data"
    assert PG20.election_day == "2020-11-03" and PP20.election_day == "2020-06-02"
    assert PG20.kind == "general" and PP20.kind == "primary"


def test_urls_2020():
    base = PG20.base_url
    g = {f.source_id: f for f in results.county_files(PG20)}
    assert len(g) == 25
    assert g["sbe-results-PG20-county-01-all"].url == f"{base}/Allegany_County_2020_General.csv"
    assert g["sbe-results-PG20-county-03-all"].url.endswith(
        "/Baltimore_City_County_2020_General.csv"
    )
    assert g["sbe-results-PG20-county-04-all"].url.endswith("/Baltimore_County_2020_General.csv")
    assert g["sbe-results-PG20-county-17-all"].url.endswith(
        "/Prince_Georges_County_2020_General.csv"
    )
    assert g["sbe-results-PG20-county-18-all"].url.endswith("/Queen_Annes_County_2020_General.csv")
    assert g["sbe-results-PG20-county-19-all"].url.endswith("/St._Marys_County_2020_General.csv")
    assert g["sbe-results-PG20-county-19-all"].county == "St. Mary's"
    assert g["sbe-results-PG20-check-all"].url == f"{base}/All_State_2020_General.csv"
    assert g["sbe-results-PG20-check-all"].role == "check"
    p = {f.source_id: f for f in results.county_files(PP20)}
    assert len(p) == 72  # no statewide file is published for the primary
    assert not any(f.role == "check" for f in p.values())
    b = PP20.base_url
    assert p["sbe-results-PP20-county-01-dem"].url == (
        f"{b}/Allegany_County_Democratic_2020_Primary.csv"
    )
    assert p["sbe-results-PP20-county-03-rep"].url.endswith(
        "/Baltimore_City_County_Republican_2020_Primary.csv"
    )
    assert p["sbe-results-PP20-county-04-dem"].url.endswith(
        "/Baltimore_County_Democratic_2020_Primary.csv"
    )
    assert p["sbe-results-PP20-county-19-non"].url.endswith(
        "/St._Marys_County_NonPartisan_2020_Primary.csv"
    )
    assert p["sbe-results-PP20-county-18-dem"].url.endswith(
        "/Queen_Annes_County_Democratic_2020_Primary.csv"
    )


def test_read_2020_general():
    frame = read_named(g20_csv(), PG20, "Allegany", "all")
    assert frame.columns[:9] == results.OUTPUT_ID_COLUMNS
    assert frame.columns[9:] == [
        "early_voting_votes", "early_voting_against", "election_day_votes",
        "election_day_against", "by_mail_votes", "by_mail_against", "prov_votes",
        "prov_against", "by_mail_2_votes", "by_mail_2_against", "total_votes", "total_against",
    ]  # fmt: skip
    assert frame["candidate"].to_list() == ["Donald J. Trump", "Joe Biden", "Other Write-Ins"]
    assert frame["winner"].to_list() == [0, 1, 0]
    assert frame["write_in"].to_list() == [0, 0, 1]
    assert frame["total_votes"].to_list() == [70, 7, 1]
    assert frame["prov_votes"].to_list() == [4, 0, 0]
    assert frame["total_against"].to_list() == [None, None, None]
    assert frame.row(0, named=True)["election"] == "PG20"


def test_read_2020_general_bad_total():
    bad = G20_ROWS.replace('"6","","70"', '"6","","71"')
    with pytest.raises(results.ResultsParseError, match="Donald J. Trump"):
        read_named(g20_csv(bad), PG20, "Allegany", "all")


def test_read_2020_primary_unquoted():
    rows = (
        "Joe Biden,DEM,President - Vice Pres,,Y,3,2,1,6\n"
        "Delegates Person,DEM,Board of Education     ,001,,1,1,0,2\n"
    )
    frame = read_named(p20_csv(rows), PP20, "Kent", "dem")
    assert frame.columns[9:] == [
        "vote_by_mail_votes", "in_person_voting_votes", "provisional_votes", "total_votes",
    ]  # fmt: skip
    assert frame["office"].to_list() == ["President - Vice Pres", "Board of Education"]
    assert frame["district"].to_list() == ["", "001"]
    assert frame["write_in"].to_list() == [0, 0]
    assert frame["in_person_voting_votes"].to_list() == [2, 1]
    with pytest.raises(results.ResultsParseError, match="Joe Biden"):
        read_named(p20_csv(rows.replace(",3,2,1,6", ",3,2,1,7")), PP20, "Kent", "dem")


def test_read_2020_primary_duplicate_unused_columns_and_cp1252():
    # Dorchester's Democratic file has two blank "N/A Votes" columns and is quoted; some counties'
    # files hold a cp1252 byte (an n with tilde)
    head = P20_HEAD.replace("Total Votes", '"N/A Votes","N/A Votes",Total Votes')
    row = "Ni\xf1o Person,DEM,President - Vice Pres,,Y,3,2,1,,,6\n"
    data = (head + row).encode("latin-1")
    frame = read_named(data, PP20, "Dorchester", "dem")
    assert frame["candidate"].to_list() == ["Ni\xf1o Person"]
    assert frame["total_votes"].to_list() == [6]
    assert "N/A Votes" not in frame.columns


def _g20_frames(rows_by_county):
    return [read_named(g20_csv(r), PG20, c, "all") for c, r in rows_by_county.items()]


def test_statewide_check_all_state():
    frames = _g20_frames({"Allegany": G20_ROWS, "Kent": G20_ROWS})
    assert results._check_statewide_all_state(PG20, frames, GOOD_ALL_STATE) == (3, 0, [])
    bad = GOOD_ALL_STATE.replace(b'"140"', b'"141"')
    with pytest.raises(results.ResultsParseError, match="Trump"):
        results._check_statewide_all_state(PG20, frames, bad)
    short = all_state([GOOD_ALL_STATE.decode().splitlines()[1] + "\n"])
    with pytest.raises(results.ResultsParseError, match="no statewide row"):
        results._check_statewide_all_state(PG20, frames, short)


def test_statewide_check_all_state_skips_offices_it_lacks():
    rows = (
        G20_ROWS
        + '"Local Person","DEM","Sheriff","","Y","","1","","1","","1","","0","","0","","3",""\n'
    )
    frames = _g20_frames({"Allegany": rows, "Kent": rows})
    assert results._check_statewide_all_state(PG20, frames, GOOD_ALL_STATE) == (3, 0, [])


def fill2020(env, election, skip=(), all_state_data=GOOD_ALL_STATE):
    for f in results.county_files(election):
        if f.source_id in skip or f.party_file == "non":
            continue
        if f.role == "check":
            data = all_state_data
        elif election.kind == "general":
            data = g20_csv()
        elif f.party_file == "dem":
            data = p20_csv()
        else:
            data = p20_csv("Donald J. Trump,REP,President - Vice Pres,,Y,1,1,0,2\n")
        archive(env, election, f, data)


def test_parse_pg20(env):
    # two counties' rows must add up to the statewide rows: use 24 identical counties
    totals = all_state(
        [
            f'"00","Donald J. Trump","REP","President - Vice Pres","","","","{240}","","{480}","","{720}","","{96}","","{144}","","{1680}",""\n',
            f'"00","Joe Biden","DEM","President - Vice Pres","","Y","","{24}","","{48}","","{72}","","0","","{24}","","{168}",""\n',
            f'"00","Other Write-Ins","","President - Vice Pres","","","Y","0","","{24}","","0","","0","","0","","{24}",""\n',
        ]
    )
    fill2020(env, PG20, all_state_data=totals)
    written, failed = results.results_parse("PG20", data_dir=env / "data")
    assert failed == [] and written == 24
    out = env / "data" / "results" / "PG20" / "county" / "03-baltimore-city-all.csv"
    assert out.exists()
    side = json.loads(out.with_name("03-baltimore-city-all_provenance.json").read_text())
    assert side["election"] == "PG20" and side["geography"] == "county"
    assert side["checks"]["row_totals"] == "ok"
    assert side["checks"]["statewide_check"].startswith("ok: 3 statewide rows (3 for, 0 against)")
    assert "All_State_2020_General" in side["checks"]["statewide_check"]
    assert results.results_parse("PG20", data_dir=env / "data")[0] == 0


def test_parse_pg20_mismatch_writes_nothing(env):
    fill2020(env, PG20)  # GOOD_ALL_STATE is for two counties, not 24
    written, failed = results.results_parse("PG20", data_dir=env / "data")
    assert written == 0 and failed and "Trump" in failed[0]
    assert not (env / "data" / "results" / "PG20").exists()


def test_parse_pp20_has_no_statewide_check(env):
    fill2020(env, PP20)
    written, failed = results.results_parse("PP20", data_dir=env / "data")
    assert failed == [] and written == 48
    out = env / "data" / "results" / "PP20" / "county" / "19-st-marys-dem.csv"
    side = json.loads(out.with_name("19-st-marys-dem_provenance.json").read_text())
    assert side["checks"]["statewide_check"] == "no statewide file published"
    assert side["checks"]["row_totals"] == "ok"


def test_parse_pp20_row_total_still_checked(env):
    fill2020(env, PP20)
    f = next(x for x in results.county_files(PP20) if x.source_id.endswith("county-05-dem"))
    archive(env, PP20, f, p20_csv("Joe Biden,DEM,President - Vice Pres,,Y,3,2,1,9\n"))
    written, failed = results.results_parse("PP20", data_dir=env / "data")
    assert written == 0 and failed and "Joe Biden" in failed[0]


def test_fetch_pp20(env, monkeypatch):
    def body(url):
        return b"<html>nope</html>" if "NonPartisan" in url else p20_csv()

    monkeypatch.setattr(fetch.httpx, "Client", fake_client(body))
    records = results.results_fetch("PP20")
    assert len(records) == 48  # no statewide file, and the missing NP files are normal
    assert (
        env / "archives/sbe/results/PP20/Baltimore_City_County_Democratic_2020_Primary.csv"
    ).exists()


def test_read_2020_primary_blank_vote_cell_is_zero_but_total_still_checked():
    # Frederick's file leaves some zero cells blank; the total must still add up
    frame = read_named(
        p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,1,,1\n"), PP20, "Frederick", "dem"
    )
    assert frame.row(0, named=True)["vote_by_mail_votes"] == 0
    assert frame.row(0, named=True)["total_votes"] == 1
    with pytest.raises(results.ResultsParseError, match="Deval Patrick"):
        read_named(
            p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,1,,2\n"), PP20, "Frederick", "dem"
        )
    # a blank total reads as 0 under this layout, and a vote in the row still fails the sum
    with pytest.raises(results.ResultsParseError, match="Deval Patrick: total votes 0 != .* 1"):
        read_named(
            p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,1,,\n"), PP20, "Frederick", "dem"
        )
    frame = read_named(
        p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,,,\n"), PP20, "Frederick", "dem"
    )
    assert frame.row(0, named=True)["total_votes"] == 0


def test_coded_still_rejects_blank_vote_cell():
    with pytest.raises(results.ResultsParseError, match="blank"):
        read_coded(
            primary_csv('"Governor / Lt. Governor","","X","DEM","","","20","30","4","5","59" \n'),
            results.RESULTS_ELECTIONS["GP26"],
            "Kent",
            "dem",
        )


# ---- review fixes: revisions, download failures, vacuous and against checks -------------------


def _sidecar(env, name):
    path = env / "data" / "results" / "GP26" / "county" / f"{name}_provenance.json"
    return json.loads(path.read_text())


def test_newest_source_is_the_last_manifest_row(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    f = results.county_files(e)[0]
    archive(env, e, f, primary_csv())
    archive(
        env,
        e,
        f,
        primary_csv(PRIMARY_ROWS + '"U.S. Senator","","Z","DEM","","1","0","0","0","0","1" \n'),
    )
    rows = [r for r in read_manifest(results.MANIFEST_PATH) if r.source_id == f.source_id]
    assert len(rows) == 2 and rows[0].sha256 != rows[1].sha256
    assert results.newest_source(results.MANIFEST_PATH, f.source_id).sha256 == rows[1].sha256
    assert results.newest_source(results.MANIFEST_PATH, "nope") is None


def test_changed_archived_file_is_reparsed_and_unchanged_is_skipped(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e)
    data = env / "data"
    assert results.results_parse("GP26", data_dir=data)[0] == 48
    assert results.results_parse("GP26", data_dir=data)[0] == 0  # same bytes: skipped
    before = _sidecar(env, "01-allegany-dem")["sha256_original"]
    # a re-fetch changes Allegany's Democratic file (a contest the breakdown does not carry)
    changed = primary_csv(
        PRIMARY_ROWS.replace(
            '"Someone Else","DEM","Y","0","0","0","0","0","0"',
            '"Someone Else","DEM","Y","0","0","0","0","1","1"',
        )
    )
    allegany = next(f for f in results.county_files(e) if f.source_id.endswith("county-01-dem"))
    archive(env, e, allegany, changed)
    assert results.results_parse("GP26", data_dir=data) == (1, [])
    after = _sidecar(env, "01-allegany-dem")["sha256_original"]
    assert after != before
    newest = results.newest_source(results.MANIFEST_PATH, allegany.source_id)
    assert after == newest.sha256
    assert _sidecar(env, "02-anne-arundel-dem")["sha256_original"] != after
    assert results.results_parse("GP26", data_dir=data)[0] == 0


def test_force_refetch_with_identical_bytes_adds_no_manifest_row(env, monkeypatch):
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: primary_csv()))
    first = results.results_fetch("PP24")
    n = len(read_manifest(results.MANIFEST_PATH))
    assert first and n == len(first)
    assert results.results_fetch("PP24", force=True) == []
    assert len(read_manifest(results.MANIFEST_PATH)) == n
    # changed bytes do add a row
    monkeypatch.setattr(
        fetch.httpx, "Client", fake_client(lambda url: primary_csv(PRIMARY_ROWS[:-1] + " \n"))
    )
    again = results.results_fetch("PP24", force=True)
    assert again and len(read_manifest(results.MANIFEST_PATH)) == n + len(again)


def failing_client(fail):
    class Client(fake_client(lambda url: primary_csv())):
        def get(self, url):
            if "05Non-Partisan" in url:
                raise fail(url)
            if "Non-Parti" in url:
                return FakeResp(b"<html>nope</html>")
            return FakeResp(primary_csv())

    return Client


def _timeout(url):
    return fetch.httpx.ConnectTimeout("timed out")


def _http_503(url):
    req = fetch.httpx.Request("GET", url)
    return fetch.httpx.HTTPStatusError("boom", request=req, response=fetch.httpx.Response(503))


def _http_404(url):
    req = fetch.httpx.Request("GET", url)
    return fetch.httpx.HTTPStatusError("gone", request=req, response=fetch.httpx.Response(404))


@pytest.mark.parametrize("fail", [_timeout, _http_503])
def test_transient_failure_on_non_file_is_a_failure_not_unpublished(env, monkeypatch, fail):
    monkeypatch.setattr(fetch.httpx, "Client", failing_client(fail))
    failures: list[str] = []
    records = results.results_fetch("GP26", failures=failures)
    assert len(records) == 50  # everything else still archives
    assert len(failures) == 1 and "05Non-Partisan" in failures[0]


def test_html_or_404_on_non_file_is_not_a_failure(env, monkeypatch):
    monkeypatch.setattr(fetch.httpx, "Client", failing_client(_http_404))
    failures: list[str] = []
    results.results_fetch("GP26", failures=failures)
    assert failures == []


def test_cli_results_fetch_exits_nonzero_on_download_failure(env, monkeypatch, capsys):
    import argparse

    from skipjack import cli

    monkeypatch.setattr(fetch.httpx, "Client", failing_client(_timeout))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_results_fetch(argparse.Namespace(election="GP26", force=False))
    assert exc.value.code == 1
    assert "05Non-Partisan" in capsys.readouterr().out


def test_fetch_file_expect_csv_rejects_empty_body(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: b""))
    dest = tmp_path / "x.csv"
    assert fetch.fetch_file("http://x/y.csv", dest, expect_csv=True) is False
    assert not dest.exists()


def test_vacuous_statewide_check_is_n_a_and_names_the_check_file(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    # a breakdown with no contest in common with the counties compares nothing
    other = breakdown(['"00","State of Maryland","Other Office","07","Y","DEM","Y","1","0" \n'])
    fill(env, e, breakdown_data=other)
    assert results.results_parse("GP26", data_dir=env / "data")[1] == []
    assert _sidecar(env, "01-allegany-dem")["checks"]["statewide_check"] == (
        "n/a: GP26_CongressionalBreakDownDemocratic.csv carries no statewide rows"
    )
    assert _sidecar(env, "01-allegany-rep")["checks"]["statewide_check"].startswith(
        "ok: 1 statewide rows (1 for, 0 against) match GP26_CongressionalBreakDownRepublican.csv"
    )


def test_ok_statewide_check_names_the_real_file(env):
    fill(env, results.RESULTS_ELECTIONS["GP26"])
    results.results_parse("GP26", data_dir=env / "data")
    assert _sidecar(env, "01-allegany-dem")["checks"]["statewide_check"] == (
        "ok: 2 statewide rows (2 for, 0 against) match GP26_CongressionalBreakDownDemocratic.csv"
    )


JUDGE = '"Judge Court of Appeals","01","Jane Roe","BOT","Y","","3","1","3","1","3","1","0","0","1","0","10","3" \n'
JUDGE_HEAD = (
    '"Vote Type (FOR=1,AGAINST=2)","County","County Name","Office Name","Office District",'
    '"Candidate Name","Party","Winner","Congressional District 1","Congressional District 2" \n'
)


def _judge_frames():
    e = results.RESULTS_ELECTIONS["PG24"]
    row = '"Judge Court of Appeals","01","Jane Roe","","","","3","1","3","1","3","1","0","0","1","0","10","3" \n'
    return e, [
        read_coded((BOM + GENERAL_HEAD + row).encode(), e, c, "all") for c in ("Allegany", "Kent")
    ]


def _judge_breakdown(for_total, against_total):
    base = (
        '"{}","00","State of Maryland","Judge Court of Appeals","01","Jane Roe","","","{}","0" \n'
    )
    return (BOM + JUDGE_HEAD + base.format(1, for_total) + base.format(2, against_total)).encode()


def test_statewide_check_against_totals_match_and_mismatch():
    e, frames = _judge_frames()
    # both blank-party county rows; counties sum to 20 FOR and 6 AGAINST
    assert results._check_statewide(e, "all", frames, _judge_breakdown(20, 6)) == (1, 1, [])
    with pytest.raises(results.ResultsParseError, match="against"):
        results._check_statewide(e, "all", frames, _judge_breakdown(20, 7))


def test_district_level_mismatch_fails():
    e = results.RESULTS_ELECTIONS["GP26"]
    frames = list(_frames(e, {"Allegany": PRIMARY_ROWS, "Kent": PRIMARY_ROWS}).values())
    ok = breakdown(
        [
            '"00","State of Maryland","U.S. Congress","06","April McClain Delaney","DEM","Y","6","4" \n'
        ]
    )
    assert results._check_statewide(e, "dem", frames, ok) == (1, 0, [])
    with pytest.raises(results.ResultsParseError, match="April McClain Delaney"):
        results._check_statewide(e, "dem", frames, ok.replace(b'"6","4"', b'"6","5"'))


def test_statewide_row_with_no_county_rows_fails():
    e = results.RESULTS_ELECTIONS["GP26"]
    frames = list(_frames(e, {"Allegany": PRIMARY_ROWS}).values())
    extra = breakdown(
        [
            '"00","State of Maryland","Governor / Lt. Governor","","Wes Moore and Aruna Miller","DEM","Y","50","19" \n',
            '"00","State of Maryland","Governor / Lt. Governor","","Eric S. Felber and LaTrece Hawkins Lytes","DEM","","5","2" \n',
            '"00","State of Maryland","Governor / Lt. Governor","","Ghost Candidate","DEM","","1","0" \n',
        ]
    )
    with pytest.raises(results.ResultsParseError, match="Ghost Candidate"):
        results._check_statewide(e, "dem", frames, extra)


def test_header_only_county_file_fails():
    with pytest.raises(results.ResultsParseError, match="no data rows"):
        read_coded(primary_csv(""), results.RESULTS_ELECTIONS["GP26"], "Kent", "dem")


def test_missing_columns_message_names_election_layout_and_file():
    bad = (BOM + '"Office Name","Total Votes"\n"x","1"\n').encode()
    with pytest.raises(results.ResultsParseError) as exc:
        read_coded(bad, results.RESULTS_ELECTIONS["GP26"], "Kent", "dem")
    msg = str(exc.value)
    assert "GP26" in msg and "coded" in msg and "GP26_15DemocraticResults.csv" in msg


# ---- hardening: archive integrity, crash safety, n/a, against path ---------------------------


def test_parse_refuses_archive_bytes_that_do_not_match_the_manifest(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e)
    f = next(x for x in results.county_files(e) if x.source_id.endswith("county-01-dem"))
    # a crash after write_bytes but before append_to_manifest: new bytes, old manifest row
    (env / "archives" / "sbe" / "results" / "GP26" / f.file_name).write_bytes(
        primary_csv(PRIMARY_ROWS + '"U.S. Senator","","Z","DEM","","1","0","0","0","0","1" \n')
    )
    written, failed = results.results_parse("GP26", data_dir=env / "data")
    assert written == 0 and len(failed) == 1
    assert "re-fetch" in failed[0] and f.file_name in failed[0]
    assert not (env / "data" / "results" / "GP26").exists()


def test_fetch_writes_archives_atomically(env, monkeypatch):
    replaced = []
    real = results.os.replace
    monkeypatch.setattr(results.os, "replace", lambda a, b: (replaced.append(b), real(a, b))[1])
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: primary_csv()))
    results.results_fetch("PP24")
    archive_dir = env / "archives" / "sbe" / "results" / "PP24"
    assert list(archive_dir.glob("*.tmp")) == []
    assert {Path(p).name for p in replaced} == {p.name for p in archive_dir.iterdir()}
    assert len(replaced) == len(list(archive_dir.iterdir())) > 0


def test_crash_before_sidecar_leaves_a_state_the_next_parse_repairs(env, monkeypatch):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e)
    data = env / "data"
    assert results.results_parse("GP26", data_dir=data)[0] == 48
    county = data / "results" / "GP26" / "county"
    side = county / "01-allegany-dem_provenance.json"
    side.unlink()  # what a crash between the CSV and the sidecar leaves
    assert results.results_parse("GP26", data_dir=data) == (1, [])
    assert side.exists()


def test_sidecar_is_written_after_the_csv_and_no_temp_files_remain(env, monkeypatch):
    fill(env, results.RESULTS_ELECTIONS["GP26"])
    order = []
    real = results.os.replace
    monkeypatch.setattr(results.os, "replace", lambda a, b: (order.append(str(b)), real(a, b))[1])
    real_write = results.ProvenanceSidecar.write

    def write(self, csv_path):
        order.append("sidecar-write")
        return real_write(self, csv_path)

    monkeypatch.setattr(results.ProvenanceSidecar, "write", write)
    results.results_parse("GP26", data_dir=env / "data")
    first = order.index("sidecar-write")
    assert order[first - 1].endswith("01-allegany-dem.csv")  # the CSV was replaced just before
    county = env / "data" / "results" / "GP26" / "county"
    assert list(county.glob("*.tmp")) == []


def test_non_partisan_county_file_without_published_breakdown_is_n_a(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e)
    non = next(x for x in results.county_files(e) if x.source_id.endswith("county-05-non"))
    archive(
        env,
        e,
        non,
        primary_csv('"Board of Education","","N N","NON","Y","1","0","0","0","0","1" \n'),
    )
    written, failed = results.results_parse("GP26", data_dir=env / "data")
    assert failed == [] and written == 49
    assert _sidecar(env, "05-calvert-non")["checks"]["statewide_check"] == (
        "n/a: GP26_CongressionalBreakDownNon-Partisian.csv not published"
    )
    assert _sidecar(env, "05-calvert-dem")["checks"]["statewide_check"].startswith("ok:")


def test_missing_dem_breakdown_still_fails_the_election(env):
    e = results.RESULTS_ELECTIONS["GP26"]
    fill(env, e, skip={"sbe-results-GP26-check-dem"})
    written, failed = results.results_parse("GP26", data_dir=env / "data")
    assert written == 0 and failed and "CongressionalBreakDownDemocratic" in failed[0]


def test_statewide_text_reports_for_and_against_separately(env):
    e = results.RESULTS_ELECTIONS["PG24"]
    row = (
        '"Judge Court of Appeals","01","Jane Roe","BOT","Y","","3","1","3","1","3","1","0","0",'
        '"1","0","10","3" \n'
        '"President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","1","","2","",'
        '"3","","1","","3","","10","" \n'
    )
    head = (
        '"Vote Type (FOR=1,AGAINST=2)","County","County Name","Office Name","Office District",'
        '"Candidate Name","Party","Winner","Congressional District 1","Congressional District 2" \n'
    )
    base = '"{}","00","State of Maryland","{}","{}","{}","{}","","{}","0" \n'
    check = (
        BOM
        + head
        + base.format(1, "Judge Court of Appeals", "01", "Jane Roe", "BOT", 240)
        + base.format(2, "Judge Court of Appeals", "01", "Jane Roe", "BOT", 72)
        + base.format(1, "President - Vice Pres", "", "Kamala D. Harris and Tim Walz", "DEM", 240)
    ).encode()
    for f in results.county_files(e):
        archive(env, e, f, check if f.role == "check" else (BOM + GENERAL_HEAD + row).encode())
    written, failed = results.results_parse("PG24", data_dir=env / "data")
    assert failed == [] and written == 24
    side = json.loads(
        (env / "data/results/PG24/county/01-allegany-all_provenance.json").read_text()
    )
    assert side["checks"]["statewide_check"] == (
        "ok: 3 statewide rows (2 for, 1 against) match PG24_CongressionalBreakDown.csv"
    )


G20_JUDGE_ROWS = (
    '"Jane Roe","","Judge Court of Appeals","01","Y","","3","1","3","1","3","1","0","0","1","0",'
    '"10","3"\n'
)


def _all_state_judge(for_total, against_total):
    return all_state(
        [
            f'"00","Jane Roe","","Judge Court of Appeals","01","Y","","{for_total}","","0","","0",'
            f'"","0","","0","","{for_total}","{against_total}"\n'
        ]
    )


def test_all_state_check_compares_against_totals():
    frames = _g20_frames({"Allegany": G20_JUDGE_ROWS, "Kent": G20_JUDGE_ROWS})
    assert results._check_statewide_all_state(PG20, frames, _all_state_judge(20, 6)) == (1, 1, [])
    with pytest.raises(results.ResultsParseError, match="against"):
        results._check_statewide_all_state(PG20, frames, _all_state_judge(20, 7))


def test_force_refetch_of_identical_bytes_rewrites_a_missing_archive_without_a_new_row(
    env, monkeypatch
):
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: primary_csv()))
    results.results_fetch("PP24")
    n = len(read_manifest(results.MANIFEST_PATH))
    gone = next((env / "archives" / "sbe" / "results" / "PP24").glob("*.csv"))
    gone.unlink()
    assert results.results_fetch("PP24", force=True) == []
    assert gone.exists() and gone.read_bytes() == primary_csv()
    assert len(read_manifest(results.MANIFEST_PATH)) == n


# ---- the named layout: 2010-2018 ------------------------------------------------------------
# The archive's county files share 2020's column names and order; the vote types differ
# (Early, Election Night, Absentees, Provisional, 2nd Absentees; the 2010 general adds a third
# absentee canvass) and the statewide check comes from the congressional (legislative in 2010)
# districts file, whose primary form has no Vote Type column. Those districts files carry
# election-night votes only: their County 00 rows sum to the counties' Election Night Votes, so
# the check fixtures here are built on that column, never the total.

ARCHIVE_TYPES = ["Early", "Election Night", "Absentees", "Provisional", "2nd Absentees"]


def named_general_head(types=ARCHIVE_TYPES) -> str:
    cols = ["Candidate Name", "Party", "Office Name", "Office District", "Winner", "Write-In?"]
    for t in types:
        cols += [f"{t} Votes", f"{t} Votes Against"]
    cols += ["Total Votes", "Total Votes Against"]
    return ",".join(f'"{c}"' for c in cols) + "\n"


def named_primary_head(types=ARCHIVE_TYPES) -> str:
    cols = ["Candidate Name", "Party", "Office Name", "Office District", "Winner"]
    cols += [f"{t} Votes" for t in types] + ["Total Votes"]
    return ",".join(f'"{c}"' for c in cols) + "\n"


G14_HEAD = named_general_head()
G10_HEAD = named_general_head(ARCHIVE_TYPES + ["3rd Absentees"])
P14_HEAD = named_primary_head()
# generals: Vote Type, County, ..., Write-In?, then the districts in the SBE's scrambled order
# (the first district name carries a leading space in the real files)
CONG_GENERAL_HEAD = (
    '"Vote Type (FOR=1,AGAINST=2)","County","Candidate Name","Party","Office Name",'
    '"Office District","Winner","Write-In?"," CONG 04","CONG 08","CONG 01"\n'
)
LEGS_GENERAL_HEAD = CONG_GENERAL_HEAD.replace("CONG 04", "LEGS 37B").replace(
    '"CONG 08","CONG 01"', '"LEGS 47","LEGS 01A"'
)
# primaries: no Vote Type, no Write-In?
CONG_PRIMARY_HEAD = (
    '"County","Candidate Name","Party","Office Name","Office District","Winner",'
    '" CONG 04","CONG 08","CONG 01"\n'
)
LEGS_PRIMARY_HEAD = CONG_PRIMARY_HEAD.replace("CONG 04", "LEGS 37B").replace(
    '"CONG 08","CONG 01"', '"LEGS 47","LEGS 01A"'
)

# one county's rows: early, election night, absentees, provisional, 2nd absentees, total
# (Hogan 10/20/3/4/1 = 38; Brown 5/6/1/0/0 = 12; Franchot 7/8/0/0/0 = 15)
G14_ROWS = (
    '"Larry Hogan","REP","Governor / Lt. Governor","","Y","","10","","20","","3","","4","","1","","38",""\n'
    '"Anthony G. Brown","DEM","Governor / Lt. Governor","","","","5","","6","","1","","0","","0","","12",""\n'
    '"Peter Franchot","DEM","Comptroller            ","","Y","","7","","8","","0","","0","","0","","15",""\n'
)
# (O'Malley 10/20/3/4/1/2 = 40; Ehrlich 5/6/1/#NULL#/0/1 = 13; Judge Jane 3/3/0/0/0/0 = 6, and
# 1 against early, 1 against election night = 2)
G10_ROWS = (
    '"Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","10","","20","","3","","4","","1","","2","","40",""\n'
    '"Robert L. Ehrlich, Jr.","REP","Governor / Lt. Governor","","","","5","","6","","1","","#NULL#","","0","","1","","13",""\n'
    '"Judge Jane","","Judge Court of Appeals","01","Y","","3","1","3","1","0","0","0","0","0","0","0","0","6","2"\n'
)
# (Hogan 10/20/3/4/1 = 38; Craig 5/6/1/#NULL#/0 = 12)
P14_ROWS = (
    '"Larry Hogan","REP","Governor / Lt. Governor","","Y","10","20","3","4","1","38"\n'
    '"David R. Craig","REP","Governor / Lt. Governor","","","5","6","1","#NULL#","0","12"\n'
)
# the counties' election-night votes per row of the fixtures above
EN = {"Hogan": 20, "Brown": 6, "Franchot": 8, "O'Malley": 20, "Ehrlich": 6, "Jane": 3, "Craig": 6}
EN_AGAINST = {"Jane": 1}

GG10 = results.RESULTS_ELECTIONS["GG10"]
GG14 = results.RESULTS_ELECTIONS["GG14"]
GG18 = results.RESULTS_ELECTIONS["GG18"]
GP10 = results.RESULTS_ELECTIONS["GP10"]
GP14 = results.RESULTS_ELECTIONS["GP14"]
PG12 = results.RESULTS_ELECTIONS["PG12"]


def g14_csv(rows: str = G14_ROWS) -> bytes:
    return (G14_HEAD + rows).encode()


def g10_csv(rows: str = G10_ROWS) -> bytes:
    return (G10_HEAD + rows).encode()


def p14_csv(rows: str = P14_ROWS) -> bytes:
    return (P14_HEAD + rows).encode()


def check_csv(head: str, rows: list[str]) -> bytes:
    return (BOM + head + "".join(rows)).encode()


def test_registry_named_archive_elections():
    days = {
        "GP10": "2010-09-14", "GG10": "2010-11-02", "PP12": "2012-04-03", "PG12": "2012-11-06",
        "GP14": "2014-06-24", "GG14": "2014-11-04", "PP16": "2016-04-26", "PG16": "2016-11-08",
        "GP18": "2018-06-26", "GG18": "2018-11-06",
    }  # fmt: skip
    for code, day in days.items():
        e = results.RESULTS_ELECTIONS[code]
        assert e.election_day == day and e.layout == "named" and e.published
        assert e.year == 2000 + int(code[2:])
        assert e.kind == ("general" if code[1] == "G" else "primary")
        assert e.base_url == (
            f"https://elections.maryland.gov/elections/archive/{e.year}/election_data"
        )
        assert e.name.startswith(str(e.year))
        # the districts files of these years carry election-night votes only
        assert e.check_votes == "election_night"
    stems = ["early", "election_night", "absentees", "provisional", "absentees_2"]
    assert [s for _, s in GG14.vote_types] == stems
    assert [s for _, s in GP10.vote_types] == stems
    assert [s for _, s in GG10.vote_types] == stems + ["absentees_3"]
    assert [lab for lab, _ in GG10.vote_types][-1] == "3rd Absentees"
    assert [lab for lab, _ in GG14.vote_types] == ARCHIVE_TYPES
    assert GG10.check == "legislative" and GP10.check == "legislative"
    assert GG14.check == "congressional" and GP14.check == "congressional"
    assert GG18.check == "congressional"
    assert PG20.check == "all_state" and PP20.check == "none"
    assert results.RESULTS_ELECTIONS["GP26"].check == "congressional"
    assert results.RESULTS_ELECTIONS["PG24"].check == "congressional"
    # the 2020 entries keep their own vote types, and every other election is checked on totals
    assert [s for _, s in PG20.vote_types][0] == "early_voting"
    for code in ("PG20", "PP20", "GP22", "GG22", "PP24", "PG24", "GP26", "GG26"):
        assert results.RESULTS_ELECTIONS[code].check_votes == "total", code
    assert list(results.RESULTS_ELECTIONS)[:2] == ["GP26", "GG26"]
    assert list(results.RESULTS_ELECTIONS)[-2:] == ["GG10", "GP10"]


def test_election_rejects_a_year_with_no_layout_rule():
    assert results._election("GG22", 2022, "general", "x", "2022-11-08").layout == "coded"
    assert results._election("GG10", 2010, "general", "x", "2010-11-02").layout == "named"
    for year in (2008, 2009, 2021):
        with pytest.raises(ValueError, match=f"no results layout rule for {year}"):
            results._election("XX", year, "general", "x", f"{year}-11-04")


def test_registry_election_days_match_the_site_lists():
    from skipjack.web import elections as web_elections

    for e in results.RESULTS_ELECTIONS.values():
        expected = web_elections.GENERALS if e.kind == "general" else web_elections.PRIMARIES
        assert e.election_day in expected, e.code


def test_urls_named_archive():
    base = GG14.base_url
    g = {f.source_id: f for f in results.county_files(GG14)}
    assert len(g) == 25
    assert g["sbe-results-GG14-county-01-all"].url == f"{base}/Allegany_County_2014_General.csv"
    assert g["sbe-results-GG14-county-03-all"].url.endswith(
        "/Baltimore_City_County_2014_General.csv"
    )
    assert g["sbe-results-GG14-county-04-all"].url.endswith("/Baltimore_County_2014_General.csv")
    assert g["sbe-results-GG14-county-19-all"].url.endswith("/St._Marys_County_2014_General.csv")
    assert g["sbe-results-GG14-check-all"].url == (
        f"{base}/State_Congressional_Districts_2014_General.csv"
    )
    assert g["sbe-results-GG14-check-all"].role == "check"
    assert g["sbe-results-GG14-check-all"].local_path.name == (
        "State_Congressional_Districts_2014_General.csv"
    )
    g10 = {f.source_id: f for f in results.county_files(GG10)}
    assert len(g10) == 25
    assert g10["sbe-results-GG10-check-all"].url.endswith(
        "/State_Legislative_Districts_2010_General.csv"
    )
    assert g10["sbe-results-GG10-county-17-all"].url.endswith(
        "/Prince_Georges_County_2010_General.csv"
    )
    p10 = {f.source_id: f for f in results.county_files(GP10)}
    assert len(p10) == 75  # 24 x 3 party files + 3 check files
    assert p10["sbe-results-GP10-county-19-non"].url.endswith(
        "/St._Marys_County_NonPartisan_2010_Primary.csv"
    )
    assert p10["sbe-results-GP10-county-18-dem"].url.endswith(
        "/Queen_Annes_County_Democratic_2010_Primary.csv"
    )
    assert p10["sbe-results-GP10-check-dem"].url.endswith(
        "/State_Legislative_Districts_Democratic_2010_Primary.csv"
    )
    p14 = {f.source_id: f for f in results.county_files(GP14)}
    assert p14["sbe-results-GP14-check-dem"].url.endswith(
        "/State_Congressional_Districts_Democratic_2014_Primary.csv"
    )
    assert p14["sbe-results-GP14-check-rep"].url.endswith(
        "/State_Congressional_Districts_Republican_2014_Primary.csv"
    )
    assert p14["sbe-results-GP14-check-non"].url.endswith(
        "/State_Congressional_Districts_NonPartisan_2014_Primary.csv"
    )
    assert p14["sbe-results-GP14-county-03-rep"].url.endswith(
        "/Baltimore_City_County_Republican_2014_Primary.csv"
    )
    # 2020 is unchanged: the all-state file for the general, no check file for the primary
    pg20 = {f.source_id: f for f in results.county_files(PG20)}
    assert pg20["sbe-results-PG20-check-all"].file_name == "All_State_2020_General.csv"
    assert not any(f.role == "check" for f in results.county_files(PP20))


def test_read_named_2010_general_six_types_and_null_cells():
    frame = read_named(g10_csv(), GG10, "Allegany", "all")
    assert frame.columns[:9] == results.OUTPUT_ID_COLUMNS
    assert frame.columns[9:] == [
        "early_votes", "early_against", "election_night_votes", "election_night_against",
        "absentees_votes", "absentees_against", "provisional_votes", "provisional_against",
        "absentees_2_votes", "absentees_2_against", "absentees_3_votes", "absentees_3_against",
        "total_votes", "total_against",
    ]  # fmt: skip
    assert frame["candidate"].to_list() == [
        "Martin O'Malley",
        "Robert L. Ehrlich, Jr.",
        "Judge Jane",
    ]
    assert frame["winner"].to_list() == [1, 0, 1]
    assert frame["absentees_3_votes"].to_list() == [2, 1, 0]
    # #NULL# where a zero is meant; the row total still has to add up
    assert frame["provisional_votes"].to_list() == [4, 0, 0]
    assert frame["total_votes"].to_list() == [40, 13, 6]
    assert frame["total_against"].to_list() == [None, None, 2]
    assert frame["early_against"].to_list() == [None, None, 1]
    bad = G10_ROWS.replace('"#NULL#","","0","","1","","13"', '"#NULL#","","0","","1","","14"')
    with pytest.raises(results.ResultsParseError, match="Ehrlich"):
        read_named(g10_csv(bad), GG10, "Allegany", "all")
    # a file the registry lists no exceptions for reports none
    assert results._read_named(g10_csv(), GG10, "Allegany", "all").row_total_exceptions == []


def test_read_named_2014_general_strips_office_names():
    frame = read_named(g14_csv(), GG14, "Kent", "all")
    assert frame["office"].to_list()[2] == "Comptroller"
    assert frame.columns[-2:] == ["total_votes", "total_against"]
    assert "absentees_3_votes" not in frame.columns
    assert frame["total_votes"].to_list() == [38, 12, 15]


def test_read_named_primary_null_cell_is_zero_but_total_still_checked():
    frame = read_named(p14_csv(), GP14, "Kent", "rep")
    assert frame.columns[9:] == [
        "early_votes", "election_night_votes", "absentees_votes", "provisional_votes",
        "absentees_2_votes", "total_votes",
    ]  # fmt: skip
    assert frame["provisional_votes"].to_list() == [4, 0]
    assert frame["total_votes"].to_list() == [38, 12]
    with pytest.raises(results.ResultsParseError, match="Craig"):
        read_named(p14_csv(P14_ROWS.replace('"0","12"', '"0","13"')), GP14, "Kent", "rep")
    # the token is matched case-insensitively
    frame = read_named(p14_csv(P14_ROWS.replace("#NULL#", "#null#")), GP14, "Kent", "rep")
    assert frame["provisional_votes"].to_list() == [4, 0]


# St. Mary's 2014 general, as the SBE prints it: a write-in with #NULL# for its Total Votes
NULL_TOTAL_ROW = (
    '"Charles U. Smith","DEM","Governor / Lt. Governor","","","Y","#NULL#","","#NULL#","","0","",'
    '"0","","0","","#NULL#",""\n'
)


def test_null_token_in_total_votes_reads_as_zero_and_the_sum_check_still_applies():
    frame = read_named(g14_csv(G14_ROWS + NULL_TOTAL_ROW), GG14, "St. Mary's", "all")
    assert frame["total_votes"].to_list() == [38, 12, 15, 0]
    assert frame["election_night_votes"].to_list()[-1] == 0
    assert frame["write_in"].to_list()[-1] == 1
    # a #NULL# (or blank) total with a vote in the row is still a mismatch
    one_vote = NULL_TOTAL_ROW.replace(
        '"0","","0","","0","","#NULL#"', '"1","","0","","0","","#NULL#"'
    )
    with pytest.raises(results.ResultsParseError, match="Smith.*total votes 0 != sum.*1"):
        read_named(g14_csv(G14_ROWS + one_vote), GG14, "St. Mary's", "all")
    with pytest.raises(results.ResultsParseError, match="Smith.*total votes 0 != sum.*1"):
        read_named(g14_csv(G14_ROWS + one_vote.replace("#NULL#", "")), GG14, "St. Mary's", "all")
    # the coded layout still refuses a blank total
    row = '"Governor","","Write-In","","","Y","0","","0","","0","","0","","0","","","" \n'
    with pytest.raises(results.ResultsParseError, match="Write-In: blank Total Votes"):
        read_coded(
            (BOM + GENERAL_HEAD + row).encode(), results.RESULTS_ELECTIONS["PG24"], "Kent", "all"
        )


def test_coded_layout_refuses_a_blank_total_even_in_a_file_with_listed_exceptions():
    # a blank total is never one of the board's arithmetic errors: refused before the list
    blank = GG10_BALTIMORE_CITY_BAD_ROWS.splitlines(keepends=True)[0].replace('"62"', '""')
    with pytest.raises(results.ResultsParseError, match="Ralph Jaffe: blank Total Votes"):
        results._read_coded(
            g10_csv(GG10_BALTIMORE_CITY_GOOD_ROWS + blank), GG10, "Baltimore City", "all"
        )


def test_null_token_in_against_cell_reads_as_none():
    # a #NULL# in a Votes Against cell is no figure, like a blank: not an Against total of 0
    rows = G14_ROWS.replace(
        '"Larry Hogan","REP","Governor / Lt. Governor","","Y","","10",""',
        '"Larry Hogan","REP","Governor / Lt. Governor","","Y","","10","#NULL#"',
    )
    frame = read_named(g14_csv(rows), GG14, "Kent", "all")
    assert frame["early_against"].to_list()[0] is None
    assert frame["total_against"].to_list()[0] is None


# Kent 2012 general, as the SBE prints it: a lone 0 in Election Night Votes Against, every other
# Against cell and the Total Votes Against blank (835 such rows across the 2012 county files)
LONE_ZERO_AGAINST_ROW = (
    '"Tiffany Renee Briscoe","DEM","President - Vice Pres","","","Y","0","","0","0","0","","0","",'
    '"0","","0",""\n'
)


def test_lone_zero_against_with_blank_total_is_no_against_figure():
    frame = read_named(g14_csv(G14_ROWS + LONE_ZERO_AGAINST_ROW), PG12, "Kent", "all")
    row = frame.row(3, named=True)
    assert row["candidate"] == "Tiffany Renee Briscoe" and row["total_votes"] == 0
    assert all(row[c] is None for c in frame.columns if c.endswith("_against"))
    # a non-zero Against cell with a blank total is still a mismatch
    one = LONE_ZERO_AGAINST_ROW.replace('"0","","0","0"', '"0","","0","1"')
    with pytest.raises(results.ResultsParseError, match="Briscoe.*total against None != .*1"):
        read_named(g14_csv(G14_ROWS + one), PG12, "Kent", "all")
    # so are zero Against cells under a total of 0 the other way round: a total with no cells
    total_only = LONE_ZERO_AGAINST_ROW.replace('"0","","0","0"', '"0","","0",""').replace(
        '"0",""\n', '"0","0"\n'
    )
    with pytest.raises(results.ResultsParseError, match="Briscoe.*total against 0 != .*None"):
        read_named(g14_csv(G14_ROWS + total_only), PG12, "Kent", "all")
    # the coded layout is unchanged: a lone 0 against a blank total is a mismatch there
    row = '"Governor","","Write-In","","","Y","0","","0","0","0","","0","","0","","0","" \n'
    with pytest.raises(results.ResultsParseError, match="total against None"):
        read_coded(
            (BOM + GENERAL_HEAD + row).encode(), results.RESULTS_ELECTIONS["PG24"], "Kent", "all"
        )


def test_coded_still_rejects_null_token():
    with pytest.raises(results.ResultsParseError, match="blank"):
        read_coded(
            primary_csv(
                '"Governor / Lt. Governor","","X","DEM","","#NULL#","20","30","4","5","59" \n'
            ),
            results.RESULTS_ELECTIONS["GP26"],
            "Kent",
            "dem",
        )


@pytest.mark.parametrize(("cell", "winner"), [("Y", 1), ("YES", 1), ("yes", 1), ("", 0), ("N", 0)])
def test_read_named_winner_spellings(cell, winner):
    rows = P14_ROWS.replace(
        '"Governor / Lt. Governor","","Y"', f'"Governor / Lt. Governor","","{cell}"'
    )
    frame = read_named(p14_csv(rows), GP14, "Kent", "rep")
    assert frame["winner"].to_list() == [winner, 0]


def test_unregistered_vote_column_is_rejected():
    # a 2014 file carrying a third absentee canvass the registry does not list would otherwise
    # be silently left out of the row sums; the row-total check would catch a nonzero one, but
    # a column of zeros would pass
    rows = (
        '"Larry Hogan","REP","Governor / Lt. Governor","","Y","","10","","20","","3","","4","",'
        '"1","","0","","38",""\n'
    )
    data = (G10_HEAD + rows).encode()
    with pytest.raises(results.ResultsParseError, match="not in the registry.*3rd Absentees Votes"):
        read_named(data, GG14, "Kent", "all")
    # the same header is fine for the 2010 general, which lists the column
    assert read_named(data, GG10, "Kent", "all")["total_votes"].to_list() == [38]
    # an N/A column (Dorchester 2020) and the Votes Against twins are not vote columns
    head = P14_HEAD.replace('"Total Votes"', '"N/A Votes","N/A Votes","Total Votes"')
    row = '"Larry Hogan","REP","Governor / Lt. Governor","","Y","10","20","3","4","1","","","38"\n'
    frame = read_named((head + row).encode(), GP14, "Dorchester", "rep")
    assert frame["total_votes"].to_list() == [38]


# ---- the board's own arithmetic: Baltimore City's 2010 general -----------------------------
# The SBE's Baltimore_City_County_2010_General.csv prints, on these 15 write-in rows, a Total
# Votes below the sum of the vote types (one 3rd Absentees cell is even -1). Copied verbatim.

GG10_BALTIMORE_CITY_GOOD_ROWS = (
    '"Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","17338","","106268","","4388","","4423","","625","","26","","133068",""\n'
    '"Robert L. Ehrlich, Jr.","REP","Governor / Lt. Governor","","","","2019","","22204","","1109","","561","","169","","11","","26073",""\n'
)
GG10_BALTIMORE_CITY_BAD_ROWS = (
    '"Ralph Jaffe","DEM","Governor / Lt. Governor","","","Y","9","","60","","1","","1","","0","","0","","62",""\n'
    '"Corrogan R. Vaughn","UNF","Governor / Lt. Governor","","","Y","15","","40","","0","","1","","0","","0","","41",""\n'
    '"Other Write-Ins","BOT","Governor / Lt. Governor","","","Y","21","","148","","12","","4","","2","","0","","166",""\n'
    '"James T. Lynch, Jr.","DEM","U.S. Senator","","","Y","1","","6","","0","","0","","0","","0","","6",""\n'
    '"Denise L. Whittington","DEM","U.S. Senator","","","Y","3","","7","","0","","0","","0","","0","","7",""\n'
    '"Lih Young","DEM","U.S. Senator","","","Y","1","","11","","0","","0","","0","","0","","11",""\n'
    '"Claud L. Asbury","REP","U.S. Senator","","","Y","1","","14","","0","","1","","0","","0","","15",""\n'
    '"Donald Kaplan","UNF","U.S. Senator","","","Y","1","","16","","0","","0","","0","","0","","16",""\n'
    '"Other Write-Ins","BOT","U.S. Senator","","","Y","21","","76","","4","","4","","0","","0","","84",""\n'
    '"Ray Bly","REP","U.S. Congress","07","","Y","2","","4","","0","","0","","0","","0","","4",""\n'
    '"Other Write-Ins","BOT","U.S. Congress","07","","Y","9","","41","","3","","1","","0","","0","","45",""\n'
    '"Sarah Matthews","DEM","Clerk Circuit Court","","","Y","58","","212","","0","","3","","0","","0","","215",""\n'
    '"Other Write-Ins","BOT","Clerk Circuit Court","","","Y","114","","733","","57","","32","","10","","-1","","832",""\n'
    '"Frances Jean Hamilton","UNF","Sheriff","","","Y","1769","","2812","","15","","71","","12","","0","","2910",""\n'
    '"Other Write-Ins","BOT","Sheriff","","","Y","248","","550","","54","","86","","6","","0","","696",""\n'
)
GG10_BALTIMORE_CITY = g10_csv(GG10_BALTIMORE_CITY_GOOD_ROWS + GG10_BALTIMORE_CITY_BAD_ROWS)


def test_row_total_exceptions_keep_the_printed_total_and_list_the_rows():
    assert results.ROW_TOTAL_EXCEPTIONS == {("GG10", "Baltimore City", "all"): 15}
    frame, exceptions = results._read_named(GG10_BALTIMORE_CITY, GG10, "Baltimore City", "all")
    assert frame.height == 17
    # the board's printed total is kept, never the computed sum
    assert frame["total_votes"].to_list()[:5] == [133068, 26073, 62, 41, 166]
    assert frame["election_night_votes"].to_list()[2:5] == [60, 40, 148]
    assert frame["absentees_3_votes"].to_list()[14] == -1
    assert len(exceptions) == 15
    assert exceptions[0] == {
        "office": "Governor / Lt. Governor",
        "district": "",
        "candidate": "Ralph Jaffe",
        "party": "DEM",
        "total_votes": 62,
        "sum_of_vote_types": 71,
    }
    assert exceptions[-1] == {
        "office": "Sheriff",
        "district": "",
        "candidate": "Other Write-Ins",
        "party": "BOT",
        "total_votes": 696,
        "sum_of_vote_types": 944,
    }
    assert [(x["total_votes"], x["sum_of_vote_types"]) for x in exceptions] == [
        (62, 71), (41, 56), (166, 187), (6, 7), (7, 10), (11, 12), (15, 16), (16, 17), (84, 105),
        (4, 6), (45, 54), (215, 273), (832, 945), (2910, 4679), (696, 944),
    ]  # fmt: skip


def test_row_total_exceptions_accept_exactly_the_listed_count():
    bad = GG10_BALTIMORE_CITY_BAD_ROWS.splitlines(keepends=True)
    fourteen = g10_csv(GG10_BALTIMORE_CITY_GOOD_ROWS + "".join(bad[:14]))
    with pytest.raises(results.ResultsParseError, match="14 rows whose total.*but 15 are listed"):
        results._read_named(fourteen, GG10, "Baltimore City", "all")
    sixteen = g10_csv(GG10_BALTIMORE_CITY_GOOD_ROWS + "".join(bad) + bad[0])
    with pytest.raises(
        results.ResultsParseError, match=r"Ralph Jaffe: total votes 62 != .*71 \(15 such rows"
    ):
        results._read_named(sixteen, GG10, "Baltimore City", "all")
    # the same rows in a file not listed fail on the first one, with no mention of a list
    with pytest.raises(results.ResultsParseError, match=r"Ralph Jaffe: total votes 62 != .*71$"):
        results._read_named(GG10_BALTIMORE_CITY, GG10, "Baltimore County", "all")
    # the list names one party file: the county's other files are not licensed by it
    with pytest.raises(results.ResultsParseError, match=r"Ralph Jaffe: total votes 62 != .*71$"):
        results._read_named(GG10_BALTIMORE_CITY, GG10, "Baltimore City", "dem")
    with pytest.raises(results.ResultsParseError, match="Ralph Jaffe"):
        results._read_named(
            g14_csv(
                '"Ralph Jaffe","DEM","Governor / Lt. Governor","","","Y","9","","60","","1","","1","","0","","62",""\n'
            ),
            GG14,
            "Baltimore City",
            "all",
        )


# ---- the statewide check on election-night votes --------------------------------------------
# Representative in Congress, district 4, 2018 general: the district's two counties' rows and
# the State_Congressional_Districts file's County 00 rows, copied verbatim. The state rows equal
# the counties' Election Night Votes (29364 + 6572 = 35936), not their totals (43551 + 9776).

CONG_2018_HEAD = (
    '"Vote Type (FOR=1,AGAINST=2)","County","Candidate Name","Party","Office Name",'
    '"Office District","Winner","Write-In?","CONG 04","CONG 08","CONG 07","CONG 01","CONG 05",'
    '"CONG 03","CONG 02","CONG 06"\n'
)
GG18_CD4_STATE = [
    '"1","00","George McDermott","REP","Representative in Congress","04","","","35936","","","","","","",""\n',
    '"1","00","Anthony G. Brown","DEM","Representative in Congress","04","Y","","127602","","","","","","",""\n',
    '"1","00","Dave Bishop","LIB","Representative in Congress","04","","","3657","","","","","","",""\n',
    '"1","00","Other Write-Ins","BOT","Representative in Congress","04","","Y","167","","","","","","",""\n',
]
GG18_CD4_COUNTIES = {
    "Anne Arundel": (
        '"George McDermott","REP","Representative in Congress","04","","","12010","","29364","","1357","","520","","300","","43551",""\n'
        '"Anthony G. Brown","DEM","Representative in Congress","04","Y","","13990","","22021","","2008","","594","","752","","39365",""\n'
        '"Dave Bishop","LIB","Representative in Congress","04","","","744","","2154","","106","","55","","50","","3109",""\n'
        '"Other Write-Ins","BOT","Representative in Congress","04","","Y","31","","47","","5","","3","","2","","88",""\n'
    ),
    "Prince George's": (
        '"George McDermott","REP","Representative in Congress","04","","","2384","","6572","","481","","187","","152","","9776",""\n'
        '"Anthony G. Brown","DEM","Representative in Congress","04","Y","","53839","","105581","","3840","","4733","","2284","","170277",""\n'
        '"Dave Bishop","LIB","Representative in Congress","04","","","553","","1503","","58","","61","","42","","2217",""\n'
        '"Other Write-Ins","BOT","Representative in Congress","04","","Y","65","","120","","2","","9","","4","","200",""\n'
    ),
}


def test_statewide_check_2018_compares_election_night_votes_to_the_real_district_rows(
    monkeypatch,
):
    frames = [read_named(g14_csv(r), GG18, c, "all") for c, r in GG18_CD4_COUNTIES.items()]
    assert sum(f["total_votes"].to_list()[0] for f in frames) == 43551 + 9776
    assert sum(f["election_night_votes"].to_list()[0] for f in frames) == 35936
    state = check_csv(CONG_2018_HEAD, GG18_CD4_STATE)
    assert results._check_statewide(GG18, "all", frames, state) == (4, 0, [])
    with pytest.raises(results.ResultsParseError, match="McDermott: counties sum to 35936.*35937"):
        results._check_statewide(GG18, "all", frames, state.replace(b'"35936"', b'"35937"'))
    # the same files compared on totals, as the 2020-2026 elections are, do not match
    monkeypatch.setattr(GG18, "check_votes", "total")
    with pytest.raises(results.ResultsParseError, match="Brown: counties sum to 209642.*127602"):
        results._check_statewide(GG18, "all", frames, state)


def test_statewide_check_refuses_a_check_column_the_frames_lack(monkeypatch):
    frames = [read_named(g14_csv(r), GG18, c, "all") for c, r in GG18_CD4_COUNTIES.items()]
    monkeypatch.setattr(GG18, "check_votes", "by_mail")
    with pytest.raises(results.ResultsParseError, match="no by_mail_votes column"):
        results._check_statewide(GG18, "all", frames, check_csv(CONG_2018_HEAD, GG18_CD4_STATE))


def _g14_frames(rows_by_county):
    return [read_named(g14_csv(r), GG14, c, "all") for c, r in rows_by_county.items()]


def test_statewide_check_cong_columns_general():
    frames = _g14_frames({"Allegany": G14_ROWS, "Kent": G14_ROWS})
    # the district columns sum to two counties' election-night votes: 40, 12, 16
    good = check_csv(
        CONG_GENERAL_HEAD,
        [
            '"1","00","Larry Hogan","REP","Governor / Lt. Governor","","Y","","30","8","2"\n',
            '"1","00","Anthony G. Brown","DEM","Governor / Lt. Governor","","","","10","2","0"\n',
            '"1","00","Peter Franchot","DEM","Comptroller","","Y","","16","0","0"\n',
            '"2","00","Some Judge","","Judge Elsewhere","","Y","","999","1","0"\n',
            # a county's own rows are not statewide rows
            '"1","01","Larry Hogan","REP","Governor / Lt. Governor","","Y","","1","1","1"\n',
        ],
    )
    assert results._check_statewide(GG14, "all", frames, good) == (3, 0, [])
    with pytest.raises(results.ResultsParseError, match="Hogan.*counties sum to 40.*41"):
        results._check_statewide(
            GG14, "all", frames, good.replace(b'"30","8","2"', b'"30","8","3"')
        )
    # a check file carrying the counties' totals (76) is a mismatch: 2010-2018 is election night
    with pytest.raises(results.ResultsParseError, match="Hogan.*counties sum to 40.*76"):
        results._check_statewide(
            GG14, "all", frames, good.replace(b'"30","8","2"', b'"50","20","6"')
        )
    # a statewide office with a candidate the check file lacks
    hogan_only = check_csv(CONG_GENERAL_HEAD, [good.decode().splitlines()[1] + "\n"])
    with pytest.raises(results.ResultsParseError, match="no statewide row.*Brown"):
        results._check_statewide(GG14, "all", frames, hogan_only)
    # an empty check file compares nothing (recorded as n/a by the parse)
    assert results._check_statewide(GG14, "all", frames, check_csv(CONG_GENERAL_HEAD, [])) == (
        0,
        0,
        [],
    )


def test_statewide_check_cong_columns_general_against():
    frames = [read_named(g10_csv(), GG10, c, "all") for c in ("Allegany", "Kent")]
    # two counties' election-night votes: 40, 12, 6 for; Judge Jane 2 against
    rows = [
        '"1","00","Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","20","15","5"\n',
        '"1","00","Robert L. Ehrlich, Jr.","REP","Governor / Lt. Governor","","","","10","2","0"\n',
        '"1","00","Judge Jane","","Judge Court of Appeals","01","Y","","6","0","0"\n',
        '"2","00","Judge Jane","","Judge Court of Appeals","01","Y","","1","1","0"\n',
    ]
    assert results._check_statewide(GG10, "all", frames, check_csv(LEGS_GENERAL_HEAD, rows)) == (
        3,
        1,
        [],
    )
    # the AGAINST rows are compared on the same column: the counties' total against (4) is not it
    rows[-1] = rows[-1].replace('"1","1","0"', '"2","2","0"')
    with pytest.raises(results.ResultsParseError, match="votes against sum to 2.*AGAINST row is 4"):
        results._check_statewide(GG10, "all", frames, check_csv(LEGS_GENERAL_HEAD, rows))


def test_statewide_check_primary_header_without_vote_type():
    frames = [read_named(p14_csv(), GP14, c, "rep") for c in ("Allegany", "Kent")]
    # two counties' election-night votes: Hogan 40, Craig 12
    good = check_csv(
        CONG_PRIMARY_HEAD,
        [
            '"00","Larry Hogan","REP","Governor / Lt. Governor","","Y","30","8","2"\n',
            '"00","David R. Craig","REP","Governor / Lt. Governor","","","10","2","0"\n',
        ],
    )
    assert results._check_statewide(GP14, "rep", frames, good) == (2, 0, [])
    with pytest.raises(results.ResultsParseError, match="Craig"):
        results._check_statewide(
            GP14, "rep", frames, good.replace(b'"10","2","0"', b'"10","2","1"')
        )
    legs = check_csv(LEGS_PRIMARY_HEAD, [ln + "\n" for ln in good.decode().splitlines()[1:]])
    frames10 = [read_named(p14_csv(), GP10, c, "rep") for c in ("Allegany", "Kent")]
    assert results._check_statewide(GP10, "rep", frames10, legs) == (2, 0, [])
    with pytest.raises(results.ResultsParseError, match="no district columns"):
        results._check_statewide(
            GP14,
            "rep",
            frames,
            check_csv(BREAKDOWN_HEAD.replace("Congressional District", "Nope"), []),
        )


# ---- the two statewide rows the SBE's own files disagree on ---------------------------------
# PG12's State_Congressional_Districts file, County 00 row for the presidential write-ins,
# verbatim: the districts sum to 7247; the 24 county files' Election Night Votes sum to 7245.
# Kent's row is real (18 election-night votes); the other county here stands in for the rest.

CONG_2012_HEAD = CONG_2018_HEAD.replace(
    '"CONG 04","CONG 08","CONG 07","CONG 01","CONG 05","CONG 03","CONG 02","CONG 06"',
    '"CONG 08","CONG 04","CONG 07","CONG 01","CONG 03","CONG 05","CONG 02","CONG 06"',
)
PG12_WRITE_INS_STATE = (
    '"1","00","Other Write-Ins","BOT","President - Vice Pres","","","Y","1043","684","701",'
    '"987","1007","954","999","872"\n'
)
PG12_WRITE_INS_KENT = (
    '"Other Write-Ins","BOT","President - Vice Pres","","","Y","1","","18","0","2","","0","","0","",'
    '"21",""\n'
)
PG12_WRITE_INS_REST = (
    '"Other Write-Ins","BOT","President - Vice Pres","","","Y","0","","7227","","0","","0","","0","",'
    '"7227",""\n'
)


def _pg12_frames(rest: str = PG12_WRITE_INS_REST):
    return [
        read_named(g14_csv(G14_ROWS + PG12_WRITE_INS_KENT), PG12, "Kent", "all"),
        read_named(g14_csv(G14_ROWS + rest), PG12, "Allegany", "all"),
    ]


PG12_STATE_ROWS = [
    '"1","00","Larry Hogan","REP","Governor / Lt. Governor","","Y","","40","","","","","","",""\n',
    '"1","00","Anthony G. Brown","DEM","Governor / Lt. Governor","","","","12","","","","","","",""\n',
    '"1","00","Peter Franchot","DEM","Comptroller","","Y","","16","","","","","","",""\n',
]


def test_statewide_check_exceptions_are_exactly_the_two_listed_pairs():
    assert results.STATEWIDE_CHECK_EXCEPTIONS == {
        ("PG12", "President - Vice Pres", "", "Other Write-Ins", "BOT"): (7245, 7247),
        ("GG10", "U.S. Senator", "", "Other Write-Ins", "BOT"): (1359, 1361),
    }
    state = check_csv(CONG_2012_HEAD, PG12_STATE_ROWS + [PG12_WRITE_INS_STATE])
    frames = _pg12_frames()
    assert sum(f["election_night_votes"].to_list()[3] for f in frames) == 7245
    assert results._check_statewide(PG12, "all", frames, state) == (
        4,
        0,
        ["President - Vice Pres / Other Write-Ins (BOT): counties 7245, statewide row 7247"],
    )
    # the listed pair only: a state row of 7246, or county rows summing to 7244, still fail
    with pytest.raises(results.ResultsParseError, match="Write-Ins: counties sum to 7245.*7246"):
        results._check_statewide(PG12, "all", frames, state.replace(b'"872"', b'"871"'))
    short = _pg12_frames(PG12_WRITE_INS_REST.replace("7227", "7226"))
    with pytest.raises(results.ResultsParseError, match="Write-Ins: counties sum to 7244.*7247"):
        results._check_statewide(PG12, "all", short, state)
    # a third mismatching row alongside the listed one still fails
    with pytest.raises(results.ResultsParseError, match="Hogan: counties sum to 40.*41"):
        results._check_statewide(PG12, "all", frames, state.replace(b'"Y","","40"', b'"Y","","41"'))
    # the same row and figures in another election are not listed
    with pytest.raises(results.ResultsParseError, match="Write-Ins: counties sum to 7245.*7247"):
        results._check_statewide(
            GG14, "all", [f.with_columns(pl.lit("GG14").alias("election")) for f in frames], state
        )


def fill_named(env, election, check_data_by_party_file, skip=(), overrides=None):
    """Archive a named-era election: the county files are the module's fixtures for its year and
    kind (one general file, a dem and a rep primary file; non files are left out), the check
    files come from the mapping (party file -> bytes; a party file not in it has no check), and
    `overrides` (source id -> bytes) replaces particular county files."""
    for f in results.county_files(election):
        if f.source_id in skip or f.party_file == "non":
            continue
        if f.role == "check":
            if f.party_file not in check_data_by_party_file:
                continue
            data = check_data_by_party_file[f.party_file]
        elif overrides and f.source_id in overrides:
            data = overrides[f.source_id]
        elif election.kind == "general":
            data = g10_csv() if election.code == "GG10" else g14_csv()
        elif f.party_file == "dem":
            data = p14_csv(
                '"Anthony G. Brown","DEM","Governor / Lt. Governor","","Y","1","1","0","0","0","2"\n'
            )
        else:
            data = p14_csv()
        archive(env, election, f, data)


ELECTION_NIGHT_NOTE = (
    " on election-night votes (the SBE's district files for 2010-2018 carry election-day votes "
    "only)"
)


def test_parse_gg10_checks_against_the_legislative_file_on_election_night_votes(env):
    # 24 counties' election-night votes; Baltimore City's file is the board's own, so its
    # Governor write-ins (60, 40, 148 on election night) need statewide rows too
    totals = check_csv(
        LEGS_GENERAL_HEAD,
        [
            f'"1","00","Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","{23 * 20}","106268","0"\n',
            f'"1","00","Robert L. Ehrlich, Jr.","REP","Governor / Lt. Governor","","","","{23 * 6}","22204","0"\n',
            '"1","00","Ralph Jaffe","DEM","Governor / Lt. Governor","","","Y","60","0","0"\n',
            '"1","00","Corrogan R. Vaughn","UNF","Governor / Lt. Governor","","","Y","40","0","0"\n',
            '"1","00","Other Write-Ins","BOT","Governor / Lt. Governor","","","Y","148","0","0"\n',
            f'"1","00","Judge Jane","","Judge Court of Appeals","01","Y","","{23 * 3}","0","0"\n',
            f'"2","00","Judge Jane","","Judge Court of Appeals","01","Y","","{23 * 1}","0","0"\n',
        ],
    )
    fill_named(
        env,
        GG10,
        {"all": totals},
        overrides={"sbe-results-GG10-county-03-all": GG10_BALTIMORE_CITY},
    )
    written, failed = results.results_parse("GG10", data_dir=env / "data")
    assert failed == [] and written == 24
    county = env / "data" / "results" / "GG10" / "county"
    out = county / "19-st-marys-all.csv"
    assert out.exists()
    assert "absentees_3_against" in out.read_text().splitlines()[0]
    side = json.loads(out.with_name("19-st-marys-all_provenance.json").read_text())
    assert side["parser_version"] == results.PARSER_VERSION == "skipjack 0.4.0"
    assert side["checks"] == {
        "rows": 3,
        "row_totals": "ok",
        "statewide_check": "ok: 7 statewide rows (6 for, 1 against) match "
        "State_Legislative_Districts_2010_General.csv" + ELECTION_NIGHT_NOTE,
    }
    # Baltimore City's sidecar records the board's arithmetic, and the CSV keeps its totals
    city = json.loads((county / "03-baltimore-city-all_provenance.json").read_text())["checks"]
    assert city["rows"] == 17 and city["statewide_check"] == side["checks"]["statewide_check"]
    assert city["row_totals"].startswith("15 rows whose printed Total Votes is not the sum")
    assert len(city["row_total_exceptions"]) == 15
    assert city["row_total_exceptions"][0] == {
        "office": "Governor / Lt. Governor",
        "district": "",
        "candidate": "Ralph Jaffe",
        "party": "DEM",
        "total_votes": 62,
        "sum_of_vote_types": 71,
    }
    assert "row_total_exceptions" not in side["checks"]
    city_csv = (county / "03-baltimore-city-all.csv").read_text().splitlines()
    assert city_csv[3].startswith("GG10,Baltimore City,all,Governor / Lt. Governor,")
    assert city_csv[3].endswith(",62,")
    assert results.results_parse("GG10", data_dir=env / "data")[0] == 0


def test_parse_gg10_with_the_wrong_number_of_row_total_exceptions_writes_nothing(env):
    bad = GG10_BALTIMORE_CITY_BAD_ROWS.splitlines(keepends=True)
    fourteen = g10_csv(GG10_BALTIMORE_CITY_GOOD_ROWS + "".join(bad[:14]))
    fill_named(
        env,
        GG10,
        {"all": check_csv(LEGS_GENERAL_HEAD, [])},
        overrides={"sbe-results-GG10-county-03-all": fourteen},
    )
    written, failed = results.results_parse("GG10", data_dir=env / "data")
    assert written == 0 and len(failed) == 1
    assert "Baltimore_City_County_2010_General.csv" in failed[0]
    assert "14 rows whose total" in failed[0] and "15 are listed" in failed[0]
    assert not (env / "data" / "results" / "GG10").exists()


def test_parse_pg12_records_the_listed_statewide_exception(env):
    state = check_csv(
        CONG_2012_HEAD,
        [
            f'"1","00","Larry Hogan","REP","Governor / Lt. Governor","","Y","","{24 * 20}","","","","","","",""\n',
            f'"1","00","Anthony G. Brown","DEM","Governor / Lt. Governor","","","","{24 * 6}","","","","","","",""\n',
            f'"1","00","Peter Franchot","DEM","Comptroller","","Y","","{24 * 8}","","","","","","",""\n',
            PG12_WRITE_INS_STATE,
        ],
    )
    overrides = {
        "sbe-results-PG12-county-15-all": g14_csv(G14_ROWS + PG12_WRITE_INS_KENT),
        "sbe-results-PG12-county-01-all": g14_csv(G14_ROWS + PG12_WRITE_INS_REST),
    }
    fill_named(env, PG12, {"all": state}, overrides=overrides)
    written, failed = results.results_parse("PG12", data_dir=env / "data")
    assert failed == [] and written == 24
    side = json.loads(
        (env / "data" / "results" / "PG12" / "county" / "15-kent-all_provenance.json").read_text()
    )
    assert side["checks"]["statewide_check"] == (
        "ok: 4 statewide rows (4 for, 0 against) match "
        "State_Congressional_Districts_2012_General.csv" + ELECTION_NIGHT_NOTE + "; the board's "
        "own figures disagree on President - Vice Pres / Other Write-Ins (BOT): counties 7245, "
        "statewide row 7247 (listed in STATEWIDE_CHECK_EXCEPTIONS, recorded, not corrected)"
    )
    # and the Kent row's lone-zero Against cells came out as no figure
    kent = (env / "data" / "results" / "PG12" / "county" / "15-kent-all.csv").read_text()
    assert kent.splitlines()[4].endswith(",Other Write-Ins,BOT,0,1,1,,18,,2,,0,,0,,21,")


def test_parse_named_general_mismatch_writes_nothing(env):
    two_counties = check_csv(
        CONG_GENERAL_HEAD,
        ['"1","00","Larry Hogan","REP","Governor / Lt. Governor","","Y","","30","8","2"\n'],
    )
    fill_named(env, GG14, {"all": two_counties})
    written, failed = results.results_parse("GG14", data_dir=env / "data")
    assert written == 0 and failed and "no statewide row" in failed[0] and "Brown" in failed[0]
    assert not (env / "data" / "results" / "GG14").exists()
    # and a wrong statewide figure (the other candidates add up over 24 counties)
    wrong = check_csv(
        CONG_GENERAL_HEAD,
        [
            '"1","00","Larry Hogan","REP","Governor / Lt. Governor","","Y","","30","8","2"\n',
            f'"1","00","Anthony G. Brown","DEM","Governor / Lt. Governor","","","","{24 * 6}","0","0"\n',
            f'"1","00","Peter Franchot","DEM","Comptroller","","Y","","{24 * 8}","0","0"\n',
        ],
    )
    fill_named(env, GG14, {"all": wrong})
    written, failed = results.results_parse("GG14", data_dir=env / "data")
    assert written == 0 and failed and "Hogan" in failed[0]
    # a check file carrying the 24 counties' totals is just as wrong for these years
    totals = check_csv(
        CONG_GENERAL_HEAD,
        [
            f'"1","00","Larry Hogan","REP","Governor / Lt. Governor","","Y","","{24 * 38}","0","0"\n',
            f'"1","00","Anthony G. Brown","DEM","Governor / Lt. Governor","","","","{24 * 12}","0","0"\n',
            f'"1","00","Peter Franchot","DEM","Comptroller","","Y","","{24 * 15}","0","0"\n',
        ],
    )
    fill_named(env, GG14, {"all": totals})
    written, failed = results.results_parse("GG14", data_dir=env / "data")
    assert written == 0 and failed
    assert f"Franchot: counties sum to {24 * 8}, statewide row is {24 * 15}" in failed[0]


def test_parse_named_general_without_check_file_fails(env):
    fill_named(env, GG14, {})
    written, failed = results.results_parse("GG14", data_dir=env / "data")
    assert written == 0 and len(failed) == 1
    assert "State_Congressional_Districts_2014_General.csv" in failed[0]


def test_parse_gp14_checks_each_party_file_and_missing_non_check_is_n_a(env):
    dem = check_csv(
        CONG_PRIMARY_HEAD,
        ['"00","Anthony G. Brown","DEM","Governor / Lt. Governor","","Y","12","6","6"\n'],
    )
    rep = check_csv(
        CONG_PRIMARY_HEAD,
        [
            f'"00","Larry Hogan","REP","Governor / Lt. Governor","","Y","{24 * 15}","{24 * 5}","0"\n',
            f'"00","David R. Craig","REP","Governor / Lt. Governor","","","{24 * 6}","0","0"\n',
        ],
    )
    fill_named(env, GP14, {"dem": dem, "rep": rep})
    non = next(x for x in results.county_files(GP14) if x.source_id.endswith("county-05-non"))
    archive(
        env, GP14, non, p14_csv('"N N","NON","Board of Education","","Y","1","0","0","0","0","1"\n')
    )
    written, failed = results.results_parse("GP14", data_dir=env / "data")
    assert failed == [] and written == 49
    county = env / "data" / "results" / "GP14" / "county"

    def side(name):
        return json.loads((county / f"{name}_provenance.json").read_text())["checks"]

    assert side("01-allegany-dem")["statewide_check"] == (
        "ok: 1 statewide rows (1 for, 0 against) match "
        "State_Congressional_Districts_Democratic_2014_Primary.csv" + ELECTION_NIGHT_NOTE
    )
    assert side("01-allegany-rep")["statewide_check"] == (
        "ok: 2 statewide rows (2 for, 0 against) match "
        "State_Congressional_Districts_Republican_2014_Primary.csv" + ELECTION_NIGHT_NOTE
    )
    assert side("05-calvert-non")["statewide_check"] == (
        "n/a: State_Congressional_Districts_NonPartisan_2014_Primary.csv not published"
    )


def test_parse_named_primary_missing_dem_check_fails(env):
    rep = check_csv(
        CONG_PRIMARY_HEAD,
        [
            f'"00","Larry Hogan","REP","Governor / Lt. Governor","","Y","{24 * 15}","{24 * 5}","0"\n',
            f'"00","David R. Craig","REP","Governor / Lt. Governor","","","{24 * 6}","0","0"\n',
        ],
    )
    fill_named(env, GP14, {"rep": rep})
    written, failed = results.results_parse("GP14", data_dir=env / "data")
    assert written == 0 and failed and "Democratic_2014_Primary" in failed[0]
    assert not (env / "data" / "results" / "GP14").exists()


def test_fetch_named_primary_skips_absent_non_files(env, monkeypatch):
    def body(url):
        if "NonPartisan" in url:
            return b"<html>nope</html>"
        if "State_Congressional" in url:
            return check_csv(CONG_PRIMARY_HEAD, [])
        return p14_csv()

    monkeypatch.setattr(fetch.httpx, "Client", fake_client(body))
    records = results.results_fetch("GP14")
    assert len(records) == 50  # 24 x (dem, rep) + the dem and rep check files
    assert (
        env / "archives/sbe/results/GP14/Baltimore_City_County_Democratic_2014_Primary.csv"
    ).exists()
    assert (
        env / "archives/sbe/results/GP14/State_Congressional_Districts_Republican_2014_Primary.csv"
    ).exists()
    assert not any("NonPartisan" in r.source_url for r in records)
