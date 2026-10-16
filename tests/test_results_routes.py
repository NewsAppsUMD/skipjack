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
    code = {"Allegany": "01", "Garrett": "11"}[county]
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
