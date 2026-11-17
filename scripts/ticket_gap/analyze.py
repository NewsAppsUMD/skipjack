"""Did the Green and Working Class tickets cost Wes Moore the votes Brown and Lierman got?

In the 2026 general (GG26) Anthony Brown (Attorney General) and Brooke Lierman (Comptroller)
got more votes than Wes Moore in all 24 jurisdictions. The governor's contest had Green and
Working Class Party tickets; the other two had none. This script tests, from county totals,
how much of the Democrats' lead over Moore those tickets can account for. Findings and
caveats: docs/ticket-gap-2026.md.

Run from the repository root, after ``skipjack build-db``::

    uv run python scripts/ticket_gap/analyze.py

It reads ``skipjack.db`` (the latest GG26 unofficial snapshot, or the certified results once
the board publishes them; certified GG22, GG18 and GG14 for comparison) and writes CSVs and
``tables.md`` to ``scripts/ticket_gap/out/``. County totals bound the answer; they cannot show
what any voter did. Precinct results or cast vote records would.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "skipjack.db"
OUT = Path(__file__).resolve().parent / "out"

GOV, AG, COMP = "Governor / Lt. Governor", "Attorney General", "Comptroller"
OFFICES = (GOV, AG, COMP)
DOWN = ((AG, "Attorney General"), (COMP, "Comptroller"))
UNOFFICIAL_PARTIES = {
    "Democratic": "DEM", "Republican": "REP", "Green": "GRN", "Working Class Party": "WCP",
    "Libertarian": "LIB",
}  # fmt: skip


def empty() -> dict:
    return {"dem": 0, "rep": 0, "minor": {}, "write_in": 0, "total": 0}


def load(conn: sqlite3.Connection, election: str) -> tuple[dict, str]:
    """county -> office -> {dem, rep, minor: {party: votes}, write_in, total}, and a label for
    where the numbers come from. Certified results when the election has them."""
    marks = ",".join("?" * len(OFFICES))
    rows = conn.execute(
        f"""SELECT county, office, party, write_in, SUM(total_votes) FROM results_county
            WHERE election = ? AND office IN ({marks}) GROUP BY 1, 2, 3, 4""",
        (election, *OFFICES),
    ).fetchall()
    label = "certified"
    if not rows:
        snapshot = conn.execute(
            "SELECT MAX(snapshot) FROM results_unofficial WHERE election = ?", (election,)
        ).fetchone()[0]
        rows = [
            (county, office, UNOFFICIAL_PARTIES.get(party, party), write_in, votes)
            for county, office, party, write_in, votes in conn.execute(
                f"""SELECT county, office, party, write_in, SUM(total_votes)
                    FROM results_unofficial
                    WHERE election = ? AND snapshot = ? AND office IN ({marks})
                    GROUP BY 1, 2, 3, 4""",
                (election, snapshot, *OFFICES),
            )
        ]
        label = f"unofficial, snapshot {snapshot}"
    out: dict[str, dict] = {}
    for county, office, party, write_in, votes in rows:
        cell = out.setdefault(county, {o: empty() for o in OFFICES})[office]
        votes = votes or 0
        cell["total"] += votes
        if write_in or not party:
            cell["write_in"] += votes
        elif party == "DEM":
            cell["dem"] += votes
        elif party == "REP":
            cell["rep"] += votes
        else:
            cell["minor"][party] = cell["minor"].get(party, 0) + votes
    return out, label


def statewide(counties: dict) -> dict:
    total = {o: empty() for o in OFFICES}
    for offices in counties.values():
        for o, cell in offices.items():
            for k in ("dem", "rep", "write_in", "total"):
                total[o][k] += cell[k]
            for party, v in cell["minor"].items():
                total[o]["minor"][party] = total[o]["minor"].get(party, 0) + v
    return total


def pieces(offices: dict, down: str) -> dict:
    """The down-ballot Democrat's lead over the Democrat for governor, split into four parts
    that add to it exactly (each contest's total is its candidates' sum):

    gap = minor-ticket votes (governor less down-ballot)
        + Republican for governor less the down-ballot Republican
        + write-ins (governor less down-ballot)
        - votes cast (governor less down-ballot)
    """
    g, d = offices[GOV], offices[down]
    out = {
        "gap": d["dem"] - g["dem"],
        "minor": sum(g["minor"].values()) - sum(d["minor"].values()),
        "rep": g["rep"] - d["rep"],
        "write_in": g["write_in"] - d["write_in"],
        "votes_cast": g["total"] - d["total"],
    }
    assert out["gap"] == out["minor"] + out["rep"] + out["write_in"] - out["votes_cast"], out
    return out


def bounds(p: dict) -> tuple[float, float] | None:
    """The least and most of a positive gap the minor tickets' voters can account for, on net
    flows: at most all of them (or the whole gap); at least what is left after every other
    source (Republican ticket-splitters, write-ins, voters who skipped the governor's contest)
    is counted in full."""
    if p["gap"] <= 0:
        return None
    other = max(0, p["rep"]) + max(0, p["write_in"]) + max(0, -p["votes_cast"])
    low = max(0, p["gap"] - other)
    high = min(p["gap"], max(0, p["minor"]))
    return low / p["gap"], high / p["gap"]


def two_party(cell: dict) -> float:
    return cell["dem"] / (cell["dem"] + cell["rep"])


def wls(y, x, w) -> dict:
    """Weighted least squares of y on x (one or more columns) with an intercept."""
    y, w = np.asarray(y, float), np.asarray(w, float)
    X = np.column_stack([np.ones(len(y)), np.asarray(x, float)])
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    resid = y - X @ beta
    dof = len(y) - X.shape[1]
    sigma2 = (w * resid**2).sum() / dof
    cov = sigma2 * np.linalg.inv((X * w[:, None]).T @ X)
    ybar = (w * y).sum() / w.sum()
    r2 = 1 - (w * resid**2).sum() / (w * (y - ybar) ** 2).sum()
    return {"beta": beta, "se": np.sqrt(np.diag(cov)), "r2": r2, "n": len(y)}


def ballots(conn: sqlite3.Connection, election: str) -> tuple[dict[str, int], str]:
    """Ballots cast by county: the certified turnout report's voters by method, or for an
    election still being counted the election-night report's ballot cards cast."""
    rows = conn.execute(
        """SELECT county, SUM(value) FROM turnout_counts
           WHERE election = ? AND family = 'official-turnout' AND geography = 'county'
             AND category = 'ALL' AND party = 'ALL' AND measure != 'eligible'
           GROUP BY 1""",
        (election,),
    ).fetchall()
    if rows:
        return dict(rows), "certified turnout report"
    as_of = conn.execute(
        """SELECT MAX(as_of) FROM turnout_counts
           WHERE election = ? AND family = 'election-night' AND geography = 'county'""",
        (election,),
    ).fetchone()[0]
    if as_of is None:
        return {}, ""
    rows = conn.execute(
        """SELECT county, value FROM turnout_counts
           WHERE election = ? AND family = 'election-night' AND geography = 'county'
             AND county != '' AND district = '' AND category = 'ALL' AND party = 'ALL'
             AND measure = 'cards-cast' AND as_of = ?""",
        (election, as_of),
    ).fetchall()
    return dict(rows), f"election-night report of {as_of} (ballot cards cast)"


def registration(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, int]], str]:
    month = conn.execute("SELECT MAX(report_date) FROM voter_registration").fetchone()[0]
    out: dict[str, dict[str, int]] = {}
    for county, party, voters in conn.execute(
        "SELECT county, party, SUM(active_voters) FROM voter_registration"
        " WHERE report_date = ? GROUP BY 1, 2",
        (month,),
    ):
        out.setdefault(county, {})[party] = voters
    return out, month


