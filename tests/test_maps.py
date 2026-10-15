"""County maps: the outlines, the colour classes, and the maps on each page.

The first half needs nothing but the committed outlines. The page tests at the end use the
built skipjack.db, like tests/test_web.py.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from skipjack.pipeline.voterfile import COUNTIES, COUNTY_FIPS
from skipjack.web import maps
from skipjack.web.app import app
from skipjack.web.db import DB_PATH
from skipjack.web.urls import slugify

# --- the outlines --------------------------------------------------------------------


def test_outlines_cover_the_24_counties_with_their_fips_codes():
    shapes = maps.load_shapes()
    assert set(shapes["counties"]) == set(COUNTIES)
    assert {n: c["fips"] for n, c in shapes["counties"].items()} == COUNTY_FIPS
    assert all(c["d"].startswith("M") and c["d"].endswith("Z") for c in shapes["counties"].values())
    x0, y0, w, h = (float(v) for v in shapes["viewBox"].split())
    assert (x0, y0, w) == (0, 0, 1000) and 400 < h < 700  # Maryland is about twice as wide as tall


def test_the_outlines_stay_small():
    assert maps.SHAPES_PATH.stat().st_size < 80_000


# --- colour classes ------------------------------------------------------------------


@pytest.mark.parametrize("raw,step", [(0.7, 0.8), (10.4, 15), (26.7, 30), (100, 100), (0, 1)])
def test_nice_steps_are_round(raw, step):
    assert maps.nice_step(raw) == pytest.approx(step)


def test_sequential_breaks_ascend_and_cover_every_value():
    values = [3.2, 10.0, 55.1, None]
    s = maps.sequential(values, "%")
    assert s.breaks == sorted(s.breaks) and len(s.breaks) == 4
    assert s.index(3.2) == 0 and s.index(55.1) == 3
    assert s.index(None) is None and s.css(None) == "map-none"
    assert [x["css"] for x in s.legend()] == [f"map-seq-{i}" for i in range(5)]


def test_a_single_value_or_no_values_do_not_break_the_scale():
    assert len(maps.sequential([5.0]).breaks) == 4
    assert len(maps.sequential([None, None]).breaks) == 4
    assert len(maps.diverging([0.0]).breaks) == 4


def test_diverging_breaks_are_symmetric_around_zero():
    for scale in (maps.diverging([-12, 4, 25]), maps.party_margin([-50.5, 66.2])):
        b = scale.breaks
        assert b == sorted(b) and b[0] == -b[3] and b[1] == -b[2]
        assert scale.index(0) == 2  # zero is in the middle class
    margin = maps.party_margin([-50.5, 66.2])
    assert margin.breaks == [-40, -10, 10, 40]
    texts = [x["text"] for x in margin.legend()]
    assert texts[0] == "Republican by 40+ points" and texts[2] == "within 10 points"


def test_hidden_values_are_never_the_lowest_class():
    s = maps.sequential([0.0, 10.0, None])
    assert s.css(0.0) == "map-seq-0" and s.css(None) == "map-none"


# --- one map -------------------------------------------------------------------------


def test_county_map_names_every_county_and_marks_hidden_ones():
    values = {c: float(i) for i, c in enumerate(COUNTIES)}
    values["Kent"] = None
    m = maps.county_map("t", "Test", values, maps.sequential(list(values.values())),
                        maps.fmt_pct(0), link="county_page", none_text="hidden")  # fmt: skip
    by_name = {c["name"]: c for c in m["counties"]}
    assert set(by_name) == set(COUNTIES)
    assert by_name["Kent"]["css"] == "map-none" and by_name["Kent"]["label"] == "Kent: hidden"
    assert by_name["Prince George's"]["slug"] == slugify("Prince George's") == "prince-georges"
    assert by_name["Allegany"]["label"] == "Allegany: 0%"
    assert m["none"] == "hidden"
    assert maps.county_map("t", "T", {c: 1.0 for c in COUNTIES}, maps.sequential([1.0]),
                           maps.fmt_pct())["none"] is None  # fmt: skip


def test_margin_labels_name_the_party():
    assert maps.fmt_margin(43.9) == "Democrats +44 points"
    assert maps.fmt_margin(-50.5) == "Republicans +50 points"
    assert maps.fmt_margin(0.2) == "even"


def test_turnout_maps_skip_a_measure_no_county_has():
    rows = [{"county": c, "counted": 100, "counted_by_mode": {"vote-by-mail": 30},
             "mail_voting": {"received": 40, "rejected": 1}} for c in COUNTIES]  # fmt: skip
    titles = [m["title"] for m in maps.turnout_certified_maps(rows, {})]
    assert titles == ["Voted by mail", "Mail ballots rejected"]
    with_turnout = maps.turnout_certified_maps(rows, {c: 70.0 for c in COUNTIES})
    assert with_turnout[0]["title"] == "Turnout"


# --- the pages -----------------------------------------------------------------------

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="skipjack.db not built")
client = TestClient(app)


def map_titles(html: str) -> list[str]:
    return re.findall(r'<figure class="county-map" data-map="[^"]+" data-title="([^"]+)"', html)


def assert_outlines_once(html: str) -> None:
    assert html.count('class="map-defs"') == 1
    for fips in COUNTY_FIPS.values():
        assert html.count(f'<path id="md-{fips}"') == 1


@needs_db
def test_home_has_a_locator_map_linking_to_every_county():
    html = client.get("/").text
    assert map_titles(html) == ["Party margin among active voters"]
    assert_outlines_once(html)
    for county in COUNTIES:
        assert f'href="/voters/county/{slugify(county)}/"' in html


@needs_db
def test_registration_month_has_three_maps():
    html = client.get("/voters/month/2026-08/").text
    assert map_titles(html) == [
        "Democratic minus Republican",
        "Unaffiliated share",
        "Change over a year",
    ]
    assert_outlines_once(html)
    assert 'aria-label="Montgomery: Democrats +' in html


@needs_db
def test_the_first_month_has_no_change_map():
    first = client.get("/voters/").text  # just to have a report; pick the oldest month
    dates = re.findall(r'value="/voters/month/(\d{4}-\d{2})/"', first)
    html = client.get(f"/voters/month/{min(dates)}/").text
    assert "Change over a year" not in map_titles(html)


@needs_db
@pytest.mark.parametrize(
    "url,titles",
    [
        ("/turnout/gg26/county/", ["Mail ballots returned"]),
        ("/turnout/pg24/county/", ["Turnout", "Voted by mail", "Mail ballots rejected"]),
    ],
)
def test_turnout_county_maps_link_to_rows_on_the_page(url, titles):
    html = client.get(url).text
    assert map_titles(html) == titles
    for slug in set(re.findall(r'href="#county-([a-z-]+)"', html)):
        assert f'id="county-{slug}"' in html


@needs_db
def test_district_turnout_pages_have_no_county_map():
    assert map_titles(client.get("/turnout/pg24/congressional/").text) == []


@needs_db
def test_young_and_race_maps():
    young = client.get("/voters/young/").text
    assert map_titles(young) == ["Change in new young registrants", "Change in Republican share"]
    assert "too few to compare" in young  # small counties are hatched, not drawn as zero
    race = client.get("/voters/race/").text
    titles = map_titles(race)
    assert titles[:5] == ["White", "Black", "Hispanic", "Asian", "Other or multiracial"]
    assert titles[5] == "Estimated Black share minus the Census"
    assert_outlines_once(race)
    for slug in set(re.findall(r'href="#county-([a-z-]+)"', race)):
        assert f'id="county-{slug}"' in race
