"""Tests for the unofficial results page readers (the board's running count, GG26).

The fixtures are the board's own pages, saved on 2026-11-04 (Last refreshed 09:21:22 AM):
Allegany (county 1: ballot questions printing 0 provisional), Caroline (county 6: mail-in not
reported), Montgomery (county 16, the largest page), the statewide Governor page, the statewide
questions page and the home page with the canvass-status table.
"""

from __future__ import annotations

from dataclasses import replace
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


@pytest.mark.parametrize("word", ["ate", "are"])
def test_a_check_with_write_ins_assigned_is_a_complete_canvass(word):
    """From 2026-11-05 the board marks a count with write-ins assigned with its own alt text."""
    alt = f"Results have been reported and write-in votes {word} assigned."
    html = page("status.html").replace('<img alt="Canvass Complete"', f'<img alt="{alt}"', 1)
    assert ru.read_canvass_status(html)[0].canvass["EV"] == "complete"


def test_an_unknown_canvass_mark_fails():
    html = page("status.html").replace(
        '<img alt="Canvass Complete"', '<img alt="Local results only"', 1
    )
    with pytest.raises(ru.UnofficialParseError, match="an unexpected canvass mark"):
        ru.read_canvass_status(html)


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


# --- a whole snapshot, built small ------------------------------------------------------------
# Synthetic pages in the board's markup: every county votes for Governor (two tickets) and on
# Question 1 (which prints 0 for provisional, as the board's question tables do); the statewide
# Governor and questions pages carry the sums. The other statewide office pages are empty.

REFRESHED = "11/04/2026 09:21:22 AM"
HEAD = (
    "<th>Early Voting</th><th>Election Day</th><th>Mail-In Ballot</th><th>Provisional</th>"
    "<th>Total</th><th>Percentage</th>"
)


def _n(v) -> str:
    return "NR" if v is None else f"{v:,}"


def _cells(votes) -> str:
    return (
        "".join(f"<td>{_n(v)}</td>" for v in votes)
        + f"<td>{_n(sum(v or 0 for v in votes))}</td><td>1%</td>"
    )


def _governor(rows) -> str:
    body = "".join(
        f"<tr><td><div>{name}</div></td><td>{party}</td>{_cells(v)}</tr>" for name, party, v in rows
    )
    totals = [None if all(v[i] is None for _, _, v in rows) else sum(v[i] or 0 for _, _, v in rows)
              for i in range(4)]  # fmt: skip
    return (
        "<h2>Governor / Lt. Governor</h2><h3>Vote for 1</h3>(1 of 1 election day precincts reported)"
        f'<div class="tableWrapper"><table class="table-banded"><thead><tr><th>Name</th>'
        f"<th>Party</th>{HEAD}</tr></thead><tbody>{body}"
        f"<tr><td><div><b>Totals</b></div></td><td></td>{_cells(totals)}</tr></tbody></table></div>"
    )


def _question(rows) -> str:
    body = "".join(f"<tr><th><span>{label}</span></th>{_cells(v)}</tr>" for label, v in rows)
    totals = [sum(v[i] or 0 for _, v in rows) for i in range(4)]
    return (
        "<hgroup><h2>Question 1: Constitutional Amendment</h2><div><p>(Ch. 1)</p><p>A title</p>"
        '</div><a href="gen_qtext_2026_C_1.html">View Question Text</a></hgroup>'
        "<div>(1 of 1 election day precincts reported)</div>"
        f'<div class="tableWrapper"><table class="table-banded"><thead><tr><th>Question 1</th>'
        f"{HEAD}</tr></thead><tbody>{body}"
        f"<tr><th><span><b>Totals</b></span></th>{_cells(totals)}</tr></tbody></table></div>"
    )


def _page(subject: str, body: str, refreshed: str = REFRESHED) -> bytes:
    return (
        f'<html><body><nav><h2>Menu</h2></nav><section id="bodyMain"><h1> Unofficial 2026 '
        f"Gubernatorial General Election Results for {subject}</h1>"
        f"<div>Last refreshed: {refreshed}</div>{body}</section><footer></footer></body></html>"
    ).encode()