# --- output ---------------------------------------------------------------------------------


def n(v: float) -> str:
    return f"{v:+,.0f}"


def pct(v: float, digits: int = 2) -> str:
    return f"{100 * v:.{digits}f}%"


def table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def write_csv(name: str, header: list[str], rows: list[list]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def fit_line(label: str, fit: dict, names: list[str]) -> list:
    cells = [label, f"{fit['beta'][0] * 100:+.2f} pts"]
    for i, _ in enumerate(names, start=1):
        cells.append(f"{fit['beta'][i]:.2f} (se {fit['se'][i]:.2f})")
    return [*cells, f"{fit['r2']:.2f}", fit["n"]]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    data = {}
    labels = {}
    for election in ("GG26", "GG22", "GG18", "GG14"):
        data[election], labels[election] = load(conn, election)
    md: list[str] = [f"GG26 results: {labels['GG26']}.", ""]

    # 1-4. statewide: the decomposition, bounds and two-party shares, by election
    md += ["## Statewide decomposition", ""]
    rows = []
    for election, counties in data.items():
        st = statewide(counties)
        minor = ", ".join(f"{p} {v:,}" for p, v in sorted(st[GOV]["minor"].items())) or "none"
        for office, name in DOWN:
            p = pieces(st, office)
            b = bounds(p)
            rows.append([
                election, name, n(p["gap"]), n(p["minor"]), n(p["rep"]), n(p["write_in"]),
                n(-p["votes_cast"]), f"{pct(b[0], 0)} to {pct(b[1], 0)}" if b else "no gap",
                pct(two_party(st[GOV]), 1), pct(two_party(st[office]), 1), minor,
            ])  # fmt: skip
    header = [
        "Election", "Contest", "Dem lead over Dem for governor", "Minor tickets",
        "Rep for governor less this Rep", "Write-ins", "More votes cast than for governor",
        "Share minor tickets can explain", "Governor Dem two-party", "This Dem two-party",
        "Minor tickets for governor",
    ]  # fmt: skip
    md += [table(header, rows), ""]
    write_csv("statewide.csv", header, rows)

    # 5. by county, GG26
    now, then = data["GG26"], data["GG22"]
    assert len(now) == 24 and len(then) == 24
    md += ["## By county, GG26", ""]
    rows = []
    for county in sorted(now):
        o = now[county]
        cast = o[GOV]["total"]
        third = sum(o[GOV]["minor"].values())
        row = [county, f"{cast:,}", f"{third:,}", pct(third / cast)]
        for office, _ in DOWN:
            p = pieces(o, office)
            b = bounds(p)
            row += [n(p["gap"]), pct(p["gap"] / cast), n(p["rep"]), n(-p["votes_cast"]),
                    f"{pct(b[0], 0)} to {pct(b[1], 0)}" if b else "no gap",
                    "yes" if p["gap"] > third else ""]  # fmt: skip
        rows.append(row)
    header = ["County", "Votes for governor", "Green + WCP", "Share"]
    for _, name in DOWN:
        short = "Brown" if name == "Attorney General" else "Lierman"
        header += [
            f"{short} − Moore", "Share", f"Cox − {short}'s opponent",
            f"More votes cast ({short}'s contest)", "Minor tickets can explain",
            "Gap exceeds Green + WCP",
        ]  # fmt: skip
    md += [table(header, rows), ""]
    write_csv("county_gg26.csv", header, rows)
    for i, (_, name) in enumerate(DOWN):
        over = [r[0] for r in rows if r[4 + 6 * i + 5] == "yes"]
        md += [f"{name}: the gap exceeds the Green + WCP vote in {len(over)} of 24: "
               f"{', '.join(over)}.", ""]  # fmt: skip

    # 6. dose-response across counties
    md += ["## Regressions across the 24 jurisdictions (weighted by votes for governor)", ""]
    w = [now[c][GOV]["total"] for c in sorted(now)]
    share = lambda c, party: now[c][GOV]["minor"].get(party, 0) / now[c][GOV]["total"]  # noqa: E731
    third = [share(c, "GRN") + share(c, "WCP") for c in sorted(now)]
    rows, rows2, rows3, rows4 = [], [], [], []
    for office, name in DOWN:
        y = [pieces(now[c], office)["gap"] / now[c][GOV]["total"] for c in sorted(now)]
        rows.append(fit_line(name, wls(y, third, w), ["x"]))
        split = [[share(c, "GRN"), share(c, "WCP")] for c in sorted(now)]
        rows2.append(fit_line(name, wls(y, split, w), ["grn", "wcp"]))
        # 7. the change since 2022, the same four candidates
        dy, dx, dleft = [], [], []
        for c in sorted(now):
            before = then[c]
            dy.append(pieces(now[c], office)["gap"] / now[c][GOV]["total"]
                      - pieces(before, office)["gap"] / before[GOV]["total"])  # fmt: skip
            dx.append(sum(now[c][GOV]["minor"].values()) / now[c][GOV]["total"]
                      - sum(before[GOV]["minor"].values()) / before[GOV]["total"])  # fmt: skip
            left = lambda o: o[GOV]["minor"].get("GRN", 0) + o[GOV]["minor"].get("WCP", 0)  # noqa: E731
            dleft.append(left(now[c]) / now[c][GOV]["total"] - left(before) / before[GOV]["total"])
        rows3.append(fit_line(name, wls(dy, dx, w), ["x"]))
        rows4.append(fit_line(name, wls(dy, dleft, w), ["x"]))
    md += ["Gap share on Green + WCP share (the theory predicts slope 1, intercept 0):", "",
           table(["Contest", "Intercept", "Slope", "R²", "n"], rows), "",
           "Gap share on Green share and WCP share:", "",
           table(["Contest", "Intercept", "Green", "WCP", "R²", "n"], rows2), "",
           "Change 2022 to 2026: change in gap share on change in minor-ticket share:", "",
           table(["Contest", "Intercept", "Slope", "R²", "n"], rows3), "",
           "The same, on the change in Green + WCP share only (2022 also had a Libertarian):", "",
           table(["Contest", "Intercept", "Slope", "R²", "n"], rows4), ""]  # fmt: skip

    # 8. minor-ticket votes against registration
    reg, month = registration(conn)
    md += [f"## Votes against registration ({month} active voters)", ""]
    rows = []
    tot = dict.fromkeys(("gv", "gr", "wv", "wr"), 0)
    for county in sorted(now):
        m = now[county][GOV]["minor"]
        gv, wv = m.get("GRN", 0), m.get("WCP", 0)
        gr, wr = reg[county].get("GRN", 0), reg[county].get("WCP", 0)
        for k, v in zip(tot, (gv, gr, wv, wr), strict=True):
            tot[k] += v
        rows.append([county, f"{gv:,}", f"{gr:,}", f"{gv / gr:.1f}", f"{wv:,}", f"{wr:,}",
                     f"{wv / wr:.1f}"])  # fmt: skip
    rows.append(["Statewide", f"{tot['gv']:,}", f"{tot['gr']:,}", f"{tot['gv'] / tot['gr']:.1f}",
                 f"{tot['wv']:,}", f"{tot['wr']:,}", f"{tot['wv'] / tot['wr']:.1f}"])  # fmt: skip
    header = ["County", "Green votes", "Green registered", "Votes per registrant", "WCP votes",
              "WCP registered", "Votes per registrant"]  # fmt: skip
    md += [table(header, rows), ""]
    write_csv("registration.csv", header, rows)

    # 9. blank votes: ballots cast less the votes in each contest
    md += ["## Ballots without a vote in each contest", ""]
    rows = []
    for election in ("GG26", "GG22"):
        cast, source = ballots(conn, election)
        if not cast:
            continue
        st = statewide(data[election])
        total = sum(cast.values())
        negative = sum(
            1 for c in cast for o in OFFICES if c in data[election]
            and cast[c] < data[election][c][o]["total"]
        )  # fmt: skip
        rows.append([election, source, f"{total:,}",
                     *(f"{total - st[o]['total']:,} ({pct((total - st[o]['total']) / total)})"
                       for o in OFFICES), negative])  # fmt: skip
    header = ["Election", "Ballots from", "Ballots cast", "No vote for governor",
              "No vote for Attorney General", "No vote for Comptroller",
              "County-contests with more votes than ballots"]  # fmt: skip
    md += [table(header, rows), ""]
    write_csv("blank_votes.csv", header, rows)

    (OUT / "tables.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
