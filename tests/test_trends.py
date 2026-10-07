"""Tests for the trend pages: how people register, removals, party switching, errata, bulk files.

The first half runs on small made-up tables. The second half checks the pages against the
built skipjack.db and is skipped when it has not been built.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from skipjack.models import SCHEMA_SQL
from skipjack.web import bulk, elections, errata, trends
from skipjack.web.app import app
from skipjack.web.dates import month_label, shift_month
from skipjack.web.db import DB_PATH
from skipjack.web.summary import signed

# --- helpers on made-up data ---------------------------------------------------------------


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def add_summary(conn, section, rows):
    """rows: (report_date, category, party, value)."""
    conn.executemany(
        "INSERT INTO registration_summary (source_id, report_date, section, category, party, value)"
        " VALUES ('s', ?, ?, ?, ?, ?)",
        [(d, section, c, p, v) for d, c, p, v in rows],
    )


def test_shift_month_crosses_years():
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2026-08", -12) == "2025-08"
    assert shift_month("2025-12", 1) == "2026-01"
    assert month_label("2026-08") == "August 2026"


def test_rolling_needs_a_full_window_of_known_months():
    assert trends.rolling([1, 2, 3, 4], window=3) == [None, None, 6, 9]
    assert trends.rolling([1, None, 3, 4], window=2) == [None, None, None, 7]


def test_add_is_none_only_when_every_input_is_none():
    assert trends.add([[1, None, None], [2, 5, None]]) == [3, 5, None]


@pytest.mark.parametrize(
    ("n", "digits", "suffix", "expected"),
    [
        (1234, 0, "", "+1,234"),
        (-4, 0, "", "−4"),
        (0, 0, "", "0"),
        (-0.02, 1, "%", "0.0%"),
        (0.04, 1, "%", "0.0%"),
        (-1.74, 1, "%", "−1.7%"),
    ],
)
def test_signed_never_prints_a_negative_zero(n, digits, suffix, expected):
    assert signed(n, digits, suffix) == expected


def test_a_category_with_no_row_counts_as_zero_once_it_has_appeared(conn):
    # SAME DAY appears in months 1 and 3 only. The report omits the row in months with nothing
    # to report, so month 2 is zero, and month 0 (before it existed) is unknown.
    add_summary(conn, "new_registration", [
        ("2020-01", "BY MAIL", "DEM", 10),
        ("2020-02", "BY MAIL", "DEM", 11),
        ("2020-02", "SAME DAY REGISTRATION", "DEM", 4),
        ("2020-03", "BY MAIL", "DEM", 12),
        ("2020-04", "BY MAIL", "DEM", 13),
        ("2020-04", "SAME DAY REGISTRATION", "DEM", 6),
    ])  # fmt: skip
    new = trends.new_registrations(conn)
    same_day = next(g for g in new.groups if g.name == "Same-day registration")
    assert same_day.monthly == [None, 4, 0, 6]


def test_a_row_printed_as_na_stays_unknown(conn):
    add_summary(conn, "new_registration", [
        ("2020-01", "BY MAIL", "DEM", 10),
        ("2020-02", "BY MAIL", "DEM", None),
        ("2020-03", "BY MAIL", "DEM", 12),
    ])  # fmt: skip
    mail = trends.new_registrations(conn).groups[0]
    assert mail.monthly == [10, None, 12]


def test_duplicates_are_not_counted_and_unaffiliated_codes_merge(conn):
    add_summary(conn, "new_registration", [
        ("2020-01", "BY MAIL", "DEM", 10),
        ("2020-01", "BY MAIL", "UNAF", 5),
        ("2020-01", "BY MAIL", "UNA", 2),
        ("2020-01", "BY MAIL", "GRN", 1),
        ("2020-01", "BY MAIL", "DUPS", 99),
    ])  # fmt: skip
    mail = trends.new_registrations(conn).groups[0]
    assert mail.monthly == [18]
    assert mail.by_party["Unaffiliated"] == [7]
    assert mail.by_party["Other"] == [1]


def test_unknown_categories_land_in_other_instead_of_vanishing(conn):
    add_summary(conn, "removal", [
        ("2020-01", "DEATH NOTICE", "DEM", 3),
        ("2020-01", "SOMETHING NEW", "DEM", 4),
    ])  # fmt: skip
    gone = trends.removals(conn)
    assert gone.unmapped == ["SOMETHING NEW"]
    assert trends.unmapped_categories(conn)["removal"] == ["SOMETHING NEW"]
    assert [g.name for g in gone.groups] == ["Death", trends.OTHER_GROUP]
    assert gone.total == [7]


def test_groups_do_not_share_categories():
    for groups in (trends.METHOD_GROUPS, trends.REMOVAL_GROUPS):
        names = [c for _, cats in groups for c in cats]
        assert len(names) == len(set(names))


def test_year_rows_mark_a_partial_year_and_election_year_share(conn):
    rows = []
    for year in (2020, 2021, 2022, 2023):
        for month in range(1, 13):
            rows.append(
                (f"{year}-{month:02d}", "ONLINE REGISTRATION", "DEM", 90 if year % 2 == 0 else 10)
            )
    rows += [("2024-01", "ONLINE REGISTRATION", "DEM", 5)]
    add_summary(conn, "new_registration", rows)
    new = trends.new_registrations(conn)
    online = new.groups[0]
    assert new.complete_years() == ["2020", "2021", "2022", "2023"]
    partial = new.year_rows()[0]
    assert partial["year"] == "2024" and partial["months"] == 1 and partial["cells"] == [5]
    # Even years hold 90% of the yearly average; the partial 2024 is ignored.
    assert new.election_year_share(online, minimum=1) == pytest.approx(90.0)
    assert new.election_year_share(online, minimum=10**9) is None  # too small to mean anything


def test_party_rows_report_shares_of_the_last_twelve_months(conn):
    rows = [(f"2020-{m:02d}", "DEATH NOTICE", "DEM", 3) for m in range(1, 13)]
    rows += [(f"2020-{m:02d}", "DEATH NOTICE", "REP", 1) for m in range(1, 13)]
    add_summary(conn, "removal", rows)
    row = trends.party_rows(trends.removals(conn))[0]
    assert row["total"] == 48
    assert row["shares"]["Democratic"] == pytest.approx(75.0)
    assert row["shares"]["Republican"] == pytest.approx(25.0)


def test_primary_rows_compare_the_primary_month_with_a_typical_month():
    dates = [f"2022-{m:02d}" for m in range(1, 13)]
    totals = [100, 100, 100, 100, 100, 200, 10, 100, 100, 100, 100, 100]  # July 2022 is the primary
    sw = trends.Switches(dates, {b: totals for b in trends.PARTY_BUCKETS}, totals)
    row = next(r for r in trends.primary_rows(sw) if r["month"] == "2022-07")
    assert row["during"] == 10 and row["before"] == 200 and row["typical"] == 100
    assert row["ratio"] == pytest.approx(0.1)
    assert row["rank"] == 1 and row["months"] == 12


def test_primary_rows_skip_years_with_too_few_months():
    sw = trends.Switches(["2022-06", "2022-07"], {}, [5, 5])
    assert trends.primary_rows(sw) == []


def test_align_places_values_on_the_chart_months():
    assert trends.align(["a", "b", "c"], ["b", "c"], [1, 2]) == [None, 1, 2]


def test_combined_csv_has_a_source_id_on_every_row(conn):
    add_summary(
        conn,
        "removal",
        [("2020-01", "DEATH NOTICE", "DEM", 3), ("2020-02", "DUPLICATE", "REP", None)],
    )
    rows = list(csv.reader(io.StringIO(bulk.bulk_csv(conn, "registration_summary_all.csv"))))
    assert rows[0] == ["report_date", "section", "category", "party", "value", "source_id"]
    assert rows[1] == ["2020-01", "removal", "DEATH NOTICE", "DEM", "3", "sbe-vrar-2020-01"]
    assert rows[2][4] == "" and rows[2][5] == "sbe-vrar-2020-02"  # NA stays blank
    with pytest.raises(KeyError):
        bulk.bulk_csv(conn, "../../etc/passwd")


# --- election dates ---------------------------------------------------------------------------


def test_election_dates_fall_on_tuesdays():
    for d in elections.PRIMARIES + elections.GENERALS:
        assert datetime.date.fromisoformat(d).weekday() == 1, d


def test_general_elections_are_the_tuesday_after_the_first_monday_in_november():
    for d in elections.GENERALS:
        day = datetime.date.fromisoformat(d)
        first = datetime.date(day.year, 11, 1)
        monday = first + datetime.timedelta(days=(0 - first.weekday()) % 7)
        assert day == monday + datetime.timedelta(days=1), d


def test_each_year_has_one_primary_and_one_general():
    assert [d[:4] for d in elections.PRIMARIES] == [d[:4] for d in elections.GENERALS]
    assert elections.PRIMARIES == sorted(elections.PRIMARIES)


def test_the_voter_files_own_election_dates_match_ours():
    # The voter file's history columns are named with the State Board's election dates, so they
    # are a source to check the hand-entered list against.
    import json

    from skipjack.web.paths import DATA_DIR

    for snapshot in (DATA_DIR / "voter_file").glob("*/snapshot.json"):
        for e in json.loads(snapshot.read_text())["elections"]:
            known = elections.PRIMARIES if e["kind"] == "primary" else elections.GENERALS
            assert e["date"] in known, f"{e['date']} ({e['label']}) is not in elections.py"


# --- against the built database --------------------------------------------------------------

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="skipjack.db not built")
client = TestClient(app)


def page(path: str) -> str:
    """A page's HTML with runs of whitespace collapsed, so sentences can be matched whole."""
    return re.sub(r"\s+", " ", client.get(path).text)


