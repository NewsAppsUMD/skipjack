"""Young voters: new registrations and party choice."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from skipjack.web.db import get_db
from skipjack.web.young import build_young

router = APIRouter(prefix="/voters/young")


def more_or_fewer(change: float | None) -> str:
    """ "3% more than" or "35% fewer than" for a percent change, in whole percents."""
    if change is None:
        return ""
    whole = round(abs(change))
    if whole == 0:
        return "about the same as"
    return f"{whole}% {'more' if change > 0 else 'fewer'} than"


def above_or_below(change: float | None) -> str:
    """ "4% below" or "17% above" for a percent change, in whole percents."""
    if change is None:
        return ""
    whole = round(abs(change))
    return "level with" if whole == 0 else f"{whole}% {'above' if change > 0 else 'below'}"


def _compared(changes: dict[int, float | None], years: list[int]) -> str:
    """ "3% more than in the same stretch of 2022 and 35% more than in 2018" for the years given."""
    pieces = []
    for i, year in enumerate(sorted(years, reverse=True)):
        change = changes.get(year)
        if change is not None:
            pieces.append(
                f"{more_or_fewer(change)} in {'the same stretch of ' if i == 0 else ''}{year}"
            )
    return " and ".join(pieces)


def bullets(y: dict) -> list[str]:
    """The headline findings, written from the numbers so they stay true as data changes."""
    out = []
    now = next(r for r in y["pace"] if r["year"] == y["year"])
    earlier = [c for c in y["compare"] if c != y["year"]]
    text = (
        f"From January 1 through {y['cutoff_label']}, {now['window']:,} Marylanders aged 18 to 22 "
        f"registered for the first time in {y['year']}."
    )
    if compared := _compared(y["changes"], earlier):
        text += f" Counted straight from the file, that is {compared}."
    out.append(text)
    if y["adjusted"]:
        years = sorted((c for c in earlier if c in y["adjusted"]), reverse=True)
        counts = " and ".join(f"{above_or_below(y['adjusted'][c]['change'])} {c}'s" for c in years)
        out.append(
            "Earlier years look smaller than they were, because some of their registrants have since left "
            f"the list. Putting back the voters who left between the {y['shrink_label']} and "
            f"{y['snapshot_label']} files, this year's count is {counts}. "
            "Voters also left before the earlier file was made, so even this flatters this year."
        )
    latest, first = y["parties"][-1], y["parties"][0]
    text = f"{latest['young']['UNA']:.0f}% of this year's new registrants aged 18 to 22 are unaffiliated"
    if first["year"] != latest["year"]:
        text += f", up from {first['young']['UNA']:.0f}% in {first['year']}"
    text += f". Among new registrants 23 and older it is {latest['older']['UNA']:.0f}%"
    if first["year"] != latest["year"]:
        text += f", up from {first['older']['UNA']:.0f}%"
    out.append(text + ".")
    ages = [r for r in y["age_rows"] if not r.get("total")]
    older = next(r for r in y["age_rows"] if r.get("total"))
    if max(ages, key=lambda r: r["UNA"])["band"] == "18-22":
        out.append(
            "Among everyone registered today, 18-to-22-year-olds are the most unaffiliated age group: "
            f"{ages[0]['UNA']:.0f}%, against {older['UNA']:.0f}% of voters 23 and older."
        )
    if len(y["parties"]) > 1:
        prev = y["parties"][-2]
        text = (
            f"The Republican share of new registrants aged 18 to 22 went from {prev['young']['REP']:.0f}% "
            f"in {prev['year']} to {latest['young']['REP']:.0f}%"
        )
        drops = sorted(
            (c for c in y["counties"] if c["rep_points"] is not None), key=lambda c: c["rep_points"]
        )[:3]
        if len(drops) == 3:
            names = [c["county"] for c in drops]
            sizes = sorted(abs(c["rep_points"]) for c in drops)
            text += (
                f". The biggest county drops were in {names[0]}, {names[1]} and {names[2]}, "
                f"by {sizes[0]:.0f} to {sizes[-1]:.0f} points"
            )
        out.append(text + ".")
    return out


@router.get("/")
async def young_voters(request: Request):
    with get_db() as conn:
        data = build_young(conn)
    if data is None:
        raise HTTPException(404, "No voter file snapshot has the young voter aggregates")
    counties = sorted(
        data["counties"], key=lambda c: (c["rep_points"] is None, c["rep_points"] or 0)
    )
    unaffiliated = data["trend"]
    chart = {
        "weeks": list(range(1, 54)),
        "week_labels": data["week_labels"],
        "month_weeks": data["month_weeks"],
        "cutoff_week": data["cutoff_week"],
        "series": [
            {"year": s["year"], "values": s["values"], "hidden": s["hidden"]}
            for s in data["series"]
        ],
        "primary_weeks": {str(k): v for k, v in data["primary_weeks"].items()},
        "years": unaffiliated["years"],
        "young": unaffiliated["young"],
        "older": unaffiliated["older"],
        "young_n": unaffiliated["young_n"],
        "older_n": unaffiliated["older_n"],
        "current_year": data["year"],
        "cutoff_label": data["cutoff_label"],
    }
    gaps = [c["gap_points"] for c in data["counties"] if c["gap_points"] is not None]
    return request.app.state.templates.TemplateResponse(
        request,
        "young.html",
        {
            "y": data,
            "bullets": bullets(data),
            "counties": counties,
            "chart": chart,
            "gap_range": (min(gaps), max(gaps), len(gaps)) if gaps else None,
            "hidden_weeks": sum(s["hidden"] for s in data["series"]),
        },
    )