COUNTY_NAMES_IN_ORDER = list(ru.COUNTY_CODES.values())
SUBJECTS = {
    n: (name if name in ("Baltimore City", "Baltimore County") else f"{name} County").replace(
        "St. Mary's", "Saint Mary's"
    )
    for n, name in enumerate(COUNTY_NAMES_IN_ORDER, start=1)
}


def _status(refreshed: str = REFRESHED) -> bytes:
    done = '<td><img alt="Canvass Complete" src="/img/check.gif"/></td>'
    rows = "".join(
        f"<tr><td><a>{name.replace('St. ', 'Saint ')}</a></td><td>11/03/26 10:00 PM</td>"
        f"<td>1 of 1</td>{done}{done}{done}<td></td><td></td></tr>"
        for name in COUNTY_NAMES_IN_ORDER
    )
    return (
        f'<html><body><section id="bodyMain"><p>Last updated {refreshed}</p><table><thead><tr>'
        "<th>County</th><th>Last Updated</th><th># Precincts Reporting</th><th>EV</th><th>EN</th>"
        f"<th>MIB 1</th><th>Prov</th><th>MIB 2</th></tr></thead><tbody>{rows}</tbody></table>"
        "</section></body></html>"
    ).encode()


def snapshot_pages(
    refreshed: str = REFRESHED, tweak=None, bump: int | None = None
) -> dict[str, bytes]:
    """The pages of one snapshot. ``bump`` adds an early vote to Moore in that county after
    the statewide sums are taken, so its page adds up but the state does not."""
    pages: dict[str, bytes] = {}
    gov_total = [[0] * 4, [0] * 4]
    q_total = [[0] * 4, [0] * 4]
    for n, subject in SUBJECTS.items():
        moore, cox = [n, 2 * n, n, None], [1, n, 0, None]
        yes, no = [n, n, 1, 0], [1, 1, 0, 0]
        for acc, v in ((gov_total[0], moore), (gov_total[1], cox), (q_total[0], yes),
                       (q_total[1], no)):  # fmt: skip
            for i in range(4):
                acc[i] += v[i] or 0
        if n == bump:
            moore[0] += 1
        body = _governor([("Wes Moore and Aruna Miller", "Democratic", moore),
                          ("Dan Cox and Rob Krop", "Republican", cox)])  # fmt: skip
        body += _question([("For the Constitutional Amendment", yes),
                           ("Against the Constitutional Amendment", no)])  # fmt: skip
        pages[f"gen_results_2026_by_county_{n}.html"] = _page(subject, body, refreshed)
    nr = lambda v: [*v[:3], None]  # noqa: E731 - the statewide pages print NR for provisional
    pages["gen_results_2026_1.html"] = _page(
        "Governor / Lt. Governor",
        _governor(
            [
                ("Wes Moore and Aruna Miller", "Democratic", nr(gov_total[0])),
                ("Dan Cox and Rob Krop", "Republican", nr(gov_total[1])),
            ]
        ),  # fmt: skip
        refreshed,
    )
    for n in range(2, 10):
        pages[f"gen_results_2026_{n}.html"] = _page(f"Office {n}", "", refreshed)
    pages["gen_qresults_2026_1_00_ALL.html"] = _page(
        "All State Questions",
        _question(
            [
                ("For the Constitutional Amendment", nr(q_total[0])),
                ("Against the Constitutional Amendment", nr(q_total[1])),
            ]
        ),  # fmt: skip
        refreshed,
    )
    pages["status.html"] = _status(refreshed)
    if tweak:
        tweak(pages)
    return pages


class FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def serve(pages_for_call):
    """A stand-in httpx.Client; pages_for_call(call number) gives the pages for that pass."""
    calls = {"n": 0}

    class Client:
        def __init__(self, *a, **k):
            calls["n"] += 1
            self.pages = pages_for_call(calls["n"])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            name = url.rsplit("/", 1)[-1] or "status.html"
            if url == "https://elections.maryland.gov/":
                name = "status.html"
            return FakeResp(self.pages[name])

    return Client, calls


