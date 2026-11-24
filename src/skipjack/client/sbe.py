"""The live source: the State Board's own files, parsed now with the pipeline's readers.

Nothing is written to archives/ or data/, and nothing falls back to the published copy: a
download or parse failure is an error that says to use --source skipjack. Results get the
readers' row-total checks; the statewide cross-check needs every county's file and is left to
`skipjack results-parse`. The pipeline modules are imported inside the handlers, so `skipjack
get` from the published files never loads them.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from skipjack.client.helpers import choice, county_for, meta
from skipjack.client.sources import ClientError

_FALLBACK = "Use --source skipjack for the published copy"

# The PDF extra's packages, as a missing one names itself; any other missing module is a bug
_PDF_READER = {"natural_pdf", "rapidocr", "pdfplumber", "pypdfium2"}


def _counties() -> list[dict]:
    """The counties as the catalog lists them, from the pipeline's own codes."""
    from skipjack.pipeline.results import COUNTY_CODES
    from skipjack.web.urls import slugify

    return [{"name": n, "slug": slugify(n), "code": c} for c, n in COUNTY_CODES.items()]


# ---- results ---------------------------------------------------------------------------------


def results(args: argparse.Namespace) -> tuple[dict, list[dict]]:
    """The certified county files, one download per county and party file asked for."""
    from skipjack.pipeline import results as pr

    code = choice("election", args.election.upper(), pr.RESULTS_ELECTIONS)
    e = pr.RESULTS_ELECTIONS[code]
    if not e.published:
        raise ClientError(
            f"the board has not posted {code}'s certified county files. {_FALLBACK} "
            "(the unofficial count, while it runs)"
        )
    if args.party_file and args.party_file not in e.party_files:
        raise ClientError(
            f"{code} has no {args.party_file!r} files; party files: {', '.join(e.party_files)}"
        )
    county = county_for(_counties(), args.county)
    office = args.office.lower() if args.office else None
    rows, urls, notes, exceptions = [], [], [], 0
    for f in pr.county_files(e):
        if f.role != "county" or (county and f.county != county["name"]):
            continue
        if args.party_file and f.party_file != args.party_file:
            continue
        status, body, detail = pr.download_status(f.url)
        if status == "unpublished" and f.party_file == "non":
            # not every county has non-partisan contests; say so rather than skip silently
            notes.append(f"{f.file_name} not posted (no non-partisan contests)")
            continue
        if status != "ok":
            raise ClientError(f"could not get {f.url} ({detail or status}). {_FALLBACK}")
        try:
            read = pr.READERS[e.layout](body, e, f.county, f.party_file)
        except pr.ResultsParseError as err:
            raise ClientError(f"{f.file_name} did not parse: {err}. {_FALLBACK}") from err
        urls.append(f.url)
        exceptions += len(read.row_total_exceptions)
        rows += [
            r
            for r in read.frame.to_dicts()
            if office is None or office in (r["office"] or "").lower()
        ]
    checks = "row totals checked"
    if exceptions:
        checks += f" ({exceptions} rows keep the board's own printed total, as published)"
    checks += "; statewide cross-check not run (it needs every county's file)"
    filters = {"county": county["name"] if county else None, "office": args.office,
               "party_file": args.party_file}  # fmt: skip
    built = meta(
        "results", urls, filters,
        election=code, name=e.name, election_day=e.election_day,
        official=True, as_of=None, checks=checks,
    )  # fmt: skip
    if notes:
        built["notes"] = notes
    return built, rows


# ---- turnout ---------------------------------------------------------------------------------


def _candidates(files: list) -> list:
    """A family's files in the order to try: workbook before its PDF twin, and a certified
    report's Updated- re-post before the original. The sort is stable, so files that tie keep
    their configured order."""
    return sorted(files, key=lambda f: (f.kind != "xlsx", "Updated-" not in f.url))


def turnout(args: argparse.Namespace) -> tuple[dict, list[dict]]:
    """One family's current report at one geography, as the board serves it now."""
    import httpx
    import polars as pl

    from skipjack.pipeline import turnout as pt

    code = choice("election", args.election.upper(), pt.ELECTIONS)
    if not args.family:
        raise ClientError("--source sbe parses one report at a time; add --family")
    if args.as_of != "latest":
        raise ClientError(
            f"the board serves only its current file, so --source sbe has no --as-of. {_FALLBACK}"
        )
    e = pt.ELECTIONS[code]
    family = choice("family", args.family, {f.family for f in e.files})
    files = [f for f in e.files if f.family == family and f.geography == args.geography]
    if not files:
        geographies = sorted({f.geography for f in e.files if f.family == family})
        raise ClientError(
            f"no {family}/{args.geography} report for {code}; geographies: {', '.join(geographies)}"
        )
    if not pt.may_publish(code, family):
        raise ClientError(
            f"{code} is certified, and {family} is the board's unofficial tally: its certified "
            "reports are the figures of record"
        )
    county = county_for(_counties(), args.county)
    failures = []
    for f in _candidates(files):  # the first that parses wins; the others' reasons are notes
        try:
            data = pt.download(f.url)
        except httpx.HTTPError as err:
            failures.append(f"could not get {f.url} ({err})")
            continue
        kind = pt.sniff(data)
        if kind is None:  # the board answers its HTML page for a file not posted yet
            failures.append(f"{f.url} is not published yet")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{family}.{kind}"
            path.write_bytes(data)
            try:
                as_of, frame = pt.parse_path(path, code, family, args.geography)
            except ModuleNotFoundError as err:
                reader = (err.name or "").split(".")[0]
                if reader not in _PDF_READER:
                    raise  # not the PDF reader missing: a genuine bug keeps its traceback
                if any(c.kind == "xlsx" for c in files):
                    # the workbook was tried first and failed; only its PDF twin is left
                    why = "the PDF twin needs the PDF reader"
                else:
                    why = f"{family} is a PDF report and needs the PDF reader"
                raise ClientError(
                    "; ".join([*failures, f"{why} ({reader}): install skipjack[pdf]"])
                    + f". {_FALLBACK}"
                ) from err
            except pt.TurnoutParseError as err:
                failures.append(f"{f.url} did not parse: {err}")
                continue
        if as_of is None and e.undated_as_of:
            # a file that prints no date of its own, as turnout_parse handles it
            as_of = e.undated_as_of
            frame = frame.with_columns(pl.lit(as_of).alias("as_of"))
        if as_of is None:
            failures.append(f"{f.url} prints no As-of date")
            continue
        unreconciled = pt.UNRECONCILED_SNAPSHOTS.get((code, family, args.geography, as_of))
        if unreconciled:
            # the pipeline leaves this snapshot unparsed; so does the live source
            failures.append(
                f"{f.url} skipped: its {as_of} report does not reconcile ({unreconciled})"
            )
            continue
        rows = frame.to_dicts()
        if county is not None:
            if rows and not any(r["county"] for r in rows):
                # e.g. sent-returned by legislative district: statewide districts
                raise ClientError(
                    f"{code} {family} {args.geography} reports have no county column values; "
                    "drop --county"
                )
            rows = [r for r in rows if r["county"] == county["name"]]
        filters = {"family": family, "geography": args.geography,
                   "county": county["name"] if county else None}  # fmt: skip
        built = meta(
            "turnout", [f.url], filters,
            election=code, name=e.name, election_day=e.election_day,
            official=e.certified, as_of={family: as_of},
        )  # fmt: skip
        if failures:
            # a newer re-post or a workbook twin passed over: say why, not silently
            built["notes"] = failures
        return built, rows
    raise ClientError("; ".join(failures) + f". {_FALLBACK}")
