"""Parse Maryland SBE monthly Voter Registration Activity Reports (VRAR) with natural-pdf.

Each report has two statewide tables:

* **County table** (page 1). One row per county plus a TOTAL row. Column groups:
  CHANGES (ADDRESS, NAME), PARTY AFFILIATION FROM (party codes + TOTAL),
  TOTAL ACTIVE REGISTRATION (party codes + TOTAL), ACTIVITY (CONF MAILING, INACTIVE).
* **Summary table** (page 2, or the last page in the 2010-era 14-page format).
  NEW REGISTRATION BY PARTY by method (party codes + TOTAL + DUPS) on the left,
  REMOVALS by reason (party codes + TOTAL) on the right.

Party codes change over time (CON, IND, AME, BAR, NLM, WCP, UNAF -> UNA ...), so
columns are read positionally from the header row rather than from an allow-list.

Most reports have a usable text layer and are read with ``Page.extract_table()``.
A few (e.g. 2025-04, 2026-01) use fonts with no Unicode mapping and a rotated page.
Those are rotated, their text layer is dropped, and natural-pdf OCR (rapidocr)
is applied; OCR words are placed into columns anchored on the header words.

Every table is reconciled against the report's own TOTAL row and TOTAL column
before anything is returned. A report that does not reconcile raises
``ReconciliationError`` instead of producing a CSV.
"""

from __future__ import annotations

import csv
import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
from natural_pdf import PDF
from natural_pdf.ocr.ocr_options import RapidOCROptions

logger = logging.getLogger(__name__)

COUNTY_NORMALIZE = {
    "ALLEGANY": "Allegany",
    "ANNE ARUNDEL": "Anne Arundel",
    "BALTIMORE CITY": "Baltimore City",
    "BALTIMORE CO.": "Baltimore County",
    "BALTIMORE CO": "Baltimore County",
    "BALTIMORE COUNTY": "Baltimore County",
    "CALVERT": "Calvert",
    "CAROLINE": "Caroline",
    "CARROLL": "Carroll",
    "CECIL": "Cecil",
    "CHARLES": "Charles",
    "DORCHESTER": "Dorchester",
    "FREDERICK": "Frederick",
    "GARRETT": "Garrett",
    "HARFORD": "Harford",
    "HOWARD": "Howard",
    "KENT": "Kent",
    "MONTGOMERY": "Montgomery",
    "PR. GEORGE'S": "Prince George's",
    "PRINCE GEORGE'S": "Prince George's",
    "QUEEN ANNE'S": "Queen Anne's",
    "ST. MARY'S": "St. Mary's",
    "SOMERSET": "Somerset",
    "TALBOT": "Talbot",
    "WASHINGTON": "Washington",
    "WICOMICO": "Wicomico",
    "WORCESTER": "Worcester",
}
N_COUNTIES = 24

# Codes seen in VRAR headers 2010-2026. Used only to correct OCR misreads and
# to warn on something new; native-text columns are never dropped for being unknown.
KNOWN_PARTY_CODES = {
    "DEM",
    "REP",
    "GRN",
    "CON",
    "IND",
    "LIB",
    "AME",
    "BAR",
    "NLM",
    "WCP",
    "UNAF",
    "UNA",
    "OTH",
}
HEADER_WORDS = KNOWN_PARTY_CODES | {"TOTAL", "DUPS", "ADDRESS", "NAME", "CONF MAILING", "INACTIVE"}

OCR_RESOLUTION = 300
# The text-angle classifier flips short digit crops 180 degrees, turning 9 into 6.
# Pages are rotated upright before OCR, so the classifier is not needed.
OCR_OPTIONS = RapidOCROptions(use_cls=False)


class VrarParseError(ValueError):
    """The report's tables could not be located or interpreted."""


class ReconciliationError(VrarParseError):
    """Parsed numbers do not add up to the report's own totals."""

    def __init__(self, message: str, problems: list[str]):
        super().__init__(message)
        self.problems = problems


