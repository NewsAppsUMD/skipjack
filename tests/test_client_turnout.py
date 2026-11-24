"""get turnout, against the miniature downloads/ folder."""

from __future__ import annotations

import json

import pytest

from skipjack.cli import main

# Not tests.client_fixture: another project's `tests` package on PYTHONPATH can shadow ours.
from client_fixture import run


def test_turnout_defaults_to_the_latest_snapshot_of_every_family(capsys, base):
    out = json.loads(run(capsys, "get", "turnout", "--election", "GG26", "--base-url", base))
    assert {(r["family"], r["as_of"]) for r in out["rows"]} == {
        ("requests", "2026-10-15"),
        ("sent-returned", "2026-10-15"),
    }
    assert out["meta"]["official"] is False  # GG26 is not certified
    assert out["meta"]["as_of"] == {"requests": "2026-10-15", "sent-returned": "2026-10-15"}


def test_turnout_one_family_every_snapshot_one_county(capsys, base):
    out = json.loads(
        run(capsys, "get", "turnout", "--election", "GG26", "--family", "requests",
            "--as-of", "all", "--county", "Allegany", "--base-url", base)
    )  # fmt: skip
    assert [(r["as_of"], r["value"]) for r in out["rows"]] == [("2026-10-14", 5), ("2026-10-15", 7)]
    assert out["meta"]["as_of"] == {"requests": ["2026-10-14", "2026-10-15"]}
    assert out["meta"]["filters"] == {"family": "requests", "geography": "county",
                                      "county": "Allegany"}  # fmt: skip


def test_turnout_a_date_without_a_family_keeps_the_families_that_have_it(capsys, base):
    out = json.loads(
        run(capsys, "get", "turnout", "--election", "GG26", "--as-of", "2026-10-14",
            "--base-url", base)
    )  # fmt: skip
    assert {r["family"] for r in out["rows"]} == {"requests"}
    assert out["meta"]["as_of"] == {"requests": "2026-10-14"}


def test_turnout_a_date_no_family_has(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "turnout", "--election", "GG26", "--as-of", "2026-01-01", "--base-url", base])
    assert exit_info.value.code == 2
    assert "no GG26 snapshot dated 2026-01-01" in capsys.readouterr().err


def test_turnout_a_missing_snapshot_date_lists_the_dates(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--family", "requests",
              "--as-of", "2026-01-01", "--base-url", base])  # fmt: skip
    assert "2026-10-15" in capsys.readouterr().err


def test_turnout_unknown_family_and_geography(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--family", "nope", "--base-url", base])
    assert "sent-returned" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--geography", "legislative",
              "--base-url", base])  # fmt: skip
    assert "county" in capsys.readouterr().err


@pytest.mark.parametrize("as_of", ["yesterday", "2026-10", "2026-13-01", "10/15/2026"])
def test_turnout_as_of_must_be_latest_all_or_a_date(capsys, base, as_of):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "turnout", "--election", "GG26", "--as-of", as_of, "--base-url", base])
    assert exit_info.value.code == 2
    assert "latest, all or a date" in capsys.readouterr().err


def _add_district_turnout(root):
    """sent-returned by legislative district, as the board publishes it: no county column value."""
    path = root / "turnout/GG26/sent-returned/legislative/2026-10-15.csv"
    path.parent.mkdir(parents=True)
    path.write_text(
        "as_of,election,family,geography,county,district,category,party,measure,value\n"
        '2026-10-15,GG26,sent-returned,legislative,"",Legislative District Code 01A,ALL,DEM,sent,9\n'
    )
    catalog = json.loads((root / "index.json").read_text())
    catalog["turnout"]["GG26"]["families"]["sent-returned"]["legislative"] = ["2026-10-15"]
    (root / "index.json").write_text(json.dumps(catalog))


def test_turnout_by_district(capsys, base, tmp_path):
    _add_district_turnout(tmp_path / "downloads")
    out = json.loads(
        run(capsys, "get", "turnout", "--election", "GG26", "--geography", "legislative",
            "--base-url", base)
    )  # fmt: skip
    assert [r["value"] for r in out["rows"]] == [9]


def test_turnout_county_at_a_geography_without_counties_is_refused(capsys, base, tmp_path):
    _add_district_turnout(tmp_path / "downloads")
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "turnout", "--election", "GG26", "--geography", "legislative",
              "--county", "Allegany", "--base-url", base])  # fmt: skip
    assert exit_info.value.code == 2
    assert "reports have no county column values; drop --county" in capsys.readouterr().err


def test_a_turnout_csv_missing_the_county_column_names_the_file(capsys, base, tmp_path):
    (tmp_path / "downloads" / "turnout/GG26/requests/county/2026-10-15.csv").write_text(
        "as_of,value\n2026-10-15,7\n"
    )
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "turnout", "--election", "GG26", "--family", "requests",
              "--county", "Allegany", "--base-url", base])  # fmt: skip
    assert exit_info.value.code == 2
    assert "requests/county/2026-10-15.csv has no 'county' column" in capsys.readouterr().err


def test_turnout_county_refusal_comes_from_the_first_file(capsys, base, tmp_path):
    # A second snapshot in the catalog with no file: refusing first means it is never read
    root = tmp_path / "downloads"
    _add_district_turnout(root)
    catalog = json.loads((root / "index.json").read_text())
    catalog["turnout"]["GG26"]["families"]["sent-returned"]["legislative"].append("2026-10-16")
    (root / "index.json").write_text(json.dumps(catalog))
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "turnout", "--election", "GG26", "--geography", "legislative",
              "--as-of", "all", "--county", "Allegany", "--base-url", base])  # fmt: skip
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "no county column values" in err and "not found" not in err


def test_turnout_county_on_a_family_with_no_rows_is_empty_not_refused(capsys, base, tmp_path):
    root = tmp_path / "downloads"
    _add_district_turnout(root)
    (root / "turnout/GG26/sent-returned/legislative/2026-10-15.csv").write_text(
        "as_of,election,family,geography,county,district,category,party,measure,value\n"
    )
    out = json.loads(
        run(capsys, "get", "turnout", "--election", "GG26", "--geography", "legislative",
            "--county", "Allegany", "--base-url", base)
    )  # fmt: skip
    assert out["rows"] == []


def test_turnout_a_family_without_the_geography_lists_its_own(capsys, base, tmp_path):
    _add_district_turnout(tmp_path / "downloads")  # only sent-returned has legislative
    with pytest.raises(SystemExit):
        main(["get", "turnout", "--election", "GG26", "--family", "requests",
              "--geography", "legislative", "--base-url", base])  # fmt: skip
    err = capsys.readouterr().err
    assert err.rstrip().endswith("geographies: county")
