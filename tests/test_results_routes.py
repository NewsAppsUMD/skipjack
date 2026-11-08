"""Tests for the /results/ pages: routes, templates and 404s.

The first half runs the app against a small in-memory database (get_db is swapped, as in the
race tests); the last test renders every page of two real elections when skipjack.db has them.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from skipjack.models import SCHEMA_SQL
from skipjack.web import results
from skipjack.web.app import configure_templates, create_app
from skipjack.web.db import DB_PATH
from skipjack.web.routes import results as results_route


def add(conn, election, county, party_file, office, district, candidates):
    """Rows for one contest in one county; a candidate is (name, party, winner, votes, ...)
    with votes a {vote type stem: n} dict, then optionally an `against` count and write_in."""
    code = {"Allegany": "01", "Garrett": "11", "Baltimore City": "03", "Prince George's": "17"}[
        county
    ]
    sid = f"sbe-results-{election}-county-{code}-{party_file}"
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_id, source_url, source_name, fetch_date)"
        " VALUES (?, ?, 'sbe', '2026-10-15')",
        (sid, f"https://elections.maryland.gov/x/{sid}.csv"),
    )
    for name, party, winner, votes, *extra in candidates:
        against = extra[0] if extra else None
        write_in = extra[1] if len(extra) > 1 else 0
        cur = conn.execute(
            """INSERT INTO results_county (source_id, election, county, party_file, office,
               district, candidate, party, winner, write_in, total_votes, total_against)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, election, county, party_file, office, district, name, party, int(winner),
             write_in, sum(votes.values()), against),
        )  # fmt: skip
        for stem, n in votes.items():
            conn.execute(
                "INSERT INTO results_votes (result_id, vote_type, votes, votes_against)"
                " VALUES (?, ?, ?, NULL)",
                (cur.lastrowid, stem, n),
            )


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(results, "N_COUNTIES", 2)  # a statewide office is on both counties
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    @contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(results_route, "get_db", fake_get_db)
    yield conn
    conn.close()


@pytest.fixture
def data(db):
    """GP26 (primary) and PG24 (general) in Allegany and Garrett."""
    for county, moore, felber in (("Allegany", 100, 30), ("Garrett", 20, 60)):
        add(db, "GP26", county, "dem", "Governor / Lt. Governor", "",
            [("Wes Moore", "DEM", True, {"early": moore, "mail_in_2": 1}),
             ("Eric Felber", "DEM", False, {"early": felber})])  # fmt: skip
        add(db, "GP26", county, "rep", "Governor / Lt. Governor", "",
            [("Dan Cox", "REP", True, {"early": 40}), ("Kelly Schulz", "REP", False, {"early": 9})])  # fmt: skip
    add(
        db,
        "GP26",
        "Allegany",
        "dem",
        "State Senator",
        "1",
        [("Sen One", "DEM", True, {"early": 5})],
    )
    add(db, "GP26", "Garrett", "dem", "Sheriff", "", [("Sam Sheriff", "DEM", True, {"early": 9})])
    add(
        db,
        "GP26",
        "Garrett",
        "non",
        "Board of Education",
        "1",
        [("Bea Board", "NON", True, {"early": 3})],
    )
    for county, trump, harris in (("Allegany", 70, 30), ("Garrett", 50, 10)):
        add(db, "PG24", county, "all", "President - Vice Pres", "",
            [("Trump", "REP", False, {"early": trump}),
             ("Harris", "DEM", True, {"early": harris}),
             ("Write-ins", "", False, {"early": 1}, None, 1)])  # fmt: skip
        add(db, "PG24", county, "all", "Appeals Judge MD at Lrg", "",
            [("Judge J", "", True, {"early": 80}, 20)])  # fmt: skip
        add(db, "PG24", county, "all", "Sheriff", "", [("Sal Sheriff", "REP", True, {"early": 4})])
    return db


@pytest.fixture
def client(data):
    return TestClient(create_app())


def test_index_lists_elections_newest_first_primary_beside_general(client):
    html = client.get("/results/").text
    assert html.index("2026 Gubernatorial Primary") < html.index("2024 Presidential General")
    assert 'href="/results/gp26/"' in html and 'href="/results/pg24/"' in html
    assert "<h2>2026</h2>" in html and "<h2>2024</h2>" in html


def test_index_empty_state(db):
    html = TestClient(create_app()).get("/results/").text
    assert "no election results on this site yet" in html