# Arithmetic errors printed in the reports themselves, each verified by reading the PDF.
# The file lists the exact reconciliation message to accept for a report_date.
# Anything not listed fails the parse. Accepted items are recorded in provenance.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DISCREPANCIES_PATH = (
    PROJECT_ROOT / "data" / "voter_registration" / "source_discrepancies.csv"
)


def load_source_discrepancies(path: Path = SOURCE_DISCREPANCIES_PATH) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    out: dict[str, set[str]] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["report_date"], set()).add(row["message"])
    return out


@dataclass
class VrarReport:
    report_date: str
    extraction_method: str  # "native" or "ocr:rapidocr"
    registration: pl.DataFrame  # report_date, county, party, active_voters
    activity: pl.DataFrame  # report_date, county, measure, party, value
    summary: pl.DataFrame  # report_date, section, category, party, value
    checks: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Cell helpers
# --------------------------------------------------------------------------------------


def _clean(cell: str | None) -> str:
    return re.sub(r"\s+", " ", cell or "").strip()


def parse_number(cell: str | None) -> int | None:
    """Parse '1,234' -> 1234. Blank or 'NA' -> None. Anything else raises."""
    s = _clean(cell).replace(",", "")
    if s.endswith("+") and s[:-1].isdigit():
        # Stray glyph printed in at least one report (2015-06, "0+").
        logger.warning("Stray '+' in numeric cell %r", cell)
        s = s[:-1]
    if s == "" or s.upper() == "NA":
        return None
    if not re.fullmatch(r"-?\d+", s):
        raise VrarParseError(f"Not a number: {cell!r}")
    return int(s)


def normalize_header(token: str, *, ocr: bool) -> str:
    tok = _clean(token).upper()
    if "CONF" in tok or "MAILING" in tok:
        return "CONF MAILING"
    if tok in HEADER_WORDS or not ocr:
        if tok not in HEADER_WORDS and tok:
            logger.warning("Unrecognized header token %r (kept positionally)", tok)
        return tok
    match = difflib.get_close_matches(tok, sorted(HEADER_WORDS), n=1, cutoff=0.6)
    if not match:
        raise VrarParseError(f"OCR header token {token!r} matches no known column")
    logger.info("OCR header %r corrected to %r", token, match[0])
    return match[0]


# --------------------------------------------------------------------------------------
# Locating tables
# --------------------------------------------------------------------------------------


@dataclass
class Grid:
    """A table as a header row plus data rows, first column is the row label."""

    header: list[str]
    rows: list[list[str | None]]


def _header_index(table: list[list[str | None]], required: set[str]) -> int | None:
    """Index of the column-header row: the first row containing all ``required`` words."""
    for i, row in enumerate(table):
        if required <= {_clean(c).upper() for c in row}:
            return i
    return None


def _grid_from_native(table: list[list[str | None]], hi: int) -> Grid:
    header = [_clean(c) for c in table[hi]]
    rows = [r for r in table[hi + 1 :] if any(_clean(c) for c in r)]
    return Grid(header=header, rows=rows)


def _group_label(table: list[list[str | None]], hi: int) -> str:
    """Caption in the first column above the header row (COUNTY, SUMMARY, or a county)."""
    return next((_clean(r[0]).upper() for r in table[:hi] if _clean(r[0])), "")


def _text_layer_garbled(pdf: PDF) -> bool:
    """True when glyphs have no Unicode mapping, which pdfplumber reports as (cid:N)."""
    return any("(cid:" in (page.extract_text() or "") for page in pdf.pages)


def _find_native(pdf: PDF) -> tuple[Grid | None, Grid | None]:
    county = None
    summaries: list[tuple[str, Grid]] = []
    for page in pdf.pages:
        for result in page.extract_tables():
            table = list(result)
            hi = _header_index(table, {"ADDRESS", "NAME", "TOTAL"})
            if hi is not None and county is None:
                county = _grid_from_native(table, hi)
                continue
            hi = _header_index(table, {"DUPS", "TOTAL"})
            if hi is not None:
                summaries.append((_group_label(table, hi), _grid_from_native(table, hi)))
    # 2010-era reports also have a per-county summary page for each county; the
    # statewide table is the one captioned SUMMARY.
    statewide = [g for label, g in summaries if label == "SUMMARY"]
    if statewide:
        return county, statewide[-1]
    if len(summaries) == 1:
        return county, summaries[0][1]
    if summaries:
        raise VrarParseError(f"{len(summaries)} summary tables and none captioned SUMMARY")
    return county, None


