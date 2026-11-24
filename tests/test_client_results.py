"""get results, against the miniature downloads/ folder."""

from __future__ import annotations

import json

import pytest

from skipjack.cli import main

# Not tests.client_fixture: another project's `tests` package on PYTHONPATH can shadow ours.
from client_fixture import run


def test_a_results_csv_missing_the_office_column_names_the_file(capsys, base, tmp_path):
    (tmp_path / "downloads" / "results" / "GG22" / "county" / "14-howard-all.csv").write_text(
        "election,county\nGG22,Howard\n"
    )
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--office", "gov"])
    assert exit_info.value.code == 2
    assert "14-howard-all.csv has no 'office' column" in capsys.readouterr().err


def test_results_for_an_election(capsys, base):
    out = json.loads(run(capsys, "get", "results", "--election", "gg22", "--base-url", base))
    assert len(out["rows"]) == 3
    assert out["rows"][1]["district"] == "06" and out["rows"][1]["total_votes"] == 50
    assert out["rows"][0]["total_against"] is None
    meta = out["meta"]
    assert meta["election"] == "GG22" and meta["official"] is True
    assert meta["election_day"] == "2022-11-08" and len(meta["source_urls"]) == 2


def test_results_filters_by_county_and_office(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--county", "Howard", "--office", "governor")
    )  # fmt: skip
    assert [r["county"] for r in out["rows"]] == ["Howard"]
    assert len(out["meta"]["source_urls"]) == 1  # only Howard's file was read


def test_results_unknown_election_lists_the_codes(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "XX99", "--base-url", base])
    assert "GG22" in capsys.readouterr().err


def test_results_party_file_all_keeps_both_files(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--party-file", "all")
    )  # fmt: skip
    assert len(out["rows"]) == 3 and len(out["meta"]["source_urls"]) == 2
    assert out["meta"]["filters"] == {"party_file": "all"}


def test_results_party_file_with_no_files_names_the_party_files(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--party-file", "dem"])
    assert exit_info.value.code == 2
    assert "GG22 has no 'dem' files; party files: all" in capsys.readouterr().err


def test_results_party_file_with_a_county_and_no_files(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base,
              "--party-file", "rep", "--county", "Howard"])  # fmt: skip
    assert exit_info.value.code == 2
    assert "GG22 has no 'rep' files for Howard; party files: all" in capsys.readouterr().err


def test_results_office_matching_nothing_is_empty(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--office", "dogcatcher")
    )  # fmt: skip
    assert out["rows"] == []


def test_results_from_the_sbe_is_not_yet_offered(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--source", "sbe"])
    assert exit_info.value.code == 2
    assert "later version" in capsys.readouterr().err


def _add_unofficial(root):
    """A live unofficial count: no party suffix on its file names, and no winner column."""
    path = "results_unofficial/GG26/2026-11-17T111403/county/15-kent.csv"
    (root / path).parent.mkdir(parents=True)
    (root / path).write_text(
        "election,county,office,district,candidate,party,early,total\n"
        "GG26,Kent,Governor / Lt. Governor,,Wes Moore and Aruna Miller,DEM,10,100\n"
    )
    catalog = json.loads((root / "index.json").read_text())
    catalog["counties"].append({"name": "Kent", "slug": "kent", "code": "15"})
    catalog["results"]["GG26"] = {
        "name": "2026 general election", "election_day": "2026-11-03", "kind": "general",
        "official": False, "as_of": "2026-11-17T111403", "files": [path],
    }  # fmt: skip
    (root / "index.json").write_text(json.dumps(catalog))


def test_results_unofficial_count_by_county(capsys, base, tmp_path):
    _add_unofficial(tmp_path / "downloads")
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG26", "--base-url", base, "--county", "kent")
    )
    assert [(r["candidate"], r["total"]) for r in out["rows"]] == [
        ("Wes Moore and Aruna Miller", 100)
    ]
    assert out["meta"]["official"] is False and out["meta"]["as_of"] == "2026-11-17T111403"


def test_results_unofficial_count_has_no_party_files(capsys, base, tmp_path):
    _add_unofficial(tmp_path / "downloads")
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG26", "--base-url", base, "--party-file", "dem"])
    assert exit_info.value.code == 2
    assert "no party files" in capsys.readouterr().err


def test_results_office_filter_survives_a_short_row(capsys, base, tmp_path):
    # csv.DictReader fills a short row's missing fields with None
    (tmp_path / "downloads" / "results" / "GG22" / "county" / "14-howard-all.csv").write_text(
        "election,county,office\nGG22,Howard\n"
    )
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--county", "howard", "--office", "gov")
    )  # fmt: skip
    assert out["rows"] == []
