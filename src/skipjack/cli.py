"""CLI entry point for Skipjack data pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def cmd_fetch(args: argparse.Namespace) -> None:
    from skipjack.pipeline.fetch import fetch_vrar

    count = 0
    for year in range(args.start_year, args.end_year + 1):
        for month in range(1, 13):
            if date(year, month, 1) > date.today():
                break
            record = fetch_vrar(year, month, force=args.force)
            if record:
                count += 1
    print(f"Fetched/verified {count} VRAR reports")


def cmd_parse(args: argparse.Namespace) -> None:
    from skipjack.pipeline.clean import normalize_parties
    from skipjack.pipeline.fetch import MANIFEST_PATH
    from skipjack.pipeline.provenance import ProvenanceSidecar, read_manifest
    from skipjack.pipeline.vrar import VrarParseError, parse_vrar

    out_dirs = {
        "registration": Path("data/voter_registration/monthly"),
        "activity": Path("data/voter_registration/activity"),
        "summary": Path("data/voter_registration/summary"),
    }
    for d in out_dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    # Latest manifest entry wins when a report was re-fetched.
    records = {
        r.source_id: r for r in read_manifest(MANIFEST_PATH) if r.source_id.startswith("sbe-vrar-")
    }

    count = 0
    failed: list[str] = []
    for source_id, record in sorted(records.items()):
        year, month = (int(x) for x in source_id.removeprefix("sbe-vrar-").split("-"))
        if args.month and f"{year}-{month:02d}" not in args.month:
            continue
        pdf_path = Path(record.local_path)
        if not pdf_path.exists():
            logging.warning("Missing file: %s", pdf_path)
            continue

        stem = f"{year}-{month:02d}"
        if (out_dirs["summary"] / f"{stem}.csv").exists() and not args.force:
            continue

        try:
            report = parse_vrar(pdf_path, year, month)
        except VrarParseError as e:
            logging.error("FAILED %s: %s", pdf_path.name, e)
            failed.append(f"{stem}: {e}")
            continue

        frames = {
            "registration": normalize_parties(report.registration),
            "activity": report.activity,
            "summary": report.summary,
        }
        for kind, df in frames.items():
            csv_path = out_dirs[kind] / f"{stem}.csv"
            df.write_csv(csv_path)
            ProvenanceSidecar.from_source(
                record,
                extraction_method=report.extraction_method,
                checks=report.checks,
                notes=f"VRAR {kind} table parsed with natural-pdf. {len(df)} rows.",
            ).write(csv_path)
        count += 1

    print(f"Parsed {count} VRAR reports -> data/voter_registration/")
    if failed:
        print(f"{len(failed)} reports failed and were not written:")
        for line in failed:
            print(f"  {line}")
        sys.exit(1)


def cmd_voterfile(args: argparse.Namespace) -> None:
    from skipjack.pipeline.voterfile import VoterFileError, run

    try:
        checks = run(
            args.path,
            args.out,
            snapshot_date=args.snapshot_date,
            suppress_below=args.suppress_below,
            force=args.force,
        )
    except VoterFileError as e:
        print(f"voterfile: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {checks['metrics']} metrics to {checks['out_dir']}")
    print(
        f"  {checks['rows_valid']:,} voters kept of {checks['rows_read']:,} read "
        f"(readme: {checks['readme_total_records'] or 'none'}); {checks['rows_rejected']} malformed rows "
        f"rejected; {checks['parts']} parts, {checks['duplicates_skipped']} duplicate skipped"
    )


def cmd_build_site(args: argparse.Namespace) -> None:
    from skipjack.web.static_site import SiteBuildError, build_site

    try:
        report = build_site(args.out, base_path=args.base_path, include_db=args.include_db)
    except SiteBuildError as e:
        print(f"build-site: {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Built {report.pages} pages ({report.files} files, {report.bytes / 1e6:.1f} MB) "
        f"in {report.out_dir}"
    )
    if report.base_path:
        print(f"  Links are prefixed with {report.base_path}")
    if report.problems:
        print(f"{len(report.problems)} broken links:", file=sys.stderr)
        for problem in report.problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(1)


def cmd_build_db(args: argparse.Namespace) -> None:
    from skipjack.pipeline.build_db import build

    db_path = build()
    print(f"Built {db_path}")


def cmd_serve(args: argparse.Namespace) -> None:
    import os

    import uvicorn

    port = int(os.environ.get("PORT", args.port))
    uvicorn.run(
        "skipjack.web.app:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="skipjack", description="Maryland elections data pipeline"
    )
    sub = parser.add_subparsers(dest="command")

    fetch_p = sub.add_parser("fetch", help="Download VRAR PDFs from SBE")
    fetch_p.add_argument("--start-year", type=int, default=2010)
    fetch_p.add_argument("--end-year", type=int, default=date.today().year)
    fetch_p.add_argument("--force", action="store_true")

    parse_p = sub.add_parser("parse", help="Parse archived PDFs into CSVs")
    parse_p.add_argument("--force", action="store_true")
    parse_p.add_argument(
        "--month", action="append", metavar="YYYY-MM", help="Only parse these months"
    )

    vf_p = sub.add_parser("voterfile", help="Aggregate a statewide voter file into JSON")
    vf_p.add_argument(
        "--path",
        required=True,
        type=Path,
        help="Directory of voter file parts, or one voter file (then give --snapshot-date)",
    )
    vf_p.add_argument("--snapshot-date", metavar="YYYY-MM-DD", help="Override the readme date")
    vf_p.add_argument("--out", type=Path, default=Path("data/voter_file"))
    vf_p.add_argument("--suppress-below", type=int, default=10)
    vf_p.add_argument("--force", action="store_true", help="Rebuild an existing snapshot")

    site_p = sub.add_parser("build-site", help="Render the site as static files")
    site_p.add_argument("--out", type=Path, default=Path("site"))
    site_p.add_argument(
        "--base-path",
        default="",
        help='URL prefix when not served from the root, e.g. "/skipjack" for a project site',
    )
    site_p.add_argument("--include-db", action="store_true", help="Offer skipjack.db to download")

    sub.add_parser("build-db", help="Build SQLite database from CSVs")

    serve_p = sub.add_parser("serve", help="Run the web server")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "fetch": cmd_fetch,
        "parse": cmd_parse,
        "voterfile": cmd_voterfile,
        "build-site": cmd_build_site,
        "build-db": cmd_build_db,
        "serve": cmd_serve,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
