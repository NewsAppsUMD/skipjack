"""Tests for the layout of the county results-over-time figures (web/results_trends_plot.py):
pure functions from the trends dicts to pixel positions, on small made-up inputs."""

from __future__ import annotations

import pytest

from skipjack.web import results_trends_plot as plot

GENERALS = [
    {"code": "GG14", "year": 2014, "cycle": "gubernatorial", "unofficial": False, "slug": "gg14"},
    {"code": "PG16", "year": 2016, "cycle": "presidential", "unofficial": False, "slug": "pg16"},
    {"code": "GG18", "year": 2018, "cycle": "gubernatorial", "unofficial": False, "slug": "gg18"},
    {"code": "GG26", "year": 2026, "cycle": "gubernatorial", "unofficial": True, "slug": "gg26"},
]
PRIMARIES = [
    {"code": "GP14", "year": 2014, "cycle": "gubernatorial", "unofficial": False, "slug": "gp14"},
    {"code": "GP18", "year": 2018, "cycle": "gubernatorial", "unofficial": False, "slug": "gp18"},
]


def gpoint(year, dem=None, rep=None, total=100, unofficial=False, winners=None, seats=1):
    """A GPOINT with shares from (name, votes) pairs; margin follows the shares."""
    d = {"name": dem[0], "votes": dem[1], "share": round(100 * dem[1] / total, 1)} if dem else None
    r = {"name": rep[0], "votes": rep[1], "share": round(100 * rep[1] / total, 1)} if rep else None
    contested = d is not None and r is not None
    return {
        "election": f"G{year}",
        "year": year,
        "unofficial": unofficial,
        "era": None,
        "seats": seats,
        "contested": contested,
        "total": total,
        "dem": d,
        "rep": r,
        "other_share": 0.0,
        "margin": round(d["share"] - r["share"], 1) if contested else None,
        "winners": winners or [],
        "leader_party": None,
        "matched": None,
        "href": f"/results/g{year}/county/test/#anchor",
        "contest_href": None,
    }


def series(key, title, points, level="county", breaks=(), district=""):
    return {
        "key": key,
        "level": level,
        "title": title,
        "district": district,
        "id": key + (f"-{district}" if district else ""),
        "districted": bool(district),
        "breaks": list(breaks),
        "general": points,
        "primary": {},
    }


def top(point, statewide, title="Governor"):
    lean = None
    if point["margin"] is not None and statewide is not None:
        lean = round(point["margin"] - statewide, 1)
    return {**point, "office": "governor", "title": title, "statewide_margin": statewide,
            "lean": lean}  # fmt: skip


@pytest.fixture
def trends():
    gov = series(
        "governor",
        "Governor",
        [
            gpoint(2014, ("Brown", 40), ("Hogan", 60)),
            None,
            gpoint(2018, ("Jealous", 30), ("Hogan", 70)),
            gpoint(2026, ("Moore", 35), ("Cox", 65), unofficial=True),
        ],
        level="state",
    )
    pres = series(
        "president",
        "President",
        [None, gpoint(2016, ("Clinton", 35), ("Trump", 65), total=200), None, None],
        level="federal",
    )
    house = series(
        "us_house",
        "U.S. House, District 6",
        [
            gpoint(2014, ("Delaney", 45), ("Bongino", 55)),
            gpoint(2016, ("Delaney", 48), ("Hoeber", 52)),
            gpoint(2018, ("Trone", 50), ("Hoeber", 50)),
            gpoint(2026, ("Delaney", 44), ("Parrott", 56), unofficial=True),
        ],
        level="federal",
        breaks=[2022],
        district="6",
    )
    sheriff = series(
        "sheriff",
        "Sheriff",
        [
            gpoint(2014, ("Dee", 48), ("Rob", 52)),
            None,
            gpoint(2018, rep=("Rob", 90)),
            gpoint(2026, ("Dee", 42), ("Rob", 58), unofficial=True),
        ],
    )
    clerk = series(
        "clerk", "Clerk of the Circuit Court", [gpoint(2014, dem=("Kay", 90)), None, None, None]
    )
    board = series(
        "board_of_education",
        "Board of Education, District 1",
        [gpoint(2014, winners=[{"name": "Bea", "party": "NON"}]), None, gpoint(2018), None],
        district="1",
    )
    tops = [
        top(gov["general"][0], -3.8),
        top(pres["general"][1], 26.4, "President"),
        top(gov["general"][2], -11.8),
        top(gov["general"][3], None),
    ]
    gap = {
        "top_title": "Governor",
        "top_dem_share": 40.0,
        "offices": [{"id": "sheriff", "title": "Sheriff", "dem_share": 48.0, "gap": 8.0}],
        "mean_gap": 8.0,
        "compared": 1,
    }
    return {
        "county": "Allegany",
        "slug": "allegany",
        "elections": PRIMARIES + GENERALS,
        "generals": GENERALS,
        "primaries": PRIMARIES,
        "levels": [
            {"level": "federal", "label": "Federal", "offices": [pres, house]},
            {"level": "state", "label": "State", "offices": [gov]},
            {"level": "county", "label": "County", "offices": [sheriff, clerk, board]},
        ],
        "summary": {
            "top": tops,
            "primary_electorate": [
                {
                    "dem": {"title": "Governor (Democratic)", "total": 600},
                    "rep": {"title": "Governor (Republican)", "total": 400},
                    "dem_share_of_two": 60.0,
                },  # fmt: skip
                None,
            ],
            "ticket_gap": [
                gap,
                None,
                {**gap, "offices": [], "compared": 0, "mean_gap": None},
                {**gap, "compared": 1},
            ],  # fmt: skip
            "disagreements": [],
            "winners_wall": [],
        },
        "source_ids": [],
    }