# --- OCR path -------------------------------------------------------------------------


def _center(el) -> tuple[float, float]:
    return (el.x0 + el.x1) / 2, (el.top + el.bottom) / 2


def _ocr_page(page, markers: tuple[str, ...]):
    """Rotate, drop the unusable text layer, and OCR.

    Returns (page, marker) for the first orientation whose OCR text contains one of
    ``markers``, or (None, None).
    """
    candidates = [page.rotate(90, "clockwise"), page.rotate(90, "counterclockwise"), page]
    for candidate in candidates:
        candidate.remove_text_layer()
        candidate.apply_ocr(engine="rapidocr", resolution=OCR_RESOLUTION, options=OCR_OPTIONS)
        text = (candidate.extract_text() or "").upper()
        for marker in markers:
            if marker in text:
                return candidate, marker
    return None, None


def _grid_from_ocr(page, header_anchor: str, label_after: set[str]) -> Grid:
    """Build a Grid from OCR words.

    Columns are anchored on the words in the column-header row (the line holding
    ``header_anchor``). A label column is assumed at the far left, and after any
    header word in ``label_after`` (the REMOVALS reason column follows DUPS).
    Rows are anchored on the words in the leftmost label column.
    """
    words = [w for w in page.find_all("text") if _clean(w.text)]
    anchor = next((w for w in words if _clean(w.text).upper() == header_anchor), None)
    if anchor is None:
        raise VrarParseError(f"OCR header anchor {header_anchor!r} not found")
    row_h = anchor.bottom - anchor.top

    # Column-header words: those vertically overlapping the anchor's line, starting at
    # the leftmost exact header word (this skips row-label captions like SUMMARY).
    # Vertically stacked words such as CONF / MAILING are merged below.
    line = [w for w in words if w.top < anchor.bottom and w.bottom > anchor.top]
    exact = [w.x0 for w in line if _clean(w.text).upper() in HEADER_WORDS]
    hdr_words = sorted((w for w in line if w.x0 >= min(exact) - 1), key=lambda w: w.x0)
    cols: list[dict] = []
    for w in hdr_words:
        if cols and w.x0 < cols[-1]["x1"]:
            cols[-1]["x0"] = min(cols[-1]["x0"], w.x0)
            cols[-1]["x1"] = max(cols[-1]["x1"], w.x1)
            cols[-1]["text"] += " " + w.text
        else:
            cols.append({"x0": w.x0, "x1": w.x1, "text": w.text})
    for c in cols:
        if {"CONF", "MAILING"} <= set(c["text"].upper().split()):
            c["text"] = "CONF MAILING"
    # Insert label columns.
    spec: list[dict] = [{"label": True, "x0": float("-inf"), "x1": cols[0]["x0"] - 2}]
    for i, c in enumerate(cols):
        spec.append({"label": False, **c})
        if normalize_header(c["text"], ocr=True) in label_after and i + 1 < len(cols):
            spec.append({"label": True, "x0": c["x1"] + 2, "x1": cols[i + 1]["x0"] - 2})

    header = [""] + ["" if s["label"] else normalize_header(s["text"], ocr=True) for s in spec[1:]]

    def col_index(w) -> int | None:
        cx, _ = _center(w)
        for i, s in enumerate(spec):
            if s["label"] and s["x0"] <= cx <= s["x1"]:
                return i
        numeric = [
            (abs(cx - (s["x0"] + s["x1"]) / 2), i) for i, s in enumerate(spec) if not s["label"]
        ]
        return min(numeric)[1] if numeric else None

    body = [w for w in words if w.top > anchor.bottom + 1]
    left_labels = sorted((w for w in body if col_index(w) == 0), key=lambda w: w.top)
    row_centers: list[float] = []
    for w in left_labels:
        cy = _center(w)[1]
        if not row_centers or cy - row_centers[-1] > row_h * 0.8:
            row_centers.append(cy)
    if not row_centers:
        raise VrarParseError("OCR found no data rows")
    pitch = min((b - a for a, b in zip(row_centers, row_centers[1:])), default=row_h * 2)

    rows: list[list[list[str]]] = [[[] for _ in spec] for _ in row_centers]
    for w in body:
        cy = _center(w)[1]
        dist, r = min((abs(cy - rc), i) for i, rc in enumerate(row_centers))
        if dist > pitch / 2:
            continue
        c = col_index(w)
        if c is not None:
            rows[r][c].append(w.text)
    grid_rows = []
    for row in rows:
        cells = [" ".join(cell) for cell in row]
        grid_rows.append(
            [c if spec[i]["label"] else _fix_ocr_digits(c) for i, c in enumerate(cells)]
        )
    return Grid(header=header, rows=grid_rows)


