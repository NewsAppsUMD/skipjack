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
    assert set(results.RESULTS_ELECTIONS) == {
        "GP22", "GG22", "PP24", "PG24", "GP26", "GG26", "PG20", "PP20",
    }  # fmt: skip
    gg26 = results.RESULTS_ELECTIONS["GG26"]
    assert gg26.published is False
    assert all(e.published for c, e in results.RESULTS_ELECTIONS.items() if c != "GG26")
    named = {c for c, e in results.RESULTS_ELECTIONS.items() if e.layout == "named2020"}
    assert named == {"PG20", "PP20"}
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
            '"2","00","State of Maryland","Judge Elsewhere","","Some Judge","BOT","Y","","999","1" \n',
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


# ---- the 2020 layout -----------------------------------------------------------------------

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
    frame = results._read_named2020(g20_csv(), PG20, "Allegany", "all")
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
        results._read_named2020(g20_csv(bad), PG20, "Allegany", "all")


def test_read_2020_primary_unquoted():
    rows = (
        "Joe Biden,DEM,President - Vice Pres,,Y,3,2,1,6\n"
        "Delegates Person,DEM,Board of Education     ,001,,1,1,0,2\n"
    )
    frame = results._read_named2020(p20_csv(rows), PP20, "Kent", "dem")
    assert frame.columns[9:] == [
        "vote_by_mail_votes", "in_person_voting_votes", "provisional_votes", "total_votes",
    ]  # fmt: skip
    assert frame["office"].to_list() == ["President - Vice Pres", "Board of Education"]
    assert frame["district"].to_list() == ["", "001"]
    assert frame["write_in"].to_list() == [0, 0]
    assert frame["in_person_voting_votes"].to_list() == [2, 1]
    with pytest.raises(results.ResultsParseError, match="Joe Biden"):
        results._read_named2020(p20_csv(rows.replace(",3,2,1,6", ",3,2,1,7")), PP20, "Kent", "dem")


def test_read_2020_primary_duplicate_unused_columns_and_cp1252():
    # Dorchester's Democratic file has two blank "N/A Votes" columns and is quoted; some counties'
    # files hold a cp1252 byte (an n with tilde)
    head = P20_HEAD.replace("Total Votes", '"N/A Votes","N/A Votes",Total Votes')
    row = "Ni\xf1o Person,DEM,President - Vice Pres,,Y,3,2,1,,,6\n"
    data = (head + row).encode("latin-1")
    frame = results._read_named2020(data, PP20, "Dorchester", "dem")
    assert frame["candidate"].to_list() == ["Ni\xf1o Person"]
    assert frame["total_votes"].to_list() == [6]
    assert "N/A Votes" not in frame.columns


def _g20_frames(rows_by_county):
    return [results._read_named2020(g20_csv(r), PG20, c, "all") for c, r in rows_by_county.items()]


def test_statewide_check_all_state():
    frames = _g20_frames({"Allegany": G20_ROWS, "Kent": G20_ROWS})
    assert results._check_statewide_all_state(PG20, frames, GOOD_ALL_STATE) == 3
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
    assert results._check_statewide_all_state(PG20, frames, GOOD_ALL_STATE) == 3


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
    assert side["checks"]["statewide_check"].startswith("ok: 3")
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
    frame = results._read_named2020(
        p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,1,,1\n"), PP20, "Frederick", "dem"
    )
    assert frame.row(0, named=True)["vote_by_mail_votes"] == 0
    assert frame.row(0, named=True)["total_votes"] == 1
    with pytest.raises(results.ResultsParseError, match="Deval Patrick"):
        results._read_named2020(
            p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,1,,2\n"), PP20, "Frederick", "dem"
        )
    with pytest.raises(results.ResultsParseError, match="total votes None"):
        results._read_named2020(
            p20_csv("Deval Patrick,DEM,President - Vice Pres,8,,,1,,\n"), PP20, "Frederick", "dem"
        )


def test_coded_still_rejects_blank_vote_cell():
    with pytest.raises(results.ResultsParseError, match="blank"):
        results._read_coded(
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
        "ok: 1 statewide contest rows match GP26_CongressionalBreakDownRepublican.csv"
    )


def test_ok_statewide_check_names_the_real_file(env):
    fill(env, results.RESULTS_ELECTIONS["GP26"])
    results.results_parse("GP26", data_dir=env / "data")
    assert _sidecar(env, "01-allegany-dem")["checks"]["statewide_check"] == (
        "ok: 2 statewide contest rows match GP26_CongressionalBreakDownDemocratic.csv"
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
        results._read_coded((BOM + GENERAL_HEAD + row).encode(), e, c, "all")
        for c in ("Allegany", "Kent")
    ]


def _judge_breakdown(for_total, against_total):
    base = (
        '"{}","00","State of Maryland","Judge Court of Appeals","01","Jane Roe","","","{}","0" \n'
    )
    return (BOM + JUDGE_HEAD + base.format(1, for_total) + base.format(2, against_total)).encode()


def test_statewide_check_against_totals_match_and_mismatch():
    e, frames = _judge_frames()
    # both blank-party county rows; counties sum to 20 FOR and 6 AGAINST
    assert results._check_statewide(e, "all", frames, _judge_breakdown(20, 6)) == 2
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
    assert results._check_statewide(e, "dem", frames, ok) == 1
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
        results._read_coded(primary_csv(""), results.RESULTS_ELECTIONS["GP26"], "Kent", "dem")


def test_missing_columns_message_names_election_layout_and_file():
    bad = (BOM + '"Office Name","Total Votes"\n"x","1"\n').encode()
    with pytest.raises(results.ResultsParseError) as exc:
        results._read_coded(bad, results.RESULTS_ELECTIONS["GP26"], "Kent", "dem")
    msg = str(exc.value)
    assert "GP26" in msg and "coded" in msg and "GP26_15DemocraticResults.csv" in msg
