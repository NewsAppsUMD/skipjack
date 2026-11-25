"""Tests for the ballot questions: readers, checks, parse."""

from __future__ import annotations

import json

import polars as pl
import pytest

from skipjack.pipeline import results

from conftest import archive

PG24 = results.RESULTS_ELECTIONS["PG24"]
GG18 = results.RESULTS_ELECTIONS["GG18"]
PG16 = results.RESULTS_ELECTIONS["PG16"]
PG12 = results.RESULTS_ELECTIONS["PG12"]
GG10 = results.RESULTS_ELECTIONS["GG10"]
PG20 = results.RESULTS_ELECTIONS["PG20"]
GG14 = results.RESULTS_ELECTIONS["GG14"]

CODED_COUNTY_HEAD = (
    '"County","JurisdictionName","QuestionNumber","How it Runs?","Winner","Early Votes",'
    '"Early Votes Against","Election Night Votes","Election Night Votes Against",'
    '"Mail-In Ballot 1 Votes","Mail-In Ballot 1 Votes Against","Provisional Votes",'
    '"Provisional Votes Against","Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against",'
    '"Total Votes","Total Votes Against" \n'
)
# Kent, PG24: For 2454+3243+801+272+1003 = 7773, Against 1047+1534+132+70+154 = 2937
CODED_COUNTY_ROWS = '"CC","Kent County","1","State","Y","2454","1047","3243","1534","801","132","272","70","1003","154","7773","2937" \n'
CODED_PRECINCT_HEAD = (
    '"County","County Name","Election District - Precinct","Congressional","Legislative",'
    '"Question","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Election Night Votes","Election Night Votes Against","Mail-In Ballot 1 Votes",'
    '"Mail-In Ballot 1 Votes Against","Provisional Votes","Provisional Votes Against",'
    '"Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against" \n'
)
# two precincts that sum to the county row above
CODED_PRECINCT_ROWS = (
    '"CC","Kent County","001-001","01","36","1","State","Y","2000","1000","3000","1500","800","130","270","70","1000","150" \n'
    '"CC","Kent County","001-002","01","36","1","State","Y","454","47","243","34","1","2","2","0","3","4" \n'
)
NAMED_COUNTY_HEAD = (
    '"County","Question Number","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Early 3rd option","Election Night Votes","Election Night Votes Against",'
    '"Election Night 3rd option","Absentees Votes","Absentees Votes Against",'
    '"Absentees 3rd option","Provisional Votes","Provisional Votes Against",'
    '"Provisional 3rd option","2nd Absentees Votes","2nd Absentees Votes Against",'
    '"2nd Absentees 3rd option","Total Votes","Total Votes Against","Total 3rd option"\n'
)
# Kent, GG18: For 2409+4078+178+80+112 = 6857, Against 290+628+31+6+9 = 964
NAMED_COUNTY_ROWS = '"00","01","State","Y","2409","290","","4078","628","","178","31","","80","6","","112","9","","6857","964",""\n'
# Wicomico, PG16: question A had three choices; the board prints the third in every column
# (2253+6514+427+182+137 = 9513). GG18's files print the identical third-choice figures again.
WICOMICO_2016 = '"23","A","County","Y","2881","5319","2253","6289","13195","6514","383","719","427","153","213","182","92","175","137","9798","19621","9513"\n'
WICOMICO_2018 = '"23","A","County","Y","6288","2665","2253","13622","5409","6514","530","234","427","381","111","182","220","106","137","21041","8525","9513"\n'
NAMED_PRECINCT_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Question Number",'
    '"How it Runs?","Winner","Election Night Votes","Election Night Votes Against",'
    '"Election Night Votes (3rd option)"\n'
)
NAMED_PRECINCT_ROWS = (
    '"CC","001","001","01","36","01","State","","4000","600",""\n'
    '"CC","001","002","01","36","01","State","","78","28",""\n'
)
# Wicomico, PG16, as the board posts them (a statewide question with no third choice beside
# question A with one)
WICOMICO_PRECINCT_2016 = (
    '"23","001","001","01","37A","01","State","","35","18",""\n'
    '"23","001","001","01","37A","A","County","","15","25","13"\n'
    '"23","001","002","01","37A","A","County","","111","244","118"\n'
)


