"""Tests for the ballot questions: readers, checks, parse."""

from __future__ import annotations

import pytest

from skipjack.pipeline import results

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