# --- the shared axis ---------------------------------------------------------------------


def test_x_axis_spreads_the_generals_evenly_and_centres_a_single_one():
    xs = plot._x_axis(GENERALS, 100, 400)
    assert xs == [100.0, 200.0, 300.0, 400.0]
    assert plot._x_axis(GENERALS[:1], 100, 400) == [250.0]


def test_runs_split_at_none_and_at_a_break():
    pts = ["a", "b", None, "c", "d", "e"]
    assert plot._runs(pts) == [["a", "b"], ["c", "d", "e"]]
    assert plot._runs(pts, break_before={4}) == [["a", "b"], ["c"], ["d", "e"]]


# --- the lean ----------------------------------------------------------------------------


def test_lean_plot_axis_is_asymmetric_on_nice_bounds(trends):
    p = plot.lean_plot(trends)
    assert p["width"] == plot.WIDTH
    # margins reach -40 (county) and 26.4 (state), a span past 60: the axis runs R+40 to
    # D+40 in twenties, each end on the tick past the extreme
    assert [t["label"] for t in p["y_ticks"]] == ["D+40", "D+20", "0", "R+20", "R+40"]
    ys = [t["y"] for t in p["y_ticks"]]
    assert ys == sorted(ys) and p["y0"] == ys[2]
    assert ys[0] == plot.LEAN_TOP and ys[-1] == plot.LEAN_HEIGHT - plot.LEAN_BOTTOM


def test_lean_plot_range_always_includes_zero_and_widens_its_step(trends):
    # every margin Democratic: the axis starts at 0 and runs up, using the whole height
    for t in trends["summary"]["top"]:
        t["margin"] = abs(t["margin"]) + 20
        if t["statewide_margin"] is not None:
            t["statewide_margin"] = abs(t["statewide_margin"])
    p = plot.lean_plot(trends)
    assert [t["label"] for t in p["y_ticks"]] == ["D+60", "D+50", "D+40", "D+30", "D+20", "D+10", "0"]  # fmt: skip
    assert p["y0"] == plot.LEAN_HEIGHT - plot.LEAN_BOTTOM
    # every margin Republican: 0 is the top
    for t in trends["summary"]["top"]:
        t["margin"] = -t["margin"]
        if t["statewide_margin"] is not None:
            t["statewide_margin"] = -t["statewide_margin"]
    p = plot.lean_plot(trends)
    assert p["y_ticks"][0]["label"] == "0" and p["y_ticks"][-1]["label"] == "R+60"
    assert p["y0"] == plot.LEAN_TOP
    # a span past 60 points takes ticks every 20
    trends["summary"]["top"][0]["margin"] = 45.0
    p = plot.lean_plot(trends)
    assert [t["label"] for t in p["y_ticks"]] == ["D+60", "D+40", "D+20", "0", "R+20", "R+40", "R+60"]  # fmt: skip
    assert [t["label"] for t in p["x_ticks"]] == ["2014", "2016", "2018", "2026"]
    assert [t["cycle"] for t in p["x_ticks"]][:2] == ["gubernatorial", "presidential"]
    assert p["x_ticks"][-1]["unofficial"] is True