def coded_county(code="15", rows=CODED_COUNTY_ROWS) -> bytes:
    return (CODED_COUNTY_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def coded_precincts(code="15", rows=CODED_PRECINCT_ROWS) -> bytes:
    return (CODED_PRECINCT_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def named_county(rows=NAMED_COUNTY_ROWS) -> bytes:
    return (NAMED_COUNTY_HEAD + rows).encode()


def named_precincts(code="15", rows=NAMED_PRECINCT_ROWS) -> bytes:
    return (NAMED_PRECINCT_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def test_read_coded_county_questions():
    frame = results.read_questions(coded_county(), PG24, "Kent").frame
    assert frame.columns[:5] == ["election", "county", "question", "how_it_runs", "winner"]
    assert frame.columns[-2:] == ["total_votes", "total_against"]
    assert not [c for c in frame.columns if c.endswith("_third")]  # the coded layout has none
    row = frame.row(0, named=True)
    assert row["question"] == "1" and row["how_it_runs"] == "State" and row["winner"] == 1
    assert row["early_votes"] == 2454 and row["early_against"] == 1047
    assert (row["total_votes"], row["total_against"]) == (7773, 2937)


def test_county_question_totals_must_be_the_sum():
    bad = CODED_COUNTY_ROWS.replace('"7773"', '"7774"')
    with pytest.raises(
        results.ResultsParseError, match="Total Votes 7774 != sum of vote types 7773"
    ):
        results.read_questions(coded_county(rows=bad), PG24, "Kent")


def test_read_named_county_questions_with_a_blank_third_option():
    frame = results.read_questions(named_county(), GG18, "Kent").frame
    assert frame["question"].to_list() == ["01"]
    assert (frame["total_votes"][0], frame["total_against"][0]) == (6857, 964)
    assert frame.columns[5:8] == ["early_votes", "early_against", "early_third"]
    assert frame.columns[-3:] == ["total_votes", "total_against", "total_third"]
    assert frame["absentees_2_third"].to_list() == [None]
    assert frame["total_third"].to_list() == [None]


@pytest.mark.parametrize(("election", "row"), [(PG16, WICOMICO_2016), (GG18, WICOMICO_2018)])
def test_wicomico_third_choice_is_carried_as_published(election, row):
    frame = results.read_questions(named_county(row), election, "Wicomico").frame
    r = frame.row(0, named=True)
    assert (r["question"], r["how_it_runs"]) == ("A", "County")
    assert (r["early_third"], r["election_night_third"], r["absentees_2_third"]) == (
        2253,
        6514,
        137,
    )
    assert r["total_third"] == 9513


def test_a_third_choice_total_must_be_the_sum():
    bad = WICOMICO_2016.replace('"9513"', '"9514"')
    with pytest.raises(
        results.ResultsParseError, match="Total 3rd option 9514 != sum of vote types 9513"
    ):
        results.read_questions(named_county(bad), PG16, "Wicomico")
    printed_alone = NAMED_COUNTY_ROWS[: -len('""\n')] + '"5"\n'  # a total with no figures
    with pytest.raises(
        results.ResultsParseError, match="Total 3rd option 5 != sum of vote types None"
    ):
        results.read_questions(named_county(printed_alone), GG18, "Kent")


def test_a_third_option_column_where_the_election_has_none_is_refused():
    # PG12's files have no third option, and the reader does not let a stray one pass unread
    with pytest.raises(results.ResultsParseError, match="third-option columns not in the registry"):
        results.read_questions(named_county(), PG12, "Kent")


def test_named_county_column_follows_how_it_runs():
    local = NAMED_COUNTY_ROWS.replace('"00","01","State"', '"15","A","County"')
    assert results.read_questions(named_county(local), GG18, "Kent").frame[
        "question"
    ].to_list() == ["A"]
    wrong = NAMED_COUNTY_ROWS.replace('"00","01","State"', '"15","01","State"')
    with pytest.raises(
        results.ResultsParseError, match="County '15' for a question that runs 'State'"
    ):
        results.read_questions(named_county(wrong), GG18, "Kent")


def test_how_it_runs_is_kept_as_printed():
    ed = CODED_COUNTY_ROWS.replace('"1","State"', '"B","Ed"')
    assert results.read_questions(coded_county(rows=ed), PG24, "Kent").frame["how_it_runs"].to_list() == ["Ed"]  # fmt: skip


def test_read_coded_precinct_questions_computes_the_totals():
    frame = results.read_questions(coded_precincts(), PG24, "Kent", level="precinct").frame
    assert frame.columns[:8] == [
        "election", "county", "precinct", "congressional", "legislative", "question",
        "how_it_runs", "winner",
    ]  # fmt: skip
    assert frame["precinct"].to_list() == ["001-001", "001-002"]
    assert frame["total_votes"].to_list() == [7070, 703]
    assert frame["total_against"].to_list() == [2850, 87]


def test_read_named_precinct_questions_election_night_only():
    frame = results.read_questions(named_precincts(), GG18, "Kent", level="precinct").frame
    assert frame.columns[-6:] == [
        "election_night_votes", "election_night_against", "election_night_third",
        "total_votes", "total_against", "total_third",
    ]  # fmt: skip
    assert frame["total_votes"].to_list() == [4000, 78]
    assert frame["total_third"].to_list() == [None, None]


def test_read_wicomico_2016_precinct_third_choice():
    body = (NAMED_PRECINCT_HEAD + WICOMICO_PRECINCT_2016).encode()
    frame = results.read_questions(body, PG16, "Wicomico", level="precinct").frame
    assert frame["question"].to_list() == ["01", "A", "A"]
    assert frame["election_night_third"].to_list() == [None, 13, 118]
    assert frame["total_third"].to_list() == [None, 13, 118]
    assert frame["total_votes"].to_list() == [35, 15, 111]


def test_read_2010_precinct_questions_without_districts():
    head = NAMED_PRECINCT_HEAD.replace('"Cong","Legs",', "").replace(
        ',"Election Night Votes (3rd option)"', ""
    )
    rows = '"15","001","001","01","State","","251","202"\n'
    frame = results.read_questions((head + rows).encode(), GG10, "Kent", level="precinct").frame
    assert frame["congressional"].to_list() == [""] and frame["total_votes"].to_list() == [251]
    assert "total_third" not in frame.columns


def test_a_named_blank_for_or_against_reads_as_zero():
    # an empty precinct: Montgomery's 004-036 prints For "0" and a blank Against
    rows = '"CC","004","036","08","19","01","State","","0","",""\n'
    frame = results.read_questions(named_precincts("16", rows), GG18, "Montgomery", level="precinct").frame  # fmt: skip
    assert frame["election_night_against"].to_list() == [0] and frame["total_against"].to_list() == [0]  # fmt: skip


def test_precinct_rows_must_be_the_countys():
    with pytest.raises(results.ResultsParseError, match="County '16' is not Kent's code 15"):
        results.read_questions(named_precincts("16"), GG18, "Kent", level="precinct")


def test_missing_question_columns_name_the_file():
    head = NAMED_PRECINCT_HEAD.replace('"Question Number",', "")
    rows = '"15","001","001","01","36","State","","4000","600",""\n'
    with pytest.raises(results.ResultsParseError, match="Kent_Questions_Precinct_2018.csv; missing \\['Question Number'\\]"):  # fmt: skip
        results.read_questions((head + rows).encode(), GG18, "Kent", level="precinct")


PG20_PRECINCT_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Question Number",'
    '"How it Runs?","Winner","Early Voting Votes","Early Voting Votes Against",'
    '"Early Voting 3rd option","Election Day Votes","Election Day Votes Against",'
    '"Election Day 3rd option","By Mail Votes","By Mail Votes Against","By Mail 3rd option",'
    '"Prov. Votes","Prov. Votes Against","Prov. 3rd option","By Mail 2 Votes",'
    '"By Mail 2 Votes Against","By Mail 2 3rd option","Total Votes","Total Votes Against",'
    '"Total 3rd option"\n'
)


def test_pg20_precinct_question_rows_stop_short_of_the_total_columns():
    # 24 cells under 26 columns, as the board posts them; Total Votes blank
    row = '"15","001","001","01","36","01","State","Y","130","65","","259","93","","49","11","","50","10","","73","34","",""\n'
    frame = results.read_questions(
        (PG20_PRECINCT_HEAD + row).encode(), PG20, "Kent", level="precinct"
    ).frame
    assert frame["total_votes"].to_list() == [561] and frame["total_against"].to_list() == [213]
    assert frame["by_mail_2_third"].to_list() == [None] and frame["total_third"].to_list() == [None]
    short = row.replace(',"73","34","",""', ',"73"')  # cut into the vote columns: refused
    with pytest.raises(results.ResultsParseError, match="rows do not all have 26 columns"):
        results.read_questions(
            (PG20_PRECINCT_HEAD + short).encode(), PG20, "Kent", level="precinct"
        )


def test_county_files_keep_every_row_whole():
    # the short-row allowance is for the precinct files only
    short = NAMED_COUNTY_ROWS[: -len(',""\n')] + "\n"
    with pytest.raises(results.ResultsParseError, match="rows do not all have 22 columns"):
        results.read_questions(named_county(short), GG18, "Kent")


GG14_COUNTY_HEAD = (
    '"County","Question Number","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Election Night Votes","Election Night Votes Against","Absentees Votes",'
    '"Absentees Votes Against","Provisional Votes","Provisional Votes Against",'
    '"2nd Absentees Votes","2nd Absentees Votes Against","Total Votes","Total Votes Against"\n'
)


def test_garretts_question_numbers_are_kept_as_printed():
    # Garrett's 2014 local questions are numbered by question and election district
    row = '"12","A 002 000","County","","9","7","218","241","0","0","7","0","1","4","235","252"\n'
    frame = results.read_questions((GG14_COUNTY_HEAD + row).encode(), GG14, "Garrett").frame
    assert frame["question"].to_list() == ["A 002 000"]
    assert (frame["total_votes"][0], frame["total_against"][0]) == (235, 252)


def test_an_unknown_county_is_a_parse_error():
    with pytest.raises(results.ResultsParseError, match="unknown county 'Narnia'"):
        results.read_questions(coded_county(), PG24, "Narnia")


def test_duplicated_unknown_columns_are_named_as_printed():
    head = NAMED_COUNTY_HEAD.replace("\n", ',"Mystery 3rd option","Mystery 3rd option"\n')
    with pytest.raises(results.ResultsParseError, match=r"not in the registry: \['Mystery 3rd option'\]$"):  # fmt: skip
        results.read_questions((head + NAMED_COUNTY_ROWS.replace("\n", ',"",""\n')).encode(), GG18, "Kent")  # fmt: skip
    head = CODED_COUNTY_HEAD.replace(" \n", ',"Mystery Votes","Mystery Votes" \n')
    rows = CODED_COUNTY_ROWS.replace(" \n", ',"1","1" \n')
    with pytest.raises(results.ResultsParseError, match=r"not in the registry: \['Mystery Votes'\]$"):  # fmt: skip
        results.read_questions((head + rows.replace('"CC"', '"15"')).encode(), PG24, "Kent")


def test_precinct_errors_name_the_precinct():
    rows = NAMED_PRECINCT_ROWS.replace('"78","28"', '"78","x"')
    with pytest.raises(results.ResultsParseError, match="precinct 001-002 question 01: 'Election Night Votes Against' is not a number"):  # fmt: skip
        results.read_questions(named_precincts(rows=rows), GG18, "Kent", level="precinct")
    rows = CODED_PRECINCT_ROWS.replace('"454","47"', '"454",""')
    with pytest.raises(results.ResultsParseError, match="precinct 001-002 question 1: blank Early Votes Against"):  # fmt: skip
        results.read_questions(coded_precincts(rows=rows), PG24, "Kent", level="precinct")


def test_a_printed_zero_third_total_with_blank_third_cells_is_accepted():
    zero = NAMED_COUNTY_ROWS[: -len('""\n')] + '"0"\n'
    frame = results.read_questions(named_county(zero), GG18, "Kent").frame
    assert frame["total_third"].to_list() == [0]  # as printed
    assert frame["early_third"].to_list() == [None]
    # a figure in a third cell under a printed 0 is still a mismatch
    bad = zero.replace('"2409","290",""', '"2409","290","4"')
    with pytest.raises(
        results.ResultsParseError, match="Total 3rd option 0 != sum of vote types 4"
    ):
        results.read_questions(named_county(bad), GG18, "Kent")


# ---- the checks ---------------------------------------------------------------------------

CODED_BREAKDOWN_HEAD = (
    '"Vote Type (FOR=1,AGAINST=2)","Vote Type Description","County","County Name",'
    '"QuestionNumber","How it Runs?","Winner","Congressional District 1",'
    '"Congressional District 2" \n'
)


def coded_breakdown(for_total: int, against_total: int) -> bytes:
    rows = (
        f'"1","For Response","00","State of Maryland","1","State","Y","{for_total - 100}","100" \n'
        f'"2","Against Response","00","State of Maryland","1","State","","{against_total}","0" \n'
        '"1","For Response","02","Anne Arundel County","A","County","Y","5","0" \n'
    )
    return (CODED_BREAKDOWN_HEAD + rows).encode()


NAMED_BREAKDOWN_HEAD = (
    '"Vote Type (FOR=1,AGAINST=2)","County","Question Number","How it Runs?","Winner", '
    "CONG 04,CONG 08\n"
)


def named_breakdown(for_total, against_total, third="", local="") -> bytes:
    rows = (
        f'1,00,"01",State,,{for_total - 10},10\n'
        f'2,00,"01",State,,{against_total},\n'
        f'3,00,"01",State,,{third},\n'
    )
    return (NAMED_BREAKDOWN_HEAD + rows + local).encode()


def test_statewide_question_check_passes_and_fails():
    frames = [results.read_questions(coded_county(), PG24, "Kent").frame] * 2
    check = results._check_questions_statewide(PG24, frames, coded_breakdown(15546, 5874))
    assert check == 1  # one statewide question compared; Anne Arundel's local question is not
    with pytest.raises(results.ResultsParseError, match="question 1: counties sum to 15546 for, the breakdown says 15547"):  # fmt: skip
        results._check_questions_statewide(PG24, frames, coded_breakdown(15547, 5874))


def test_statewide_question_check_uses_election_night_votes_through_2018():
    # the 2012-2018 breakdowns sum to the counties' Election Night Votes (checked against every
    # county's file for 2012-2018); Kent GG18: election night 4078 for, 628 against
    frames = [results.read_questions(named_county(), GG18, "Kent").frame]
    assert results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628)) == 1
    with pytest.raises(results.ResultsParseError, match="counties sum to 4078 for, the breakdown says 6857"):  # fmt: skip
        results._check_questions_statewide(GG18, frames, named_breakdown(6857, 964))


