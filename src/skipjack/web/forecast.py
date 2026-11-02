"""The 2026 general election turnout forecast, read from the model's committed outputs.

The model itself is R (scripts/turnout_model, run with ``make turnout-model``) and writes
CSVs with provenance under data/turnout_model/gg26. This module only reads those files:
nothing here recomputes a forecast, so the page shows exactly what the last model run
wrote, dated by its provenance.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from skipjack.web.paths import DATA_DIR, PROJECT_ROOT

MODEL_DIR = DATA_DIR / "turnout_model" / "gg26"
REPORT_PATH = PROJECT_ROOT / "docs" / "turnout-model-2026.html"
STATEWIDE = "Maryland"
SCENARIOS = ("low", "central", "high")
SCENARIO_LABELS = {
    "low": "Low: a 2022-like midterm",
    "central": "Central: the model's blend",
    "high": "High: a 2018-like midterm",
}
PARTIES = [("DEM", "Democrats"), ("REP", "Republicans"), ("OTH", "Unaffiliated and other")]
VOTES = [
    ("vote_reg", "Registration momentum"),
    ("vote_mail", "Mail ballots sent per registrant"),
    ("vote_primary", "Primary turnout per registrant"),
    ("vote_banked", "Ballots cast before election day per registrant"),
]
CAVEATS = [
    "The spread between the low and high scenarios is the real uncertainty; the intervals "
    "within a scenario are narrow by construction.",
    "The central scenario's weights come from a written rule set before the numbers were in; "
    "the mail and early-ballot measures have no 2018 counterpart, and the early-ballot measure "
    "counts voters who moved from election day to early voting as more engagement.",
    "The early-voting count is the State Board's unofficial tally; mail ballots still to "
    "arrive are projected at 2022's rate of arrival.",
    "The 2026 primary had an incumbent governor, and its party split is estimated from the "
    "voter file.",
    "Composition is centred within party because the voter file lacks the 2022 voters who "
    "have since died or moved.",
    "Not modelled: election-day weather, late changes in the races, changes to mail-voting "
    "rules, and registration after September other than same-day registration.",
    "The 2014 general election is left out.",
    "The State Board's 2018-2024 turnout sheets divide by active registration; its 2026 "
    "guide divides by all eligible voters. Both rates are shown.",
]


def _rows(name: str, model_dir: Path) -> list[dict]:
    with (model_dir / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(v: str | None) -> float | None:
    return None if v in (None, "", "NA") else float(v)


def _pct(v: str | float | None) -> float | None:
    x = _num(v) if isinstance(v, str) or v is None else v
    return None if x is None else 100 * x


def available(model_dir: Path = MODEL_DIR) -> bool:
    return (model_dir / "forecast_statewide.csv").is_file()


def report_available() -> bool:
    return REPORT_PATH.is_file()


def build_forecast(model_dir: Path = MODEL_DIR) -> dict:
    """Everything the forecast page shows, from the model's CSVs."""
    sw: dict[str, dict[str, dict]] = {}
    for r in _rows("forecast_statewide.csv", model_dir):
        sw.setdefault(r["scenario"], {})[r["measure"]] = r
    provenance = json.loads(
        (model_dir / "forecast_statewide_provenance.json").read_text(encoding="utf-8")
    )

    def stat(scenario: str, measure: str, col: str = "mean") -> float | None:
        return _num(sw.get(scenario, {}).get(measure, {}).get(col))

    scenarios = [
        {
            "key": s,
            "label": SCENARIO_LABELS[s],
            "ballots": stat(s, "ballots"),
            "q05": stat(s, "ballots", "q05"),
            "q95": stat(s, "ballots", "q95"),
            "rate_active": _pct(stat(s, "rate_active")),
            "rate_eligible": _pct(stat(s, "rate_eligible")),
        }
        for s in SCENARIOS
    ]
    actual = {
        year: {
            "ballots": stat(f"actual_GG{year % 100}", "ballots"),
            "rate_active": _pct(stat(f"actual_GG{year % 100}", "rate_active")),
        }
        for year in (2022, 2018)
    }
    headline_range = {
        "low": stat("headline_range", "ballots", "q05"),
        "high": stat("headline_range", "ballots", "q95"),
        "rate_low": _pct(stat("headline_range", "rate_active", "q05")),
        "rate_high": _pct(stat("headline_range", "rate_active", "q95")),
    }

    parties = [
        {
            "code": code,
            "label": label,
            "share": {s: _pct(stat(s, f"share_{code}")) for s in SCENARIOS},
            "rate": {s: _pct(stat(s, f"rate_active_{code}")) for s in SCENARIOS},
            "share_2022": _pct(stat("actual_GG22", f"share_{code}")),
            "share_2018": _pct(stat("actual_GG18", f"share_{code}")),
            "rate_2022": _pct(stat("actual_GG22", f"rate_active_{code}")),
            "rate_2018": _pct(stat("actual_GG18", f"rate_active_{code}")),
        }
        for code, label in PARTIES
    ]
    # the one public poll that prints its likely voters' party mix, for comparison only
    inputs = model_dir.parent / "inputs"
    polls = _rows("polls_2026.csv", inputs) if (inputs / "polls_2026.csv").is_file() else []
    poll = next((p for p in polls if p["dem_share_lv"]), None)
    if poll:
        poll = {
            "pollster": poll["pollster"],
            "field_end": poll["field_end"],
            "url": poll["source_url"],
            "shares": [_pct(poll[k]) for k in ("dem_share_lv", "rep_share_lv", "una_share_lv")],
        }

    rule = []
    for r in _rows("level_rule.csv", model_dir):
        rule.append(
            {
                "label": dict(PARTIES)[r["party_group"]],
                "votes": [int(float(r[k])) for k, _ in VOTES],
                "w": _num(r["w"]),
            }
        )

    # early_vote_check.csv is statewide-only in older runs and gains county rows later
    banked: dict[str, dict] = {}
    county_banked: dict[str, dict] = {}
    for r in _rows("early_vote_check.csv", model_dir):
        county = r.get("county") or STATEWIDE
        row = {k: _num(v) for k, v in r.items() if k not in ("scenario", "county")}
        if county == STATEWIDE:
            banked[r["scenario"]] = row
        elif r["scenario"] == "central":
            county_banked[county] = row
    mode = {r["county"]: r for r in _rows("mode_split.csv", model_dir)}
    sdr = {r["county"]: r for r in _rows("same_day_registration.csv", model_dir)}
    md = mode[STATEWIDE]
    early = {
        "early": banked["central"]["early"],
        "mail_received": _num(md["mail_received"]),
        "mail_projected": banked["central"]["mail"],
        "banked": banked["central"]["banked"],
        "banked_2022": banked["actual_GG22"]["banked"],
        "need": {s: _pct(banked[s]["rate_not_yet_voted"]) for s in SCENARIOS},
        "need_2022": _pct(banked["actual_GG22"]["rate_not_yet_voted"]),
        "need_2018": _pct(banked["actual_GG18"]["rate_not_yet_voted"]),
        "sdr_extra": _num(sdr[STATEWIDE]["extra"]),
        "modes": [
            {
                "label": label,
                "ballots": _num(md[col]),
                "share": _pct(md[f"share_{col}"]),
                "share_2022": _pct(md[f"gg22_share_{col}"]),
            }
            for col, label in [
                ("mail", "Mail"),
                ("early", "Early voting"),
                ("election_day", "Election day"),
                ("provisional", "Provisional"),
            ]
        ],
    }

    by_county: dict[str, dict] = {}
    for r in _rows("forecast_county.csv", model_dir):
        c = by_county.setdefault(r["county"], {"county": r["county"]})
        c[f"rate_{r['scenario']}"] = _pct(r["rate_active_mean"])
        c[f"ballots_{r['scenario']}"] = _num(r["ballots_mean"])
        if r["scenario"] == "central":
            c["active"] = _num(r["active"])
    counties = []
    for name in sorted(by_county):
        c = by_county[name]
        m = mode.get(name, {})
        c["share_mail"] = _pct(m.get("share_mail"))
        c["share_early"] = _pct(m.get("share_early"))
        c["share_election_day"] = _pct(m.get("share_election_day"))
        cb = county_banked.get(name)
        c["need"] = _pct(cb["rate_not_yet_voted"]) if cb else None
        counties.append(c)

    backtest = [
        {
            "held_out": r["held_out"].replace("GG", "20"),
            "grain": r["grain"].replace("_", " and "),
            "model": _num(r["model"]),
            "naive": _num(r["naive"]),
        }
        for r in _rows("backtest.csv", model_dir)
        if r["metric"] == "rate_mae_pts"
    ]

    downloads = sorted(
        p.relative_to(DATA_DIR).as_posix() for p in model_dir.iterdir() if p.suffix == ".csv"
    )
    return {
        "scenarios": scenarios,
        "central": scenarios[1],
        "actual": actual,
        "range": headline_range,
        "parties": parties,
        "poll": poll,
        "rule": rule,
        "votes": [label for _, label in VOTES],
        "early": early,
        "counties": counties,
        "has_county_need": any(c["need"] is not None for c in counties),
        "backtest": backtest,
        "caveats": CAVEATS,
        "mail_as_of": provenance.get("early_as_of", {}).get("sent_returned_gg26")
        or sw["central"]["ballots"]["gg26_mail_as_of"],
        "early_as_of": provenance.get("early_as_of", {}).get("early_voting"),
        "run_date": provenance["run_date"][:10],
        "downloads": downloads,
    }