def test_primary_hub_groups_per_party_with_totals_and_leaves_out_local(client):
    html = client.get("/results/gp26/").text
    assert "Governor / Lt. Governor (Democratic)" in html
    assert "Governor / Lt. Governor (Republican)" in html
    assert html.index("(Democratic)") < html.index("(Republican)")
    assert ">122<" in html and ">90<" in html  # Moore 101 + 21, Felber 30 + 60, over two counties
    assert ">80<" in html  # Cox's 40 + 40
    assert 'href="/results/gp26/governor-lt-governor-dem/"' in html
    assert 'href="/results/gp26/state-senator-1-dem/"' in html  # compact district list
    assert "<details" in html
    assert "Sheriff" not in html and "Board of Education" not in html
    assert "files behind this election" in html
    assert "State Board of Elections" in html


def test_general_hub_one_list_spans_parties(client):
    html = client.get("/results/pg24/").text
    assert "President / Vice President" in html
    assert "Supreme Court of Maryland judges, at large" in html
    assert "(Democratic)" not in html
    assert "Against" in html  # the retention question's against column
    assert "Sheriff" not in html


def test_winner_mark_has_hidden_text_and_follows_the_sbe_flag(client):
    html = client.get("/results/pg24/president-vice-pres-all/").text
    harris = re.search(r"<th scope=\"row\">Harris.*?</th>", html, re.S).group(0)
    trump = re.search(r"<th scope=\"row\">Trump.*?</th>", html, re.S).group(0)
    assert "winner</span>" in harris and "&#10003;" in harris
    assert "winner" not in trump  # Trump has more votes; the SBE did not flag him


def test_contest_page_candidates_county_table_and_map(client):
    html = client.get("/results/gp26/governor-lt-governor-dem/").text
    assert "<h1>Governor / Lt. Governor (Democratic)</h1>" in html
    assert 'id="county-allegany"' in html and 'id="county-garrett"' in html
    assert "<caption" in html and 'scope="col"' in html
    assert "<svg" in html and 'class="county-map"' in html
    assert 'href="#county-allegany"' in html  # the map links to the table rows
    assert 'href="/results/gp26/county/allegany/"' in html
    # Garrett's leader is Felber: marked 'leads', not 'winner'
    garrett = re.search(r'<tr id="county-garrett">.*?</tr>', html, re.S).group(0)
    assert garrett.count("> leads</span>") == 1
    assert garrett.index("60") < garrett.index("leads")
    assert "Mail-In Ballot 2" in html
    assert "two separate counts of mail-in ballots" in " ".join(html.split())
    assert "second canvass" not in html
    assert "elections.maryland.gov/x/sbe-results-GP26-county-01-dem.csv" in html


def test_general_contest_has_the_margin_map_and_write_in_label(client):
    html = client.get("/results/pg24/president-vice-pres-all/").text
    assert "Democratic minus Republican" in html
    assert "(write-in)" in html


def test_retention_contest_has_no_map_and_an_against_column(client):
    html = client.get("/results/pg24/appeals-judge-md-at-lrg-all/").text
    assert 'class="county-map"' not in html
    assert "Judge J against" in html and "Judge J for" in html
    assert "leads" not in html  # no leader in a for/against question


def test_against_column_only_where_present(client):
    assert "against" not in client.get("/results/pg24/president-vice-pres-all/").text.lower()


def test_county_index(client):
    html = client.get("/results/gp26/county/").text
    assert 'href="/results/gp26/county/allegany/"' in html
    assert 'href="/results/gp26/county/garrett/"' in html


def test_primary_county_page_has_d_r_and_np_sections(client):
    html = client.get("/results/gp26/county/garrett/").text
    for section in ("section-dem", "section-rep", "section-non"):
        assert f'id="{section}"' in html
    assert "Democratic ballot" in html and "Nonpartisan ballot" in html
    # a statewide contest links to its page and has an anchor; a local one has neither
    assert 'id="governor-lt-governor-dem"' in html
    assert 'href="/results/gp26/governor-lt-governor-dem/"' in html
    assert 'id="sheriff-dem"' in html
    assert re.search(r"<h3>Sheriff \(Democratic\) <span", html)  # local: a title, no link
    assert "Election Night" in html and "Mail-In Ballot 2" in html
    assert 'id="section-non"' in html
    allegany = client.get("/results/gp26/county/allegany/").text
    assert 'id="section-non"' not in allegany


