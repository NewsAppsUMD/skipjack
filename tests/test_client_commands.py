"""The data CLI end to end, against a miniature downloads/ folder."""

from __future__ import annotations

import json

import pytest

from skipjack.cli import main
from client_fixture import write_downloads


@pytest.fixture
def base(tmp_path):
    return str(write_downloads(tmp_path / "downloads"))


def run(capsys, *argv):
    main(list(argv))
    return capsys.readouterr().out


def test_list_elections(capsys, base):
    out = json.loads(run(capsys, "list", "elections", "--base-url", base))
    codes = {(r["code"], r["dataset"]) for r in out["rows"]}
    assert codes == {("GG22", "results"), ("GG26", "turnout")}


def test_list_counties_as_csv(capsys, base):
    out = run(capsys, "list", "counties", "--base-url", base, "--format", "csv")
    assert out.splitlines()[0] == "name,slug,code"
    assert "Allegany,allegany,01" in out


def test_list_turnout_families(capsys, base):
    out = json.loads(run(capsys, "list", "turnout", "GG26", "--base-url", base))
    assert {"family": "requests", "geography": "county", "snapshots": 2,
            "first": "2026-10-14", "latest": "2026-10-15"} in out["rows"]  # fmt: skip


def test_registration_defaults_to_json_with_meta(capsys, base):
    out = json.loads(run(capsys, "get", "registration", "--base-url", base))
    assert len(out["rows"]) == 4
    assert out["rows"][0]["active_voters"] == 100  # a number, not "100"
    meta = out["meta"]
    assert meta["dataset"] == "registration" and meta["table"] == "monthly"
    assert meta["source"] == "skipjack" and meta["official"] is True
    assert meta["source_urls"][0].endswith("combined/registration_monthly_all.csv")
    assert meta["rows"] == 4 and "retrieved_at" in meta


def test_registration_filters(capsys, base):
    out = json.loads(
        run(capsys, "get", "registration", "--base-url", base, "--county", "allegany",
            "--party", "dem", "--from", "2026-10")
    )  # fmt: skip
    assert [(r["report_date"], r["active_voters"]) for r in out["rows"]] == [("2026-10-31", 110)]
    assert out["meta"]["filters"] == {"county": "Allegany", "party": "DEM", "from": "2026-10"}


def test_registration_csv(capsys, base):
    out = run(capsys, "get", "registration", "--base-url", base, "--to", "2026-09",
              "--format", "csv")  # fmt: skip
    lines = out.splitlines()
    assert lines[0] == "report_date,county,party,party_name,active_voters,source_id"
    assert len(lines) == 4


def test_output_file(capsys, base, tmp_path):
    target = tmp_path / "out.json"
    assert run(capsys, "get", "registration", "--base-url", base, "-o", str(target)) == ""
    assert json.loads(target.read_text())["meta"]["dataset"] == "registration"


def test_an_unknown_county_lists_the_choices(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base, "--county", "atlantis"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "atlantis" in err and "Allegany" in err


def test_registration_cannot_come_from_the_sbe(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "registration", "--base-url", base, "--source", "sbe"])
    assert "--source skipjack" in capsys.readouterr().err


def test_registration_summary_with_a_county_is_refused(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base, "--table", "summary",
              "--county", "howard"])  # fmt: skip
    assert exit_info.value.code == 2
    assert "statewide" in capsys.readouterr().err


def test_a_table_missing_from_the_catalog_is_a_clean_error(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base, "--table", "activity"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "activity" in err and "monthly" in err


def test_the_summary_table_filters_by_party_and_month(capsys, base, tmp_path):
    summary = (
        "report_date,section,category,party,value,source_id\n"
        "2026-09,new_registration,MVA,DEM,5,sbe-vrar-2026-09\n"
        "2026-10,new_registration,MVA,DEM,6,sbe-vrar-2026-10\n"
        "2026-10,new_registration,MVA,REP,7,sbe-vrar-2026-10\n"
    )
    root = tmp_path / "downloads"
    (root / "combined" / "registration_summary_all.csv").write_text(summary)
    catalog = json.loads((root / "index.json").read_text())
    catalog["registration"]["tables"]["summary"] = "combined/registration_summary_all.csv"
    (root / "index.json").write_text(json.dumps(catalog))
    out = json.loads(
        run(capsys, "get", "registration", "--base-url", base, "--table", "summary",
            "--party", "dem", "--from", "2026-10")
    )  # fmt: skip
    assert [(r["report_date"], r["value"]) for r in out["rows"]] == [("2026-10", 6)]


def test_list_turnout_needs_an_election(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["list", "turnout", "--base-url", base])
    assert exit_info.value.code == 2
    assert "election code" in capsys.readouterr().err