def test_statewide_question_check_uses_totals_from_2020():
    row = '"00","01","State","Y","130","65","","259","93","","49","11","","50","10","","73","34","","561","213",""\n'  # fmt: skip
    head = PG20_PRECINCT_HEAD.replace('"Election District","Election Precinct","Cong","Legs",', "")
    frames = [results.read_questions((head + row).encode(), PG20, "Kent").frame]
    assert results._check_questions_statewide(PG20, frames, named_breakdown(561, 213)) == 1
    # election night alone (259) is not what the 2020 breakdown carries
    with pytest.raises(results.ResultsParseError, match="counties sum to 561 for, the breakdown says 259"):  # fmt: skip
        results._check_questions_statewide(PG20, frames, named_breakdown(259, 93))


def test_statewide_question_check_compares_the_third_choice():
    third = NAMED_COUNTY_ROWS.replace('"4078","628",""', '"4078","628","5"').replace(
        '"964",""', '"964","5"'
    )
    frames = [results.read_questions(named_county(third), GG18, "Kent").frame]
    assert results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628, "5")) == 1
    with pytest.raises(results.ResultsParseError, match="question 01: counties sum to 5 third, the breakdown says 6"):  # fmt: skip
        results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628, "6"))
    # a blank Vote Type 3 row and blank third cells agree; a local question's
    # third-choice row (Wicomico's A) is not compared
    frames = [results.read_questions(named_county(), GG18, "Kent").frame]
    local = "3,23,A,County,,6514,\n"
    assert results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628, "", local)) == 1  # fmt: skip
    with pytest.raises(results.ResultsParseError, match="counties sum to 0 third, the breakdown says 3"):  # fmt: skip
        results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628, "3"))


