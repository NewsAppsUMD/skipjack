"""Tests for the natural-pdf VRAR parser.

Unit tests use synthetic grids. Integration tests read archived PDFs and are skipped
when a PDF is missing. Expected values are the report's own printed TOTAL figures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skipjack.pipeline.vrar import (
    Grid,
    ReconciliationError,
    VrarParseError,
    interpret_county_table,
    interpret_summary_table,
    normalize_header,
    parse_number,
    parse_vrar,
)

ARCHIVE = Path(__file__).resolve().parents[1] / "archives/sbe/voter_registration/monthly"

COUNTIES = [
    "ALLEGANY", "ANNE ARUNDEL", "BALTIMORE CITY", "BALTIMORE CO.", "CALVERT", "CAROLINE",
    "CARROLL", "CECIL", "CHARLES", "DORCHESTER", "FREDERICK", "GARRETT", "HARFORD", "HOWARD",
    "KENT", "MONTGOMERY", "PR. GEORGE'S", "QUEEN ANNE'S", "ST. MARY'S", "SOMERSET", "TALBOT",
    "WASHINGTON", "WICOMICO", "WORCESTER",
]  # fmt: skip


# --- unit ---------------------------------------------------------------------------


def test_parse_number():
    assert parse_number("1,234") == 1234
    assert parse_number(" 12 ") == 12
    assert parse_number("NA") is None
    assert parse_number("") is None
    assert parse_number("0+") == 0  # stray glyph printed in 2015-06
    with pytest.raises(VrarParseError):
        parse_number("12a")


def test_ocr_header_correction():
    assert normalize_header("HTH", ocr=True) == "OTH"
    assert normalize_header("CONF", ocr=True) == "CONF MAILING"
    with pytest.raises(VrarParseError):
        normalize_header("CHANGES", ocr=True)


def county_grid(parties: list[str], active: dict[str, int], total_override=None) -> Grid:
    header = ["", "ADDRESS", "NAME", *parties, "TOTAL", *parties, "TOTAL", "CONF MAILING",
              "INACTIVE"]  # fmt: skip
    rows = []
    for c in COUNTIES:
        vals = [str(active[p]) for p in parties]
        rows.append([c, "1", "1", *(["0"] * len(parties)), "0", *vals,
                     str(sum(active.values())), "1", "1"])  # fmt: skip
    n = len(COUNTIES)
    tot = [str(active[p] * n) for p in parties]
    grand = total_override if total_override is not None else sum(active.values()) * n
    rows.append(["TOTAL", str(n), str(n), *(["0"] * len(parties)), "0", *tot, str(grand),
                 str(n), str(n)])  # fmt: skip
    return Grid(header=header, rows=rows)


def test_unlisted_party_code_is_kept_positionally():
    # A code the parser has never seen must not be dropped (the old AME/BAR bug).
    parties = ["DEM", "REP", "ZZZ", "UNA", "OTH"]
    reg, act, checks = interpret_county_table(
        county_grid(parties, {"DEM": 10, "REP": 5, "ZZZ": 1, "UNA": 3, "OTH": 1}), "2030-01"
    )
    assert set(reg["party"]) == set(parties)
    assert checks["statewide_active_total"] == 20 * 24


def test_reconciliation_failure_raises():
    grid = county_grid(["DEM", "REP"], {"DEM": 10, "REP": 5}, total_override=999)
    with pytest.raises(ReconciliationError) as e:
        interpret_county_table(grid, "2030-01")
    assert e.value.problems == ["statewide active total: parsed 360 but report says 999"]


def test_known_discrepancy_is_accepted_and_recorded():
    grid = county_grid(["DEM", "REP"], {"DEM": 10, "REP": 5}, total_override=999)
    msg = "statewide active total: parsed 360 but report says 999"
    _, _, checks = interpret_county_table(grid, "2030-01", frozenset({msg}))
    assert checks["county_table_source_discrepancies"] == [msg]


def test_summary_table_na_and_dups():
    header = ["", "DEM", "REP", "TOTAL", "DUPS", "", "DEM", "REP", "TOTAL"]
    rows = [
        ["BY MAIL", "2", "1", "3", "4", "DEATH NOTICE", "5", "1", "6"],
        ["SOCIAL SERVICES", "NA", "NA", "0", "NA", "", "", "", ""],
        ["TOTAL", "2", "1", "3", "4", "TOTAL", "5", "1", "6"],
    ]
    df, checks = interpret_summary_table(Grid(header, rows), "2030-01")
    assert checks == {"new_registrations_total": 3, "removals_total": 6,
                      "summary_table_source_discrepancies": []}  # fmt: skip
    na = df.filter(df["category"] == "SOCIAL SERVICES")
    assert na["value"].is_null().all()
    assert df.filter((df["party"] == "DUPS") & (df["category"] == "BY MAIL"))["value"][0] == 4


# --- integration --------------------------------------------------------------------


def report(year: int, month: int, **kw):
    pdf = ARCHIVE / f"MSR-{year}_{month:02d}.pdf"
    if not pdf.exists():
        pytest.skip(f"{pdf.name} not archived")
    return parse_vrar(pdf, year, month, **kw)


def statewide(r, party: str) -> int:
    return int(r.registration.filter(r.registration["party"] == party)["active_voters"].sum())


def test_august_2026():
    r = report(2026, 8)
    assert r.extraction_method == "native"
    assert r.checks["parties"] == ["DEM", "REP", "GRN", "WCP", "UNA", "OTH"]
    assert r.checks["statewide_active_total"] == 4_322_671
    assert statewide(r, "DEM") == 2_222_557
    assert statewide(r, "UNA") == 1_005_402
    assert r.checks["new_registrations_total"] == 41_106
    assert r.checks["removals_total"] == 40_037
    mva = r.summary.filter(
        (r.summary["category"] == "MOTOR VEHICLE ADMINISTRATION") & (r.summary["party"] == "DEM")
    )
    assert mva["value"][0] == 16_722
    inactive = r.activity.filter(r.activity["measure"] == "inactive")["value"].sum()
    assert inactive == 288_388


def test_americans_elect_column_is_kept():
    r = report(2012, 6)
    assert "AME" in r.checks["parties"]
    assert r.checks["statewide_active_total"] == 3_546_094


def test_bread_and_roses_column_is_kept():
    r = report(2019, 6)
    assert "BAR" in r.checks["parties"]
    assert r.checks["statewide_active_total"] == 4_015_003
    # The printed RECRUITING row total is wrong in the source; it is a listed discrepancy.
    assert r.checks["summary_table_source_discrepancies"]


def test_source_discrepancy_fails_without_listing():
    with pytest.raises(ReconciliationError):
        report(2019, 6, known_discrepancies={})


def test_2010_uses_statewide_summary_page():
    r = report(2010, 1)
    assert r.checks["parties"] == ["DEM", "REP", "GRN", "CON", "IND", "LIB", "UNAF", "OTH"]
    assert r.checks["new_registrations_total"] == 9_913
    assert r.checks["removals_total"] == 11_970


def test_summary_table_on_first_page():
    r = report(2024, 10)  # page order is swapped in this report
    assert r.checks["counties"] == 24


@pytest.mark.slow
def test_garbled_font_report_uses_ocr():
    r = report(2025, 4)
    assert r.extraction_method == "ocr:rapidocr"
    assert r.checks["statewide_active_total"] == 4_300_831
    assert statewide(r, "DEM") == 2_231_549
    assert r.checks["new_registrations_total"] == 17_370