def real():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


@needs_db
def test_every_category_in_the_reports_belongs_to_a_group():
    # When the State Board adds a category, this fails until someone names its group.
    with real() as c:
        assert trends.unmapped_categories(c) == {"new_registration": [], "removal": []}


@needs_db
def test_group_totals_match_the_reports_own_totals():
    with real() as c:
        new, gone = trends.new_registrations(c), trends.removals(c)
    assert new.total[new.dates.index("2026-08")] == 41_106
    assert gone.total[gone.dates.index("2026-08")] == 40_037


@needs_db
def test_yearly_totals_add_up_across_groups():
    with real() as c:
        new = trends.new_registrations(c)
    for row in new.year_rows():
        assert sum(v or 0 for v in row["cells"]) == row["total"]
    assert next(r for r in new.year_rows() if r["year"] == "2024")["total"] == 357_511


@needs_db
def test_same_day_registration_starts_in_2016_and_is_zero_between_elections():
    with real() as c:
        new = trends.new_registrations(c)
    same_day = next(g for g in new.groups if g.name == "Same-day registration")
    i = new.dates.index("2018-08")
    assert same_day.monthly[new.dates.index("2016-03")] is None
    assert same_day.monthly[i] == 0  # no row on the 2018-08 report
    assert same_day.monthly[new.dates.index("2018-10")] == 2_004


