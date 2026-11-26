"""Tests for the precinct results: file names, the precinct reader, the precinct-to-county check
and the gzipped output."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import polars as pl
import pytest

from skipjack.pipeline import fetch, results

# Not tests.<module>: another project's `tests` package on PYTHONPATH can shadow ours.
from conftest import archive, fake_client
from test_results import BOM, GENERAL_HEAD, fill2020

PG24 = results.RESULTS_ELECTIONS["PG24"]
ARCHIVE = "https://elections.maryland.gov/elections/archive"


def test_registry_precinct_and_question_flags():
    elections = results.RESULTS_ELECTIONS
    assert [c for c, e in elections.items() if not e.precincts] == ["PP20"]
    assert {c for c, e in elections.items() if e.questions} == {
        c for c, e in elections.items() if e.kind == "general"
    }
    assert elections["GG18"].precinct_vote_types == [("Election Night", "election_night")]
    assert elections["GP10"].precinct_vote_types == [("Election Night", "election_night")]
    assert PG24.precinct_vote_types == PG24.vote_types
    assert elections["PG20"].precinct_vote_types == results.VOTE_TYPES_2020_GENERAL
    assert elections["GG10"].question_check is False
    assert elections["PG12"].question_check is True and PG24.question_check is True


def test_precinct_file_names_and_ids():
    by_id = {f.source_id: f for f in results.precinct_files(PG24)}
    assert len(by_id) == 24
    kent = by_id["sbe-results-PG24-precinct-15-all"]
    assert kent.url == f"{ARCHIVE}/2024/election_data/PG24_15PrecinctsResults.csv"
    assert kent.local_path.parts[-4:] == ("sbe", "results-precinct", "PG24", kent.file_name)
    gp22 = {f.source_id: f for f in results.precinct_files(results.RESULTS_ELECTIONS["GP22"])}
    assert gp22["sbe-results-GP22-precinct-15-non"].file_name == "GP22_15PrecinctsNonPartisan.csv"
    assert gp22["sbe-results-GP22-precinct-15-dem"].file_name == "GP22_15PrecinctsDemocratic.csv"
    gg18 = {f.source_id: f for f in results.precinct_files(results.RESULTS_ELECTIONS["GG18"])}
    assert gg18["sbe-results-GG18-precinct-03-all"].file_name == (
        "Baltimore_City_By_Precinct_2018_General.csv"
    )
    assert gg18["sbe-results-GG18-precinct-04-all"].file_name == (
        "Baltimore_By_Precinct_2018_General.csv"
    )
    assert gg18["sbe-results-GG18-precinct-15-all"].url == (
        f"{ARCHIVE}/2018/election_data/Kent_By_Precinct_2018_General.csv"
    )
    assert gg18["sbe-results-GG18-precinct-19-all"].file_name == (
        "St._Marys_By_Precinct_2018_General.csv"
    )
    gp10 = {f.source_id: f for f in results.precinct_files(results.RESULTS_ELECTIONS["GP10"])}
    assert gp10["sbe-results-GP10-precinct-15-rep"].file_name == (
        "Kent_By_Precinct_Republican_2010_Primary.csv"
    )
    assert results.precinct_files(results.RESULTS_ELECTIONS["PP20"]) == []


def test_question_file_names_and_ids():
    by_id = {f.source_id: f for f in results.question_files(PG24)}
    assert len(by_id) == 49  # 24 county files, 24 precinct files, one statewide breakdown
    assert by_id["sbe-results-PG24-question-15"].file_name == "PG24_15QuestionResults.csv"
    assert by_id["sbe-results-PG24-question-precinct-15"].file_name == (
        "PG24_15QuestionbyPrecinctsResults.csv"
    )
    check = by_id["sbe-results-PG24-question-check"]
    assert check.file_name == "PG24_AllQuestionsCongressionalBreakDown.csv"
    assert check.local_path.parts[-3:-1] == ("results-questions", "PG24")
    gg18 = {f.source_id: f for f in results.question_files(results.RESULTS_ELECTIONS["GG18"])}
    assert gg18["sbe-results-GG18-question-15"].file_name == "Kent_Questions_County_2018.csv"
    assert gg18["sbe-results-GG18-question-precinct-15"].file_name == (
        "Kent_Questions_Precinct_2018.csv"
    )
    assert gg18["sbe-results-GG18-question-check"].file_name == (
        "Questions_State_Congressional_Districts_2018.csv"
    )
    gg10 = results.question_files(results.RESULTS_ELECTIONS["GG10"])
    assert len(gg10) == 48 and all(f.role != "question-check" for f in gg10)
    assert results.question_files(results.RESULTS_ELECTIONS["GP26"]) == []


def test_files_for_each_level():
    assert results.files_for(PG24, "county") == results.county_files(PG24)
    assert results.files_for(PG24, "precinct") == results.precinct_files(PG24)
    assert results.files_for(PG24, "questions") == results.question_files(PG24)
    assert len(results.files_for(PG24, "all")) == 25 + 24 + 49
    with pytest.raises(ValueError):
        results.files_for(PG24, "towns")


def test_fetch_precinct_level_archives_only_the_precinct_files(env, monkeypatch):
    monkeypatch.setattr(fetch.httpx, "Client", fake_client(lambda url: b'"County"\n"15"\n'))
    records = results.results_fetch("PG24", level="precinct")
    assert len(records) == 24
    assert all("/results-precinct/PG24/" in r.local_path for r in records)
    assert records[0].document_title == "PG24 Allegany results by precinct (all)"
    assert results.results_fetch("PG24", level="precinct") == []  # archived already
    questions = results.results_fetch("PG24", level="questions")
    assert len(questions) == 49
    assert {r.document_title for r in questions} >= {
        "PG24 Kent ballot questions",
        "PG24 Kent ballot questions by precinct",
        "PG24 ballot questions congressional breakdown",
    }


def test_fetch_html_non_partisan_precinct_file_is_logged_not_a_failure(env, monkeypatch, caplog):
    def body(url):
        if "PrecinctsNonPartisan" in url:
            return b"\xef\xbb\xbf<!DOCTYPE html><html></html>"
        return b'"County"\n"15"\n'

    monkeypatch.setattr(fetch.httpx, "Client", fake_client(body))
    failures: list[str] = []
    with caplog.at_level(logging.INFO, logger=results.logger.name):
        records = results.results_fetch("GP22", failures=failures, level="precinct")
    assert failures == []
    assert len(records) == 48  # 24 counties x dem, rep
    assert not any("NonPartisan" in r.local_path for r in records)
    assert "no non-partisan precinct file for Kent (normal)" in caplog.text


def test_cli_results_fetch_passes_the_level(monkeypatch):
    from skipjack import cli

    seen = {}

    def fake_fetch(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(results, "results_fetch", fake_fetch)
    cli.main(["results-fetch", "--election", "PG24", "--level", "precinct"])
    assert seen["level"] == "precinct" and seen["election"] == "PG24"


# ---- the coded-layout precinct reader --------------------------------------------------------

PRECINCT_GENERAL_HEAD = (
    '"County","County Name","Election District - Precinct","Congressional","Legislative",'
    '"Office Name","Office District","Candidate Name","Party","Winner","Write-In?",'
    '"Early Votes","Early Votes Against","Election Night Votes","Election Night Votes Against",'
    '"Mail-In Ballot 1 Votes","Mail-In Ballot 1 Votes Against","Provisional Votes",'
    '"Provisional Votes Against","Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against" \n'
)
# Two precincts of one contest, as PG24's files print them (CC becomes the county's code).
# Harris 70+149+23+22+124 = 388 and 30+51+7+8+16 = 112; Trump 99+390+12+38+51 = 590 and
# 1+10+0+2+9 = 22: the county rows below are the sums.
PRECINCT_ROWS = (
    '"CC","Kent County","001-001","01","36","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","70","","149","","23","","22","","124","" \n'
    '"CC","Kent County","001-001","01","36","President - Vice Pres","","Donald J. Trump and JD Vance","REP","","","99","","390","","12","","38","","51","" \n'
    '"CC","Kent County","001-002","01","36","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","30","","51","","7","","8","","16","" \n'
    '"CC","Kent County","001-002","01","36","President - Vice Pres","","Donald J. Trump and JD Vance","REP","","","1","","10","","0","","2","","9","" \n'
)
COUNTY_ROWS = (
    '"President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","100","","200","","30","","30","","140","","500","" \n'
    '"President - Vice Pres","","Donald J. Trump and JD Vance","REP","","","100","","400","","12","","40","","60","","612","" \n'
)
PRECINCT_COLUMNS = [
    "election", "county", "party_file", "precinct", "congressional", "legislative", "office",
    "district", "candidate", "party", "winner", "write_in",
]  # fmt: skip


def precinct_csv(code: str = "15", rows: str = PRECINCT_ROWS) -> bytes:
    return (BOM + PRECINCT_GENERAL_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def county_csv(rows: str = COUNTY_ROWS) -> bytes:
    return (BOM + GENERAL_HEAD + rows).encode()


def test_read_precinct_coded_general_computes_the_totals():
    read = results.read_precinct(precinct_csv(), PG24, "Kent", "all")
    frame = read.frame
    assert frame.columns[:12] == PRECINCT_COLUMNS
    assert frame.columns[-2:] == ["total_votes", "total_against"]
    assert frame["precinct"].to_list() == ["001-001", "001-001", "001-002", "001-002"]
    assert frame["congressional"][0] == "01" and frame["legislative"][0] == "36"
    assert frame["county"].unique().to_list() == ["Kent"]
    assert frame["total_votes"].to_list() == [388, 590, 112, 22]
    assert frame["total_against"].to_list() == [None, None, None, None]
    assert frame["winner"].to_list() == [1, 0, 1, 0]
    assert read.row_total_exceptions == []


def test_read_precinct_sums_the_against_votes_of_a_retention_question():
    row = (
        '"15","Kent County","001-001","01","36","Judge","","Jane Roe","","","",'
        '"3","1","3","2","3","1","0","0","1","0" \n'
    )
    frame = results.read_precinct((PRECINCT_GENERAL_HEAD + row).encode(), PG24, "Kent", "all").frame
    assert frame["total_votes"].to_list() == [10] and frame["total_against"].to_list() == [4]


def test_read_precinct_coded_primary():
    head = (
        '"County","County Name","Election District - Precinct","Congressional","Legislative",'
        '"Office Name","Office District","Candidate Name","Party","Winner","Early Votes",'
        '"Election Night Votes","Mail-In Ballot 1 Votes","Provisional Votes",'
        '"Mail-In Ballot 2 Votes" \n'
    )
    row = '"15","Kent County","1-12","01","36","President - Vice Pres","","Joe Biden","DEM","Y","11","18","16","6","3" \n'
    e = results.RESULTS_ELECTIONS["PP24"]
    frame = results.read_precinct((head + row).encode(), e, "Kent", "dem").frame
    assert frame["precinct"].to_list() == ["001-012"]  # normalized to DDD-PPP
    assert frame["total_votes"].to_list() == [54]
    assert "total_against" not in frame.columns and frame["write_in"].to_list() == [0]


def test_read_precinct_refuses_another_countys_rows_and_a_bad_precinct():
    with pytest.raises(results.ResultsParseError, match="not Kent's code 15"):
        results.read_precinct(precinct_csv("16"), PG24, "Kent", "all")
    bad = PRECINCT_ROWS.replace('"001-002"', '"Absentee"')
    with pytest.raises(results.ResultsParseError, match="'Absentee' is not district-precinct"):
        results.read_precinct(precinct_csv("15", bad), PG24, "Kent", "all")
    bad = PRECINCT_ROWS.replace('"001-002"', '"-001"')
    with pytest.raises(results.ResultsParseError, match="'-001' is not district-precinct"):
        results.read_precinct(precinct_csv("15", bad), PG24, "Kent", "all")


def test_read_precinct_coded_refuses_a_blank_vote_cell():
    blank = PRECINCT_ROWS.replace('"390"', '""')
    with pytest.raises(results.ResultsParseError, match="Trump.*blank Election Night Votes"):
        results.read_precinct(precinct_csv("15", blank), PG24, "Kent", "all")


def test_read_precinct_reads_the_write_in_flag():
    row = '"15","Kent County","001-001","01","36","President - Vice Pres","","A Write-In","","","Y","0","","1","","0","","0","","0","" \n'
    frame = results.read_precinct(
        precinct_csv("15", PRECINCT_ROWS + row), PG24, "Kent", "all"
    ).frame
    assert frame["write_in"].to_list() == [0, 0, 0, 0, 1]


def test_read_precinct_names_the_precinct_file_when_columns_are_missing():
    head = PRECINCT_GENERAL_HEAD.replace('"Legislative",', "")
    rows = PRECINCT_ROWS.replace('"36",', "")
    with pytest.raises(results.ResultsParseError, match="PG24_15PrecinctsResults.csv.*Legislative"):
        results.read_precinct((head + rows.replace('"CC"', '"15"')).encode(), PG24, "Kent", "all")


# ---- the precinct-to-county check -----------------------------------------------------------

TOTAL_COLUMNS = [
    "early_votes", "early_against", "election_night_votes", "election_night_against",
    "mail_in_1_votes", "mail_in_1_against", "provisional_votes", "provisional_against",
    "mail_in_2_votes", "mail_in_2_against", "total_votes", "total_against",
]  # fmt: skip


def frames(precincts: str = PRECINCT_ROWS, county: str = COUNTY_ROWS):
    return (
        results.read_precinct(precinct_csv("15", precincts), PG24, "Kent", "all").frame,
        results._read_coded(county_csv(county), PG24, "Kent", "all").frame,
    )


def test_sidecar_path_drops_csv_and_gz():
    assert results.sidecar_path(Path("d/15-kent-all.csv.gz")) == Path(
        "d/15-kent-all_provenance.json"
    )
    assert results.sidecar_path(Path("d/15-kent-all.csv")) == Path("d/15-kent-all_provenance.json")


def test_precinct_check_compares_every_vote_type_and_the_totals():
    check = results._check_precinct_county(PG24, "Kent", "all", *frames())
    assert check == results.SumCheck(contests=1, rows=2, columns=TOTAL_COLUMNS, exceptions=[])


def test_precinct_check_names_the_row_and_column_that_differ():
    precincts = PRECINCT_ROWS.replace('"390"', '"389"')
    with pytest.raises(
        results.ResultsParseError,
        match=r"Trump and JD Vance \(REP\): precincts sum to 399 election_night_votes, "
        r"the county file says 400",
    ):
        results._check_precinct_county(PG24, "Kent", "all", *frames(precincts))


def test_precinct_check_needs_every_contest_on_both_sides():
    senate = (
        '"U.S. Senator","","Some One","DEM","Y","","1","","0","","0","","0","","0","","1","" \n'
    )
    with pytest.raises(results.ResultsParseError, match="U.S. Senator has no precinct rows"):
        results._check_precinct_county(PG24, "Kent", "all", *frames(county=COUNTY_ROWS + senate))
    senate_precinct = '"CC","Kent County","001-001","01","36","U.S. Senator","","Some One","DEM","Y","","1","","0","","0","","0","","0","" \n'
    with pytest.raises(results.ResultsParseError, match="U.S. Senator is not in the county file"):
        results._check_precinct_county(
            PG24, "Kent", "all", *frames(precincts=PRECINCT_ROWS + senate_precinct)
        )


def test_a_county_row_of_zeros_needs_no_precinct_rows():
    zero = '"President - Vice Pres","","Other Write-Ins","BOT","","Y","0","","0","","0","","0","","0","","0","" \n'
    check = results._check_precinct_county(PG24, "Kent", "all", *frames(county=COUNTY_ROWS + zero))
    assert check.rows == 3


def test_a_listed_precinct_exception_is_accepted_and_reported(monkeypatch):
    key = (
        "PG24",
        "Kent",
        "all",
        "President - Vice Pres",
        "",
        "Donald J. Trump and JD Vance",
        "REP",
    )
    monkeypatch.setitem(
        results.PRECINCT_CHECK_EXCEPTIONS, (*key, "election_night_votes"), (399, 400)
    )
    monkeypatch.setitem(results.PRECINCT_CHECK_EXCEPTIONS, (*key, "total_votes"), (611, 612))
    check = results._check_precinct_county(
        PG24, "Kent", "all", *frames(PRECINCT_ROWS.replace('"390"', '"389"'))
    )
    assert len(check.exceptions) == 2
    assert check.exceptions[0].endswith("election_night_votes: precincts 399, county file 400")


# ---- parse ------------------------------------------------------------------------------------


def fill_precincts(env, election=PG24, overrides=None, skip=()):
    """Archive every county's county file and precinct file: the same two-precinct fixture in each
    county under its own code; `overrides` (source id -> bytes) replaces a precinct file."""
    for f in results.county_files(election):
        if f.role == "county":
            archive(env, election, f, county_csv())
    for f in results.precinct_files(election):
        if f.source_id not in skip:
            archive(
                env, election, f, (overrides or {}).get(f.source_id) or precinct_csv(f.county_code)
            )


def test_parse_precincts_writes_gzipped_csvs_and_sidecars(env):
    fill_precincts(env)
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert failed == [] and written == 24
    out = env / "data" / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    frame = pl.read_csv(out, infer_schema=False)
    assert frame.height == 4 and frame["precinct"].to_list()[:2] == ["001-001", "001-001"]
    side = json.loads((out.parent / "15-kent-all_provenance.json").read_text())
    assert side["source_id"] == "sbe-results-PG24-precinct-15-all"
    assert side["family"] == "results" and side["geography"] == "precinct"
    assert side["checks"]["rows"] == 4
    assert side["checks"]["row_totals"].startswith("computed")
    assert side["checks"]["county_check"] == (
        "ok: the precincts sum to PG24_15CountyResults.csv's 2 rows (1 contests) on "
        + ", ".join(TOTAL_COLUMNS)
    )
    assert not (env / "data" / "results" / "PG24" / "county").exists()  # precinct level only


def test_parse_precincts_writes_the_same_bytes_every_time(env):
    fill_precincts(env)
    results.results_parse("PG24", data_dir=env / "data", level="precinct")
    out = env / "data" / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    first = out.read_bytes()
    assert (
        results.results_parse("PG24", force=True, data_dir=env / "data", level="precinct")[0] == 24
    )
    assert out.read_bytes() == first
    assert gzip.decompress(first).startswith(b"election,county,party_file,precinct")


def test_parse_precincts_is_all_or_nothing(env):
    bad = precinct_csv("24", PRECINCT_ROWS.replace('"149"', '"150"'))
    fill_precincts(env, overrides={"sbe-results-PG24-precinct-24-all": bad})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 0 and len(failed) == 1
    assert "PG24_24PrecinctsResults.csv" in failed[0] and "election_night_votes" in failed[0]
    assert not (env / "data" / "results" / "PG24" / "precinct").exists()


def test_parse_precincts_missing_required_file_fails(env):
    fill_precincts(env, skip={"sbe-results-PG24-precinct-05-all"})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 0 and "PG24_05PrecinctsResults.csv" in failed[0]


def test_current_precinct_csvs_need_no_archive(env):
    """The precinct archives are gitignored: a clone without them must not fail to parse."""
    fill_precincts(env)
    results.results_parse("PG24", data_dir=env / "data", level="precinct")
    for p in (env / "archives").rglob("*Precincts*"):
        p.unlink()
    assert results.results_parse("PG24", data_dir=env / "data", level="precinct") == (0, [])


def test_each_level_is_all_or_nothing_on_its_own(env):
    fill_precincts(env)  # no statewide check file: the county level fails
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="county")
    assert written == 0 and len(failed) == 1
    assert failed[0].startswith("PG24 county: missing required file PG24_CongressionalBreakDown")
    # an explicit --level precinct still runs, checked against the archived county files
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 24 and failed == []
    assert (env / "data" / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz").exists()
    assert not (env / "data" / "results" / "PG24" / "county").exists()


def test_level_all_skips_the_precincts_when_the_county_level_failed(env):
    fill_precincts(env)  # no statewide check file: the county level fails
    written, failed = results.results_parse("PG24", data_dir=env / "data")
    assert written == 0 and len(failed) == 3
    assert failed[0].startswith("PG24 county: missing required file")
    assert failed[1] == "PG24 precinct: not checked, the county level failed"
    # the questions do not depend on the county files: they fail on their own (not fetched)
    assert failed[2].startswith("PG24 questions: question files not fetched")
    assert not (env / "data" / "results" / "PG24").exists()


def test_level_all_skips_an_election_without_precinct_files(env):
    fill2020(env, results.RESULTS_ELECTIONS["PP20"])
    written, failed = results.results_parse("PP20", data_dir=env / "data")
    assert failed == [] and written == 48
    assert not (env / "data" / "results" / "PP20" / "precinct").exists()


def test_unfetched_precinct_files_are_one_failure(env):
    for f in results.county_files(PG24):
        if f.role == "county":
            archive(env, PG24, f, county_csv())
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 0
    assert failed == [
        "PG24 precinct: precinct files not fetched (skipjack results-fetch --level precinct)"
    ]


def _kent_county():
    return next(
        f for f in results.county_files(PG24) if f.role == "county" and f.county_code == "15"
    )


def test_a_changed_county_file_rechecks_its_precincts(env):
    fill_precincts(env)
    assert results.results_parse("PG24", data_dir=env / "data", level="precinct") == (24, [])
    out = env / "data" / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    side_path = out.parent / "15-kent-all_provenance.json"
    source = json.loads(side_path.read_text())["checks"]["county_source"]
    assert source["source_id"] == "sbe-results-PG24-county-15-all"
    before = (out.read_bytes(), side_path.read_text())

    # a re-posted county file whose figures the precincts no longer sum to
    archive(
        env,
        PG24,
        _kent_county(),
        county_csv(COUNTY_ROWS.replace('"400"', '"401"').replace('"612"', '"613"')),
    )
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 0 and len(failed) == 1 and "PG24_15PrecinctsResults.csv" in failed[0]
    assert (out.read_bytes(), side_path.read_text()) == before

    # a re-posted county file whose figures still match: Kent is rewritten, the rest are current
    archive(env, PG24, _kent_county(), county_csv(COUNTY_ROWS.replace('"Y"', '""', 1)))
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert (written, failed) == (1, [])
    new_sha = results.newest_source(results.MANIFEST_PATH, _kent_county().source_id).sha256
    assert new_sha != source["sha256"]
    assert json.loads(side_path.read_text())["checks"]["county_source"] == {
        "source_id": "sbe-results-PG24-county-15-all",
        "sha256": new_sha,
    }
    assert results.results_parse("PG24", data_dir=env / "data", level="precinct") == (0, [])


def test_sum_check_counts_the_county_files_rows():
    # Trump's county figures split over two rows: three rows, two summed keys
    split = COUNTY_ROWS.replace('"400"', '"399"').replace('"612"', '"611"') + (
        '"President - Vice Pres","","Donald J. Trump and JD Vance","REP","","",'
        '"0","","1","","0","","0","","0","","1","" \n'
    )
    check = results._check_precinct_county(PG24, "Kent", "all", *frames(county=split))
    assert check.rows == 3 and check.contests == 1


def test_sum_check_treats_a_missing_key_cell_as_blank():
    parts = pl.DataFrame({"office": ["A"], "district": [None], "votes": [3]})
    whole = pl.DataFrame({"office": ["A"], "district": [""], "votes": [3]})
    check = results._check_sums(
        "x", ["office", "district"], 2, ["votes"], parts, whole, lambda *a: None
    )
    assert check.contests == 1 and check.exceptions == []


def test_precinct_check_text_and_columns_share_one_rule():
    gg18 = results.RESULTS_ELECTIONS["GG18"]
    assert gg18.precinct_every_vote_type is False and PG24.precinct_every_vote_type is True
    assert results.RESULTS_ELECTIONS["PG20"].precinct_every_vote_type is True
    check = results.SumCheck(1, 2, ["election_night_votes"], [])
    assert "election-night votes only" in results._precinct_check_text(gg18, "f.csv", check)
    assert "election-night" not in results._precinct_check_text(PG24, "f.csv", check)
    assert "total_votes" not in results._precinct_check_columns(gg18)


def test_cli_results_parse_passes_the_level(monkeypatch):
    from skipjack import cli

    seen = {}

    def fake_parse(**kwargs):
        seen.update(kwargs)
        return 0, []

    monkeypatch.setattr(results, "results_parse", fake_parse)
    cli.main(["results-parse", "--election", "PG24", "--level", "precinct"])
    assert seen["level"] == "precinct" and seen["election"] == "PG24"


# ---- the named layout -------------------------------------------------------------------------

GG10 = results.RESULTS_ELECTIONS["GG10"]
PG12 = results.RESULTS_ELECTIONS["PG12"]
GG18 = results.RESULTS_ELECTIONS["GG18"]
GP18 = results.RESULTS_ELECTIONS["GP18"]
PG20 = results.RESULTS_ELECTIONS["PG20"]

NAMED_2018_GENERAL_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Candidate Name","Party",'
    '"Office Name","Office District","Winner","Write-In?","Election Night Votes",'
    '"Election Night Votes Against"\n'
)
NAMED_2018_PRIMARY_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Candidate Name","Party",'
    '"Office Name","Office District","Winner","Election Night Votes"\n'
)
NAMED_2010_GENERAL_HEAD = NAMED_2018_GENERAL_HEAD.replace('"Cong","Legs",', "")
PG20_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Candidate Name","Party",'
    '"Office Name","Office District","Winner","Write-In?","Early Voting Votes",'
    '"Early Voting Votes Against","Election Day Votes","Election Day Votes Against",'
    '"By Mail Votes","By Mail Votes Against","Prov. Votes","Prov. Votes Against",'
    '"By Mail 2 Votes","By Mail 2 Votes Against","Total Votes","Total Votes Against"\n'
)


def test_read_precinct_2018_general_is_election_night_only():
    rows = (
        '"15","001","001","01","36","Larry Hogan","REP","Governor / Lt. Governor","","Y","","420",""\n'
        '"15","001","002","01","36","Judge Jane","","Judge Court of Appeals","","","","3","1"\n'
    )  # fmt: skip
    frame = results.read_precinct(
        (NAMED_2018_GENERAL_HEAD + rows).encode(), GG18, "Kent", "all"
    ).frame
    assert frame["precinct"].to_list() == ["001-001", "001-002"]
    assert frame["congressional"].to_list() == ["01", "01"]
    assert frame.columns[12:] == [
        "election_night_votes", "election_night_against", "total_votes", "total_against",
    ]  # fmt: skip
    assert frame["total_votes"].to_list() == [420, 3]
    assert frame["total_against"].to_list() == [None, 1]


def test_read_precinct_2018_primary():
    row = '"15","001","001","01","36","Ben Jealous","DEM","Governor / Lt. Governor","","Y","17"\n'
    frame = results.read_precinct(
        (NAMED_2018_PRIMARY_HEAD + row).encode(), GP18, "Kent", "dem"
    ).frame
    assert frame["congressional"].to_list() == ["01"] and frame["total_votes"].to_list() == [17]


def test_read_precinct_2010_has_no_districts():
    row = '"15","001","001","Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","195",""\n'
    frame = results.read_precinct(
        (NAMED_2010_GENERAL_HEAD + row).encode(), GG10, "Kent", "all"
    ).frame
    assert frame["congressional"].to_list() == [""] and frame["legislative"].to_list() == [""]
    assert frame["total_votes"].to_list() == [195]


def test_read_precinct_2010_refuses_a_file_without_its_precinct_columns():
    row = '"15","001","Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","195",""\n'
    head = NAMED_2010_GENERAL_HEAD.replace('"Election Precinct",', "")
    with pytest.raises(results.ResultsParseError, match="missing \\['Election Precinct'\\]"):
        results.read_precinct((head + row).encode(), GG10, "Kent", "all")


def test_read_precinct_2012_lone_zero_against_is_no_against():
    # the 2012 county files print a lone 0 in Election Night Votes Against on 835 rows (a
    # write-in for president, say), which the county reader reads as no Against figures; a
    # precinct file with the same pattern reads alike, contest by contest, while a retention
    # judge's real 0 in one precinct stays a 0 (other precincts voted against)
    rows = (
        '"15","001","001","01","36","Tiffany Renee Briscoe","DEM","President - Vice Pres","","","Y","0","0"\n'
        '"15","001","002","01","36","Tiffany Renee Briscoe","DEM","President - Vice Pres","","","Y","1","0"\n'
        '"15","001","001","01","36","Stuart R. Berger","BOT","Judge Special Appeals At Large","","Y","","3","0"\n'
        '"15","001","002","01","36","Stuart R. Berger","BOT","Judge Special Appeals At Large","","Y","","616","138"\n'
    )  # fmt: skip
    frame = results.read_precinct(
        (NAMED_2018_GENERAL_HEAD + rows).encode(), PG12, "Kent", "all"
    ).frame
    assert frame["election_night_against"].to_list() == [None, None, 0, 138]
    assert frame["total_against"].to_list() == [None, None, 0, 138]


def test_read_precinct_pg20_checks_its_printed_total():
    row = '"15","001","001","01","36","Joe Biden","DEM","President - Vice Pres","","Y","","76","","89","","55","","26","","78","","324",""\n'  # fmt: skip
    frame = results.read_precinct((PG20_HEAD + row).encode(), PG20, "Kent", "all").frame
    assert frame["total_votes"].to_list() == [324] and frame["by_mail_2_votes"].to_list() == [78]
    with pytest.raises(results.ResultsParseError, match="total votes 325 != sum of vote types 324"):
        results.read_precinct((PG20_HEAD + row.replace('"324"', '"325"')).encode(), PG20, "Kent", "all")  # fmt: skip


def test_read_precinct_pg20_follows_the_row_rule_not_the_contest_rule():
    # PG20's precinct files print Total Votes Against, so the county reader's row rule applies:
    # a lone 0 with a blank total is no Against figure. A contest whose Against cells are 0 in
    # every precinct under a printed 0 total keeps its zeros (the 2010-2018 contest rule would
    # make them None)
    rows = (
        '"15","001","001","01","36","Joe Biden","DEM","President - Vice Pres","","Y","","1","","2","0","3","","4","","5","","15",""\n'
        '"15","001","001","01","36","Jane Roe","","Judge Court of Appeals","","Y","","1","0","1","0","1","0","1","0","1","0","5","0"\n'
        '"15","001","002","01","36","Jane Roe","","Judge Court of Appeals","","Y","","2","0","2","0","2","0","2","0","2","0","10","0"\n'
    )  # fmt: skip
    frame = results.read_precinct((PG20_HEAD + rows).encode(), PG20, "Kent", "all").frame
    assert frame["election_day_against"].to_list() == [None, 0, 0]
    assert frame["total_against"].to_list() == [None, 0, 0]


def test_named_precinct_check_compares_election_night_only():
    assert results._precinct_check_columns(GG18) == ["election_night_votes", "election_night_against"]  # fmt: skip
    assert results._precinct_check_columns(GP18) == ["election_night_votes"]
    assert results._precinct_check_columns(PG20)[-2:] == ["total_votes", "total_against"]


def test_parse_gp18_precincts_tolerates_a_missing_non_partisan_file(env):
    county_head = (
        '"Candidate Name","Party","Office Name","Office District","Winner","Early Votes",'
        '"Election Night Votes","Absentees Votes","Provisional Votes","2nd Absentees Votes",'
        '"Total Votes"\n'
    )
    county = county_head + '"Ben Jealous","DEM","Governor / Lt. Governor","","Y","5","17","1","0","0","23"\n'  # fmt: skip
    precincts = '"CC","001","001","01","36","Ben Jealous","DEM","Governor / Lt. Governor","","Y","17"\n'  # fmt: skip
    for f in results.county_files(GP18):
        if f.role == "county" and f.party_file != "non":
            archive(env, GP18, f, county.encode())
    for f in results.precinct_files(GP18):
        if f.party_file != "non":
            body = NAMED_2018_PRIMARY_HEAD + precincts.replace('"CC"', f'"{f.county_code}"')
            archive(env, GP18, f, body.encode())
    written, failed = results.results_parse("GP18", data_dir=env / "data", level="precinct")
    assert failed == [] and written == 48
    side = json.loads(
        (env / "data" / "results" / "GP18" / "precinct" / "15-kent-dem_provenance.json").read_text()
    )
    assert side["checks"]["county_check"].endswith(
        "on election_night_votes (the board's precinct files for 2010-2018 carry election-night "
        "votes only)"
    )


def test_parse_pg12_precincts_with_the_lone_zero_pattern_pass_the_check(env):
    # the county file's lone 0 and the precinct file's (or its blank) both read as no Against
    county_head = (
        '"Candidate Name","Party","Office Name","Office District","Winner","Write-In?",'
        '"Early Votes","Early Votes Against","Election Night Votes","Election Night Votes Against",'
        '"Absentees Votes","Absentees Votes Against","Provisional Votes","Provisional Votes Against",'
        '"2nd Absentees Votes","2nd Absentees Votes Against","Total Votes","Total Votes Against"\n'
    )
    county = county_head + '"Tiffany Renee Briscoe","DEM","President - Vice Pres","","","Y","0","","1","0","0","","0","","0","","1",""\n'  # fmt: skip
    precincts = (
        '"CC","001","001","01","36","Tiffany Renee Briscoe","DEM","President - Vice Pres","","","Y","1","0"\n'
        '"CC","001","002","01","36","Tiffany Renee Briscoe","DEM","President - Vice Pres","","","Y","0",""\n'
    )  # fmt: skip
    for f in results.county_files(PG12):
        if f.role == "county":
            archive(env, PG12, f, county.encode())
    for f in results.precinct_files(PG12):
        body = NAMED_2018_GENERAL_HEAD + precincts.replace('"CC"', f'"{f.county_code}"')
        archive(env, PG12, f, body.encode())
    written, failed = results.results_parse("PG12", data_dir=env / "data", level="precinct")
    assert failed == [] and written == 24
    out = env / "data" / "results" / "PG12" / "precinct" / "15-kent-all.csv.gz"
    frame = pl.read_csv(gzip.decompress(out.read_bytes()))
    assert frame["total_against"].to_list() == [None, None]


# ---- the coded primaries' countywide districts (GP22, GP26, PP24) ----------------------------

CODED_PRIMARY_PRECINCT_HEAD = (
    '"County","County Name","Election District - Precinct","Congressional","Legislative",'
    '"Office Name","Office District","Candidate Name","Party","Winner","Early Votes",'
    '"Election Night Votes","Mail-In Ballot 1 Votes","Provisional Votes",'
    '"Mail-In Ballot 2 Votes" \n'
)
CODED_PRIMARY_COUNTY_HEAD = (
    '"Office Name","Office District","Candidate Name","Party","Winner","Early Votes",'
    '"Election Night Votes","Mail-In Ballot 1 Votes","Provisional Votes",'
    '"Mail-In Ballot 2 Votes","Total Votes" \n'
)
GP22 = results.RESULTS_ELECTIONS["GP22"]
PP24 = results.RESULTS_ELECTIONS["PP24"]
# GP22_01PrecinctsRepublican.csv prints Allegany's Sheriff under district "01" (the county's
# code); GP22_01RepublicanResults.csv prints it blank. Two of its precincts, as printed, and a
# county row that is their sum.
ALLEGANY_SHERIFF_PRECINCTS = (
    '"01","Allegany County","001-000","06","01C","Sheriff","01","Craig Robertson","REP","Y","2","95","7","5","7" \n'
    '"01","Allegany County","002-000","06","01C","Sheriff","01","Craig Robertson","REP","Y","6","102","8","4","3" \n'
)  # fmt: skip
ALLEGANY_SHERIFF_COUNTY = (
    '"Sheriff","","Craig Robertson","REP","Y","8","197","15","9","10","239" \n'
)


def coded_primary(election, county, code, precincts, county_rows):
    return (
        results.read_precinct(
            (CODED_PRIMARY_PRECINCT_HEAD + precincts).encode(), election, county, "rep"
        ).frame,
        results._read_coded(
            (CODED_PRIMARY_COUNTY_HEAD + county_rows).encode(), election, county, "rep"
        ).frame,
    )


def test_a_countywide_district_printed_as_the_county_code_is_read_as_blank():
    precincts, county = coded_primary(
        GP22, "Allegany", "01", ALLEGANY_SHERIFF_PRECINCTS, ALLEGANY_SHERIFF_COUNTY
    )
    with pytest.raises(results.ResultsParseError, match="Sheriff has no precinct rows"):
        results._check_precinct_county(GP22, "Allegany", "rep", precincts, county)
    frame, blanked = results._blank_countywide_districts(precincts, county, "01")
    assert blanked == ["Sheriff"] and frame["district"].to_list() == ["", ""]
    assert results._check_precinct_county(GP22, "Allegany", "rep", frame, county).contests == 1
    # PP24_03PrecinctsDemocratic.csv: Baltimore City's Mayor under "03"
    mayor = (
        '"03","Baltimore City","001-001","07","46","Mayor","03","""Uncle Wayne"" Baker","DEM","","0","2","0","0","0" \n'
        '"03","Baltimore City","001-001","07","46","Mayor","03","Wendy Bozel","DEM","","0","1","0","1","0" \n'
    )  # fmt: skip
    county_mayor = (
        '"Mayor","","""Uncle Wayne"" Baker","DEM","","0","2","0","0","0","2" \n'
        '"Mayor","","Wendy Bozel","DEM","","0","1","0","1","0","2" \n'
    )
    precincts, county = coded_primary(PP24, "Baltimore City", "03", mayor, county_mayor)
    frame, blanked = results._blank_countywide_districts(precincts, county, "03")
    assert blanked == ["Mayor"]
    assert results._check_precinct_county(PP24, "Baltimore City", "rep", frame, county).rows == 2


