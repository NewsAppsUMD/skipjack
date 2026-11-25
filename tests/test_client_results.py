"""get results, against the miniature downloads/ folder."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

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


# ---- --geography precinct ----------------------------------------------------------------------


def test_precinct_results_for_a_county(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "Allegany")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-001", "001-002"]
    assert out["rows"][0]["total_votes"] == 4000 and out["rows"][0]["congressional"] == "06"
    assert out["meta"]["filters"] == {"county": "Allegany", "geography": "precinct"}
    assert out["meta"]["source_urls"][0].endswith("01-allegany-all.csv.gz")
    assert "notes" not in out["meta"]  # 2022: every vote type


def test_precinct_results_before_2020_say_they_are_election_night_votes(capsys, base, tmp_path):
    # the catalog gives the election day; an election through 2018 gets the note
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    catalog["results"]["GG22"]["election_day"] = "2018-11-06"
    index.write_text(json.dumps(catalog))
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "Allegany")
    )  # fmt: skip
    assert out["meta"]["notes"] == [
        "the board's precinct files for 2010-2018 carry election-night votes only, so "
        "total_votes and total_against are election-night votes"
    ]
    county = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base)
    )  # the county files carry every vote type
    assert "notes" not in county["meta"]


def test_one_precinct_matches_loosely(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "Allegany", "--precinct", "1-2")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-002"]
    assert out["meta"]["filters"]["precinct"] == "001-002"


def test_precinct_results_with_the_party_file_filter(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "Allegany", "--party-file", "all")
    )  # fmt: skip
    assert len(out["rows"]) == 2
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Allegany", "--party-file", "dem"])  # fmt: skip
    assert "GG22 has no 'dem' files for Allegany; party files: all" in capsys.readouterr().err


def test_every_countys_precincts_need_an_output_file(capsys, base, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        main(
            ["get", "results", "--election", "GG22", "--base-url", base, "--geography", "precinct"]
        )
    assert exit_info.value.code == 2
    assert "add --county, or -o FILE" in capsys.readouterr().err
    out = tmp_path / "all.json"
    main(["get", "results", "--election", "GG22", "--base-url", base, "--geography", "precinct",
          "-o", str(out)])  # fmt: skip
    assert len(json.loads(out.read_text())["rows"]) == 2


def test_a_county_without_a_precinct_file_says_so(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Howard"])  # fmt: skip
    assert exit_info.value.code == 2
    assert "GG22 has no precinct files for Howard on this site" in capsys.readouterr().err


def test_precinct_needs_the_precinct_geography(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG22", "--base-url", base, "--precinct", "001-001"])
    assert "--precinct needs --geography precinct" in capsys.readouterr().err


def test_a_bad_precinct_is_refused(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Allegany", "--precinct", "north"])  # fmt: skip
    assert "'north' is not a precinct like 001-012" in capsys.readouterr().err


def test_a_catalog_without_precinct_files_says_so(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    del catalog["results"]["GG22"]["precinct_files"]
    index.write_text(json.dumps(catalog))
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Allegany"])  # fmt: skip
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "GG22 has no precinct files on this site" in err
    assert "Traceback" not in err


def test_the_unofficial_count_has_no_precinct_files(capsys, base, tmp_path):
    _add_unofficial(tmp_path / "downloads")
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG26", "--base-url", base, "--geography",
              "precinct", "--county", "Kent"])  # fmt: skip
    assert "GG26's unofficial count has no precinct files" in capsys.readouterr().err


def test_precinct_needs_a_county(capsys, base, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--precinct", "001-001", "-o", str(tmp_path / "x.json")])  # fmt: skip
    assert exit_info.value.code == 2
    assert "--precinct needs --county: precinct numbers repeat across counties" in (
        capsys.readouterr().err
    )


def test_a_many_file_precinct_pull_says_what_it_reads(capsys, base, tmp_path):
    root = Path(base)
    index = root / "index.json"
    catalog = json.loads(index.read_text())
    second = "results/GG22/precinct/02-anne-arundel-all.csv.gz"
    catalog["results"]["GG22"]["precinct_files"].append(second)
    index.write_text(json.dumps(catalog))
    (root / second).write_bytes(
        (root / catalog["results"]["GG22"]["precinct_files"][0]).read_bytes()
    )
    out = tmp_path / "all.json"
    main(["get", "results", "--election", "GG22", "--base-url", base, "--geography", "precinct",
          "-o", str(out)])  # fmt: skip
    captured = capsys.readouterr()
    assert "reading results/GG22/precinct/01-allegany-all.csv.gz (1 of 2)" in captured.err
    assert "reading results/GG22/precinct/02-anne-arundel-all.csv.gz (2 of 2)" in captured.err
    assert "reading" not in captured.out
    assert len(json.loads(out.read_text())["rows"]) == 4


def test_one_precinct_file_reads_quietly(capsys, base):
    run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
        "--geography", "precinct", "--county", "Allegany")  # fmt: skip
    assert "reading" not in capsys.readouterr().err


def test_a_corrupt_precinct_file_fails_cleanly(capsys, base):
    path = Path(base) / "results/GG22/precinct/01-allegany-all.csv.gz"
    good = gzip.compress(gzip.decompress(path.read_bytes()) * 50)
    path.write_bytes(good[:10] + bytes(b ^ 0xFF for b in good[10:-8]) + good[-8:])
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Allegany"])  # fmt: skip
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "could not decompress" in err and "Traceback" not in err


def test_election_night_only_follows_the_registry():
    from skipjack.client.helpers import election_night_only
    from skipjack.pipeline.results import RESULTS_ELECTIONS

    # the downloads path's year rule and the pipeline's flag agree for every election
    for e in RESULTS_ELECTIONS.values():
        if e.precincts:
            assert election_night_only(e.year) == (not e.precinct_every_vote_type), e.code
    assert election_night_only("") is False and election_night_only(None) is False


# ---- get questions ---------------------------------------------------------------------------


def test_questions_by_county(capsys, base):
    out = json.loads(run(capsys, "get", "questions", "--election", "gg22", "--base-url", base))
    assert out["rows"] == [
        {"election": "GG22", "county": "Allegany", "question": "1", "how_it_runs": "State",
         "winner": 1, "early_votes": 3000, "early_against": 1000, "total_votes": 3000,
         "total_against": 1000}
    ]  # fmt: skip
    meta = out["meta"]
    assert meta["dataset"] == "questions" and meta["election"] == "GG22"
    assert meta["source_urls"][0].endswith("questions/county/01-allegany.csv")
    assert "notes" not in meta


def test_questions_by_precinct(capsys, base):
    out = json.loads(
        run(capsys, "get", "questions", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "allegany")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-001", "001-002"]
    assert out["meta"]["filters"] == {"county": "Allegany", "geography": "precinct"}
    assert out["meta"]["source_urls"][0].endswith("questions/precinct/01-allegany.csv.gz")
    assert "notes" not in out["meta"]  # 2022: every vote type
    out = json.loads(
        run(capsys, "get", "questions", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "allegany", "--precinct", "1-2")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-002"]


def test_questions_pass_the_third_choice_through(capsys, base, tmp_path):
    path = tmp_path / "downloads" / "results" / "GG22" / "questions" / "county" / "01-allegany.csv"
    path.write_text(
        "election,county,question,how_it_runs,winner,early_votes,early_against,early_third,"
        "total_votes,total_against,total_third\n"
        "GG22,Allegany,A,County,1,20,50,30,20,50,30\n"
        "GG22,Allegany,1,State,1,7,3,,7,3,\n"
    )
    rows = json.loads(run(capsys, "get", "questions", "--election", "GG22", "--base-url", base))["rows"]  # fmt: skip
    assert (rows[0]["early_third"], rows[0]["total_third"]) == (30, 30)
    assert rows[1]["total_third"] is None
    csv_out = run(capsys, "get", "questions", "--election", "GG22", "--base-url", base, "--format", "csv")  # fmt: skip
    assert csv_out.splitlines()[0].endswith("total_votes,total_against,total_third")


def test_precinct_questions_before_2020_say_they_are_election_night_votes(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    catalog["results"]["GG22"]["election_day"] = "2018-11-06"
    index.write_text(json.dumps(catalog))
    out = json.loads(
        run(capsys, "get", "questions", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "allegany")
    )  # fmt: skip
    [note] = out["meta"]["notes"]
    assert "election-night votes only" in note and "total_third" in note
    county = json.loads(run(capsys, "get", "questions", "--election", "GG22", "--base-url", base))
    assert "notes" not in county["meta"]


def test_questions_for_an_election_without_them(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    catalog["results"]["GG22"]["question_files"] = {"county": [], "precinct": []}
    index.write_text(json.dumps(catalog))
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG22", "--base-url", base])
    assert "GG22 has no ballot-question files on this site" in capsys.readouterr().err
    catalog["results"]["GG22"]["kind"] = "primary"
    index.write_text(json.dumps(catalog))
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG22", "--base-url", base])
    assert "(they come with general elections)" in capsys.readouterr().err
    del catalog["results"]["GG22"]["question_files"]  # a catalog from before the questions
    index.write_text(json.dumps(catalog))
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG22", "--base-url", base])
    assert "GG22 has no ballot-question files on this site: its catalog predates them" in (
        capsys.readouterr().err
    )


def test_the_unofficial_count_has_no_questions(capsys, base, tmp_path):
    _add_unofficial(tmp_path / "downloads")
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG26", "--base-url", base])
    assert "GG26's unofficial count has no ballot questions" in capsys.readouterr().err


def test_questions_say_which_file_they_read_and_carry_as_of(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    county = catalog["results"]["GG22"]["question_files"]["county"]
    second = county[0].replace("01-allegany", "14-howard")
    (tmp_path / "downloads" / second).write_text(
        (tmp_path / "downloads" / county[0]).read_text().replace("Allegany", "Howard")
    )
    county.append(second)
    catalog["results"]["GG22"]["as_of"] = "2022-12-09"
    index.write_text(json.dumps(catalog))
    main(["get", "questions", "--election", "GG22", "--base-url", base])
    captured = capsys.readouterr()
    assert "(1 of 2)" in captured.err and "(2 of 2)" in captured.err
    out = json.loads(captured.out)
    assert len(out["rows"]) == 2 and out["meta"]["as_of"] == "2022-12-09"
    main(["get", "questions", "--election", "GG22", "--base-url", base, "--county", "howard"])
    assert "reading" not in capsys.readouterr().err  # one file: no progress line


def test_precinct_questions_before_2016_have_no_third_choice_to_mention(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    catalog["results"]["GG22"]["election_day"] = "2014-11-04"
    index.write_text(json.dumps(catalog))
    out = json.loads(
        run(capsys, "get", "questions", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "allegany")
    )  # fmt: skip
    [note] = out["meta"]["notes"]
    assert "election-night votes only" in note and "total_third" not in note


def test_question_third_from_follows_the_registry():
    from skipjack.client.helpers import QUESTION_THIRD_FROM, election_night_only
    from skipjack.pipeline.results import RESULTS_ELECTIONS, question_third

    for e in RESULTS_ELECTIONS.values():
        if e.kind == "general" and e.precincts and election_night_only(e.year):
            assert question_third(e) == (e.year >= QUESTION_THIRD_FROM), e.code


def test_questions_for_a_county_without_them(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG22", "--base-url", base, "--county", "howard"])
    assert "GG22 has no ballot-question files for Howard on this site" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--precinct", "1-1", "--county", "allegany"], "--precinct needs --geography precinct"),
        (["--geography", "precinct", "--precinct", "1-1"], "--precinct needs --county"),
    ],
)
def test_question_precinct_needs_its_geography_and_county(capsys, base, extra, message):
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG22", "--base-url", base, *extra])
    assert message in capsys.readouterr().err


def test_questions_are_not_parsed_live(capsys, base):
    with pytest.raises(SystemExit):  # argparse: questions take no --source
        main(["get", "questions", "--election", "GG22", "--base-url", base, "--source", "sbe"])