@needs_db
def test_start_months_named_on_the_methods_page():
    with real() as c:
        new = trends.new_registrations(c)

    def first(name):
        group = next(g for g in new.groups if g.name == name)
        return next(d for d, v in zip(new.dates, group.monthly, strict=True) if v is not None)

    assert first("Online") == "2012-07"
    assert first("Same-day registration") == "2016-04"
    assert "July 2012" in page("/voters/methods/") and "April 2016" in page("/voters/methods/")


@needs_db
def test_from_another_board_has_been_zero_since_april_2019():
    # The methods page says so; this keeps the sentence true when a new report is loaded.
    with real() as c:
        new = trends.new_registrations(c)
    group = next(g for g in new.groups if g.name == "From another board")
    after = new.dates.index("2019-04") + 1
    assert sum(v or 0 for v in group.monthly[after:]) == 0
    assert group.monthly[new.dates.index("2019-04")] > 0


@needs_db
@pytest.mark.parametrize(
    "path",
    ["/voters/methods/", "/voters/removals/", "/voters/party-switching/", "/errata/"],
)
def test_new_pages_render_with_the_section_nav(path):
    r = client.get(path)
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text
    for link in ("/voters/methods/", "/voters/removals/", "/voters/party-switching/", "/errata/"):
        assert f'href="{link}"' in r.text