def test_statewide_question_check_needs_each_question_on_both_sides():
    frames = [results.read_questions(named_county(), GG18, "Kent").frame]
    extra = '1,00,"02",State,,1,\n2,00,"02",State,,1,\n'
    with pytest.raises(results.ResultsParseError, match="question 02 for is in the breakdown but no county file"):  # fmt: skip
        results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628, "", extra))
    two = named_county(NAMED_COUNTY_ROWS + NAMED_COUNTY_ROWS.replace('"01"', '"02"'))
    frames = [results.read_questions(two, GG18, "Kent").frame]
    with pytest.raises(results.ResultsParseError, match="question 02 for is in the county files but not the breakdown"):  # fmt: skip
        results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628))


def test_statewide_question_check_refuses_an_unknown_vote_type():
    frames = [results.read_questions(named_county(), GG18, "Kent").frame]
    with pytest.raises(results.ResultsParseError, match="unknown vote type '4'"):
        results._check_questions_statewide(GG18, frames, named_breakdown(4078, 628, "", '4,00,"01",State,,1,\n'))  # fmt: skip


def test_question_precinct_check():
    county = results.read_questions(coded_county(), PG24, "Kent").frame
    precincts = results.read_questions(coded_precincts(), PG24, "Kent", level="precinct").frame
    check = results._check_question_precincts(PG24, "Kent", precincts, county)
    assert check.contests == 1 and check.rows == 1
    assert check.columns[-2:] == ["total_votes", "total_against"]
    bad = results.read_questions(
        coded_precincts(rows=CODED_PRECINCT_ROWS.replace('"3000"', '"3001"')), PG24, "Kent", level="precinct"
    ).frame  # fmt: skip
    with pytest.raises(results.ResultsParseError, match="precincts sum to 3244 election_night_votes"):  # fmt: skip
        results._check_question_precincts(PG24, "Kent", bad, county)


