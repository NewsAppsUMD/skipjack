"""Tests for the voter file aggregation pipeline.

All data here is synthetic: fake names, a dozen made-up rows, no real voter records.
"""

from __future__ import annotations

import json
import shutil
from datetime import date

import polars as pl
import pytest

from skipjack.pipeline import voterfile as vf
from skipjack.pipeline.voterfile import (
    FIXED_COLUMNS,
    VoterFileError,
    build_metrics,
    columns_from_header,
    discover_parts,
    general_election_day,
    load_voterfile,
    parse_election,
    read_readme,
    suppress,
)

ELECTION_COLUMNS = ["07/19/2022-GP", "11/08/2022-GG", "05/14/2024-PP", "11/05/2024-PG",
                    "06/23/2026-GP"]  # fmt: skip
HISTORY = {c: f"history {c}" for c in ELECTION_COLUMNS}
SNAPSHOT = "2026-08-12"


def row(vtr, county, status, party, gender, cong, leg, birth, reg, voted=(), last="TESTLAST"):
    """One 45-field line: 38 fixed columns, 5 history columns, County, trailing tab."""
    fields = dict.fromkeys(FIXED_COLUMNS, "")
    fields.update(
        VTR_ID=str(vtr), LastName=last, FirstName="TESTFIRST", StatusCode=status, Party=party,
        Gender=gender, Congressional=cong, Legislative=leg, BirthDate=birth,
        StateRegistrationDate=reg, CountyRegistrationDate=reg,
    )  # fmt: skip
    history = [HISTORY[c] if c in voted else "" for c in ELECTION_COLUMNS]
    return "\t".join([*fields.values(), *history, county, ""])


ALL5 = tuple(ELECTION_COLUMNS)
ROWS = [
    # 1 voted everything
    row(1, "Allegany", "A", "DEM", "Female", "06", "01A", "03/15/1950", "01/01/2000", ALL5),
    # 2 voted both generals' ... 2022 and 2024 generals only
    row(
        2,
        "Allegany",
        "A",
        "REP",
        "Male",
        "06",
        "01A",
        "06/01/1960",
        "05/05/2005",
        ("11/08/2022-GG", "11/05/2024-PG"),
    ),  # fmt: skip
    # 3 unaffiliated, 2024 general only
    row(
        3,
        "Allegany",
        "A",
        "UNA",
        "Female",
        "06",
        "01B",
        "01/01/1990",
        "03/03/2010",
        ("11/05/2024-PG",),
    ),  # fmt: skip
    # 4 inactive, never voted
    row(4, "Allegany", "I", "DEM", "Male", "06", "01B", "02/02/1975", "01/01/1999"),
    # 5 registered after the 2024 general, voted in the 2026 primary
    row(
        5,
        "Saint Mary's",
        "A",
        "DEM",
        "Female",
        "05",
        "29B",
        "12/01/2000",
        "02/02/2025",
        ("06/23/2026-GP",),
    ),  # fmt: skip
    # 6 Libertarian, voted only the 2024 primary
    row(
        6,
        "Saint Mary's",
        "A",
        "OLB",
        "Male",
        "05",
        "29B",
        "04/04/1985",
        "01/01/2012",
        ("05/14/2024-PP",),
    ),  # fmt: skip
    # 7 Green, 2022 general only
    row(
        7,
        "Allegany",
        "A",
        "GRN",
        "Female",
        "06",
        "01A",
        "09/09/1970",
        "01/01/2012",
        ("11/08/2022-GG",),
    ),  # fmt: skip
    # 8 unknown birth date and blank gender
    row(8, "Allegany", "A", "UNA", "", "06", "01A", "", "03/03/2010"),
    # 9 age 17 today, 18 before the November 2026 general: eligible for the 2026 primary only
    row(9, "Allegany", "A", "DEM", "Female", "06", "01A", "11/01/2008", "03/03/2025"),
    # 10 registered at 15, 19 today: too young for every election but 2026
    row(10, "Allegany", "A", "REP", "Male", "06", "01A", "12/01/2006", "01/01/2022"),
]
MALFORMED = "9999\tSHORT\tROW"  # a fragment of a record that contained a newline