def test_a_genuine_district_numbered_like_the_county_is_never_blanked():
    # the county file has the office under "01" (a commissioner district 1): no pairing
    district_1 = ALLEGANY_SHERIFF_PRECINCTS.replace('"Sheriff"', '"County Commissioner"')
    county_1 = (
        '"County Commissioner","01","Craig Robertson","REP","Y","8","197","15","9","10","239" \n'
    )
    precincts, county = coded_primary(GP22, "Allegany", "01", district_1, county_1)
    frame, blanked = results._blank_countywide_districts(precincts, county, "01")
    assert blanked == [] and frame["district"].to_list() == ["01", "01"]
    # ... nor when the county file has the office both blank and under "01"
    both = county_1 + county_1.replace('"01"', '""', 1)
    precincts, county = coded_primary(GP22, "Allegany", "01", district_1, both)
    assert results._blank_countywide_districts(precincts, county, "01")[1] == []
    # ... nor when the precinct file has blank-district rows of the office itself
    mixed = district_1 + district_1.replace(
        '"County Commissioner","01"', '"County Commissioner",""'
    )
    precincts, county = coded_primary(
        GP22, "Allegany", "01", mixed, county_1.replace('"01"', '""', 1)
    )
    assert results._blank_countywide_districts(precincts, county, "01")[1] == []
    # ... nor another county's number
    precincts, county = coded_primary(
        GP22, "Allegany", "01", ALLEGANY_SHERIFF_PRECINCTS.replace('"Sheriff","01"', '"Sheriff","02"'),
        ALLEGANY_SHERIFF_COUNTY,
    )  # fmt: skip
    assert results._blank_countywide_districts(precincts, county, "01")[1] == []