def test_lean_plot_points_dots_and_titles(trends):
    p = plot.lean_plot(trends)
    county = p["county"]
    assert [c and c["party"] for c in county] == ["rep", "rep", "rep", "rep"]
    assert [c["hollow"] for c in county] == [False, False, False, True]
    assert county[0]["href"] == "/results/g2014/county/test/#anchor"
    assert county[0]["title"] == "2014 Governor: Allegany R+20.0, Maryland R+3.8, lean −16.2"
    assert county[3]["title"] == "2026 Governor: Allegany R+30.0"
    # the state has no 2026 point yet
    assert p["state"][3] is None and p["state"][2] is not None
    assert len(p["segments"]["county"]) == 1 and len(p["segments"]["state"]) == 1
    assert p["segments"]["state"][0] == p["state"][:3]
    # R+20 sits below the zero line, D+ margins above
    assert county[0]["y"] > p["y0"] > p["state"][1]["y"]
    assert len(p["band"]) == 1 and p["band"][0].count(" ") == 5  # six corners
    assert p["label"]["text"] == "lean vs. the state" and p["label"]["x"] > county[2]["x"]


def test_lean_plot_breaks_the_line_at_a_missing_general(trends):
    trends["summary"]["top"][1] = None
    p = plot.lean_plot(trends)
    assert p["county"][1] is None
    assert [len(r) for r in p["segments"]["county"]] == [1, 2]
    assert p["band"] == []  # the state has no 2026 point, so no stretch has two of both


def test_lean_plot_is_none_without_a_top(trends):
    trends["summary"]["top"] = [None] * 4
    assert plot.lean_plot(trends) is None


def test_lean_summary_counts_wins_and_names_the_move(trends):
    s = plot.lean_summary(trends)
    assert s["party_label"] == "Republican" and (s["won"], s["of"]) == (4, 4)
    assert s["first"] == "R+20.0" and s["latest"] == "R+30.0"
    assert s["first_year"] == 2014 and s["latest_year"] == 2026
    assert s["shift"] == -10.0 and s["shift_text"] == "10.0 points"
    assert s["shift_direction"] == "toward Republicans"
    assert s["verb"] == "widened"
    trends["summary"]["top"][3]["margin"] = 5.0
    s = plot.lean_summary(trends)
    assert s["verb"] == "moved" and (s["won"], s["of"]) == (3, 4)
    assert s["party_label"] == "Republican"
    trends["summary"]["top"][3]["margin"] = -10.0
    assert plot.lean_summary(trends)["verb"] == "narrowed"
    assert plot.lean_summary(trends)["shift_direction"] == "toward Democrats"
    trends["summary"]["top"][3]["margin"] = -20.0
    s = plot.lean_summary(trends)
    assert s["shift_text"] == "no change" and s["shift_direction"] is None
    trends["summary"]["top"][3]["margin"] = -21.0
    assert plot.lean_summary(trends)["shift_text"] == "1.0 point"
    trends["summary"]["top"] = [None] * 4
    assert plot.lean_summary(trends) is None


# --- the ballot grid ---------------------------------------------------------------------


