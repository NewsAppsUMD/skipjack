"""Layout of the county results-over-time figures: pure functions from the dicts of
``skipjack.web.results_trends`` to pixel positions, drawn by templates/_results_trends.html.

Follows ``skipjack.web.ticket._plot``: Python computes every coordinate, class and label, the
template emits plain inline SVG with ``<title>`` tooltips, and a table twin under each figure
stays the accessible form. One axis per chart; 2px lines, 8px county dots; the unofficial
count's marks hollow. Colours are classes (style.css ``.trend-*``) except the dot-row series,
which reuse ``ticket.SERIES_COLORS``. Every figure shares the ordinal election axis of
``_x_axis``: one x per general, evenly spaced, whatever the gaps in years.
"""

from __future__ import annotations

import math

from skipjack.web import maps
from skipjack.web.summary import signed
from skipjack.web.ticket import SERIES_COLORS, _nice_ceiling

WIDTH = 680
GRAY = "#898781"
# the lean timeline
LEAN_HEIGHT = 260
LEAN_LEFT, LEAN_RIGHT, LEAN_TOP, LEAN_BOTTOM = 46, 112, 14, 36
# the ballot grid
LABEL_W, CELL_W, CELL_H, CELL_GAP, HEADER_H = 190, 54, 22, 2, 34
LABEL_CHARS = 32  # what fits in LABEL_W at the grid's 11px font
DARK_CELLS = ("map-party-0", "map-party-4")  # tiles that take light text
# a series' share lines
SHARE_W, SHARE_H = 300, 156
SHARE_LEFT, SHARE_RIGHT, SHARE_TOP, SHARE_BOTTOM = 30, 42, 16, 26
END_GAP = 12  # the least distance between two end labels
# the dot rows against the ticket
DOT_LABEL_W, DOT_ROW, DOT_TOP = 150, 24, 34
NAMED_OFFICES = 4  # offices with their own colour; the rest fold into gray
# the columns
COL_HEIGHT = 220
COL_LEFT, COL_RIGHT, COL_TOP, COL_BOTTOM = 50, 16, 20, 26
COL_RADIUS = 4
DISTRICTED_STATE = ("state_senate", "house_of_delegates")


# --- helpers -----------------------------------------------------------------------------


def _x_axis(generals: list[dict], left: float, right: float) -> list[float]:
    """One x per election, evenly spaced from ``left`` to ``right`` (a single election sits
    in the middle)."""
    n = len(generals)
    if n == 1:
        return [round((left + right) / 2, 1)]
    step = (right - left) / (n - 1)
    return [round(left + i * step, 1) for i in range(n)]


def _runs(points: list, break_before: set[int] | None = None) -> list[list]:
    """Consecutive non-None points, split where a point is None and before each index in
    ``break_before`` (a redistricting year, where the line must not join)."""
    breaks = break_before or set()
    runs: list[list] = []
    for i, p in enumerate(points):
        if p is None:
            continue
        if runs and points[i - 1] is not None and i not in breaks:
            runs[-1].append(p)
        else:
            runs.append([p])
    return runs


def _short(margin: float | None) -> str:
    """A margin as the page prints it: "D+23.4", "R+8.0" or "even"."""
    if margin is None:
        return "—"
    if round(margin, 1) == 0:
        return "even"
    return f"{'D' if margin > 0 else 'R'}+{abs(margin):.1f}"


def _k(v: float) -> str:
    """A vote count for an axis: 0, 500, 1k, 2.5k, 100k, 1.5m."""
    if v >= 1_000_000:
        return f"{v / 1_000_000:g}m"
    if v >= 1000:
        return f"{v / 1000:g}k"
    return f"{v:g}"