def test_general_county_page_is_one_list_with_against_only_where_present(client):
    html = client.get("/results/pg24/county/allegany/").text
    assert 'id="section-all"' in html and "section-dem" not in html
    assert "ballot</h2>" not in html
    president = html[html.index('id="president-vice-pres-all"') :]
    president = president[: president.index("</article>")]
    assert "Against" not in president
    judge = html[html.index('id="appeals-judge-md-at-lrg-all"') :]
    judge = judge[: judge.index("</article>")]
    assert "Against" in judge


def test_county_page_labels_the_flag_as_contest_wide_and_the_county_leader_separately(client):
    html = client.get("/results/gp26/county/garrett/").text
    art = html[html.index('id="governor-lt-governor-dem"') :]
    art = art[: art.index("</article>")]
    felber = re.search(r"<th scope=\"row\">Eric Felber.*?</th>", art, re.S).group(0)
    moore = re.search(r"<th scope=\"row\">Wes Moore.*?</th>", art, re.S).group(0)
    assert "leads here" in felber and "winner" not in felber and "won statewide" not in felber
    assert "won statewide" in moore and "leads here" not in moore
    senate = client.get("/results/gp26/county/allegany/").text
    assert "won the district" in senate
    # a local contest: the flag is the county's own result, and it is labelled as a county office
    sheriff = html[html.index('id="sheriff-dem"') :]
    sheriff = sheriff[: sheriff.index("</article>")]
    assert "county office" in sheriff and '<span class="sr-only"> winner</span>' in sheriff


def test_retention_county_table_has_no_total_column_and_ties_are_marked(client, data):
    html = client.get("/results/pg24/appeals-judge-md-at-lrg-all/").text
    table = html[html.index("<table data-sortable") :]
    assert ">Total<" not in table.split("</thead>")[0]
    add(
        data,
        "PG24",
        "Allegany",
        "all",
        "U.S. Congress",
        "3",
        [("A", "DEM", True, {"early": 5}), ("B", "REP", False, {"early": 5})],
    )
    add(
        data,
        "PG24",
        "Garrett",
        "all",
        "U.S. Congress",
        "3",
        [("A", "DEM", True, {"early": 5}), ("B", "REP", False, {"early": 1})],
    )
    html = client.get("/results/pg24/u-s-congress-3-all/").text
    allegany = re.search(r'<tr id="county-allegany">.*?</tr>', html, re.S).group(0)
    assert allegany.count("> tied</span>") == 2 and "leads" not in allegany


def test_duplicate_local_slugs_get_unique_anchors(client, data):
    for office in ("DEM Cent Comm at Large", "Dem Cent Comm at Large"):
        add(data, "GP26", "Garrett", "dem", office, "", [("X", "DEM", True, {"early": 1})])
    html = client.get("/results/gp26/county/garrett/").text
    ids = re.findall(r'<article class="contest-card" id="([^"]+)"', html)
    assert len(ids) == len(set(ids)) and "dem-cent-comm-at-large-dem-2" in ids


@pytest.mark.parametrize(
    "url",
    [
        "/results/zz99/",
        "/results/zz99/county/",
        "/results/gp26/county/atlantis/",
        "/results/gp26/no-such-contest/",
        "/results/gp26/sheriff-dem/",  # a local contest has no page
        "/results/gp26/county/county/",
    ],
)
def test_unknown_slugs_are_404(client, url):
    assert client.get(url).status_code == 404


def test_county_routes_are_not_read_as_contests(client):
    assert client.get("/results/gp26/county/").status_code == 200
    assert "by county" in client.get("/results/gp26/county/").text.lower()


def test_nav_link_on_a_normal_page(db):
    assert 'href="/results/"' in TestClient(create_app()).get("/about").text


def test_pages_use_the_base_path(data):
    app = create_app()
    configure_templates(app.state.templates, base="/skipjack", static_site=True)
    client = TestClient(app)
    for url in (
        "/results/",
        "/results/gp26/",
        "/results/gp26/county/",
        "/results/gp26/county/garrett/",
        "/results/gp26/governor-lt-governor-dem/",
        "/results/county/",
        "/results/county/garrett/",
    ):
        html = client.get(url).text
        assert 'href="/skipjack/results/' in html
        assert 'href="/results/' not in html
        assert 'src="/static' not in html and 'href="/static' not in html


