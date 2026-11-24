"""skipjack list and skipjack get: the argument parsers, dispatch and output.

Every handler (in datasets.py) returns (meta, rows); run() adds the shared meta fields and
renders.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from skipjack.client import datasets
from skipjack.client.output import render, typed
from skipjack.client.sources import DEFAULT_BASE_URL, ClientError, SkipjackSource


def _month(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a month like 2026-10")
    return value


def _as_of(value: str) -> str:
    if value in ("latest", "all"):
        return value
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"{value!r} is not latest, all or a date like 2026-10-15")


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

    res = get_sub.add_parser("results", help="An election's results by county")
    res.add_argument("--election", required=True, help="An election code, e.g. GG22")
    res.add_argument("--county")
    res.add_argument("--office", help="Keep offices containing this text (case-insensitive)")
    res.add_argument("--party-file", choices=["dem", "rep", "non", "all"],
                     help="A primary's party file")  # fmt: skip
    _shared(res, sbe=True)

    tur = get_sub.add_parser("turnout", help="An election's turnout reports")
    tur.add_argument("--election", required=True, help="An election code, e.g. GG26")
    tur.add_argument("--family", help="One report family (see: skipjack list turnout CODE)")
    tur.add_argument("--geography", default="county",
                     help="One geography (see: skipjack list turnout CODE)")  # fmt: skip
    tur.add_argument("--as-of", default="latest", metavar="latest|all|YYYY-MM-DD", type=_as_of)
    tur.add_argument("--county")
    _shared(tur, sbe=True)


# ---- dispatch --------------------------------------------------------------------------------

_GET = {
    "registration": datasets.registration,
    "results": datasets.results,
    "turnout": datasets.turnout,
}


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
        # The devnull fd is left open: the process exits on the next line.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


def _text(args: argparse.Namespace) -> str:
    """The rendered output, or a ClientError."""
    with SkipjackSource(args.base_url) as source:
        try:
            if args.command == "list":
                meta, rows = datasets.list_(args, source)
            else:
                meta, rows = _GET[args.dataset](args, source)
        except (KeyError, IndexError, TypeError) as err:
            # A catalog missing a key, or a CSV missing a column the handler reads. These
            # also hide our own bugs, so SKIPJACK_DEBUG lets the traceback through.
            if os.environ.get("SKIPJACK_DEBUG"):
                raise
            detail = f"missing {err}" if isinstance(err, KeyError) else f"({err})"
            raise ClientError(
                f"{source.url('index.json')} is not a catalog this skipjack understands, "
                f"or one of its files is not as expected: {detail} "
                "(set SKIPJACK_DEBUG=1 for the traceback)"
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