def _column_scale(top: float, y_top: float, y0: float) -> tuple[list[dict], float]:
    """Ticks on nice multiples from zero to above ``top`` (the tallest column keeps headroom
    for its label), and the pixel height of one unit."""
    step = maps.nice_step(top / 3)
    ceiling = step * (top // step + 1)
    px = (y0 - y_top) / ceiling
    ticks = []
    v = 0.0
    while v <= ceiling + 1e-9:
        ticks.append({"y": round(y0 - v * px, 1), "label": _k(v)})
        v += step
    return ticks, px


def _column_path(x: float, y: float, w: float, h: float) -> str:
    """A column with its two top corners rounded, anchored on the baseline."""
    r = min(COL_RADIUS, h / 2, w / 2)
    return (
        f"M{x},{y + h} L{x},{y + r} Q{x},{y} {x + r},{y} L{x + w - r},{y} "
        f"Q{x + w},{y} {x + w},{y + r} L{x + w},{y + h} Z"
    )


def is_nonpartisan(series: dict) -> bool:
    """Whether every general point of a series names neither a Democrat nor a Republican (a
    school board): the series gets a winners table, not share lines."""
    points = [p for p in series["general"] if p]
    return bool(points) and all(p["dem"] is None and p["rep"] is None for p in points)


# --- the lean ----------------------------------------------------------------------------


def _lean_range(values: list[float]) -> tuple[float, float, int]:
    """Nice bounds for the lean axis: from the margins' minimum to their maximum, always
    including zero, each end rounded out to a tick (every 10 points, or 20 when the margins
    span more than 60), so a county on one side of zero uses the whole height."""
    lo_v, hi_v = min(values + [0.0]), max(values + [0.0])
    step = 10 if hi_v - lo_v <= 60 else 20
    return float(step * math.floor(lo_v / step)), float(step * math.ceil(hi_v / step)), step


def lean_plot(trends: dict) -> dict | None:
    """The county's top-of-ticket margin (ink, dots by the leading party) against the
    statewide margin (gray) on one axis, the band between them shaded, or None when no
    general has a top-of-ticket contest."""
    tops = trends["summary"]["top"]
    if not any(t and t["margin"] is not None for t in tops):
        return None
    generals = trends["generals"]
    values = [v for t in tops if t for v in (t["margin"], t["statewide_margin"]) if v is not None]
    lo, hi, step = _lean_range(values)
    y_top, y_bottom = LEAN_TOP, LEAN_HEIGHT - LEAN_BOTTOM
    px = (y_bottom - y_top) / (hi - lo)

    def y(v: float) -> float:
        return round(y_top + (hi - v) * px, 1)

    xs = _x_axis(generals, LEAN_LEFT, WIDTH - LEAN_RIGHT)
    y_ticks = []
    v = hi
    while v >= lo - 1e-9:
        label = "0" if v == 0 else f"{'D' if v > 0 else 'R'}+{abs(v):g}"
        y_ticks.append({"y": y(v), "label": label})
        v -= step
    county, state = [], []
    for e, x, t in zip(generals, xs, tops, strict=True):
        if t is None or t["margin"] is None:
            county.append(None)
            state.append(None)
            continue
        m = t["margin"]
        title = f"{e['year']} {t['title']}: {trends['county']} {_short(m)}"
        if t["statewide_margin"] is not None:
            title += f", Maryland {_short(t['statewide_margin'])}, lean {signed(t['lean'], 1)}"
        county.append(
            {
                "x": x,
                "y": y(m),
                "hollow": t["unofficial"],
                "party": "dem" if m > 0 else "rep" if m < 0 else None,
                "href": t["href"],
                "title": title,
            }
        )
        s = t["statewide_margin"]
        state.append({"x": x, "y": y(s), "hollow": t["unofficial"]} if s is not None else None)
    both = [(c, s) if c and s else None for c, s in zip(county, state, strict=True)]
    band = []
    for run in _runs(both):
        if len(run) < 2:
            continue
        corners = [f"{c['x']},{c['y']}" for c, _ in run] + [f"{s['x']},{s['y']}" for _, s in reversed(run)]  # fmt: skip
        band.append(" ".join(corners))
    label = None
    last = next((i for i in range(len(both) - 1, -1, -1) if both[i]), None)
    if last is not None:
        c, s = both[last]
        label = {"x": c["x"] + 10, "y": round((c["y"] + s["y"]) / 2 + 4, 1), "text": "lean vs. the state"}  # fmt: skip
    return {
        "width": WIDTH,
        "height": LEAN_HEIGHT,
        "x_ticks": [
            {"x": x, "label": str(e["year"]), "cycle": e["cycle"], "unofficial": e["unofficial"]}
            for e, x in zip(generals, xs, strict=True)
        ],
        "y_ticks": y_ticks,
        "y0": y(0),
        "y_top": y_top,
        "y_bottom": y_bottom,
        "county": county,
        "state": state,
        "band": band,
        "segments": {"county": _runs(county), "state": _runs(state)},
        "label": label,
    }


def lean_summary(trends: dict) -> dict | None:
    """The pieces of the lead sentence: which party the county has voted for at the top of
    the ticket and how often, and how its margin moved from the first general to the latest.
    None when no general has a top-of-ticket margin."""
    tops = [t for t in trends["summary"]["top"] if t and t["margin"] is not None]
    if not tops:
        return None
    dem = sum(1 for t in tops if t["margin"] > 0)
    rep = sum(1 for t in tops if t["margin"] < 0)
    if dem != rep:
        party = "Democratic" if dem > rep else "Republican"
    else:
        party = "Democratic" if tops[-1]["margin"] >= 0 else "Republican"
    first, latest = tops[0]["margin"], tops[-1]["margin"]
    if first == latest:
        verb = "held"
    elif (first > 0) != (latest > 0) and latest != 0 and first != 0:
        verb = "moved"
    elif abs(latest) > abs(first):
        verb = "widened"
    else:
        verb = "narrowed"
    shift = round(latest - first, 1)
    if shift == 0:
        shift_text, direction = "no change", None
    else:
        shift_text = f"{abs(shift):.1f} point{'s' if abs(shift) != 1 else ''}"
        direction = f"toward {'Democrats' if shift > 0 else 'Republicans'}"
    return {
        "party_label": party,
        "won": dem if party == "Democratic" else rep,
        "of": len(tops),
        "first": _short(first),
        "first_year": tops[0]["year"],
        "latest": _short(latest),
        "latest_year": tops[-1]["year"],
        "latest_title": tops[-1]["title"],
        "latest_unofficial": tops[-1]["unofficial"],
        "shift": shift,
        "shift_text": shift_text,
        "shift_direction": direction,
        "verb": verb,
    }


# --- the ballot grid ---------------------------------------------------------------------


def _cell_kind(p: dict) -> str:
    if p["contested"]:
        return "contested"
    if p["dem"] is not None or p["rep"] is not None:
        return "one_party"
    return "nonpartisan"


def _cell_text(margin: float) -> str:
    whole = math.floor(abs(margin) + 0.5)  # half up, as the tooltip's one decimal reads
    return "even" if whole == 0 else f"{'D' if margin > 0 else 'R'}+{abs(whole)}"


def ballot_grid(trends: dict) -> dict | None:
    """A tile per office and general: contested tiles on the maps' margin ramp (one scale for
    the whole page), one-party tiles in a light party tint, nonpartisan tiles neutral, absent
    tiles hatched. Rows are the page's series in order under their level's header row.
    None when the county has no series."""
    generals = trends["generals"]
    all_series = [(lv, s) for lv in trends["levels"] for s in lv["offices"]]
    if not all_series:
        return None
    margins = [p["margin"] for _, s in all_series for p in s["general"] if p and p["contested"]]
    scale = maps.party_margin(margins)
    pitch_x, pitch_y = CELL_W + CELL_GAP, CELL_H + CELL_GAP
    columns = [
        {
            "x": LABEL_W + i * pitch_x + CELL_W / 2,
            "label": str(e["year"]),
            "unofficial": e["unofficial"],
        }  # fmt: skip
        for i, e in enumerate(generals)
    ]
    rows, cells = [], []
    for lv in trends["levels"]:
        if not lv["offices"]:
            continue
        rows.append(
            {
                "y": HEADER_H + len(rows) * pitch_y,
                "label": lv["label"],
                "title": lv["label"],
                "level_header": True,
                "href": None,
                "id": None,
                "level": lv["level"],
            }  # fmt: skip
        )
        for s in lv["offices"]:
            y = HEADER_H + len(rows) * pitch_y
            label = s["title"]
            if len(label) > LABEL_CHARS:
                label = label[: LABEL_CHARS - 1].rstrip() + "…"
            rows.append(
                {
                    "y": y,
                    "label": label,
                    "title": s["title"],
                    "level_header": False,
                    "href": f"#{s['id']}",
                    "id": s["id"],
                    "level": lv["level"],
                }  # fmt: skip
            )
            for col, (e, p) in enumerate(zip(generals, s["general"], strict=True)):
                head = f"{e['year']} {s['title']}: "
                cell = {
                    "row": s["id"],
                    "col": col,
                    "x": LABEL_W + col * pitch_x,
                    "y": y,
                    "w": CELL_W,
                    "h": CELL_H,
                    "unofficial": e["unofficial"] and p is not None,
                    "break": e["year"] in s["breaks"] and p is not None,
                }
                if p is None:
                    cell.update(kind="absent", css="trend-absent", text="", href=None, dark=False, title=head + "not on the ballot")  # fmt: skip
                else:
                    kind = _cell_kind(p)
                    if kind == "contested":
                        cell.update(css=scale.css(p["margin"]), text=_cell_text(p["margin"]), title=head + _short(p["margin"]))  # fmt: skip
                    elif kind == "one_party":
                        side = "Democrat" if p["dem"] else "Republican"
                        cell.update(css=f"trend-only-{side[:3].lower()}", text=side[0], title=head + f"only a {side} on the ballot")  # fmt: skip
                    else:
                        cell.update(css="trend-np", text="NP", title=head + "nonpartisan")
                    cell.update(kind=kind, href=p["href"], dark=cell["css"] in DARK_CELLS)
                    if cell["break"]:
                        cell["title"] += " (new district lines)"
                cells.append(cell)
    legend = scale.legend() + [
        {"css": "trend-only-dem", "text": "only a Democrat on the ballot"},
        {"css": "trend-only-rep", "text": "only a Republican on the ballot"},
        {"css": "trend-np", "text": "nonpartisan"},
        {"css": "trend-absent", "text": "not on the ballot"},
    ]
    return {
        "width": LABEL_W + len(generals) * pitch_x + 2,
        "height": HEADER_H + len(rows) * pitch_y + 2,
        "label_x": LABEL_W - 8,
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "legend": legend,
    }


# --- share lines -------------------------------------------------------------------------


def _share_points(series: dict, generals: list[dict], xs: list[float], side: str, y) -> list:
    letter = "D" if side == "dem" else "R"
    out = []
    for e, x, p in zip(generals, xs, series["general"], strict=True):
        c = p[side] if p else None
        if c is None or c["share"] is None:
            out.append(None)
            continue
        out.append(
            {
                "x": x,
                "y": y(c["share"]),
                "share": c["share"],
                "hollow": p["unofficial"],
                "title": f"{e['year']}: {c['name']} ({letter}) {c['share']:.1f}%",
            }
        )
    return out


def share_lines(series: dict, generals: list[dict]) -> dict | None:
    """A small-multiple card for one series: the Democratic and Republican shares of the vote
    per general, lines broken at a missing general and at a redistricting year, the final
    share labelled at the right end. None for a series with fewer than two general points."""
    if sum(1 for p in series["general"] if p) < 2:
        return None
    y_top, y_bottom = SHARE_TOP, SHARE_H - SHARE_BOTTOM

    def y(share: float) -> float:
        return round(y_top + (100 - share) / 100 * (y_bottom - y_top), 1)

    xs = _x_axis(generals, SHARE_LEFT, SHARE_W - SHARE_RIGHT)
    break_before = {i for i, e in enumerate(generals) if e["year"] in series["breaks"]}
    sides = {}
    for side in ("dem", "rep"):
        points = _share_points(series, generals, xs, side, y)
        last = next((p for p in reversed(points) if p), None)
        if last is None:
            sides[side] = None
            continue
        sides[side] = {
            "segments": _runs(points, break_before),
            "end": {"x": last["x"] + 6, "y": last["y"], "text": f"{last['share']:.0f}%"},
        }
    if sides["dem"] and sides["rep"]:
        d, r = sides["dem"]["end"], sides["rep"]["end"]
        if abs(d["y"] - r["y"]) < END_GAP:
            mid = (d["y"] + r["y"]) / 2
            upper, lower = (d, r) if d["y"] <= r["y"] else (r, d)
            upper["y"], lower["y"] = round(mid - END_GAP / 2, 1), round(mid + END_GAP / 2, 1)
    return {
        "width": SHARE_W,
        "height": SHARE_H,
        "x": xs,
        "x_ticks": [
            {"x": x, "label": str(e["year"]), "unofficial": e["unofficial"]}
            for i, (e, x) in enumerate(zip(generals, xs, strict=True))
            if i % 2 == 0
        ],
        "y_ticks": [
            {"y": y(v), "label": str(v) if v in (0, 50, 100) else None}
            for v in (0, 25, 50, 75, 100)
        ],
        "y_top": y_top,
        "y_bottom": y_bottom,
        "dem": sides["dem"],
        "rep": sides["rep"],
        "breaks": [
            {"x": round((xs[i - 1] + xs[i]) / 2, 1), "label": "new lines"}
            for i in sorted(break_before)
            if i > 0
        ],
    }


# --- against the ticket ------------------------------------------------------------------


def ticket_dots(trends: dict) -> dict | None:
    """A row per general where county offices were compared with the top of the ticket, a
    dot per office at its gap from the ticket's Democratic share, or None when none was."""
    rows_in = [
        (e, g, t)
        for e, g, t in zip(
            trends["generals"],
            trends["summary"]["ticket_gap"],
            trends["summary"]["top"],
            strict=True,
        )  # fmt: skip
        if g and g["compared"] > 0
    ]
    if not rows_in:
        return None
    gaps = [o["gap"] for _, g, _ in rows_in for o in g["offices"]]
    hi = _nice_ceiling(max(gaps, default=0))
    lo = -_nice_ceiling(-min(gaps)) if gaps and min(gaps) < 0 else 0.0
    span = WIDTH - DOT_LABEL_W - 20
    px = span / (hi - lo)

    def x(v: float) -> float:
        return round(DOT_LABEL_W + (v - lo) * px, 1)

    step = 5 if hi - lo <= 40 else 10
    ticks = [{"x": x(t), "label": signed(t)} for t in range(int(lo), int(hi) + 1, step)]
    colors: dict[str, str] = {}
    legend = []
    folded = False
    rows = []
    for i, (e, g, t) in enumerate(rows_in):
        y = DOT_TOP + DOT_ROW * i + 6
        dots = []
        for o in g["offices"]:
            if o["id"] not in colors:
                if len(colors) < NAMED_OFFICES:
                    colors[o["id"]] = SERIES_COLORS[len(colors)]
                    legend.append({"color": colors[o["id"]], "text": o["title"]})
                else:
                    colors[o["id"]] = GRAY
                    folded = True
            gap = o["gap"]
            if gap == 0:
                where = "even with"
            else:
                where = f"{abs(gap):.1f} points {'above' if gap > 0 else 'below'}"
            dots.append(
                {
                    "x": x(gap),
                    "color": colors[o["id"]],
                    "series": o["title"],
                    "href": f"#{o['id']}",
                    "title": f"{o['title']}: Dem share {o['dem_share']:.1f}%, {where} the Democratic ticket",  # fmt: skip
                }
            )
        xs = [d["x"] for d in dots] + [x(0)]
        rows.append(
            {
                "y": y,
                "label": f"{e['year']}, {g['top_title']}",
                "cycle": e["cycle"],
                "unofficial": e["unofficial"],
                "href": t["href"] if t else None,
                "x_min": min(xs),
                "x_max": max(xs),
                "dots": dots,
            }
        )
    if folded:
        legend.append({"color": GRAY, "text": "other county offices"})
    return {
        "width": WIDTH,
        "height": DOT_TOP + DOT_ROW * len(rows) + 4,
        "top": DOT_TOP,
        "label_x": DOT_LABEL_W - 8,
        "ticks": ticks,
        "x0": x(0),
        "rows": rows,
        "legend": legend,
    }


# --- ballots cast ------------------------------------------------------------------------


def ballots_columns(trends: dict) -> dict | None:
    """A column per general of the votes in the top-of-ticket contest, presidential and
    gubernatorial years on two steps of the green ramp, the largest and the latest labelled.
    None when no general has a top."""
    generals, tops = trends["generals"], trends["summary"]["top"]
    present = [(i, t) for i, t in enumerate(tops) if t and t["total"]]
    if not present:
        return None
    y0, y_top = COL_HEIGHT - COL_BOTTOM, COL_TOP
    y_ticks, px = _column_scale(max(t["total"] for _, t in present), y_top, y0)
    xs = _x_axis(generals, COL_LEFT + 30, WIDTH - COL_RIGHT - 30)
    pitch = xs[1] - xs[0] if len(xs) > 1 else 60
    w = round(min(48, pitch * 0.7), 1)
    biggest = max(present, key=lambda it: it[1]["total"])[0]
    latest = present[-1][0]
    columns = []
    for i, t in present:
        e = generals[i]
        h = round(t["total"] * px, 1)
        x = round(xs[i] - w / 2, 1)
        columns.append(
            {
                "x": x,
                "y": round(y0 - h, 1),
                "w": w,
                "h": h,
                "path": _column_path(x, round(y0 - h, 1), w, h),
                "css": "trend-col-pres" if e["cycle"] == "presidential" else "trend-col-gub",
                "hollow": e["unofficial"],
                "label": f"{t['total']:,}" if i in (biggest, latest) else None,
                "title": f"{e['year']} {t['title']}: {t['total']:,} votes",
                "href": t["href"],
                "year": e["year"],
            }
        )
    return {
        "width": WIDTH,
        "height": COL_HEIGHT,
        "y0": y0,
        "columns": columns,
        "x_ticks": [
            {"x": x, "label": str(e["year"]), "unofficial": e["unofficial"]}
            for e, x in zip(generals, xs, strict=True)
        ],
        "y_ticks": y_ticks,
        "legend": [
            {"css": "trend-col-pres", "text": "presidential year"},
            {"css": "trend-col-gub", "text": "gubernatorial year"},
        ],
    }


def electorate_columns(trends: dict) -> dict | None:
    """Per primary, a pair of columns: the votes in the top contest on the Democratic and the
    Republican ballot, the Democratic share of the two labelled above each pair. None when
    no primary has an electorate."""
    primaries, electorate = trends["primaries"], trends["summary"]["primary_electorate"]
    present = [(i, pe) for i, pe in enumerate(electorate) if pe and (pe["dem"] or pe["rep"])]
    if not present:
        return None
    totals = [side["total"] for _, pe in present for side in (pe["dem"], pe["rep"]) if side]
    if not max(totals):
        return None
    y0, y_top = COL_HEIGHT - COL_BOTTOM, COL_TOP
    y_ticks, px = _column_scale(max(totals), y_top, y0)
    xs = _x_axis(primaries, COL_LEFT + 30, WIDTH - COL_RIGHT - 30)
    pitch = xs[1] - xs[0] if len(xs) > 1 else 60
    w = round(min(24, pitch * 0.3), 1)
    columns, labels = [], []
    for i, pe in present:
        e = primaries[i]
        tops = []
        for side, css, name in (("dem", "trend-col-dem", "Democratic"), ("rep", "trend-col-rep", "Republican")):  # fmt: skip
            s = pe[side]
            if not s:
                continue
            h = round(s["total"] * px, 1)
            x = round(xs[i] - w - 1 if side == "dem" else xs[i] + 1, 1)
            y = round(y0 - h, 1)
            tops.append(y)
            columns.append(
                {
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "path": _column_path(x, y, w, h),
                    "css": css,
                    "hollow": e["unofficial"],
                    "label": None,
                    "title": f"{e['year']} {name} primary: {s['total']:,} votes in {s['title']}",
                    "href": None,
                    "year": e["year"],
                }
            )
        if pe["dem_share_of_two"] is not None:
            labels.append({"x": xs[i], "y": round(min(tops) - 6, 1), "text": f"D {pe['dem_share_of_two']:.0f}%"})  # fmt: skip
    return {
        "width": WIDTH,
        "height": COL_HEIGHT,
        "y0": y0,
        "columns": columns,
        "labels": labels,
        "x_ticks": [
            {"x": x, "label": str(e["year"]), "unofficial": e["unofficial"]}
            for e, x in zip(primaries, xs, strict=True)
        ],
        "y_ticks": y_ticks,
        "legend": [
            {"css": "trend-col-dem", "text": "Democratic ballots"},
            {"css": "trend-col-rep", "text": "Republican ballots"},
        ],
    }


# --- the page ----------------------------------------------------------------------------


def _level_layout(lv: dict, generals: list[dict]) -> dict:
    """How a level's series are shown: ``cards`` (series, share lines) in the open grid,
    ``more`` (the state's districted seats, share lines or None for a one-point series)
    behind a disclosure, ``nonpartisan`` series in one winners table and ``sparse`` series
    (one point) as a table alone."""
    out: dict[str, list] = {"cards": [], "more": [], "nonpartisan": [], "sparse": []}
    for s in lv["offices"]:
        if is_nonpartisan(s):
            out["nonpartisan"].append(s)
            continue
        p = share_lines(s, generals)
        if lv["level"] == "state" and s["key"] in DISTRICTED_STATE:
            out["more"].append((s, p))
        elif p is None:
            out["sparse"].append(s)
        else:
            out["cards"].append((s, p))
    return out


def county_plots(trends: dict) -> dict:
    """Every figure of a county's page, keyed by section, plus each level's layout."""
    return {
        "lean": lean_plot(trends),
        "lean_summary": lean_summary(trends),
        "ballot": ballot_grid(trends),
        "ticket": ticket_dots(trends),
        "ballots": ballots_columns(trends),
        "electorate": electorate_columns(trends),
        "levels": {lv["level"]: _level_layout(lv, trends["generals"]) for lv in trends["levels"]},
    }
