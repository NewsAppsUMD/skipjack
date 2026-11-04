"""Tests for the unofficial results page readers (the board's running count, GG26).

The fixtures are the board's own pages, saved on 2026-11-04 (Last refreshed 09:21:22 AM):
Allegany (county 1: ballot questions printing 0 provisional), Caroline (county 6: mail-in not
reported), Montgomery (county 16, the largest page), the statewide Governor page, the statewide
questions page and the home page with the canvass-status table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skipjack.pipeline import results_unofficial as ru

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "results_unofficial"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def allegany() -> ru.Page:
    return ru.read_results_page(page("gen_results_2026_by_county_1.html"))


@pytest.fixture(scope="module")
def caroline() -> ru.Page:
    return ru.read_results_page(page("gen_results_2026_by_county_6.html"))


@pytest.fixture(scope="module")
def montgomery() -> ru.Page:
    return ru.read_results_page(page("gen_results_2026_by_county_16.html"))


@pytest.fixture(scope="module")
def status() -> list[ru.CountyStatus]:
    return ru.read_canvass_status(page("status.html"))


def contest(p: ru.Page, office: str, district: str = "") -> ru.Contest:
    found = [c for c in p.contests if c.office == office and c.district == district]
    assert len(found) == 1, (office, district, [(c.office, c.district) for c in p.contests])
    return found[0]


# --- the refreshed time --------------------------------------------------------------------


def test_snapshot_id_is_the_boards_refreshed_time_in_24_hours():
    assert ru.snapshot_id("11/04/2026 09:21:22 AM") == "2026-11-04T092122"
    assert ru.snapshot_id("11/04/2026 12:05:00 AM") == "2026-11-04T000500"
    assert ru.snapshot_id("11/04/2026 01:30:09 PM") == "2026-11-04T133009"


def test_last_refreshed_reads_the_page_and_refuses_a_page_without_one():
    assert ru.last_refreshed(page("gen_results_2026_1.html")) == "11/04/2026 09:21:22 AM"
    # the home page says "Last updated", not "Last refreshed", and carries the same time
    assert ru.last_refreshed(page("status.html")) == "11/04/2026 09:21:22 AM"
    with pytest.raises(ru.UnofficialParseError):
        ru.last_refreshed("<html><body>nothing</body></html>")


# --- county pages --------------------------------------------------------------------------


def test_county_page_names_the_county_in_the_repos_spelling(allegany, montgomery):
    assert allegany.subject == "Allegany County"
    assert allegany.county == "Allegany"
    assert montgomery.county == "Montgomery"
    assert allegany.last_refreshed == "11/04/2026 09:21:22 AM"


@pytest.mark.parametrize(
    ("subject", "county"),
    [
        ("Baltimore City", "Baltimore City"),
        ("Baltimore County", "Baltimore County"),
        ("Saint Mary's County", "St. Mary's"),
        ("Prince George's County", "Prince George's"),
        ("Governor / Lt. Governor", None),
    ],
)
def test_county_from_subject(subject, county):
    assert ru.county_from_subject(subject) == county


def test_governor_in_allegany(allegany):
    gov = contest(allegany, "Governor / Lt. Governor")
    assert gov.kind == "contest"
    assert gov.vote_for == 1
    assert (gov.precincts_reported, gov.precincts_total) == (30, 30)
    moore = gov.rows[0]
    assert moore.candidate == "Wes Moore and Aruna Miller"
    assert moore.party == "Democratic"
    assert (moore.early, moore.election_day, moore.mail_in) == (1275, 3477, 906)
    assert moore.provisional is None  # NR, not zero
    assert moore.total == 5658
    assert not moore.write_in
    assert gov.rows[-1].candidate == "Other Write-Ins"
    assert gov.rows[-1].write_in
    assert gov.totals.total == 19350
    assert gov.totals.provisional is None


def test_mail_in_not_reported_in_caroline(caroline):
    gov = contest(caroline, "Governor / Lt. Governor")
    assert all(r.mail_in is None and r.provisional is None for r in gov.rows)
    assert all(r.early is not None and r.election_day is not None for r in gov.rows)


def test_districts_vote_for_and_seat_rules(montgomery):
    d9a = contest(montgomery, "House of Delegates", "District 9A")
    assert d9a.vote_for == 2
    assert d9a.seat_rule == ""
    assert d9a.rows[0].candidate == "Chao Wu"
    assert (d9a.precincts_reported, d9a.precincts_total) == (4, 4)
    # every House of Delegates table in Montgomery is under one <h2>, each with its district
    delegates = [c for c in montgomery.contests if c.office == "House of Delegates"]
    assert len(delegates) > 1
    assert all(c.district.startswith("District ") for c in delegates)


def test_named_write_ins_drop_the_label_and_are_flagged():
    gov_page = ru.read_results_page(page("gen_results_2026_by_county_16.html"))
    flagged = [r for c in gov_page.contests for r in c.rows if r.write_in]
    assert flagged
    assert not any("(Write-In)" in r.candidate for c in gov_page.contests for r in c.rows)


def test_judges_continuance_is_a_retention_contest(allegany):
    tables = [
        c for c in allegany.contests
        if c.office == "Judge, Appellate Court of Maryland At Large"
    ]  # fmt: skip
    assert tables
    friedman = next(c for c in tables if c.candidate == "Dan Friedman")
    assert friedman.kind == "retention"
    assert [r.choice for r in friedman.rows] == ["Yes", "No"]
    assert friedman.rows[0].candidate == "Dan Friedman"
    assert friedman.rows[0].party == ""
    assert friedman.rows[0].total == 13810
    assert friedman.totals.total == 16566


def test_ballot_questions_in_allegany(allegany):
    q1 = contest(allegany, "Question 1: Constitutional Amendment")
    assert q1.kind == "question"
    assert q1.detail == (
        "(Ch. 155 of the 2026 Legislative Session) — "
        "Arbitration Reform for State Employees Act of 2026"
    )
    assert q1.text_url.endswith("gen_qtext_2026_C_1.html")
    assert [r.choice for r in q1.rows] == [
        "For the Constitutional Amendment",
        "Against the Constitutional Amendment",
    ]
    assert q1.rows[0].candidate == ""
    # the county pages print 0 for provisional where the statewide page prints NR
    assert (q1.rows[0].mail_in, q1.rows[0].provisional, q1.rows[0].total) == (1107, 0, 11636)
    assert q1.totals.total == 17812
    assert (q1.precincts_reported, q1.precincts_total) == (30, 30)


def test_every_table_is_read(allegany, montgomery):
    for p, name in ((allegany, "gen_results_2026_by_county_1.html"),
                    (montgomery, "gen_results_2026_by_county_16.html")):  # fmt: skip
        assert len(p.contests) == page(name).count('class="table-banded"')


def test_unknown_office_fails():
    html = page("gen_results_2026_by_county_1.html").replace(
        "<h2>Sheriff</h2>", "<h2>Dogcatcher</h2>"
    )
    with pytest.raises(ru.UnofficialParseError, match="Dogcatcher"):
        ru.read_results_page(html)


def test_printed_total_is_kept_as_printed(allegany):
    # the reader does not check arithmetic (the parse does), it records what is printed
    for c in allegany.contests:
        for r in c.rows:
            assert r.total is not None


# --- statewide pages ------------------------------------------------------------------------


def test_statewide_governor_page():
    p = ru.read_results_page(page("gen_results_2026_1.html"))
    assert p.county is None
    assert p.subject == "Governor / Lt. Governor"
    gov = contest(p, "Governor / Lt. Governor")
    assert {r.candidate: r.total for r in gov.rows}["Wes Moore and Aruna Miller"] == 1163909
    assert (gov.precincts_reported, gov.precincts_total) == (1968, 1979)


def test_statewide_questions_page_prints_nr_for_provisional():
    p = ru.read_results_page(page("gen_qresults_2026_1_00_ALL.html"))
    q1 = contest(p, "Question 1: Constitutional Amendment")
    assert q1.text_url.endswith("gen_qtext_2026_S_1.html")
    assert q1.rows[0].total == 1303235
    assert q1.rows[0].provisional is None
    assert [c.office for c in p.contests][:3] == [
        "Question 1: Constitutional Amendment",
        "Question 2: Constitutional Amendment",
        "Question 3: Constitutional Amendment",
    ]


# --- the canvass-status table ---------------------------------------------------------------


def test_canvass_status_has_every_county(status):
    assert [s.county for s in status][:4] == [
        "Allegany", "Anne Arundel", "Baltimore City", "Baltimore County"
    ]  # fmt: skip
    assert len(status) == 24
    assert "St. Mary's" in {s.county for s in status}


def test_canvass_status_values(status):
    by = {s.county: s for s in status}
    city = by["Baltimore City"]
    assert city.last_updated == "11/04/26 01:24 AM"
    assert (city.precincts_reported, city.precincts_total) == (294, 295)
    assert city.canvass == {
        "EV": "complete", "EN": "partial", "MIB 1": "partial", "Prov": "not_reported",
        "MIB 2": "not_reported",
    }  # fmt: skip
    assert by["Caroline"].canvass["MIB 1"] == "not_reported"
    assert by["Allegany"].canvass["MIB 1"] == "partial"


# --- a printed 0 in a canvass that has not reported -----------------------------------------


def test_zero_in_an_unreported_canvass_becomes_not_counted(allegany, status):
    fixed = ru.apply_not_reported(allegany, {s.county: s for s in status})
    q1 = contest(fixed, "Question 1: Constitutional Amendment")
    assert q1.rows[0].provisional is None
    assert q1.totals.provisional is None
    assert q1.rows[0].mail_in == 1107  # MIB 1 is partly reported, so its figure stands
    assert q1.rows[0].total == 11636


def test_zero_in_a_reported_canvass_is_kept(caroline, status):
    fixed = ru.apply_not_reported(caroline, {s.county: s for s in status})
    # write-in rows print real zeros in early voting, a canvass Caroline has completed
    zeros = [r for c in fixed.contests for r in c.rows if r.early == 0]
    assert zeros


def test_mail_in_zero_stays_when_only_the_second_mail_in_canvass_is_unreported():
    s = ru.CountyStatus(
        county="Allegany", last_updated="", precincts_reported=1, precincts_total=1,
        canvass={"EV": "complete", "EN": "complete", "MIB 1": "complete",
                 "Prov": "not_reported", "MIB 2": "not_reported"},
    )  # fmt: skip
    assert not ru.unreported(s, "mail_in")
    assert ru.unreported(s, "provisional")


def test_apply_not_reported_needs_the_countys_status(allegany):
    with pytest.raises(ru.UnofficialParseError, match="Allegany"):
        ru.apply_not_reported(allegany, {})
