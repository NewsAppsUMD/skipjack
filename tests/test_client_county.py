"""get county: a county's whole document, or one section as rows."""

from __future__ import annotations

import json

import pytest

from skipjack.cli import main

# Not tests.client_fixture: another project's `tests` package on PYTHONPATH can shadow ours.
from client_fixture import run


def test_county_json_is_the_whole_document(capsys, base):
    out = json.loads(run(capsys, "get", "county", "Allegany", "--base-url", base))
    assert out["county"] == "Allegany"
    assert set(out) == {"meta", "county", "slug", "registration", "turnout", "results",
                        "results_over_time"}  # fmt: skip
    assert list(out)[0] == "meta"
    assert out["meta"]["dataset"] == "county" and out["meta"]["source"] == "skipjack"
    assert out["meta"]["sections"] == {"registration": 1, "turnout": 1, "results": 1}


def test_county_section_as_json_rows_and_as_csv(capsys, base):
    out = json.loads(run(capsys, "get", "county", "allegany", "--section", "turnout",
                         "--base-url", base))  # fmt: skip
    assert out["rows"][0]["measure"] == "requested" and out["meta"]["section"] == "turnout"
    assert out["rows"][0]["value"] == 7 and out["meta"]["rows"] == 1
    text = run(capsys, "get", "county", "allegany", "--section", "results", "--format", "csv",
               "--base-url", base)  # fmt: skip
    assert text.splitlines()[0].startswith("election,county,party_file,office")
    assert text.splitlines()[1].endswith(",6796,")  # total_against None: an empty cell


def test_county_csv_needs_a_section(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "county", "Allegany", "--format", "csv", "--base-url", base])
    assert "--section registration|turnout|results" in capsys.readouterr().err


def test_county_unknown_and_unbuilt(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "county", "Atlantis", "--base-url", base])
    assert "one of" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # in the catalog, but the fixture has no document for it
        main(["get", "county", "Howard", "--base-url", base])
    assert "not found" in capsys.readouterr().err


def test_county_from_the_sbe_is_refused(capsys, base):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "county", "Allegany", "--source", "sbe", "--base-url", base])
    assert exit_info.value.code == 2
    assert "--source skipjack" in capsys.readouterr().err


def test_a_county_document_that_is_not_json_names_the_file(capsys, base, tmp_path):
    (tmp_path / "downloads" / "county" / "allegany.json").write_text("<html>oops</html>")
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "county", "Allegany", "--base-url", base])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "county/allegany.json is not valid JSON" in err and "Traceback" not in err


def test_county_whole_document_to_a_file(capsys, base, tmp_path):
    target = tmp_path / "out" / "allegany.json"
    assert run(capsys, "get", "county", "Allegany", "--base-url", base, "-o", str(target)) == ""
    doc = json.loads(target.read_text())
    assert doc["meta"]["county"] == "Allegany" and doc["results_over_time"]["levels"] == []


def _rewrite(tmp_path, change):
    path = tmp_path / "downloads" / "county" / "allegany.json"
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))


def test_county_official_is_per_section(capsys, base):
    out = json.loads(run(capsys, "get", "county", "Allegany", "--base-url", base))
    # GG22 results are official; the fixture's turnout is GG26, not certified
    assert out["meta"]["official"] == {"registration": True, "results": True, "turnout": False}
    out = json.loads(run(capsys, "get", "county", "Allegany", "--section", "turnout",
                         "--base-url", base))  # fmt: skip
    assert out["meta"]["official"] is False
    out = json.loads(run(capsys, "get", "county", "Allegany", "--section", "results",
                         "--base-url", base))  # fmt: skip
    assert out["meta"]["official"] is True


def test_county_an_election_missing_from_the_catalog_is_not_official(capsys, base, tmp_path):
    _rewrite(tmp_path, lambda doc: doc["results"].append({**doc["results"][0], "election": "XX99"}))
    out = json.loads(run(capsys, "get", "county", "Allegany", "--base-url", base))
    assert out["meta"]["official"]["results"] is False


def test_county_a_section_that_is_not_rows_names_the_file(capsys, base, tmp_path):
    _rewrite(tmp_path, lambda doc: doc.update(turnout={"as_of": "2026-10-15"}))
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "county", "Allegany", "--section", "turnout", "--base-url", base])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "county/allegany.json has no turnout section" in err and "index.json" not in err


def test_county_a_document_missing_a_section_names_the_file(capsys, base, tmp_path):
    _rewrite(tmp_path, lambda doc: doc.pop("registration"))
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "county", "Allegany", "--base-url", base])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "county/allegany.json has no registration section" in err and "index.json" not in err


def test_county_a_document_with_its_own_meta_does_not_replace_ours(capsys, base, tmp_path):
    _rewrite(tmp_path, lambda doc: doc.update(meta={"dataset": "impostor"}))
    out = json.loads(run(capsys, "get", "county", "Allegany", "--base-url", base))
    assert list(out)[0] == "meta" and out["meta"]["dataset"] == "county"


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc.update(turnout={"as_of": "x"}),
        lambda doc: doc.update(turnout=None),
        lambda doc: doc.pop("turnout"),
    ],
    ids=["dict", "none", "missing"],
)
def test_county_one_section_ignores_a_malformed_other_section(capsys, base, tmp_path, change):
    _rewrite(tmp_path, change)
    out = json.loads(run(capsys, "get", "county", "Allegany", "--section", "results",
                         "--base-url", base))  # fmt: skip
    assert out["rows"][0]["total_votes"] == 6796 and out["meta"]["official"] is True
