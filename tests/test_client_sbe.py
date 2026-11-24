"""The data CLI's live State Board source, with the downloads patched to archived files."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from skipjack.cli import main
from skipjack.pipeline import results, turnout

ROOT = Path(__file__).resolve().parents[1]
GG22 = ROOT / "archives" / "sbe" / "results" / "GG22"
GP22 = ROOT / "archives" / "sbe" / "results" / "GP22"
GG26_STATS = ROOT / "archives" / "sbe" / "campaign_stats" / "GG26"
SENT = sorted(GG26_STATS.glob("sent-returned__county__*.xlsx"))

needs_results = pytest.mark.skipif(
    not (GG22 / "GG22_01CountyResults.csv").exists(), reason="GG22 results not archived"
)
needs_primary = pytest.mark.skipif(
    not (GP22 / "GP22_01DemocraticResults.csv").exists(), reason="GP22 results not archived"
)
needs_turnout = pytest.mark.skipif(not SENT, reason="GG26 sent-returned workbook not archived")


def run(capsys, *argv):
    main(list(argv))
    return json.loads(capsys.readouterr().out)


def fails(capsys, *argv) -> str:
    """Run the CLI expecting exit 2; returns stderr."""
    with pytest.raises(SystemExit) as exit_info:
        main(list(argv))
    assert exit_info.value.code == 2
    return capsys.readouterr().err


def archived(folder: Path):
    """A results._download that answers from the archive, and "unpublished" for a file the
    archive lacks (as the board does for a county with no non-partisan contests)."""

    def fake(url, timeout=30.0):
        path = folder / url.rsplit("/", 1)[1]
        if not path.exists():
            return "unpublished", b"", "HTTP 404"
        return "ok", path.read_bytes(), ""

    return fake


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any download a test does not patch fails the test rather than reach the board."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached for the network")

    monkeypatch.setattr(results, "_download", refuse)
    monkeypatch.setattr(turnout, "download", refuse)


# ---- results ---------------------------------------------------------------------------------


@needs_results
def test_results_live_reads_only_the_counties_asked_for(capsys, monkeypatch):
    asked = []

    def fake(url, timeout=30.0):
        asked.append(url)
        return "ok", (GG22 / url.rsplit("/", 1)[1]).read_bytes(), ""

    monkeypatch.setattr(results, "_download", fake)
    out = run(capsys, "get", "results", "--election", "GG22", "--county", "Allegany",
              "--source", "sbe")  # fmt: skip
    assert len(asked) == 1 and asked[0].endswith("GG22_01CountyResults.csv")
    assert {r["county"] for r in out["rows"]} == {"Allegany"}
    assert out["rows"][0]["total_votes"] > 0
    meta = out["meta"]
    assert meta["source"] == "sbe" and meta["source_urls"] == asked
    assert "statewide cross-check not run" in meta["checks"]


@needs_results
def test_results_live_rows_match_the_published_columns(capsys, monkeypatch):
    published = ROOT / "data" / "results" / "GG22" / "county" / "01-allegany-all.csv"
    if not published.exists():
        pytest.skip("GG22 results not parsed")
    monkeypatch.setattr(results, "_download", archived(GG22))
    out = run(capsys, "get", "results", "--election", "GG22", "--county", "Allegany",
              "--source", "sbe")  # fmt: skip
    with published.open(newline="") as f:
        reader = csv.DictReader(f)
        header, first = reader.fieldnames, next(reader)
    assert list(out["rows"][0]) == header
    assert out["rows"][0]["candidate"] == first["candidate"]
    assert out["rows"][0]["total_votes"] == int(first["total_votes"])


@needs_results
def test_results_live_office_filter(capsys, monkeypatch):
    monkeypatch.setattr(results, "_download", archived(GG22))
    out = run(capsys, "get", "results", "--election", "gg22", "--county", "kent",
              "--office", "GOVERNOR", "--source", "sbe")  # fmt: skip
    assert out["rows"] and all("governor" in r["office"].lower() for r in out["rows"])
    assert out["meta"]["filters"] == {"county": "Kent", "office": "GOVERNOR"}


@needs_results
def test_results_live_fails_loudly_on_a_download_error(capsys, monkeypatch):
    monkeypatch.setattr(results, "_download", lambda url, timeout=30.0: ("error", b"", "HTTP 503"))
    err = fails(capsys, "get", "results", "--election", "GG22", "--county", "Allegany",
                "--source", "sbe")  # fmt: skip
    assert "HTTP 503" in err and "--source skipjack" in err


def test_results_live_fails_loudly_on_a_file_that_does_not_parse(capsys, monkeypatch):
    garbage = b"this,is,not\nthe,board's,layout\n"
    monkeypatch.setattr(results, "_download", lambda url, timeout=30.0: ("ok", garbage, ""))
    err = fails(capsys, "get", "results", "--election", "GG22", "--county", "Allegany",
                "--source", "sbe")  # fmt: skip
    assert "GG22_01CountyResults.csv" in err and "--source skipjack" in err
    assert "Traceback" not in err


def test_results_live_refuses_an_election_the_board_has_not_posted(capsys, monkeypatch):
    monkeypatch.setattr(results.RESULTS_ELECTIONS["GG26"], "published", False)
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG26", "--source", "sbe"])
    assert "--source skipjack" in capsys.readouterr().err


def test_results_live_party_file_must_be_the_elections(capsys):
    err = fails(capsys, "get", "results", "--election", "GG22", "--party-file", "dem",
                "--source", "sbe")  # fmt: skip
    assert "GG22 has no 'dem' files; party files: all" in err


def test_results_live_unknown_election_and_county(capsys):
    assert "unknown election 'XX99'; one of:" in fails(
        capsys, "get", "results", "--election", "XX99", "--source", "sbe"
    )
    assert "unknown county 'Atlantis'; one of:" in fails(
        capsys, "get", "results", "--election", "GG22", "--county", "Atlantis", "--source", "sbe"
    )


@needs_primary
def test_results_live_a_primary_county_without_non_partisan_contests(capsys, monkeypatch):
    missing = [
        code
        for code in results.COUNTY_CODES
        if not (GP22 / f"GP22_{code}Non-PartisanResults.csv").exists()
    ]
    if not missing:
        pytest.skip("every GP22 county has a non-partisan file")
    county = results.COUNTY_CODES[missing[0]]
    monkeypatch.setattr(results, "_download", archived(GP22))
    out = run(capsys, "get", "results", "--election", "GP22", "--county", county,
              "--source", "sbe")  # fmt: skip
    assert {r["party_file"] for r in out["rows"]} == {"dem", "rep"}
    assert len(out["meta"]["source_urls"]) == 2


# ---- turnout ---------------------------------------------------------------------------------


@needs_turnout
def test_turnout_live_parses_the_boards_current_file(capsys, monkeypatch):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: SENT[-1].read_bytes())
    out = run(capsys, "get", "turnout", "--election", "GG26", "--family", "sent-returned",
              "--source", "sbe")  # fmt: skip
    assert out["rows"] and {r["family"] for r in out["rows"]} == {"sent-returned"}
    assert out["meta"]["source"] == "sbe" and out["meta"]["official"] is False
    assert out["meta"]["as_of"]["sent-returned"] == out["rows"][0]["as_of"]


@needs_turnout
def test_turnout_live_rows_match_the_published_columns(capsys, monkeypatch):
    as_of = SENT[-1].name.split("__")[2]
    published = ROOT / "data" / "turnout" / "GG26" / "sent-returned" / "county" / f"{as_of}.csv"
    if not published.exists():
        pytest.skip("that snapshot is not parsed")
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: SENT[-1].read_bytes())
    out = run(capsys, "get", "turnout", "--election", "GG26", "--family", "sent-returned",
              "--source", "sbe")  # fmt: skip
    with published.open(newline="") as f:
        reader = csv.DictReader(f)
        header, rows = reader.fieldnames, list(reader)
    assert list(out["rows"][0]) == header and len(out["rows"]) == len(rows)


@needs_turnout
def test_turnout_live_county_filter(capsys, monkeypatch):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: SENT[-1].read_bytes())
    out = run(capsys, "get", "turnout", "--election", "GG26", "--family", "sent-returned",
              "--county", "st marys", "--source", "sbe")  # fmt: skip
    assert out["rows"] and {r["county"] for r in out["rows"]} == {"St. Mary's"}
    assert out["meta"]["filters"]["county"] == "St. Mary's"


GG22_STATS = ROOT / "archives" / "sbe" / "campaign_stats" / "GG22"
GG22_OFFICIAL = {
    "2022-12-08": GG22_STATS / "official-turnout__county__2022-12-08__725250fe1235.xlsx",
    "2026-04-29": GG22_STATS / "official-turnout__county__2026-04-29__83efd0240441.xlsx",
}


@pytest.mark.skipif(
    not all(p.exists() for p in GG22_OFFICIAL.values()), reason="GG22 official turnout not archived"
)
def test_turnout_live_skips_a_re_post_that_does_not_reconcile(capsys, monkeypatch):
    files = [
        f
        for f in turnout.ELECTIONS["GG22"].files
        if (f.family, f.geography) == ("official-turnout", "county")
    ]
    original, updated = (f.url for f in files)
    assert "Updated-" in updated and "Updated-" not in original
    by_url = {original: GG22_OFFICIAL["2022-12-08"], updated: GG22_OFFICIAL["2026-04-29"]}
    asked = []

    def fake(url, timeout=30.0):
        asked.append(url)
        return by_url[url].read_bytes()

    monkeypatch.setattr(turnout, "download", fake)
    out = run(capsys, "get", "turnout", "--election", "GG22", "--family", "official-turnout",
              "--source", "sbe")  # fmt: skip
    assert asked == [updated, original]  # the re-post is tried first
    meta = out["meta"]
    assert meta["as_of"] == {"official-turnout": "2022-12-08"}
    assert meta["source_urls"] == [original] and meta["official"] is True
    # the re-post fails the reader's own Statewide reconciliation before the
    # UNRECONCILED_SNAPSHOTS listing is consulted; either way it is a note, not silence
    assert len(meta["notes"]) == 1 and meta["notes"][0].startswith(updated)
    assert "Statewide" in meta["notes"][0]
    assert out["rows"] and {r["as_of"] for r in out["rows"]} == {"2022-12-08"}


@pytest.mark.skipif(
    not all(p.exists() for p in GG22_OFFICIAL.values()), reason="GG22 official turnout not archived"
)
def test_turnout_live_refuses_a_snapshot_the_pipeline_leaves_unparsed(capsys, monkeypatch):
    monkeypatch.setattr(
        turnout, "download", lambda url, timeout=30.0: GG22_OFFICIAL["2022-12-08"].read_bytes()
    )
    key = ("GG22", "official-turnout", "county", "2022-12-08")
    monkeypatch.setitem(turnout.UNRECONCILED_SNAPSHOTS, key, "a reason on file")
    err = fails(capsys, "get", "turnout", "--election", "GG22", "--family", "official-turnout",
                "--source", "sbe")  # fmt: skip
    assert "skipped: its 2022-12-08 report does not reconcile (a reason on file)" in err
    assert "--source skipjack" in err


def test_turnout_live_needs_a_family_and_serves_only_the_current_file(capsys):
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--source", "sbe"])
    assert "--family" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--family", "requests", "--as-of", "all",
              "--source", "sbe"])  # fmt: skip
    assert "current file" in capsys.readouterr().err


def test_turnout_live_unknown_family_and_geography(capsys):
    assert "unknown family 'nope'; one of:" in fails(
        capsys, "get", "turnout", "--election", "GG26", "--family", "nope", "--source", "sbe"
    )
    err = fails(capsys, "get", "turnout", "--election", "GG26", "--family", "requests",
                "--geography", "legislative", "--source", "sbe")  # fmt: skip
    assert "no requests/legislative report for GG26" in err


def test_turnout_live_says_when_the_board_has_not_posted_the_file(capsys, monkeypatch):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: b"<html>soon</html>")
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--family", "sent-returned",
              "--source", "sbe"])  # fmt: skip
    assert "not published" in capsys.readouterr().err


def test_turnout_live_refuses_a_certified_elections_unofficial_tally(capsys):
    err = fails(capsys, "get", "turnout", "--election", "PG24", "--family", "early-voting",
                "--source", "sbe")  # fmt: skip
    assert "PG24 is certified" in err


def test_turnout_live_a_pdf_family_without_the_pdf_reader(capsys, monkeypatch):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: b"%PDF-1.7 ...")

    def no_reader(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'natural_pdf'", name="natural_pdf")

    monkeypatch.setattr(turnout, "parse_path", no_reader)
    err = fails(capsys, "get", "turnout", "--election", "GG26", "--family", "early-voting",
                "--source", "sbe")  # fmt: skip
    assert "skipjack[pdf]" in err and "natural_pdf" in err and "--source skipjack" in err


def test_turnout_live_a_file_that_does_not_parse(capsys, monkeypatch):
    monkeypatch.setattr(turnout, "download", lambda url, timeout=30.0: b"PK not a workbook")
    err = fails(capsys, "get", "turnout", "--election", "GG26", "--family", "sent-returned",
                "--source", "sbe")  # fmt: skip
    assert "did not parse" in err and "--source skipjack" in err


def test_sbe_does_not_open_the_skipjack_source(capsys, monkeypatch):
    from skipjack.client import commands

    def refuse(*args, **kwargs):
        raise AssertionError("--source sbe opened the skipjack downloads")

    monkeypatch.setattr(commands, "SkipjackSource", refuse)
    fails(capsys, "get", "turnout", "--election", "GG26", "--source", "sbe")