@pytest.fixture
def voter_dir(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    header = "\t".join([*FIXED_COLUMNS, *ELECTION_COLUMNS, "County", ""])
    (d / "List_Part_1-2026-08-12.txt").write_text("\n".join([header, *ROWS[:5]]) + "\n")
    (d / "List_Part_2-2026-08-12 (1).txt").write_text("\n".join([*ROWS[5:], MALFORMED]) + "\n")
    shutil.copy(d / "List_Part_2-2026-08-12 (1).txt", d / "List_Part_2-2026-08-12 (2).txt")
    (d / "List_Part_1-2026-08-12Readme.txt").write_text(
        f"Total Records :\t{len(ROWS)}\n\nUserName      :\tTest, User\n\nDate          :\t08/12/2026\n"
    )
    return d


@pytest.fixture
def loaded(voter_dir):
    readme = read_readme(voter_dir)
    parts, duplicates = discover_parts(voter_dir)
    names, elections = columns_from_header(parts)
    df, checks = load_voterfile(parts, names, elections, readme)
    return df, checks, elections, parts, duplicates


# --- discovery -----------------------------------------------------------------------


def test_read_readme(voter_dir):
    r = read_readme(voter_dir)
    assert (r.total_records, r.snapshot_date) == (len(ROWS), SNAPSHOT)


def test_identical_parts_are_deduped(loaded):
    _, _, _, parts, duplicates = loaded
    assert [p.number for p in parts] == [1, 2]
    assert [d.name for d in duplicates] == ["List_Part_2-2026-08-12 (2).txt"]
    assert [p.has_header for p in parts] == [True, False]


def test_elections_come_from_the_header(loaded):
    elections = loaded[2]
    assert [e.key for e in elections] == ["2022-GP", "2022-GG", "2024-PP", "2024-PG", "2026-GP"]
    assert [e.kind for e in elections] == ["primary", "general", "primary", "general", "primary"]
    assert elections[0].date == "2022-07-19"
    # A primary's age cutoff is that year's general election, not the primary itself.
    assert elections[0].adult_by == "2022-11-08"
    assert elections[1].adult_by == "2022-11-08"
    assert elections[4].adult_by == "2026-11-03"


def test_general_election_day():
    assert general_election_day(2022) == date(2022, 11, 8)
    assert general_election_day(2024) == date(2024, 11, 5)
    assert general_election_day(2026) == date(2026, 11, 3)


def test_unrecognized_election_column_fails():
    with pytest.raises(VoterFileError):
        parse_election("Precinct")


def test_header_mismatch_fails(voter_dir):
    p = next(voter_dir.glob("List_Part_1-2026-08-12.txt"))
    p.write_text(p.read_text().replace("LastName", "Surname", 1))
    parts, _ = discover_parts(voter_dir)
    with pytest.raises(VoterFileError):
        columns_from_header(parts)


# --- loading -------------------------------------------------------------------------


def test_load_rejects_fragment_and_reconciles(loaded):
    df, checks, *_ = loaded
    assert checks["rows_read"] == len(ROWS) + 1
    assert checks["rows_valid"] == len(ROWS) == df.height
    assert checks["rows_rejected"] == 1
    assert checks["reconciled"] is True
    assert checks["status_counts"] == {"A": 9, "I": 1}


def test_county_names_match_the_monthly_reports(loaded):
    counties = set(loaded[0]["county"].cast(pl.String))
    assert counties == {"Allegany", "St. Mary's"}


def test_unreconciled_when_readme_disagrees(voter_dir):
    (voter_dir / "List_Part_1-2026-08-12Readme.txt").write_text(
        "Total Records :\t5000\nDate :\t08/12/2026\n"
    )
    readme = read_readme(voter_dir)
    parts, _ = discover_parts(voter_dir)
    names, elections = columns_from_header(parts)
    _, checks = load_voterfile(parts, names, elections, readme)
    assert checks["reconciled"] is False


# --- aggregates ----------------------------------------------------------------------


@pytest.fixture
def metrics(loaded):
    return {m.name: m for m in build_metrics(loaded[0], loaded[2])}


def cell(metric, **where):
    df = metric.df
    for k, v in where.items():
        df = df.filter(pl.col(k) == v)
    assert df.height == 1, (where, df)
    return df.row(0, named=True)


def test_party_by_county_and_statewide(metrics):
    m = metrics["party_by_county"]
    assert cell(m, county="Allegany", party_group="ALL", status="A")["count"] == 7
    assert cell(m, county="Allegany", party_group="ALL", status="I")["count"] == 1
    assert cell(m, county="St. Mary's", party_group="OTH", status="A")["count"] == 1
    # Statewide equals the sum of counties.
    assert cell(m, county="Maryland", party_group="DEM", status="A")["count"] == 3  # 1, 5, 9
    assert cell(m, county="Maryland", party_group="ALL", status="A")["count"] == 9


def test_all_parties_row_sums_party_groups(metrics):
    m = metrics["party_by_county"].df
    for county in ("Allegany", "St. Mary's", "Maryland"):
        part = m.filter((pl.col("county") == county) & (pl.col("party_group") != "ALL"))
        total = m.filter((pl.col("county") == county) & (pl.col("party_group") == "ALL"))
        assert part["count"].sum() == total["count"].sum()


def test_turnout_counts_voters_and_eligible(metrics):
    m = metrics["turnout_by_election"]
    # Voted in the 2024 general: voters 1, 2, 3.
    row = cell(m, county="Maryland", party_group="ALL", election="2024-PG")
    assert row["count"] == 3
    # Eligible: registered by 2024-11-05 and 18+: 1,2,3,4,6,7,8. Not 5 or 9 (registered
    # later) and not 10 (too young).
    assert row["eligible"] == 7
    # The 2026 primary: voter 5 voted; everyone is eligible (voter 9 is 17 but turns 18
    # before the November 2026 general).
    row = cell(m, county="Maryland", party_group="ALL", election="2026-GP")
    assert (row["count"], row["eligible"]) == (2, 10)


def test_primary_turnout_by_party(metrics):
    row = cell(metrics["turnout_by_election"], county="Maryland", party_group="DEM",
               election="2022-GP")  # fmt: skip
    # Only voter 1 voted; DEM voters 1 and 4 were eligible (5 and 9 registered later).
    assert (row["count"], row["eligible"]) == (1, 2)


def test_age_bands_use_age_today(metrics):
    m = metrics["turnout_by_age"]
    young = cell(m, county="Maryland", age_band="Under 18", election="2026-GP")
    assert young["eligible"] == 1  # voter 9
    unknown = cell(m, county="Maryland", age_band="Unknown", election="2024-PG")
    assert unknown["eligible"] == 1  # voter 8, no birth date


def test_voter_types(metrics):
    m = metrics["voter_types"]

    def n(**kw):
        return cell(m, county="Maryland", party_group="ALL", **kw)["count"]

    assert n(eligible_elections=5, voter_type="every_election") == 1  # voter 1
    assert n(eligible_elections=5, voter_type="general_only") == 3  # voters 2, 3 and 7
    assert n(eligible_elections=1, voter_type="every_election") == 1  # voter 5
    assert n(eligible_elections=1, voter_type="none") == 2  # voters 9 and 10
    assert n(eligible_elections=5, voter_type="primary_only") == 1  # voter 6


def test_new_voters_voted(metrics):
    row = cell(metrics["new_voters_voted"], county="Maryland", party_group="ALL")
    # Registered after the 2024 general (voters 5 and 9) and eligible for the 2026 primary.
    assert (row["count"], row["eligible"]) == (1, 2)


def test_districts(metrics):
    m = metrics["districts"]
    assert cell(m, district_type="congressional", district="06", party_group="ALL",
                status="A")["count"] == 7  # fmt: skip
    assert cell(m, district_type="legislative", district="29B", party_group="ALL",
                status="A")["count"] == 2  # fmt: skip


def test_registration_cohorts_and_gender(metrics):
    assert cell(metrics["registration_cohorts"], county="Maryland", party_group="ALL",
                reg_year=2010)["count"] == 2  # fmt: skip
    g = metrics["gender"]
    assert cell(g, county="Maryland", party_group="ALL", gender="Unknown")["count"] == 1


def test_minor_party_codes_are_kept(metrics):
    row = cell(metrics["minor_parties"], county="St. Mary's", party="OLB")
    assert row["count"] == 1


# --- suppression and output ----------------------------------------------------------


def test_suppress_hides_small_nonzero_counts():
    df = pl.DataFrame({"count": [0, 1, 9, 10, 500], "eligible": [3, 12, 9, 10, 700]})
    out = suppress(df, ["count", "eligible"], 10)
    assert out["count"].to_list() == [0, None, None, 10, 500]
    assert out["eligible"].to_list() == [None, 12, None, 10, 700]


@pytest.fixture
def snapshot_run(voter_dir, tmp_path):
    out = tmp_path / "data" / "voter_file"
    checks = vf.run(
        voter_dir,
        out,
        suppress_below=2,
        manifest_path=tmp_path / "manifest.csv",
        data_dir=tmp_path / "data",
    )
    return out / SNAPSHOT, checks, tmp_path


def test_run_writes_aggregates_and_provenance(snapshot_run):
    out, checks, tmp = snapshot_run
    assert checks["rows_valid"] == len(ROWS) and checks["duplicates_skipped"] == 1
    names = {p.name for p in out.glob("*.json")}
    assert {"snapshot.json", "snapshot_provenance.json", "turnout_by_election.json",
            "voter_types.json", "districts.json"} <= names  # fmt: skip
    meta = json.loads((out / "snapshot.json").read_text())
    assert [e["key"] for e in meta["elections"]][-1] == "2026-GP"
    assert meta["limitations"] and meta["vrar_cross_check"] is None
    prov = json.loads((out / "snapshot_provenance.json").read_text())
    assert prov["source_id"] == f"sbe-voterfile-{SNAPSHOT}"
    assert prov["checks"]["reconciled"] is True
    assert len(prov["checks"]["parts"]) == 2
    assert prov["checks"]["duplicate_parts_skipped"] == ["part 2"]
    assert [part["part"] for part in prov["checks"]["parts"]] == [1, 2]
    assert "List_Part" not in json.dumps(prov)  # file names are not recorded
    manifest = (tmp / "manifest.csv").read_text()
    assert manifest.count("sbe-voterfile-2026-08-12-part-") == 2


def test_outputs_contain_no_personal_data(snapshot_run):
    out, *_ = snapshot_run
    text = "".join(p.read_text() for p in out.glob("*.json"))
    for forbidden in ("TESTLAST", "TESTFIRST", "VTR_ID", "BirthDate", "Test, User"):
        assert forbidden not in text
    assert "9999" not in text  # the malformed row's first field


def test_small_cells_are_null_in_output(snapshot_run):
    out, *_ = snapshot_run
    rows = json.loads((out / "party_by_county.json").read_text())["rows"]
    cell_ = next(r for r in rows if r["county"] == "St. Mary's" and r["party_group"] == "OTH")
    assert cell_["count"] is None  # one voter, below the threshold of 2
    allegany = next(r for r in rows if r["county"] == "Allegany" and r["party_group"] == "ALL"
                    and r["status"] == "A")  # fmt: skip
    assert allegany["count"] == 7


def test_existing_snapshot_needs_force(voter_dir, snapshot_run):
    out, _, tmp = snapshot_run
    with pytest.raises(VoterFileError, match="--force"):
        vf.run(voter_dir, out.parent, manifest_path=tmp / "manifest.csv", data_dir=tmp / "data")
    vf.run(voter_dir, out.parent, force=True, suppress_below=2,
           manifest_path=tmp / "manifest.csv", data_dir=tmp / "data")  # fmt: skip
    assert (tmp / "manifest.csv").read_text().count("-part-01") == 1  # not duplicated


def test_refuses_to_write_inside_the_input_directory(voter_dir, tmp_path):
    with pytest.raises(VoterFileError, match="inside"):
        vf.run(voter_dir, voter_dir / "out", manifest_path=tmp_path / "m.csv")


def test_unreconciled_file_is_not_written(voter_dir, tmp_path):
    (voter_dir / "List_Part_1-2026-08-12Readme.txt").write_text(
        "Total Records :\t99999\nDate :\t08/12/2026\n"
    )
    out = tmp_path / "out"
    with pytest.raises(VoterFileError, match="does not match"):
        vf.run(voter_dir, out, manifest_path=tmp_path / "m.csv")
    assert not out.exists()


def test_build_db_loads_snapshot(snapshot_run, tmp_path):
    from skipjack.pipeline.build_db import build

    out, *_ = snapshot_run
    import sqlite3

    db = build(tmp_path / "test.db", data_dir=tmp_path / "data")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT snapshot_date FROM voter_file_snapshots").fetchone() == (SNAPSHOT,)
    n = conn.execute("SELECT COUNT(*) FROM voter_file_counts").fetchone()[0]
    assert n > 100
    assert conn.execute(
        "SELECT count, eligible FROM voter_file_counts WHERE metric = 'turnout_by_election' "
        "AND county = 'Maryland' AND party_group = 'ALL' AND election = '2024-PG'"
    ).fetchone() == (3, 7)
    assert conn.execute(
        "SELECT extraction_method FROM sources WHERE source_id = ?", (f"sbe-voterfile-{SNAPSHOT}",)
    ).fetchone() == ("polars",)


def test_cli(voter_dir, tmp_path, monkeypatch, capsys):
    from skipjack.cli import main

    monkeypatch.setattr(vf, "MANIFEST_PATH", tmp_path / "manifest.csv")
    monkeypatch.setattr(vf, "DATA_DIR", tmp_path / "data")
    main(
        [
            "voterfile",
            "--path",
            str(voter_dir),
            "--out",
            str(tmp_path / "o"),
            "--suppress-below",
            "2",
        ]
    )
    assert "Wrote 13 metrics" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        main(["voterfile", "--path", str(voter_dir), "--out", str(tmp_path / "o")])
    assert e.value.code == 1


def test_numeric_dimensions_are_numbers_in_json(snapshot_run):
    out, *_ = snapshot_run
    cohorts = json.loads((out / "registration_cohorts.json").read_text())["rows"]
    assert all(isinstance(r["reg_year"], int) for r in cohorts)
    types = json.loads((out / "voter_types.json").read_text())["rows"]
    assert all(isinstance(r["eligible_elections"], int) for r in types)


def test_electorate_coverage_compares_with_monthly_reports(voter_dir, tmp_path):
    root = tmp_path / "data" / "voter_registration"
    (root / "monthly").mkdir(parents=True)
    (root / "activity").mkdir(parents=True)
    (root / "monthly" / "2024-11.csv").write_text(
        "report_date,county,party,active_voters,party_name\n"
        "2024-11,Allegany,DEM,8,Democratic\n2024-11,St. Mary's,DEM,2,Democratic\n"
    )
    (root / "activity" / "2024-11.csv").write_text(
        "report_date,county,measure,party,value\n"
        "2024-11,Allegany,inactive,,2\n2024-11,St. Mary's,inactive,,0\n"
    )
    out = tmp_path / "data" / "voter_file"
    vf.run(voter_dir, out, suppress_below=2, manifest_path=tmp_path / "m.csv",
           data_dir=tmp_path / "data")  # fmt: skip
    coverage = json.loads((out / SNAPSHOT / "snapshot.json").read_text())["electorate_coverage"]
    assert set(coverage) == {"2024-PG"}  # only election months that have a report
    assert coverage["2024-PG"]["Maryland"] == {"reported": 12, "in_file": 7, "pct": 58.3}
    assert coverage["2024-PG"]["counties"]["Allegany"] == {
        "reported": 10,
        "in_file": 6,
        "pct": 60.0,
    }


def test_first_limitation_says_these_are_not_official_turnout(snapshot_run):
    out, *_ = snapshot_run
    first = json.loads((out / "snapshot.json").read_text())["limitations"][0]
    assert "not official turnout" in first
    assert "election day" in first


# --- an older export: one file, no readme, an election that had not happened yet ------------

OLD_COLUMNS = ["11/08/2016-PG", "11/03/2020-PG", "11/05/2024-PG"]
OLD_SNAPSHOT = "2024-09-11"


def old_row(vtr, county, party, birth, reg, voted=()):
    fields = dict.fromkeys(FIXED_COLUMNS, "")
    fields.update(
        VTR_ID=str(vtr), LastName="TESTLAST", FirstName="TESTFIRST", StatusCode="A", Party=party,
        Gender="Female", Congressional="05", Legislative="01A", BirthDate=birth,
        StateRegistrationDate=reg, CountyRegistrationDate=reg,
    )  # fmt: skip
    history = [f"history {c}" if c in voted else "" for c in OLD_COLUMNS]
    return "\t".join([*fields.values(), *history, county, ""])


@pytest.fixture
def old_file(tmp_path):
    path = tmp_path / "old" / "export.txt"
    path.parent.mkdir()
    header = "\t".join([*FIXED_COLUMNS, *OLD_COLUMNS, "County", ""])
    rows = [
        old_row(
            1, "Allegany", "DEM", "03/15/1950", "01/01/2000", ("11/08/2016-PG", "11/03/2020-PG")
        ),
        old_row(2, "Allegany", "OGRN", "06/01/1960", "05/05/2005", ("11/03/2020-PG",)),
        old_row(3, "Saint Mary's", "OWCP", "01/01/1990", "03/03/2010"),
        old_row(4, "Saint Mary's", "UNA", "02/02/1975", "08/20/2024"),
    ]
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def run_old(old_file, tmp_path, **kwargs):
    return vf.run(
        old_file,
        tmp_path / "data" / "voter_file",
        suppress_below=1,
        manifest_path=tmp_path / "manifest.csv",
        data_dir=tmp_path / "data",
        **kwargs,
    )


def test_a_single_file_has_no_date_unless_given(old_file, tmp_path):
    with pytest.raises(VoterFileError, match="snapshot-date"):
        run_old(old_file, tmp_path)


def test_a_single_file_becomes_part_one(old_file):
    parts, duplicates = discover_parts(old_file)
    assert [(p.number, p.has_header) for p in parts] == [(1, True)] and duplicates == []


def test_an_export_made_before_an_election_leaves_that_election_out(old_file, tmp_path):
    checks = run_old(old_file, tmp_path, snapshot_date=OLD_SNAPSHOT)
    assert checks["reconciled"] and checks["rows_valid"] == 4
    assert checks["readme_total_records"] is None
    assert checks["elections_not_yet_held"] == ["2024-PG"]
    assert checks["latest_registration_date"] == "2024-08-20"
    out = tmp_path / "data" / "voter_file" / OLD_SNAPSHOT
    meta = json.loads((out / "snapshot.json").read_text())
    assert [e["key"] for e in meta["elections"]] == ["2016-PG", "2020-PG"]
    assert meta["records"]["latest_registration_date"] == "2024-08-20"
    notes = " ".join(meta["limitations"])
    assert "without a readme" in notes and "2024-PG" in notes
    turnout = json.loads((out / "turnout_by_election.json").read_text())["rows"]
    assert {r["election"] for r in turnout} == {"2016-PG", "2020-PG"}
    prov = json.loads((out / "snapshot_provenance.json").read_text())
    assert prov["checks"]["reconciled"] is True and "export.txt" not in json.dumps(prov)


def test_the_older_green_and_working_class_codes_join_their_groups(old_file, tmp_path):
    run_old(old_file, tmp_path, snapshot_date=OLD_SNAPSHOT)
    rows = json.loads(
        (tmp_path / "data/voter_file" / OLD_SNAPSHOT / "party_by_county.json").read_text()
    )["rows"]
    statewide = {r["party_group"]: r["count"] for r in rows if r["county"] == "Maryland"}
    assert statewide["GRN"] == 1 and statewide["WCP"] == 1 and statewide.get("OTH", 0) == 0


def test_a_snapshot_before_every_election_is_refused(old_file, tmp_path):
    with pytest.raises(VoterFileError, match="No election"):
        run_old(old_file, tmp_path, snapshot_date="2010-01-01")