def test_parse_writes_the_countywide_district_blank_and_says_so(env):
    for f in results.county_files(GP22):
        if f.role == "county" and f.party_file != "non":
            archive(env, GP22, f, (CODED_PRIMARY_COUNTY_HEAD + ALLEGANY_SHERIFF_COUNTY).encode())
    for f in results.precinct_files(GP22):
        if f.party_file != "non":
            rows = ALLEGANY_SHERIFF_PRECINCTS.replace(
                '"01","Allegany County"', f'"{f.county_code}","X"'
            )
            rows = rows.replace('"Sheriff","01"', f'"Sheriff","{f.county_code}"')
            archive(env, GP22, f, (CODED_PRIMARY_PRECINCT_HEAD + rows).encode())
    written, failed = results.results_parse("GP22", data_dir=env / "data", level="precinct")
    assert failed == [] and written == 48
    out = env / "data" / "results" / "GP22" / "precinct" / "01-allegany-rep.csv.gz"
    frame = pl.read_csv(gzip.decompress(out.read_bytes()), infer_schema=False)
    assert frame["district"].fill_null("").to_list() == ["", ""]
    side = json.loads((out.parent / "01-allegany-rep_provenance.json").read_text())
    assert side["checks"]["district_read_as_blank"] == ["Sheriff"]