def _article(html, anchor):
    art = html[html.index(f'id="{anchor}"') :]
    return art[: art.index("</article>")]


def test_district_map_links_only_counties_that_have_a_row(client):
    html = client.get("/results/gp26/state-senator-1-dem/").text  # Allegany only
    assert 'id="county-allegany"' in html and 'id="county-garrett"' not in html
    assert 'href="#county-allegany"' in html
    assert 'href="#county-garrett"' not in html
    assert 'aria-label="Garrett: not on the ballot, or no votes cast"' in html  # still drawn


def test_other_maps_keep_their_links_and_markup(client):
    from skipjack.web.maps import Scale, county_map

    scale = Scale("seq", [1, 2, 3, 4])
    assert all("linked" not in c for c in county_map("x", "T", {}, scale, str)["counties"])
    linked = county_map("x", "T", {}, scale, str, linked_counties={"Garrett"})["counties"]
    assert {c["name"] for c in linked if c["linked"]} == {"Garrett"}


def test_district_contest_is_headed_totals_not_statewide(client):
    senate = client.get("/results/gp26/state-senator-1-dem/").text
    assert "<h2>Totals</h2>" in senate and "<h2>Statewide</h2>" not in senate
    assert "statewide" not in senate.lower()
    assert "<h2>Statewide</h2>" in client.get("/results/gp26/governor-lt-governor-dem/").text


def test_hub_does_not_call_district_totals_statewide(client, data):
    add(data, "GP26", "Allegany", "dem", "Representative in Congress", "1",
        [("Rep One", "DEM", True, {"early": 5})])  # fmt: skip
    html = client.get("/results/gp26/").text
    assert "Representative in Congress, District 1 (Democratic), totals" in html
    assert "District 1 (Democratic), statewide" not in html
    assert "Governor / Lt. Governor (Democratic), statewide totals" in html


def test_retention_contest_notes_and_labels(client):
    html = client.get("/results/pg24/appeals-judge-md-at-lrg-all/").text
    assert "retained" in html and "won statewide" not in html
    assert '>Early <span class="muted">for</span>' in html
    assert "retention result" in html
    assert "votes cast, not of voters" not in html


def test_two_candidate_races_show_the_mail_note_once_and_softened(client):
    html = " ".join(client.get("/results/gp26/county/allegany/").text.split())
    assert html.count("two separate counts of mail-in ballots, as labelled") == 1
    assert "second canvass" not in html


def test_county_page_explains_the_check_mark_once(client):
    html = " ".join(client.get("/results/gp26/county/garrett/").text.split())
    assert "State Board's winner flag for the contest in this county" not in html
    assert html.count("a check mark is the result of the whole contest") == 1
    assert "won statewide, won the district, or retained" in html
    assert "retained" in client.get("/results/pg24/county/allegany/").text


def test_county_page_marks_no_leader_on_local_or_multi_winner_contests(client, data):
    html = client.get("/results/gp26/county/garrett/").text
    assert "leads here" not in _article(html, "sheriff-dem")  # local: leader is the winner
    add(data, "GP26", "Allegany", "dem", "House of Delegates", "2A",
        [("Del A", "DEM", True, {"early": 9}), ("Del B", "DEM", True, {"early": 9}),
         ("Del C", "DEM", False, {"early": 3})])  # fmt: skip
    add(data, "GP26", "Garrett", "dem", "House of Delegates", "2A",
        [("Del A", "DEM", True, {"early": 4}), ("Del B", "DEM", True, {"early": 4}),
         ("Del C", "DEM", False, {"early": 1})])  # fmt: skip
    county = _article(
        client.get("/results/gp26/county/allegany/").text, "house-of-delegates-2a-dem"
    )
    assert "leads here" not in county and "tied for the most" not in county
    page = client.get("/results/gp26/house-of-delegates-2a-dem/").text
    assert "tied for the most" not in page and 'class="num leader"' not in page
    assert "votes cast, not of voters" in page
    assert "a dot marks" not in page


def test_hub_shows_each_winners_own_votes(client, data):
    add(data, "GP26", "Allegany", "dem", "House of Delegates", "2A",
        [("Del A", "DEM", True, {"early": 4010}), ("Del B", "DEM", True, {"early": 4093})])  # fmt: skip
    html = client.get("/results/gp26/").text
    row = re.search(r"<th scope=\"row\"><a[^>]*>2A</a></th>\s*<td>.*?</td>", html, re.S).group(0)
    assert "4,010" in row and "4,093" in row and "8,103" not in html
    assert ">Votes<" not in html.split("district-list")[1]