# Wicomico PG16 question A: election night 6289 for, 13195 against, 6514 third
WICOMICO_PRECINCTS_A = (
    '"23","001","001","01","37A","A","County","","6000","13000","6500"\n'
    '"23","001","002","01","37A","A","County","","289","195","14"\n'
)


def test_question_precinct_check_compares_the_third_choice():
    county = results.read_questions(named_county(WICOMICO_2016), PG16, "Wicomico").frame
    body = (NAMED_PRECINCT_HEAD + WICOMICO_PRECINCTS_A).encode()
    precincts = results.read_questions(body, PG16, "Wicomico", level="precinct").frame
    check = results._check_question_precincts(PG16, "Wicomico", precincts, county)
    # election night only: the precinct file's total_third is its election-night third choice,
    # the county file's covers every vote type, so the totals are not compared
    assert check.columns == [
        "election_night_votes", "election_night_against", "election_night_third",
    ]  # fmt: skip
    body = (NAMED_PRECINCT_HEAD + WICOMICO_PRECINCTS_A.replace('"14"', '"15"')).encode()
    bad = results.read_questions(body, PG16, "Wicomico", level="precinct").frame
    with pytest.raises(results.ResultsParseError, match="A: precincts sum to 6515 election_night_third, the county file says 6514"):  # fmt: skip
        results._check_question_precincts(PG16, "Wicomico", bad, county)


