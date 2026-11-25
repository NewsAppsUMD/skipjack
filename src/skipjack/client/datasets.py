"""What each list and get command returns: (meta, rows), before run() stamps and renders."""

from __future__ import annotations

import argparse
import json
import sys

from skipjack.client.helpers import (
    ELECTION_NIGHT_ONLY,
    ELECTION_NIGHT_ONLY_QUESTIONS,
    choice,
    election_night_only,
    county_for,
    file_stem,
    meta,
    precinct_args,
    read_rows,
)
from skipjack.client.sources import ClientError, SkipjackSource

# ---- list ------------------------------------------------------------------------------------


def list_(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    if args.election and args.what != "turnout":
        raise ClientError(f"list {args.what} takes no election code; only `list turnout` takes one")
    catalog = source.catalog()
    if args.what == "counties":
        rows = catalog["counties"]
    elif args.what == "elections":
        rows = [
            {"code": code, "dataset": dataset, "name": e["name"],
             "election_day": e["election_day"],
             "official": e["official"] if dataset == "results" else e["certified"]}
            for dataset in ("results", "turnout")
            for code, e in catalog[dataset].items()
        ]  # fmt: skip
        rows.sort(key=lambda r: (r["election_day"] or "", r["dataset"]), reverse=True)
    else:
        if not args.election:
            raise ClientError(
                "list turnout needs an election code, e.g. skipjack list turnout GG26"
            )
        election = catalog["turnout"][choice("election", args.election.upper(), catalog["turnout"])]
        rows = [
            {"family": family, "geography": geography, "snapshots": len(dates),
             "first": dates[0], "latest": dates[-1]}
            for family, geographies in election["families"].items()
            for geography, dates in geographies.items()
        ]  # fmt: skip
    return meta(f"list {args.what}", [source.url("index.json")]), rows


# ---- get registration ------------------------------------------------------------------------


def registration(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    """The monthly, activity or summary table. All three have report_date and party; summary
    is statewide (no county column), and activity's party is blank for some measures."""
    if args.county and args.table == "summary":
        raise ClientError("the summary table is statewide; drop --county")
    catalog = source.catalog()
    tables = catalog["registration"]["tables"]
    path = tables[choice("table", args.table, tables)]
    county = county_for(catalog["counties"], args.county)
    party = args.party.upper() if args.party else None
    # Every table has report_date; the other columns are checked only when filtered on
    needs = ["report_date"] + (["county"] if county else []) + (["party"] if party else [])
    rows = []
    for row in read_rows(source, path, needs):
        month = (row["report_date"] or "")[:7]
        if county and row["county"] != county["name"]:
            continue
        if party and row["party"] != party:
            continue
        if (args.start and month < args.start) or (args.end and month > args.end):
            continue
        rows.append(row)
    filters = {
        "county": county["name"] if county else None,
        "party": party,
        "from": args.start,
        "to": args.end,
    }
    built = meta("registration", [source.url(path)], filters, table=args.table, official=True)
    return built, rows


# ---- get results -----------------------------------------------------------------------------


def results(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    """County files are NN-<county-slug>-<party_file>.csv (certified) or NN-<county-slug>.csv
    (the live unofficial count), so the filters pick files by name before any download. With
    --geography precinct they are the certified precinct files, NN-<county-slug>-<party_file>
    .csv.gz, which the catalog lists as precinct_files (a catalog built before them has no such
    key: an older site)."""
    catalog = source.catalog()
    code = choice("election", args.election.upper(), catalog["results"])
    election = catalog["results"][code]
    geography, precinct = precinct_args(args)
    if args.party_file and not election["official"]:
        raise ClientError(
            f"{code}'s unofficial count has no party files (it covers every party); "
            "drop --party-file"
        )
    county = county_for(catalog["counties"], args.county)
    if geography == "precinct":
        if not election["official"]:
            raise ClientError(
                f"{code}'s unofficial count has no precinct files; they come with the certified results"
            )
        if "precinct_files" not in election:
            raise ClientError(
                f"{code} has no precinct files on this site: its catalog predates them"
            )
        files = election["precinct_files"]
        if not files:
            raise ClientError(f"{code} has no precinct files on this site")
        if county is None and not getattr(args, "output", None):
            raise ClientError(
                "precinct results are large (a statewide election is about 100,000 rows): add "
                "--county, or -o FILE to write them all"
            )
    else:
        files = election["files"]
    every = files
    if county:
        # The two-digit code keeps 03-baltimore-city apart from 04-baltimore-county
        prefix = f"{county['code']}-{county['slug']}"
        files = [f for f in files if (s := file_stem(f)) == prefix or s.startswith(prefix + "-")]
    if args.party_file:
        files = [f for f in files if file_stem(f).endswith(f"-{args.party_file}")]
        if not files:
            # Party files are single words, so the stem's last hyphenated part names one
            have = sorted({file_stem(f).rsplit("-", 1)[-1] for f in every})
            where = f" for {county['name']}" if county else ""
            raise ClientError(
                f"{code} has no {args.party_file!r} files{where}; party files: {', '.join(have)}"
            )
    if geography == "precinct" and county and not files:
        raise ClientError(f"{code} has no precinct files for {county['name']} on this site")
    office = args.office.lower() if args.office else None
    needs = (["office"] if office else []) + (["precinct"] if precinct else [])
    rows = []
    for i, f in enumerate(files, 1):
        if geography == "precinct" and len(files) > 1:
            # a statewide pull reads one file per county and party file: say which, on stderr
            print(f"reading {f} ({i} of {len(files)})", file=sys.stderr, flush=True)
        rows += [
            row
            for row in read_rows(source, f, needs)
            # A short row's missing fields are None
            if (office is None or office in (row["office"] or "").lower())
            and (precinct is None or row["precinct"] == precinct)
        ]
    filters = {"county": county["name"] if county else None, "office": args.office,
               "party_file": args.party_file,
               "geography": geography if geography != "county" else None,
               "precinct": precinct}  # fmt: skip
    built = meta(
        "results", [source.url(f) for f in files], filters,
        election=code, name=election["name"], election_day=election["election_day"],
        official=election["official"], as_of=election["as_of"],
    )  # fmt: skip
    year = (election.get("election_day") or "")[:4]
    if geography == "precinct" and election_night_only(year):
        built["notes"] = [ELECTION_NIGHT_ONLY]
    return built, rows


# ---- get questions ---------------------------------------------------------------------------


def questions(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    """Certified ballot questions: the catalog's question_files, questions/county/
    NN-<county-slug>.csv or, with --geography precinct, questions/precinct/NN-<county-slug>
    .csv.gz, passed through as written (the third choice's *_third and total_third where the
    files carry them). Generals only; the live unofficial count has none."""
    catalog = source.catalog()
    code = choice("election", args.election.upper(), catalog["results"])
    election = catalog["results"][code]
    geography, precinct = precinct_args(args)
    files = (election.get("question_files") or {}).get(geography) or []
    if not files:
        raise ClientError(f"{code} has no ballot-question files on this site")
    county = county_for(catalog["counties"], args.county)
    if county:
        files = [f for f in files if file_stem(f) == f"{county['code']}-{county['slug']}"]
        if not files:
            raise ClientError(
                f"{code} has no ballot-question files for {county['name']} on this site"
            )
    rows = []
    for f in files:
        rows += [
            row
            for row in read_rows(source, f, ["precinct"] if precinct else [])
            if precinct is None or row["precinct"] == precinct
        ]
    filters = {"county": county["name"] if county else None,
               "geography": geography if geography != "county" else None,
               "precinct": precinct}  # fmt: skip
    built = meta(
        "questions", [source.url(f) for f in files], filters,
        election=code, name=election["name"], election_day=election["election_day"],
        official=election["official"],
    )  # fmt: skip
    year = (election.get("election_day") or "")[:4]
    if geography == "precinct" and election_night_only(year):
        built["notes"] = [ELECTION_NIGHT_ONLY_QUESTIONS]
    return built, rows


# ---- get turnout -----------------------------------------------------------------------------


def turnout(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    """Files are turnout/<code>/<family>/<geography>/<date>.csv, one per snapshot; the catalog
    lists each family's dates by geography, oldest first."""
    catalog = source.catalog()
    code = choice("election", args.election.upper(), catalog["turnout"])
    election = catalog["turnout"][code]
    county = county_for(catalog["counties"], args.county)
    families = election["families"]
    if args.family:
        families = {args.family: families[choice("family", args.family, families)]}
    at_geography = {f: g[args.geography] for f, g in families.items() if args.geography in g}
    if not at_geography:
        available = sorted({g for geos in families.values() for g in geos})
        raise ClientError(
            f"no {args.geography!r} reports for {code}"
            + (f" {args.family}" if args.family else "")
            + f"; geographies: {', '.join(available)}"
        )
    picked, as_of = {}, {}
    for family, dates in at_geography.items():
        if args.as_of == "latest":
            chosen = dates[-1:]
        elif args.as_of == "all":
            chosen = dates
        elif args.as_of in dates:
            chosen = [args.as_of]
        elif args.family:
            raise ClientError(f"no {family} snapshot dated {args.as_of}; dates: {', '.join(dates)}")
        else:
            chosen = []  # a date across every family: keep the families that have it
        if chosen:
            as_of[family] = chosen if args.as_of == "all" else chosen[-1]
            picked[family] = [f"turnout/{code}/{family}/{args.geography}/{d}.csv" for d in chosen]
    if not picked:
        raise ClientError(f"no {code} snapshot dated {args.as_of} at {args.geography}")
    rows = []
    for family, paths in picked.items():
        checked = county is None  # whether this family's rows have shown they name counties
        for path in paths:
            file_rows = read_rows(source, path, ["county"] if county else [])
            if county is None:
                rows += file_rows
                continue
            if not checked and file_rows:
                # The first file with rows decides for the family, before reading the rest
                if not any(row["county"] for row in file_rows):
                    # e.g. sent-returned by legislative district: statewide districts
                    raise ClientError(
                        f"{code} {family} {args.geography} reports have no county column "
                        "values; drop --county" + ("" if args.family else " or pick a --family")
                    )
                checked = True
            rows += [row for row in file_rows if row["county"] == county["name"]]
    paths = [p for family_paths in picked.values() for p in family_paths]
    filters = {"family": args.family, "geography": args.geography,
               "county": county["name"] if county else None}  # fmt: skip
    built = meta(
        "turnout", [source.url(p) for p in paths], filters,
        election=code, name=election["name"], election_day=election["election_day"],
        official=election["certified"], as_of=as_of,
    )  # fmt: skip
    return built, rows


# ---- get county ------------------------------------------------------------------------------

SECTIONS = ("registration", "turnout", "results")


def _official(catalog: dict, doc: dict, sections: list[str]) -> dict[str, bool]:
    """For each of ``sections`` (validated already; the others are not read), whether every
    row's election is official (results) or certified (turnout) by the catalog; an election
    the catalog does not list counts as not official. Registration is the board's monthly
    reports, official as published."""

    def all_official(section: str, dataset: str, flag: str) -> bool:
        listed = catalog[dataset]
        return all(bool(listed.get(row.get("election"), {}).get(flag)) for row in doc[section])

    flags = {
        "registration": lambda: True,
        "results": lambda: all_official("results", "results", "official"),
        "turnout": lambda: all_official("turnout", "turnout", "certified"),
    }
    return {section: flags[section]() for section in sections}


def county_history(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list | dict]:
    """The county document, county/<slug>.json: registration, turnout and results rows plus
    the nested results_over_time. With --section the rows are that section's list; without,
    meta["document"] is True and the rows are the document itself (run() pops the flag)."""
    if args.format == "csv" and not args.section:
        raise ClientError(
            "the county document is nested; for CSV pick one: "
            "--section registration|turnout|results"
        )
    catalog = source.catalog()
    county = county_for(catalog["counties"], args.name)
    path = catalog["county_history"].format(slug=county["slug"])
    url = source.url(path)
    try:
        doc = json.loads(source.text(path))
    except json.JSONDecodeError as err:
        raise ClientError(f"{url} is not valid JSON ({err})") from err
    if not isinstance(doc, dict):
        raise ClientError(f"{url} is not a county document")
    wanted = [args.section] if args.section else list(SECTIONS)
    for section in wanted:
        rows = doc.get(section)
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ClientError(f"{url} has no {section} section as a list of rows")
    official = _official(catalog, doc, wanted)
    if args.section:
        built = meta("county", [url], county=county["name"], official=official[args.section])
        return {**built, "section": args.section}, doc[args.section]
    built = meta("county", [url], county=county["name"], official=official)
    sections = {s: len(doc[s]) for s in SECTIONS}
    return {**built, "sections": sections, "document": True}, doc