def test_hub_cards_list_named_candidates_and_count_write_ins(client):
    html = client.get("/results/pg24/").text
    president = html[html.index("President / Vice President") :]
    president = president[: president.index("</article>")]
    assert "Write-ins" not in president
    assert "and 2 write-in candidates" in president or "and 1 write-in candidate<" in president
    assert "(write-in)" in client.get("/results/pg24/president-vice-pres-all/").text


def test_index_and_county_index_copy(client):
    index = client.get("/results/").text
    assert "election data page" in index and "elections.maryland.gov" in index
    county_index = client.get("/results/gp26/county/").text
    assert "with the vote counts by way of voting" in county_index
    assert "Every contest on a county's ballot, with" not in county_index


# --- real data ---------------------------------------------------------------------------


def _real_results() -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM results_county").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()


@pytest.mark.skipif(not _real_results(), reason="skipjack.db has no results rows")
@pytest.mark.parametrize("code", ["GP26", "PP20"])
def test_real_every_page_renders(code):
    from skipjack.web.db import get_db

    client = TestClient(create_app())
    with get_db() as conn:
        urls = [f"/results/{code.lower()}/", f"/results/{code.lower()}/county/"]
        urls += [f"/results/{code.lower()}/{s}/" for s in results.contest_slugs(conn, code)]
        urls += [f"/results/{code.lower()}/county/{s}/" for s in results.county_slugs(conn, code)]
    assert len(urls) > 30
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        text = response.text
        assert not re.search(r"\bNone\b", text), url
        assert not re.search(r"<tbody>\s*</tbody>", text), url
    assert client.get("/results/").status_code == 200


def test_general_county_page_has_the_ahead_of_the_ticket_table(client):
    # Allegany PG24: Trump 70, Harris 30; the sheriff (REP, 4 votes) is unopposed
    html = client.get("/results/pg24/county/allegany/").text
    assert 'id="outran"' in html and 'href="#outran"' in html
    section = html[html.index('id="outran"') :]
    assert "Trump, REP: 70" in section and "Harris, DEM: 30" in section
    assert "No candidate on this ballot could be compared." in section


def test_primary_county_page_has_no_ahead_of_the_ticket_table(client):
    assert 'id="outran"' not in client.get("/results/gp26/county/garrett/").text


# --- results over time -------------------------------------------------------------------


@pytest.fixture
def history_client(data):
    """The fixture elections plus a GG22 general, so a county has two top-of-ticket points."""
    for county, cox, moore in (("Allegany", 60, 40), ("Garrett", 70, 30)):
        add(data, "GG22", county, "all", "Governor / Lt. Governor", "",
            [("Dan Cox", "REP", False, {"early": cox}), ("Wes Moore", "DEM", True, {"early": moore})])  # fmt: skip
        add(data, "GG22", county, "all", "Sheriff", "",
            [("Sal Sheriff", "REP", True, {"early": 50}), ("Dee Sheriff", "DEM", False, {"early": 30})])  # fmt: skip
    return TestClient(create_app())


def test_trends_index_lists_every_county_with_the_shift_and_the_map(history_client):
    html = history_client.get("/results/county/").text
    assert "<h1>Results by county, over time</h1>" in html
    assert 'href="/results/county/allegany/"' in html and 'href="/results/county/garrett/"' in html
    assert "data-sortable" in html
    # Allegany: Cox +20 in 2022 to Trump +40 in 2024 (of 101 votes with the write-in)
    table = html[html.index("<tbody>") :]
    row = table[table.index('href="/results/county/allegany/"') :]
    row = row[: row.index("</tr>")]
    assert "Republicans +39.6" in row
    assert "−19.6" in row  # the shift, a real minus sign
    assert 'id="map-trends-shift"' in html and "map-defs" in html
    assert 'href="/results/county/montgomery/"' in html  # the map links every county