def test_question_precinct_check_columns_from_2020_include_the_third_total():
    columns = results._question_precinct_check_columns(PG20)
    assert columns[:3] == ["early_voting_votes", "early_voting_against", "early_voting_third"]
    assert columns[-3:] == ["total_votes", "total_against", "total_third"]
    assert "election_night_third" not in results._question_precinct_check_columns(PG12)
    assert results._question_precinct_check_columns(PG12) == [
        "election_night_votes", "election_night_against",
    ]  # fmt: skip


# ---- the parse ----------------------------------------------------------------------------


def fill_questions(env, election=PG24, overrides=None, skip=()):
    for f in results.question_files(election):
        if f.source_id in skip:
            continue
        data = {
            "question": lambda: coded_county(f.county_code),
            "question-precinct": lambda: coded_precincts(f.county_code),
            "question-check": lambda: coded_breakdown(7773 * 24, 2937 * 24),
        }[f.role]()
        archive(env, election, f, (overrides or {}).get(f.source_id) or data)


def test_parse_questions_writes_county_csvs_and_gzipped_precinct_files(env):
    fill_questions(env)
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert failed == [] and written == 48
    base = env / "data" / "results" / "PG24" / "questions"
    county = pl.read_csv(base / "county" / "15-kent.csv", infer_schema=False)
    assert county["total_votes"].to_list() == ["7773"]
    side = json.loads((base / "county" / "15-kent_provenance.json").read_text())
    assert side["family"] == "questions" and side["geography"] == "county"
    assert side["election"] == "PG24"
    assert side["source_id"] == "sbe-results-PG24-question-15"
    assert side["checks"]["statewide_check"].startswith(
        "ok: 1 statewide questions match PG24_AllQuestionsCongressionalBreakDown.csv"
    )
    precinct = base / "precinct" / "15-kent.csv.gz"
    assert pl.read_csv(precinct).height == 2
    side = json.loads((base / "precinct" / "15-kent_provenance.json").read_text())
    assert side["geography"] == "precinct"
    assert side["source_id"] == "sbe-results-PG24-question-precinct-15"
    assert side["checks"]["county_check"].startswith(
        "ok: the precincts sum to PG24_15QuestionResults.csv's 1 rows (1 questions)"
    )
    kent = next(f for f in results.question_files(PG24) if f.role == "question" and f.county_code == "15")  # fmt: skip
    assert side["checks"]["county_source"] == {
        "source_id": "sbe-results-PG24-question-15",
        "sha256": results.newest_source(results.MANIFEST_PATH, kent.source_id).sha256,
    }
    # current files are not written again
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (0, [])


