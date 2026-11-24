"""The data CLI end to end: list, registration, and run's output and errors."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

from skipjack.cli import main

# Not tests.client_fixture: another project's `tests` package on PYTHONPATH can shadow ours.
from client_fixture import run


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


def test_a_malformed_catalog_is_a_clean_error(capsys, base, tmp_path):
    (tmp_path / "downloads" / "index.json").write_text('{"version": 1}')
    with pytest.raises(SystemExit) as exit_info:
        main(["list", "counties", "--base-url", base])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "'counties'" in err and "Traceback" not in err
    assert "SKIPJACK_DEBUG=1" in err


def test_skipjack_debug_lets_the_traceback_through(base, tmp_path, monkeypatch):
    (tmp_path / "downloads" / "index.json").write_text('{"version": 1}')
    monkeypatch.setenv("SKIPJACK_DEBUG", "1")
    with pytest.raises(KeyError, match="counties"):
        main(["list", "counties", "--base-url", base])


def test_a_csv_missing_a_column_is_a_clean_error(capsys, base, tmp_path):
    (tmp_path / "downloads" / "combined" / "registration_monthly_all.csv").write_text(
        "county,party\nAllegany,DEM\n"
    )
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "registration_monthly_all.csv has no 'report_date' column" in err


def test_output_into_a_missing_folder(capsys, base, tmp_path):
    target = tmp_path / "new" / "deeper" / "out.csv"
    run(capsys, "get", "registration", "--base-url", base, "--format", "csv", "-o", str(target))
    assert target.read_text().startswith("report_date,")


def test_output_that_cannot_be_written(capsys, base, tmp_path):
    target = tmp_path / "a-folder"
    target.mkdir()
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base, "-o", str(target)])
    assert exit_info.value.code == 2
    assert "could not write" in capsys.readouterr().err


def test_a_closed_pipe_exits_quietly(base):
    # The reader stops after one line; the CLI should neither trace back nor fail.
    script = (
        "import sys; from skipjack.cli import main; "
        "main(['get', 'registration', '--base-url', sys.argv[1], '--format', 'csv'])"
    )
    big = [f"2026-09-30,Allegany,DEM,Democratic,{i},x\n" for i in range(200_000)]
    path = f"{base}/combined/registration_monthly_all.csv"
    with open(path, "a") as f:
        f.writelines(big)
    pipeline = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(base)} | head -1"
    )
    # pipefail: the pipeline's status is the CLI's own, not head's
    proc = subprocess.run(
        ["bash", "-o", "pipefail", "-c", pipeline],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("report_date,")
    assert "Traceback" not in proc.stderr and "BrokenPipe" not in proc.stderr


def test_a_render_error_is_a_clean_error(capsys, base, tmp_path):
    root = tmp_path / "downloads"
    catalog = json.loads((root / "index.json").read_text())
    catalog["counties"][0]["aliases"] = ["Alleghany"]
    (root / "index.json").write_text(json.dumps(catalog))
    with pytest.raises(SystemExit) as exit_info:
        main(["list", "counties", "--base-url", base, "--format", "csv"])
    assert exit_info.value.code == 2
    assert "nested values" in capsys.readouterr().err


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "202610", "Oct 2026"])
def test_from_and_to_must_be_months(capsys, base, month):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base, "--from", month])
    assert exit_info.value.code == 2
    assert "2026-10" in capsys.readouterr().err


@pytest.mark.parametrize("what", ["elections", "counties"])
def test_only_list_turnout_takes_an_election(capsys, base, what):
    with pytest.raises(SystemExit) as exit_info:
        main(["list", what, "GG22", "--base-url", base])
    assert exit_info.value.code == 2
    assert "only `list turnout`" in capsys.readouterr().err


@pytest.mark.parametrize("given", ["St Marys", "st. mary's", "ST-MARYS", "St. Mary\u2019s"])
def test_county_names_match_loosely(capsys, base, tmp_path, given):
    path = tmp_path / "downloads" / "combined" / "registration_monthly_all.csv"
    path.write_text(path.read_text() + "2026-10-31,St. Mary's,DEM,Democratic,5,x\n")
    out = json.loads(run(capsys, "get", "registration", "--base-url", base, "--county", given))
    assert [r["county"] for r in out["rows"]] == ["St. Mary's"]


def test_a_filter_column_missing_names_the_file_and_column(capsys, base, tmp_path):
    (tmp_path / "downloads" / "combined" / "registration_monthly_all.csv").write_text(
        "report_date,county\n2026-09-30,Allegany\n"
    )
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "registration", "--base-url", base, "--party", "dem"])
    assert exit_info.value.code == 2
    assert "registration_monthly_all.csv has no 'party' column" in capsys.readouterr().err