def test_trends_county_page_sections_and_links(history_client):
    html = history_client.get("/results/county/allegany/").text
    assert "<h1>Allegany: results over time</h1>" in html
    for anchor in ("lean", "ballot", "federal", "state", "county", "ticket", "ballots",
                   "primaries", "disagreements", "winners"):  # fmt: skip
        assert f'href="#{anchor}"' in html, anchor
    for level in ("Federal", "State", "County"):
        assert f'<h2 id="{level.lower()}">{level}</h2>' in html
    # the ballot grid links each margin cell to the election's county page
    grid = html[html.index('id="ballot"') :]
    assert '<a href="/results/gg22/county/allegany/#' in grid
    assert "R+20.0" in grid
    assert "R only" in grid  # the sheriff in 2024 had no Democrat
    assert "source-strip" in html and "elections.maryland.gov" in html
    assert 'href="/results/county/allegany/"' in html  # the county nav's own link
    assert 'href="/voters/county/allegany/"' in html


def test_trends_county_page_tables_carry_the_summary(history_client):
    html = history_client.get("/results/county/allegany/").text
    lean = html[html.index('id="lean"') : html.index('id="ballot"')]
    assert "2022" in lean and "2024" in lean
    winners = html[html.index('id="winners"') :]
    assert "Sal Sheriff (REP)" in winners and "2022, 2024" in winners
    primaries = html[html.index('id="primaries"') : html.index('id="disagreements"')]
    assert "Wes Moore" in primaries and "Dan Cox" in primaries


def test_trends_unknown_county_is_404(history_client):
    assert history_client.get("/results/county/atlantis/").status_code == 404


def test_trends_routes_are_not_read_as_an_election(client):
    assert client.get("/results/county/").status_code == 200
    assert client.get("/results/county/allegany/").status_code == 200
    assert client.get("/results/gp26/county/").status_code == 200
    assert client.get("/results/gp26/county/allegany/").status_code == 200


def test_results_pages_link_to_the_trends(client):
    assert 'href="/results/county/"' in client.get("/results/").text
    assert "County files start with the 2010 elections." in client.get("/results/").text
    county = client.get("/results/pg24/county/allegany/").text
    assert 'href="/results/county/allegany/">results over time</a>' in county


def test_source_links_can_carry_the_election(history_client, data):
    links = results_route.source_links(
        data,
        ["sbe-results-PG24-county-01-all", "sbe-results-GP26-county-11-dem"],
        with_election=True,
    )
    assert [x["label"] for x in links] == [
        "2024 general, Allegany",
        "2026 primary, Frederick, Democratic",
    ]
    plain = results_route.source_links(data, ["sbe-results-PG24-county-01-all"])
    assert [x["label"] for x in plain] == ["Allegany"]
    html = history_client.get("/results/county/allegany/").text
    strip = html[html.index("source-strip") :]
    assert "2022 general, Allegany" in strip and "2026 primary, Allegany, Democratic" in strip


def test_trends_county_page_draws_each_section_with_a_table_twin(history_client):
    html = history_client.get("/results/county/allegany/").text
    sections = ["lean", "ballot", "federal", "ticket", "ballots", "primaries", "disagreements"]
    for a, b in zip(sections, sections[1:], strict=False):
        part = html[html.index(f'id="{a}"') : html.index(f'id="{b}"')]
        if a in ("lean", "ballot", "ticket", "ballots", "primaries"):
            assert (
                "<svg" in part and '<details class="trend-table"><summary>Table</summary>' in part
            ), a
    lean = html[html.index('id="lean"') : html.index('id="ballot"')]
    # the lead sentence and the hero figures: Cox +20 in 2022, Trump +39.6 in 2024
    flat = " ".join(lean.split())
    assert "Allegany has voted Republican for president or governor in 2 of 2 generals since 2022" in flat  # fmt: skip
    assert "its margin widened from R+20.0 in 2022 to R+39.6 in 2024" in flat
    assert '<p class="trend-hero"><strong>R+39.6</strong>' in lean
    assert "<strong>19.6 points</strong> toward Republicans since 2022" in lean
    assert 'class="trend-dot-rep"' in lean and "lean vs. the state" in lean
    assert "<title>2022 Governor: Allegany R+20.0, Maryland R+30.0, lean +10.0</title>" in lean
    grid = html[html.index('id="ballot"') : html.index('id="federal"')]
    assert 'id="trend-hatch"' in grid and 'class="trend-cell trend-absent"' in grid
    assert 'class="trend-cell trend-only-rep"' in grid and ">R+20<" in grid
    assert "<title>2022 Sheriff: R+25.0</title>" in grid and "<title>2024 Sheriff: only a Republican on the ballot</title>" in grid  # fmt: skip
    # the federal level: the president has one point, so a table alone, no card
    federal = html[html.index('id="federal"') : html.index('id="state"')]
    assert "<svg" not in federal and 'id="president"' in federal
    county = html[html.index('id="county"') : html.index('id="ticket"')]
    assert '<article class="viz-card trend-card" id="sheriff">' in county
    assert (
        "trend-line-rep" in county
        and '<text x="' in county
        and ">100%<" in county
        and ">38%<" in county
    )  # the latest shares
    ticket = html[html.index('id="ticket"') : html.index('id="ballots"')]
    assert "2022, Governor" in ticket and "Sheriff: Dem share 37.5%, 2.5 points below the Democratic ticket" in ticket  # fmt: skip
    assert 'role="group" aria-label="2022, Governor:' in ticket and 'role="img"' not in ticket
    assert "Presidential years have no county offices on the ballot." in ticket
    assert html.count('<div class="trend-scroll"><svg') == 5  # every full-width figure scrolls
    ballots = html[html.index('id="ballots"') : html.index('id="primaries"')]
    assert (
        'class="trend-col trend-col-pres"' in ballots and ">101<" in ballots
    )  # the 2024 total, the max and latest
    primaries = html[html.index('id="primaries"') : html.index('id="disagreements"')]
    assert ">D 73%<" in primaries  # 131 Democratic ballots of 180
    assert "None" not in html