# ---- PP12's presidential primary by congressional district -----------------------------------

PP12 = results.RESULTS_ELECTIONS["PP12"]
PP12_COUNTY_HEAD = (
    '"Candidate Name","Party","Office Name","Office District","Winner","Early Votes",'
    '"Election Night Votes","Absentees Votes","Provisional Votes","2nd Absentees Votes",'
    '"Total Votes"\n'
)
# Montgomery's county file prints the presidential primary statewide and again for each of its
# congressional districts (03, 06, 08); its precinct file prints the statewide rows only, with
# each precinct's Cong. One precinct per district here; the county rows are their sums.
PP12_PRECINCTS = (
    '"16","001","001","03","15","Barack Obama","DEM","President - Vice Pres","","Y","100"\n'
    '"16","002","001","06","15","Barack Obama","DEM","President - Vice Pres","","Y","200"\n'
    '"16","003","001","08","15","Barack Obama","DEM","President - Vice Pres","","Y","300"\n'
)
PP12_COUNTY = (
    '"Barack Obama","DEM","President - Vice Pres","","Y","9","600","0","0","0","609"\n'
    '"Barack Obama","DEM","President - Vice Pres","03","Y","1","100","0","0","0","101"\n'
    '"Barack Obama","DEM","President - Vice Pres","06","Y","3","200","0","0","0","203"\n'
    '"Barack Obama","DEM","President - Vice Pres","08","Y","5","300","0","0","0","305"\n'
)