def test_ballot_grid_rows_columns_and_cell_kinds(trends):
    g = plot.ballot_grid(trends)
    assert [c["label"] for c in g["columns"]] == ["2014", "2016", "2018", "2026"]
    assert [c["unofficial"] for c in g["columns"]] == [False, False, False, True]
    labels = [(r["label"], r["level_header"]) for r in g["rows"]]
    assert labels[:3] == [
        ("Federal", True),
        ("President", False),
        ("U.S. House, District 6", False),
    ]
    assert labels[3] == ("State", True)
    assert next(r for r in g["rows"] if r["label"] == "Sheriff")["href"] == "#sheriff"
    by = {(c["row"], c["col"]): c for c in g["cells"]}
    assert by[("sheriff", 0)]["kind"] == "contested" and by[("sheriff", 0)]["text"] == "R+4"
    assert by[("sheriff", 0)]["css"].startswith("map-party-")
    assert by[("sheriff", 1)]["kind"] == "absent" and by[("sheriff", 1)]["href"] is None
    assert by[("sheriff", 2)] == {**by[("sheriff", 2)], "kind": "one_party", "css": "trend-only-rep", "text": "R"}  # fmt: skip
    assert by[("clerk", 0)]["css"] == "trend-only-dem" and by[("clerk", 0)]["text"] == "D"
    assert by[("board_of_education-1", 0)]["kind"] == "nonpartisan"
    assert by[("board_of_education-1", 0)]["css"] == "trend-np" and by[("board_of_education-1", 0)]["text"] == "NP"  # fmt: skip
    assert by[("sheriff", 3)]["unofficial"] is True and by[("sheriff", 0)]["unofficial"] is False
    assert by[("president", 1)]["text"] == "R+15"
    assert "2016 President: R+15.0" in by[("president", 1)]["title"]
    assert by[("sheriff", 1)]["title"] == "2016 Sheriff: not on the ballot"
    assert by[("sheriff", 2)]["title"] == "2018 Sheriff: only a Republican on the ballot"
    # every cell has the same size and sits in its column
    assert {(c["w"], c["h"]) for c in g["cells"]} == {(plot.CELL_W, plot.CELL_H)}
    assert by[("sheriff", 1)]["x"] - by[("sheriff", 0)]["x"] == plot.CELL_W + plot.CELL_GAP


def test_ballot_grid_marks_a_break_and_uses_one_scale_for_the_page(trends):
    trends["levels"][0]["offices"][1]["breaks"] = [2018]
    g = plot.ballot_grid(trends)
    by = {(c["row"], c["col"]): c for c in g["cells"]}
    assert by[("us_house-6", 2)]["break"] is True and by[("us_house-6", 1)]["break"] is False
    assert by[("us_house-6", 2)]["title"].endswith("(new district lines)")
    # the same margin gets the same class wherever it is
    assert (
        by[("governor", 0)]["css"] == by[("us_house-6", 3)]["css"] != by[("us_house-6", 2)]["css"]
    )
    assert [x["css"] for x in g["legend"]][:5] == [f"map-party-{i}" for i in range(5)]
    assert [x["css"] for x in g["legend"]][5:] == ["trend-only-dem", "trend-only-rep", "trend-np", "trend-absent"]  # fmt: skip


def test_ballot_grid_truncates_a_long_row_label_and_keeps_the_full_title(trends):
    long = "House of Delegates, District 09A and then some more words"
    trends["levels"][1]["offices"][0]["title"] = long
    g = plot.ballot_grid(trends)
    row = next(r for r in g["rows"] if r["title"] == long)
    assert row["label"].endswith("…") and len(row["label"]) <= plot.LABEL_CHARS


def test_cell_text_rounds_half_up():
    assert plot._cell_text(2.5) == "D+3" and plot._cell_text(-2.5) == "R+3"
    assert plot._cell_text(0.4) == "even" and plot._cell_text(-0.5) == "R+1"


def test_ballot_grid_is_none_without_series(trends):
    for lv in trends["levels"]:
        lv["offices"] = []
    assert plot.ballot_grid(trends) is None


# --- share lines -------------------------------------------------------------------------


def test_share_lines_segments_end_labels_and_breaks(trends):
    house = trends["levels"][0]["offices"][1]
    house["breaks"] = [2018]
    p = plot.share_lines(house, GENERALS)
    assert (p["width"], p["height"]) == (300, 156)
    # the year labels (at height - 6) and the 9px unofficial tag above them clear the floor
    assert p["height"] - 6 - 11 - 7 >= p["y_bottom"] == 130
    assert [t["label"] for t in p["y_ticks"]] == ["0", None, "50", None, "100"]
    # a run ends before the 2018 break: 2014-16, then 2018-26
    assert [len(r) for r in p["dem"]["segments"]] == [2, 2]
    assert [len(r) for r in p["rep"]["segments"]] == [2, 2]
    assert p["dem"]["segments"][1][1]["hollow"] is True
    assert p["dem"]["segments"][0][0]["title"] == "2014: Delaney (D) 45.0%"
    assert p["rep"]["end"]["text"] == "56%" and p["dem"]["end"]["text"] == "44%"
    assert p["rep"]["end"]["x"] == p["dem"]["end"]["x"] > p["dem"]["segments"][1][1]["x"]
    assert p["breaks"] == [{"x": p["breaks"][0]["x"], "label": "new lines"}]
    assert p["x"][1] < p["breaks"][0]["x"] < p["x"][2]
    assert [t["label"] for t in p["x_ticks"]] == ["2014", "2018"]