@needs_db
def test_methods_page_describes_the_latest_twelve_months():
    html = page("/voters/methods/")
    assert "September 2025 to August 2026" in html
    assert "Motor Vehicle Administration (77%)" in html
    assert "Online (87%)" in html  # concentrated in election years
    assert 'id="trend-data"' in html and 'src="/static/breakdown.js"' in html


@needs_db
def test_removals_page_warns_that_county_transfers_are_not_departures():
    html = page("/voters/removals/")
    assert "Removals are not all voters leaving Maryland" in html
    assert "101,880" in html  # transfers out in the 12 months to August 2026
    assert "+60,341" in html  # change in the rolls over the same months


@needs_db
def test_party_switching_page_lists_every_primary_without_overclaiming():
    html = page("/voters/party-switching/")
    assert html.count("/voters/month/20") >= 9
    assert "In 4 of the 9 primaries" in html
    assert "The pattern is not the same every year." in html
    assert "The 2026 figures cover only the 8 months reported so far." in html
    assert "coded IND" in html  # the 2010 spike is named, not explained


@needs_db
def test_errata_page_lists_every_recorded_discrepancy():
    rows = errata.load_discrepancies()
    html = page("/errata/")
    assert len(rows) == 18
    assert f"{len(rows)} mismatches in" in html
    for row in rows:
        assert re.sub(r"\s+", " ", str(escape(row["message"]))) in html
    assert "https://" in html and "MSR-2013_09.pdf" in html


@needs_db
def test_month_page_shows_changes_and_the_source():
    html = page("/voters/month/2026-08/")
    assert "+5,128" in html  # statewide change from July 2026
    assert "State Board of Elections report for 2026-08 (PDF)" in html
    assert "MSR-2026_08.pdf" in html
    assert "/downloads/voter_registration/monthly/2026-08_provenance.json" in html
    assert "does not add up" not in html  # no recorded mismatch this month


@needs_db
def test_month_page_with_a_source_error_says_so():
    html = page("/voters/month/2024-07/")
    assert "does not add up this month (2 mismatches)" in html
    assert 'href="/errata/"' in html


@needs_db
def test_earliest_months_have_no_changes_to_compare():
    html = page("/voters/month/2010-01/")
    # No December 2009 report and no January 2009 report, so no numbers in the change columns.
    assert 'class="num delta-up"' not in html and 'class="num delta-down"' not in html


@needs_db
def test_unaffiliated_change_survives_the_unaf_to_una_rename():
    # January 2025 is the first report to print UNA; a year earlier it was UNAF.
    html = page("/voters/month/2025-01/")
    row = re.search(r"Change from year before.*?</tr>", html, re.S).group(0)
    changes = [
        int(n.replace(",", "").replace("−", "-")) for n in re.findall(r'data-sort="(-?\d+)"', row)
    ]
    assert (
        changes and max(abs(n) for n in changes) < 200_000
    )  # not the whole party appearing from nothing


@needs_db
def test_home_page_highlights_describe_the_latest_report():
    html = page("/")
    assert "August 2026 in registration" in html
    assert "4,322,671 active registered voters in August 2026" in html
    assert 'id="home-data"' in html


@needs_db
def test_county_pages_link_to_each_other():
    trends_page = page("/voters/county/kent/")
    assert 'href="/voters/file/county/kent/"' in trends_page
    voter_file = page("/voters/file/county/kent/")
    assert 'href="/voters/county/kent/"' in voter_file
    statewide = page("/voters/county/")
    assert 'href="/voters/file/"' in statewide
    assert 'href="/voters/county/"' in page("/voters/file/")


@needs_db
def test_combined_downloads():
    r = client.get("/downloads/combined/registration_monthly_all.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    with real() as c:
        expected = c.execute("SELECT COUNT(*) FROM voter_registration").fetchone()[0]
    assert len(rows) - 1 == expected
    assert rows[0][-1] == "source_id" and rows[1][-1] == "sbe-vrar-2010-01"
    assert client.get("/downloads/combined/nope.csv").status_code == 404
    assert "registration_summary_all.csv" in client.get("/data/").text