def pp12_frames(precincts=PP12_PRECINCTS):
    return (
        results.read_precinct(
            (NAMED_2018_PRIMARY_HEAD + precincts).encode(), PP12, "Montgomery", "dem"
        ).frame,
        results._read_named((PP12_COUNTY_HEAD + PP12_COUNTY).encode(), PP12, "Montgomery", "dem").frame,
    )  # fmt: skip


def test_pp12_president_by_congressional_district_is_checked_on_the_cong_column(monkeypatch):
    check = results._check_precinct_county(PP12, "Montgomery", "dem", *pp12_frames())
    assert check.contests == 4 and check.rows == 4 and check.exceptions == []
    # a vote moved between districts: the statewide sum holds, the districts' do not
    moved = PP12_PRECINCTS.replace('"Y","200"', '"Y","199"').replace('"Y","300"', '"Y","301"')
    with pytest.raises(results.ResultsParseError, match=r"President - Vice Pres 06 / Barack Obama \(DEM\): precincts sum to 199 election_night_votes, the county file says 200"):  # fmt: skip
        results._check_precinct_county(PP12, "Montgomery", "dem", *pp12_frames(moved))
    # the same exception mechanism, keyed by the district
    key = ("PP12", "Montgomery", "dem", "President - Vice Pres")
    monkeypatch.setitem(results.PRECINCT_CHECK_EXCEPTIONS, (*key, "06", "Barack Obama", "DEM", "election_night_votes"), (199, 200))  # fmt: skip
    monkeypatch.setitem(results.PRECINCT_CHECK_EXCEPTIONS, (*key, "08", "Barack Obama", "DEM", "election_night_votes"), (301, 300))  # fmt: skip
    check = results._check_precinct_county(PP12, "Montgomery", "dem", *pp12_frames(moved))
    assert len(check.exceptions) == 2