def test_share_lines_offsets_end_labels_that_collide(trends):
    house = trends["levels"][0]["offices"][1]
    house["general"][3] = gpoint(2026, ("Delaney", 50), ("Parrott", 50), unofficial=True)
    p = plot.share_lines(house, GENERALS)
    assert p["dem"]["end"]["y"] - p["rep"]["end"]["y"] == -plot.END_GAP
    assert p["dem"]["end"]["y"] < p["dem"]["segments"][-1][-1]["y"] < p["rep"]["end"]["y"]


def test_share_lines_skips_a_party_without_points_and_a_missing_general(trends):
    sheriff = trends["levels"][2]["offices"][0]
    p = plot.share_lines(sheriff, GENERALS)
    assert [len(r) for r in p["dem"]["segments"]] == [1, 1]  # 2014, then 2026 (no 2018 Dem)
    assert [len(r) for r in p["rep"]["segments"]] == [1, 2]
    assert p["dem"]["end"]["text"] == "42%"
    clerk = trends["levels"][2]["offices"][1]
    assert plot.share_lines(clerk, GENERALS) is None  # one point only
    clerk["general"][2] = gpoint(2018, dem=("Kay", 80))
    p = plot.share_lines(clerk, GENERALS)
    assert p["rep"] is None and p["dem"]["end"]["text"] == "80%"


def test_nonpartisan_series_is_detected(trends):
    board, clerk = trends["levels"][2]["offices"][2], trends["levels"][2]["offices"][1]
    assert plot.is_nonpartisan(board) and not plot.is_nonpartisan(clerk)


# --- against the ticket ------------------------------------------------------------------


def test_ticket_dots_rows_only_where_offices_were_compared(trends):
    p = plot.ticket_dots(trends)
    assert [r["label"] for r in p["rows"]] == ["2014, Governor", "2026, Governor"]
    assert p["rows"][1]["unofficial"] is True and p["rows"][0]["unofficial"] is False
    assert [r["cycle"] for r in p["rows"]] == ["gubernatorial", "gubernatorial"]
    dot = p["rows"][0]["dots"][0]
    assert dot["x"] > p["x0"] and dot["color"] == plot.SERIES_COLORS[0]
    assert dot["title"] == "Sheriff: Dem share 48.0%, 8.0 points above the Democratic ticket"
    assert dot["href"] == "#sheriff"
    assert p["legend"] == [{"color": plot.SERIES_COLORS[0], "text": "Sheriff"}]
    assert [t["label"] for t in p["ticks"]] == ["0", "+5", "+10"]


def test_ticket_dots_fold_a_fifth_office_into_gray(trends):
    offices = [
        {"id": f"o{i}", "title": f"Office {i}", "dem_share": 40.0 + i, "gap": float(i) - 2}
        for i in range(6)
    ]
    trends["summary"]["ticket_gap"][0] = {**trends["summary"]["ticket_gap"][0], "offices": offices, "compared": 6}  # fmt: skip
    p = plot.ticket_dots(trends)
    colors = [d["color"] for d in p["rows"][0]["dots"]]
    assert colors[:4] == list(plot.SERIES_COLORS) and colors[4:] == [plot.GRAY, plot.GRAY]
    assert p["legend"][-1] == {"color": plot.GRAY, "text": "other county offices"}
    assert "below" in p["rows"][0]["dots"][0]["title"] and "even with" in p["rows"][0]["dots"][2]["title"]  # fmt: skip
    assert [t["label"] for t in p["ticks"]] == ["−5", "0", "+5", "+10"]


def test_ticket_dots_is_none_without_comparisons(trends):
    trends["summary"]["ticket_gap"] = [None, None, None, None]
    assert plot.ticket_dots(trends) is None


# --- ballots cast ------------------------------------------------------------------------