def test_parse_questions_is_all_or_nothing(env):
    fill_questions(env, overrides={"sbe-results-PG24-question-check": coded_breakdown(1, 1)})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert written == 0 and failed[0].startswith("PG24 questions: ")
    assert "PG24_AllQuestionsCongressionalBreakDown.csv" in failed[0]
    assert not (env / "data" / "results" / "PG24" / "questions").exists()


def test_parse_questions_names_the_precinct_file_that_does_not_add_up(env):
    bad = coded_precincts("15", CODED_PRECINCT_ROWS.replace('"3000"', '"3001"'))
    fill_questions(env, overrides={"sbe-results-PG24-question-precinct-15": bad})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert written == 0 and failed[0].startswith("PG24 questions: PG24_15QuestionbyPrecinctsResults.csv: ")  # fmt: skip


def test_parse_questions_missing_and_unfetched_files(env):
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert failed == [
        "PG24 questions: question files not fetched (skipjack results-fetch --level questions)"
    ]
    fill_questions(env, skip={"sbe-results-PG24-question-precinct-15"})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert written == 0
    assert failed == [
        "PG24 questions: missing required file PG24_15QuestionbyPrecinctsResults.csv "
        "(sbe-results-PG24-question-precinct-15)"
    ]


def _kent_question():
    return next(
        f for f in results.question_files(PG24) if f.role == "question" and f.county_code == "15"
    )