def test_the_breakdown_is_for_the_check_only():
    precincts, county = pp12_frames()
    assert results._congressional_breakdown(PP12, precincts, county).height == 6
    assert precincts.height == 3  # the written frame keeps the board's rows
    other = results.RESULTS_ELECTIONS["PP16"]  # not listed: no breakdown, so the check fails
    assert results._congressional_breakdown(other, precincts, county).height == 3


# ---- the listed disagreements -----------------------------------------------------------------


def test_the_listed_disagreements_are_accepted_and_no_other_figure_is():
    gg10 = results.RESULTS_ELECTIONS["GG10"]
    head = NAMED_2010_GENERAL_HEAD
    county_head = (
        '"Candidate Name","Party","Office Name","Office District","Winner","Write-In?",'
        '"Early Votes","Early Votes Against","Election Night Votes","Election Night Votes Against",'
        '"Absentees Votes","Absentees Votes Against","Provisional Votes",'
        '"Provisional Votes Against","2nd Absentees Votes","2nd Absentees Votes Against",'
        '"3rd Absentees Votes","3rd Absentees Votes Against","Total Votes","Total Votes Against"\n'
    )

    def check(precinct_votes):
        precincts = f'"13","001","001","Joseph Leonard Delozier","DEM","Pres County Council","","","Y","{precinct_votes}",""\n'  # fmt: skip
        county = '"Joseph Leonard Delozier","DEM","Pres County Council","","","Y","146","","433","","10","","5","","1","","0","","595",""\n'  # fmt: skip
        return results._check_precinct_county(
            gg10, "Harford", "all",
            results.read_precinct((head + precincts).encode(), gg10, "Harford", "all").frame,
            results._read_named((county_head + county).encode(), gg10, "Harford", "all").frame,
        )  # fmt: skip

    assert check(433).exceptions == []
    # Harford_By_Precinct_2010_General.csv sums to 432 where the county file says 433: listed
    assert check(432).exceptions == [
        "Pres County Council / Joseph Leonard Delozier (DEM) election_night_votes: precincts "
        "432, county file 433"
    ]
    with pytest.raises(
        results.ResultsParseError, match="precincts sum to 431 election_night_votes"
    ):
        check(431)
    assert len(results.PRECINCT_CHECK_EXCEPTIONS) == 10