# Glyphs OCR has been seen to substitute for digits in numeric cells. Any wrong
# substitution is caught by reconciliation against the report's totals.
_OCR_DIGIT_FIXES = str.maketrans(
    {"ε": "3", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1", ".": ","}
)


def _fix_ocr_digits(cell: str) -> str:
    fixed = cell.translate(_OCR_DIGIT_FIXES).replace(" ", "")
    if fixed != cell.replace(" ", ""):
        logger.info("OCR numeric cell %r read as %r", cell, fixed)
    return fixed


def _find_ocr(pdf: PDF) -> tuple[Grid | None, Grid | None]:
    county = summary = None
    for page in pdf.pages:
        ocr_page, marker = _ocr_page(page, ("ALLEGANY", "REMOVALS"))
        if marker == "ALLEGANY" and county is None:
            county = _grid_from_ocr(ocr_page, "ADDRESS", set())
        elif marker == "REMOVALS":
            summary = _grid_from_ocr(ocr_page, "DUPS", {"DUPS"})
    return county, summary


# --------------------------------------------------------------------------------------
# Interpreting tables
# --------------------------------------------------------------------------------------


def _split_parties(header: list[str], start: int) -> tuple[list[str], int]:
    """Party codes from ``start`` up to the next TOTAL. Returns (codes, index_of_TOTAL)."""
    try:
        end = header.index("TOTAL", start)
    except ValueError as e:
        raise VrarParseError(f"No TOTAL column after position {start} in {header}") from e
    return header[start:end], end


def _check(checks: list[str], label: str, got: int, expected: int | None) -> None:
    if expected is None:
        return
    if got != expected:
        checks.append(f"{label}: parsed {got:,} but report says {expected:,}")


def _resolve(problems: list[str], allowed: set[str], where: str) -> list[str]:
    """Raise on unexpected problems; return the accepted (known) ones."""
    unexpected = [p for p in problems if p not in allowed]
    if unexpected:
        raise ReconciliationError(f"{where}: " + "; ".join(unexpected), unexpected)
    return [p for p in problems if p in allowed]


def interpret_county_table(
    grid: Grid, report_date: str, allowed: frozenset[str] = frozenset()
) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    h = [c.upper() for c in grid.header]
    try:
        i_addr, i_name = h.index("ADDRESS"), h.index("NAME")
    except ValueError as e:
        raise VrarParseError(f"County table header missing ADDRESS/NAME: {h}") from e
    from_parties, i_from_total = _split_parties(h, i_name + 1)
    active_parties, i_active_total = _split_parties(h, i_from_total + 1)
    if from_parties != active_parties:
        raise VrarParseError(f"Party groups differ: {from_parties} vs {active_parties}")
    if not active_parties:
        raise VrarParseError("No party columns found")
    tail = h[i_active_total + 1 :]
    i_conf = i_active_total + 1 + next(i for i, t in enumerate(tail) if "CONF" in t)
    i_inactive = h.index("INACTIVE")

    registration, activity = [], []
    total_row = None
    problems: list[str] = []
    for row in grid.rows:
        label = _clean(row[0]).upper()
        if label == "TOTAL":
            total_row = row
            continue
        if label not in COUNTY_NORMALIZE:
            raise VrarParseError(f"Unrecognized county label {row[0]!r}")
        county = COUNTY_NORMALIZE[label]

        def num(i: int, *, _row=row, _c=county) -> int:
            v = parse_number(_row[i])
            if v is None:
                raise VrarParseError(f"{_c}: blank cell in column {h[i]}")
            return v

        active = {
            p: num(i_active_total - len(active_parties) + k) for k, p in enumerate(active_parties)
        }
        changes = {p: num(i_from_total - len(from_parties) + k) for k, p in enumerate(from_parties)}
        _check(problems, f"{county} active total", sum(active.values()), num(i_active_total))
        _check(
            problems, f"{county} affiliation-change total", sum(changes.values()), num(i_from_total)
        )

        for p, v in active.items():
            registration.append(
                {"report_date": report_date, "county": county, "party": p, "active_voters": v}
            )
        for measure, idx in (
            ("address_changes", i_addr),
            ("name_changes", i_name),
            ("confirmation_mailings", i_conf),
            ("inactive", i_inactive),
        ):
            activity.append(
                {
                    "report_date": report_date,
                    "county": county,
                    "measure": measure,
                    "party": None,
                    "value": num(idx),
                }
            )
        for p, v in changes.items():
            activity.append(
                {
                    "report_date": report_date,
                    "county": county,
                    "measure": "party_affiliation_changes_from",
                    "party": p,
                    "value": v,
                }
            )

    n = len({r["county"] for r in registration})
    if n != N_COUNTIES:
        raise VrarParseError(f"Expected {N_COUNTIES} counties, found {n}")
    if total_row is None:
        raise VrarParseError("County table has no TOTAL row")

    reg_df = pl.DataFrame(registration)
    act_df = pl.DataFrame(
        activity,
        schema={
            "report_date": pl.String,
            "county": pl.String,
            "measure": pl.String,
            "party": pl.String,
            "value": pl.Int64,
        },
    )
    by_party = dict(reg_df.group_by("party").agg(pl.col("active_voters").sum()).iter_rows())
    for k, p in enumerate(active_parties):
        _check(
            problems,
            f"statewide {p} active",
            by_party.get(p, 0),
            parse_number(total_row[i_active_total - len(active_parties) + k]),
        )
    statewide_total = parse_number(total_row[i_active_total])
    _check(problems, "statewide active total", int(reg_df["active_voters"].sum()), statewide_total)
    for measure, idx in (
        ("address_changes", i_addr),
        ("name_changes", i_name),
        ("confirmation_mailings", i_conf),
        ("inactive", i_inactive),
    ):
        got = int(act_df.filter(pl.col("measure") == measure)["value"].sum())
        _check(problems, f"statewide {measure}", got, parse_number(total_row[idx]))
    accepted = _resolve(problems, set(allowed), f"{report_date} county table")

    checks = {
        "parties": active_parties,
        "statewide_active_total": statewide_total,
        "counties": n,
        "county_table_source_discrepancies": accepted,
    }
    return reg_df, act_df, checks


def interpret_summary_table(
    grid: Grid, report_date: str, allowed: frozenset[str] = frozenset()
) -> tuple[pl.DataFrame, dict]:
    h = [c.upper() for c in grid.header]
    new_parties, i_new_total = _split_parties(h, 1)
    if i_new_total + 1 >= len(h) or h[i_new_total + 1] != "DUPS":
        raise VrarParseError(f"Expected DUPS after new-registration TOTAL: {h}")
    i_dups = i_new_total + 1
    i_reason = i_dups + 1
    rem_parties, i_rem_total = _split_parties(h, i_reason + 1)

    out, problems = [], []
    new_total_row = rem_total_row = None
    for row in grid.rows:
        method = _clean(row[0]).upper()
        reason = _clean(row[i_reason]).upper() if i_reason < len(row) else ""
        if method == "TOTAL":
            new_total_row = row
        elif method:
            vals = {p: parse_number(row[1 + k]) for k, p in enumerate(new_parties)}
            _check(
                problems,
                f"new registration {method}",
                sum(v or 0 for v in vals.values()),
                parse_number(row[i_new_total]),
            )
            for p, v in vals.items():
                out.append(
                    {
                        "report_date": report_date,
                        "section": "new_registration",
                        "category": method,
                        "party": p,
                        "value": v,
                    }
                )
            out.append(
                {
                    "report_date": report_date,
                    "section": "new_registration",
                    "category": method,
                    "party": "DUPS",
                    "value": parse_number(row[i_dups]),
                }
            )
        if reason == "TOTAL":
            rem_total_row = row
        elif reason:
            vals = {p: parse_number(row[i_reason + 1 + k]) for k, p in enumerate(rem_parties)}
            _check(
                problems,
                f"removal {reason}",
                sum(v or 0 for v in vals.values()),
                parse_number(row[i_rem_total]),
            )
            for p, v in vals.items():
                out.append(
                    {
                        "report_date": report_date,
                        "section": "removal",
                        "category": reason,
                        "party": p,
                        "value": v,
                    }
                )

    if new_total_row is None or rem_total_row is None:
        raise VrarParseError("Summary table is missing a TOTAL row")
    df = pl.DataFrame(
        out,
        schema={
            "report_date": pl.String,
            "section": pl.String,
            "category": pl.String,
            "party": pl.String,
            "value": pl.Int64,
        },
    )

    def col_sum(section: str, party: str) -> int:
        return int(
            df.filter((pl.col("section") == section) & (pl.col("party") == party))["value"]
            .fill_null(0)
            .sum()
        )

    for k, p in enumerate(new_parties):
        _check(
            problems,
            f"new registration total {p}",
            col_sum("new_registration", p),
            parse_number(new_total_row[1 + k]),
        )
    _check(
        problems,
        "new registration DUPS",
        col_sum("new_registration", "DUPS"),
        parse_number(new_total_row[i_dups]),
    )
    for k, p in enumerate(rem_parties):
        _check(
            problems,
            f"removal total {p}",
            col_sum("removal", p),
            parse_number(rem_total_row[i_reason + 1 + k]),
        )
    accepted = _resolve(problems, set(allowed), f"{report_date} summary table")

    checks = {
        "new_registrations_total": parse_number(new_total_row[i_new_total]),
        "removals_total": parse_number(rem_total_row[i_rem_total]),
        "summary_table_source_discrepancies": accepted,
    }
    return df, checks


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def parse_vrar(
    pdf_path: Path,
    year: int,
    month: int,
    known_discrepancies: dict[str, set[str]] | None = None,
) -> VrarReport:
    """Parse one monthly report. Raises VrarParseError / ReconciliationError on failure."""
    report_date = f"{year}-{month:02d}"
    known = load_source_discrepancies() if known_discrepancies is None else known_discrepancies
    allowed = frozenset(known.get(report_date, set()))

    def interpret(county: Grid, summary: Grid):
        return (
            interpret_county_table(county, report_date, allowed),
            interpret_summary_table(summary, report_date, allowed),
        )

    pdf = PDF(str(pdf_path))
    try:
        garbled = _text_layer_garbled(pdf)
        county, summary = (None, None) if garbled else _find_native(pdf)
    finally:
        pdf.close()

    if not garbled:
        if county is None or summary is None:
            missing = "county" if county is None else "summary"
            raise VrarParseError(f"{pdf_path.name}: {missing} table not found")
        method = "native"
        interpreted = interpret(county, summary)
    else:
        interpreted = None

    if interpreted is None:
        logger.info("%s: text layer has unmapped glyphs; using OCR", pdf_path.name)
        pdf = PDF(str(pdf_path))
        try:
            county, summary = _find_ocr(pdf)
        finally:
            pdf.close()
        if county is None or summary is None:
            raise VrarParseError(f"{pdf_path.name}: tables not found even with OCR")
        method = "ocr:rapidocr"
        interpreted = interpret(county, summary)

    (reg, act, county_checks), (summ, summary_checks) = interpreted
    return VrarReport(
        report_date=report_date,
        extraction_method=method,
        registration=reg,
        activity=act,
        summary=summ,
        checks={"reconciled": True, **county_checks, **summary_checks},
    )