def test_trends_county_page_marks_new_lines_on_a_redistricted_series(history_client, data):
    for code, name in (("GG18", "Dave Trone"), ("PG24", "April Delaney")):
        add(data, code, "Allegany", "all", "U.S. Congress", "6",
            [("Neil Parrott", "REP", False, {"early": 55}), (name, "DEM", True, {"early": 45})])  # fmt: skip
    html = history_client.get("/results/county/allegany/").text
    federal = html[html.index('id="federal"') : html.index('id="state"')]
    assert 'id="us_house-6"' in federal and ">new lines<" in federal
    assert "Lines redrawn for 2022." in federal
    # a general without a top-of-ticket contest leaves a gap, not a crash
    assert "2018" in html[html.index('id="lean"') : html.index('id="ballot"')]


def test_trends_baltimore_city_level_is_the_city(history_client, data):
    for code in ("GG22", "PG24"):
        office = "Governor / Lt. Governor" if code == "GG22" else "President - Vice Pres"
        add(data, code, "Baltimore City", "all", office, "",
            [("Rep R", "REP", False, {"early": 30}), ("Dem D", "DEM", True, {"early": 70})])  # fmt: skip
    add(data, "PG24", "Baltimore City", "all", "Mayor", "",
        [("Rep M", "REP", False, {"early": 20}), ("Dem M", "DEM", True, {"early": 80})])  # fmt: skip
    html = history_client.get("/results/county/baltimore-city/").text
    assert '<h2 id="county">City</h2>' in html and '<a href="#county">City</a>' in html
    # the city elects its offices in presidential years, so the ticket caption does not deny it
    ticket = html[html.index('id="ticket"') : html.index('id="ballots"')]
    assert "2024, President" in ticket and "Presidential years have no county offices" not in ticket
    assert '<h2 id="county">County</h2>' in history_client.get("/results/county/allegany/").text


def test_possessive_filter_follows_ap_style(history_client, data):
    from skipjack.web.app import possessive

    assert possessive("Prince George's") == "Prince George's"
    assert possessive("Allegany") == "Allegany's"
    add(data, "GG22", "Prince George's", "all", "Governor / Lt. Governor", "",
        [("Dan Cox", "REP", False, {"early": 30}), ("Wes Moore", "DEM", True, {"early": 70})])  # fmt: skip
    html = history_client.get("/results/county/prince-georges/").text.replace("&#39;", "'")
    assert "George's's" not in html and "Prince George's margin for president" in html


def test_every_trend_class_the_templates_emit_is_styled():
    from pathlib import Path

    root = Path(results.__file__).parent
    css = (root / "static" / "style.css").read_text()
    html = (root / "templates" / "_results_trends.html").read_text()
    classes = set(re.findall(r"trend-[a-z-]+[a-z]", html)) - {"trend-hatch"}  # the pattern's id
    assert classes >= {"trend-legend", "trend-table", "trend-hero", "trend-cell-unofficial"}
    missing = sorted(c for c in classes if f".{c}" not in css)
    assert missing == []