def test_a_reposted_county_question_file_rechecks_its_precincts(env):
    fill_questions(env)
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (48, [])
    base = env / "data" / "results" / "PG24" / "questions"
    out = base / "precinct" / "15-kent.csv.gz"
    side_path = base / "precinct" / "15-kent_provenance.json"
    before = (out.read_bytes(), side_path.read_text())

    # a re-posted county file the precincts no longer sum to: nothing is written
    shifted = CODED_COUNTY_ROWS.replace('"3243","1534"', '"3244","1534"').replace('"7773"', '"7774"')  # fmt: skip
    archive(env, PG24, _kent_question(), coded_county("15", shifted))
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert written == 0 and len(failed) == 1
    assert (out.read_bytes(), side_path.read_text()) == before

    # one whose figures still match (the winner flag changed): Kent's county file and its
    # precinct file are written again, every other county's are current
    archive(env, PG24, _kent_question(), coded_county("15", CODED_COUNTY_ROWS.replace('"Y"', '""')))
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (2, [])
    new_sha = results.newest_source(results.MANIFEST_PATH, _kent_question().source_id).sha256
    assert json.loads(side_path.read_text())["checks"]["county_source"]["sha256"] == new_sha
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (0, [])


def test_a_precinct_question_file_checked_against_an_older_county_file_is_stale(env):
    fill_questions(env)
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (48, [])
    side_path = env / "data" / "results" / "PG24" / "questions" / "precinct" / "15-kent_provenance.json"  # fmt: skip
    side = json.loads(side_path.read_text())
    side["checks"]["county_source"]["sha256"] = "0" * 64
    side_path.write_text(json.dumps(side))
    assert results.results_parse("PG24", data_dir=env / "data", level="questions") == (1, [])


def test_level_all_parses_the_questions_of_a_general(env):
    fill_questions(env)  # no county or precinct files: those levels fail, the questions do not
    written, failed = results.results_parse("PG24", data_dir=env / "data")
    assert written == 48
    assert [f.split(":")[0] for f in failed] == ["PG24 county", "PG24 precinct"]


GG10_COUNTY_HEAD = (
    '"County","Question Number","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Election Night Votes","Election Night Votes Against","Absentees Votes",'
    '"Absentees Votes Against","Provisional Votes","Provisional Votes Against",'
    '"2nd Absentees Votes","2nd Absentees Votes Against","3rd Absentees Votes",'
    '"3rd Absentees Votes Against","Total Votes","Total Votes Against"\n'
)
GG10_COUNTY_ROW = '"00","01","State","","669","747","2670","2323","147","179","48","28","22","32","8","13","3564","3322"\n'  # fmt: skip
GG10_PRECINCT = (
    '"County","Election District","Election Precinct","Question Number","How it Runs?",'
    '"Winner","Election Night Votes","Election Night Votes Against"\n'
    '"CC","001","001","01","State","","2670","2323"\n'
)


def test_gg10_has_no_statewide_question_breakdown(env):
    for f in results.question_files(GG10):
        if f.role == "question":
            data = GG10_COUNTY_HEAD + GG10_COUNTY_ROW
        else:
            data = GG10_PRECINCT.replace('"CC"', f'"{f.county_code}"')
        archive(env, GG10, f, data.encode())
    written, failed = results.results_parse("GG10", data_dir=env / "data", level="questions")
    assert failed == [] and written == 48
    base = env / "data" / "results" / "GG10" / "questions"
    side = json.loads((base / "county" / "15-kent_provenance.json").read_text())
    assert side["checks"]["statewide_check"] == (
        "n/a: the board posted no statewide question breakdown for 2010"
    )
    side = json.loads((base / "precinct" / "15-kent_provenance.json").read_text())
    assert "election-night votes only" in side["checks"]["county_check"]


def test_cli_results_parse_takes_the_questions_level(monkeypatch):
    from skipjack import cli

    seen = {}

    def fake_parse(**kwargs):
        seen.update(kwargs)
        return 0, []

    monkeypatch.setattr(results, "results_parse", fake_parse)
    cli.main(["results-parse", "--election", "PG24", "--level", "questions"])
    assert seen["level"] == "questions"
