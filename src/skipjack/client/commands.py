"""skipjack list and skipjack get: the argument parsers and what each command returns.

Every handler returns (meta, rows); run() adds the shared meta fields and renders.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from skipjack.client.output import render, typed
from skipjack.client.sources import DEFAULT_BASE_URL, ClientError, SkipjackSource


def _month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a month like 2026-10")
    return value


def _shared(parser: argparse.ArgumentParser, sbe: bool = False) -> None:
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("-o", "--output", type=Path, help="Write to a file instead of stdout")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SKIPJACK_URL", DEFAULT_BASE_URL),
        help="The skipjack downloads/ root: a URL or a local folder (env: SKIPJACK_URL)",
    )
    if sbe:
        parser.add_argument(
            "--source",
            choices=["skipjack", "sbe"],
            default="skipjack",
            help="skipjack: the published, checked files. sbe: the State Board's file, now",
        )


def register(sub: argparse._SubParsersAction) -> None:
    list_p = sub.add_parser("list", help="List the elections, counties or turnout reports")
    list_p.add_argument("what", choices=["elections", "counties", "turnout"])
    list_p.add_argument("election", nargs="?", help="For 'turnout': the election code")
    _shared(list_p)

    get_p = sub.add_parser("get", help="Print a dataset as JSON or CSV")
    get_sub = get_p.add_subparsers(dest="dataset", required=True)

    reg = get_sub.add_parser("registration", help="Voter registration by month")
    reg.add_argument("--table", choices=["monthly", "activity", "summary"], default="monthly")
    reg.add_argument("--county")
    reg.add_argument("--party", help="A party code, e.g. DEM")
    reg.add_argument("--from", dest="start", metavar="YYYY-MM", type=_month)
    reg.add_argument("--to", dest="end", metavar="YYYY-MM", type=_month)
    _shared(reg, sbe=True)


# ---- helpers ---------------------------------------------------------------------------------


def _county_key(text: str) -> str:
    """Lowercase, no periods or apostrophes, spaces and hyphens as one hyphen: "St Marys",
    "st. mary's" and "ST-MARYS" all become "st-marys"."""
    text = re.sub(r"[.'\u2019]", "", text.strip().lower())
    return re.sub(r"[\s-]+", "-", text)


def _county(catalog: dict, given: str | None) -> dict | None:
    """The catalog's county for a name or slug, loosely matched; None when not asked for."""
    if given is None:
        return None
    wanted = _county_key(given)
    for county in catalog["counties"]:
        if wanted in (_county_key(county["name"]), _county_key(county["slug"])):
            return county
    names = ", ".join(c["name"] for c in catalog["counties"])
    raise ClientError(f"unknown county {given!r}; one of: {names}")


def _choice(kind: str, given: str, choices) -> str:
    choices = sorted(choices)
    if given not in choices:
        raise ClientError(f"unknown {kind} {given!r}; one of: {', '.join(choices)}")
    return given


# ---- list ------------------------------------------------------------------------------------


def _list(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
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
        election = catalog["turnout"][
            _choice("election", args.election.upper(), catalog["turnout"])
        ]
        rows = [
            {"family": family, "geography": geography, "snapshots": len(dates),
             "first": dates[0], "latest": dates[-1]}
            for family, geographies in election["families"].items()
            for geography, dates in geographies.items()
        ]  # fmt: skip
    return {"dataset": f"list {args.what}", "source_urls": [source.url("index.json")]}, rows


# ---- get registration ------------------------------------------------------------------------


def _registration(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    """The monthly, activity or summary table. All three have report_date and party; summary
    is statewide (no county column), and activity's party is blank for some measures."""
    if args.source == "sbe":
        raise ClientError(
            "registration is not parsed live: the board's monthly PDFs need OCR and skipjack's "
            "checked errata. Use --source skipjack"
        )
    if args.county and args.table == "summary":
        raise ClientError("the summary table is statewide; drop --county")
    catalog = source.catalog()
    tables = catalog["registration"]["tables"]
    path = tables[_choice("table", args.table, tables)]
    county = _county(catalog, args.county)
    party = args.party.upper() if args.party else None
    rows = []
    for row in source.rows(path):
        month = row["report_date"][:7]
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
    meta = {
        "dataset": "registration",
        "table": args.table,
        "official": True,
        "source_urls": [source.url(path)],
        "filters": {k: v for k, v in filters.items() if v is not None},
    }
    return meta, rows


# ---- dispatch --------------------------------------------------------------------------------

_GET = {"registration": _registration}


def run(args: argparse.Namespace) -> None:
    try:
        text = _text(args)
    except ClientError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(2)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        except OSError as err:
            print(f"error: could not write {args.output} ({err})", file=sys.stderr)
            sys.exit(2)
        return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (e.g. `| head`): that is not a failure, so exit 0, quietly.
        # Pointing stdout at devnull keeps Python's exit-time flush from raising again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


def _text(args: argparse.Namespace) -> str:
    """The rendered output, or a ClientError."""
    with SkipjackSource(args.base_url) as source:
        try:
            if args.command == "list":
                meta, rows = _list(args, source)
            else:
                meta, rows = _GET[args.dataset](args, source)
        except (KeyError, IndexError, TypeError) as err:
            # A catalog missing a key, or a CSV missing a column the handler reads
            detail = f"missing {err}" if isinstance(err, KeyError) else f"({err})"
            raise ClientError(
                f"{source.url('index.json')} is not a catalog this skipjack understands, "
                f"or one of its files is not as expected: {detail}"
            ) from err
    rows = typed(rows)
    meta = {
        **meta,
        "source": getattr(args, "source", "skipjack"),
        "rows": len(rows),
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        return render(meta, rows, args.format)
    except ValueError as err:
        raise ClientError(str(err)) from err
