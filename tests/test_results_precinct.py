"""Tests for the precinct results: file names, the precinct reader, the precinct-to-county check
and the gzipped output."""

from __future__ import annotations

import logging

import pytest

from skipjack.pipeline import fetch, results

# Not tests.<module>: another project's `tests` package on PYTHONPATH can shadow ours.
from conftest import fake_client
from test_results import BOM, GENERAL_HEAD

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


def test_read_precinct_names_the_precinct_file_when_columns_are_missing():
    head = PRECINCT_GENERAL_HEAD.replace('"Legislative",', "")
    rows = PRECINCT_ROWS.replace('"36",', "")
    with pytest.raises(results.ResultsParseError, match="PG24_15PrecinctsResults.csv.*Legislative"):
        results.read_precinct((head + rows.replace('"CC"', '"15"')).encode(), PG24, "Kent", "all")
