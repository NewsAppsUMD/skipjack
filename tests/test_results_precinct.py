"""Tests for the precinct results: file names, the precinct reader, the precinct-to-county check
and the gzipped output."""

from __future__ import annotations

import pytest

from skipjack.pipeline import results

# Not tests.test_results: another project's `tests` package on PYTHONPATH can shadow ours.
from test_results import BOM, GENERAL_HEAD, archive, env, fake_client  # noqa: F401 (env: fixture)

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


@pytest.mark.usefixtures("env")
def test_fetch_precinct_level_archives_only_the_precinct_files(monkeypatch):
    monkeypatch.setattr(results.httpx, "Client", fake_client(lambda url: b'"County"\n"15"\n'))
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