def test_ballots_columns_steps_labels_and_hollow(trends):
    for i, t in enumerate(trends["summary"]["top"]):
        t["total"] = [100_000, 250_000, 120_000, 90_000][i]
    p = plot.ballots_columns(trends)
    cols = p["columns"]
    assert [c["css"] for c in cols] == ["trend-col-gub", "trend-col-pres", "trend-col-gub", "trend-col-gub"]  # fmt: skip
    assert [c["hollow"] for c in cols] == [False, False, False, True]
    assert [c["label"] for c in cols] == [None, "250,000", None, "90,000"]  # the max and the latest
    assert cols[1]["h"] > cols[0]["h"] > cols[3]["h"] and cols[1]["y"] < cols[0]["y"]
    assert cols[0]["title"] == "2014 Governor: 100,000 votes" and cols[0]["href"].startswith(
        "/results/"
    )
    assert cols[0]["path"].startswith("M") and "Z" in cols[0]["path"]
    assert [t["label"] for t in p["y_ticks"]] == ["0", "100k", "200k", "300k"]
    assert p["y_ticks"][0]["y"] == p["y0"] and p["y_ticks"][-1]["y"] < p["y_ticks"][0]["y"]
    assert [x["css"] for x in p["legend"]] == ["trend-col-pres", "trend-col-gub"]


def test_ballots_columns_skips_a_general_without_a_top_and_is_none_without_any(trends):
    trends["summary"]["top"][1] = None
    p = plot.ballots_columns(trends)
    assert [c["title"][:4] for c in p["columns"]] == ["2014", "2018", "2026"]
    assert [t["label"] for t in p["x_ticks"]] == ["2014", "2016", "2018", "2026"]
    trends["summary"]["top"] = [None] * 4
    assert plot.ballots_columns(trends) is None


# --- the primary electorate --------------------------------------------------------------


def test_electorate_columns_pairs_and_share_labels(trends):
    p = plot.electorate_columns(trends)
    assert len(p["columns"]) == 2  # 2018's electorate is None
    dem, rep = p["columns"]
    assert (dem["css"], rep["css"]) == ("trend-col-dem", "trend-col-rep")
    assert dem["x"] < rep["x"] and dem["h"] > rep["h"]
    assert dem["title"] == "2014 Democratic primary: 600 votes in Governor (Democratic)"
    assert p["labels"] == [{"x": p["labels"][0]["x"], "y": p["labels"][0]["y"], "text": "D 60%"}]
    assert p["labels"][0]["y"] < dem["y"]
    assert [t["label"] for t in p["x_ticks"]] == ["2014", "2018"]
    assert [t["label"] for t in p["y_ticks"]] == ["0", "200", "400", "600", "800"]
    assert [x["css"] for x in p["legend"]] == ["trend-col-dem", "trend-col-rep"]
    trends["summary"]["primary_electorate"] = [None, None]
    assert plot.electorate_columns(trends) is None


def test_k_format():
    assert [plot._k(v) for v in (0, 500, 1000, 2500, 100_000, 1_500_000)] == ["0", "500", "1k", "2.5k", "100k", "1.5m"]  # fmt: skip


# --- the page's plots ---------------------------------------------------------------------


def test_county_plots_groups_each_level(trends):
    p = plot.county_plots(trends)
    assert set(p) >= {"lean", "lean_summary", "ballot", "ticket", "ballots", "electorate", "levels"}
    federal = p["levels"]["federal"]
    assert [s["id"] for s, _ in federal["cards"]] == ["us_house-6"]
    assert [s["id"] for s in federal["sparse"]] == ["president"]
    county = p["levels"]["county"]
    assert [s["id"] for s, _ in county["cards"]] == ["sheriff"]
    assert [s["id"] for s in county["sparse"]] == ["clerk"]
    assert [s["id"] for s in county["nonpartisan"]] == ["board_of_education-1"]
    assert federal["more"] == [] and county["more"] == []
    # the state's districted series go behind a disclosure
    senate = series("state_senate", "State Senate, District 1", trends["levels"][1]["offices"][0]["general"], level="state", district="1")  # fmt: skip
    trends["levels"][1]["offices"].append(senate)
    state = plot.county_plots(trends)["levels"]["state"]
    assert [s["id"] for s, _ in state["cards"]] == ["governor"]
    assert [s["id"] for s, _ in state["more"]] == ["state_senate-1"]
    # a one-point districted series goes behind the same disclosure, as a table alone
    house = series("house_of_delegates", "House of Delegates, District 1B",
                   [None, None, None, gpoint(2026, ("Ann", 40), ("Bob", 60), unofficial=True)],
                   level="state", district="1B")  # fmt: skip
    trends["levels"][1]["offices"].append(house)
    state = plot.county_plots(trends)["levels"]["state"]
    assert [(s["id"], p is None) for s, p in state["more"]] == [("state_senate-1", False), ("house_of_delegates-1B", True)]  # fmt: skip
    assert state["sparse"] == []