@pytest.fixture
def env(tmp_path, monkeypatch):
    from skipjack.pipeline import fetch

    arch = tmp_path / "archives"
    monkeypatch.setattr(ru, "ARCHIVES_DIR", arch)
    monkeypatch.setattr(ru, "MANIFEST_PATH", tmp_path / "manifest.csv")
    monkeypatch.setattr(ru, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(fetch, "ARCHIVES_DIR", arch)
    return tmp_path


def test_registry_lists_the_35_pages():
    pages = ru.UNOFFICIAL_RESULTS["GG26"].pages()
    assert len(pages) == 35
    assert pages[0][1].endswith("/2026/General_results/gen_results_2026_by_county_1.html")
    assert pages[-1] == ("status.html", "https://elections.maryland.gov/")


def test_gg26_is_live_until_its_certified_results_are_published(monkeypatch):
    from skipjack.pipeline.results import RESULTS_ELECTIONS

    assert ru.is_live("GG26")
    assert not ru.is_live("GP26")
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", True)
    assert not ru.is_live("GG26")


def test_fetch_archives_a_snapshot_named_by_the_boards_time(env, monkeypatch):
    client, _ = serve(lambda n: snapshot_pages())
    monkeypatch.setattr(ru.httpx, "Client", client)
    assert ru.unofficial_fetch("GG26") == "2026-11-04T092122"
    snap = env / "archives/sbe/results-unofficial/GG26/2026-11-04T092122"
    assert len(list(snap.iterdir())) == 35
    rows = ru.read_manifest(ru.MANIFEST_PATH)
    assert len(rows) == 35
    assert (
        rows[0].source_id
        == "sbe-results-unofficial-GG26-2026-11-04T092122-gen_results_2026_by_county_1"
    )
    # the board has not refreshed since: nothing new
    assert ru.unofficial_fetch("GG26") is None
    assert len(ru.read_manifest(ru.MANIFEST_PATH)) == 35


def test_fetch_retries_once_when_a_refresh_lands_mid_fetch(env, monkeypatch):
    def mixed(pages):
        pages["gen_results_2026_by_county_5.html"] = pages[
            "gen_results_2026_by_county_5.html"
        ].replace(b"09:21:22", b"09:31:22")

    client, calls = serve(lambda n: snapshot_pages(tweak=mixed) if n == 1 else snapshot_pages())
    monkeypatch.setattr(ru.httpx, "Client", client)
    assert ru.unofficial_fetch("GG26") == "2026-11-04T092122"
    assert calls["n"] == 2


def test_fetch_gives_up_on_mixed_times_twice_and_writes_nothing(env, monkeypatch):
    def mixed(pages):
        pages["gen_results_2026_1.html"] = snapshot_pages("11/04/2026 09:31:22 AM")[
            "gen_results_2026_1.html"
        ]

    client, _ = serve(lambda n: snapshot_pages(tweak=mixed))
    monkeypatch.setattr(ru.httpx, "Client", client)
    with pytest.raises(ru.UnofficialFetchError, match="different refreshed times"):
        ru.unofficial_fetch("GG26")
    assert ru.snapshots("GG26") == []
    assert not ru.MANIFEST_PATH.exists()


def test_fetch_and_parse_do_nothing_once_certified(env, monkeypatch):
    from skipjack.pipeline.results import RESULTS_ELECTIONS

    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", True)
    monkeypatch.setattr(ru.httpx, "Client", lambda *a, **k: pytest.fail("fetched"))
    assert ru.unofficial_fetch("GG26") is None
    assert ru.unofficial_parse("GG26") == ([], [])


def _fetch(monkeypatch, **kw):
    client, _ = serve(lambda n: snapshot_pages(**kw))
    monkeypatch.setattr(ru.httpx, "Client", client)
    return ru.unofficial_fetch("GG26")


def test_parse_writes_a_csv_per_county_and_the_canvass_status(env, monkeypatch):
    snap = _fetch(monkeypatch)
    assert ru.unofficial_parse("GG26") == ([snap], [])
    out = env / "data/results_unofficial/GG26" / snap
    files = sorted(p.name for p in (out / "county").glob("*.csv"))
    assert len(files) == 24
    assert files[0] == "01-allegany.csv"
    assert "19-st-marys.csv" in files
    import csv as csv_mod

    with open(out / "county/01-allegany.csv") as f:
        rows = list(csv_mod.DictReader(f))
    gov = rows[0]
    assert (gov["county"], gov["kind"], gov["candidate"], gov["party"]) == (
        "Allegany", "contest", "Wes Moore and Aruna Miller", "Democratic"
    )  # fmt: skip
    assert (gov["early"], gov["election_day"], gov["mail_in"], gov["provisional"]) == (
        "1",
        "2",
        "1",
        "",
    )
    q = rows[2]
    assert q["kind"] == "question"
    assert q["choice"] == "For the Constitutional Amendment"
    assert q["provisional"] == ""  # printed 0, the provisional canvass has not reported
    assert q["mail_in"] == "1"
    assert q["text_url"].endswith("/2026/General_results/gen_qtext_2026_C_1.html")
    side = ru.ProvenanceSidecar.read(out / "county/01-allegany_provenance.json")
    assert side.family == "results-unofficial"
    assert side.checks["unofficial"] is True
    assert side.checks["statewide_check"].startswith("ok: 2 statewide tables")
    assert side.checks["zeros_not_reported"] == 2
    with open(out / "canvass-status.csv") as f:
        status = list(csv_mod.DictReader(f))
    assert len(status) == 24
    assert (status[0]["mib1"], status[0]["prov"]) == ("complete", "not_reported")
    # parsed snapshots are not parsed again
    assert ru.unofficial_parse("GG26") == ([], [])


def test_parse_refuses_a_snapshot_whose_counties_do_not_sum_to_the_state(env, monkeypatch):
    snap = _fetch(monkeypatch, bump=3)
    written, failures = ru.unofficial_parse("GG26")
    assert written == []
    assert len(failures) == 1 and snap in failures[0]
    assert "do not sum to the statewide figures" in failures[0]
    assert not (env / "data/results_unofficial/GG26" / snap).exists()


def test_parse_refuses_a_row_that_does_not_add_up(env, monkeypatch):
    def bad_total(pages):
        p = pages["gen_results_2026_by_county_3.html"].decode()
        # Moore in county 3: 3 + 6 + 3 = 12; print 13
        pages["gen_results_2026_by_county_3.html"] = p.replace(
            "<td>12</td>", "<td>13</td>", 1
        ).encode()

    _fetch(monkeypatch, tweak=bad_total)
    written, failures = ru.unofficial_parse("GG26")
    assert written == [] and "do not add up" in failures[0]


def test_parse_refuses_archived_bytes_that_do_not_match_the_manifest(env, monkeypatch):
    snap = _fetch(monkeypatch)
    page = env / "archives/sbe/results-unofficial/GG26" / snap / "gen_results_2026_1.html"
    page.write_bytes(page.read_bytes() + b" ")
    written, failures = ru.unofficial_parse("GG26")
    assert written == [] and "sha256" in failures[0]


def test_check_statewide_compares_a_county_split_table_with_that_county():
    county = ru.read_results_page(page("gen_results_2026_by_county_1.html"))
    governor = contest(county, "Governor / Lt. Governor")
    split = replace(governor, area="Allegany County")
    statewide = ru.Page("Governor / Lt. Governor", None, county.last_refreshed, (split,))
    assert ru.check_statewide([county], [statewide]) == 1
    wrong = replace(split, area="Garrett County")
    with pytest.raises(ru.UnofficialParseError, match="no county page"):
        ru.check_statewide(
            [county], [ru.Page("Governor / Lt. Governor", None, county.last_refreshed, (wrong,))]
        )
