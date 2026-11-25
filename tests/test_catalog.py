"""The downloads catalog: what the data CLI reads to find files."""

from __future__ import annotations

from skipjack.pipeline.results import RESULTS_ELECTIONS
from skipjack.web.catalog import build_catalog


def _touch(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


def test_the_catalog_lists_only_what_is_offered(tmp_path, monkeypatch):
    # GG26's count is live while its certified results are unpublished (is_live reads this flag)
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", False)
    _touch(tmp_path, "voter_registration/monthly/2026-09.csv")
    _touch(tmp_path, "voter_registration/monthly/2026-10.csv")
    _touch(tmp_path, "results/GG22/county/01-allegany-all.csv")
    _touch(tmp_path, "results/GG22/county/01-allegany-all_provenance.json")
    _touch(tmp_path, "results/GG26/county/01-allegany-all.csv")  # not published: left out
    _touch(tmp_path, "results_unofficial/GG26/2026-11-04T010000/county/01-allegany.csv")
    _touch(tmp_path, "results_unofficial/GG26/2026-11-05T010000/county/01-allegany.csv")
    _touch(tmp_path, "turnout/GG22/official-turnout/county/2022-12-08.csv")
    _touch(tmp_path, "turnout/GG22/early-voting/county/2022-11-03.csv")  # certified: left out
    _touch(tmp_path, "turnout/GG26/requests/county/2026-10-14.csv")
    _touch(tmp_path, "turnout/GG26/requests/county/2026-10-15.csv")

    catalog = build_catalog(tmp_path)

    assert catalog["version"] == 1
    assert catalog["registration"]["months"] == ["2026-09", "2026-10"]
    assert catalog["registration"]["tables"]["monthly"] == "combined/registration_monthly_all.csv"
    assert {"name": "Allegany", "slug": "allegany", "code": "01"} in catalog["counties"]
    assert len(catalog["counties"]) == 24

    gg22 = catalog["results"]["GG22"]
    assert gg22["official"] is True and gg22["as_of"] is None
    assert gg22["kind"] == "general" and gg22["election_day"] == "2022-11-08"
    assert gg22["files"] == ["results/GG22/county/01-allegany-all.csv"]

    gg26 = catalog["results"]["GG26"]  # the live count: newest snapshot only, marked unofficial
    assert gg26["official"] is False and gg26["as_of"] == "2026-11-05T010000"
    assert gg26["files"] == ["results_unofficial/GG26/2026-11-05T010000/county/01-allegany.csv"]

    assert catalog["turnout"]["GG22"]["certified"] is True
    assert catalog["turnout"]["GG22"]["families"] == {
        "official-turnout": {"county": ["2022-12-08"]}
    }
    assert catalog["turnout"]["GG26"]["families"]["requests"]["county"] == [
        "2026-10-14",
        "2026-10-15",
    ]
    assert catalog["county_history"] == "county/{slug}.json"


def test_the_catalog_lists_precinct_and_question_files_apart(tmp_path):
    _touch(tmp_path, "results/PG24/county/15-kent-all.csv")
    _touch(tmp_path, "results/PG24/precinct/15-kent-all.csv.gz")
    _touch(tmp_path, "results/PG24/precinct/15-kent-all_provenance.json")
    _touch(tmp_path, "results/PG24/questions/county/15-kent.csv")
    _touch(tmp_path, "results/PG24/questions/precinct/15-kent.csv.gz")
    pg24 = build_catalog(tmp_path)["results"]["PG24"]
    assert pg24["files"] == ["results/PG24/county/15-kent-all.csv"]  # county only: version 1
    assert pg24["precinct_files"] == ["results/PG24/precinct/15-kent-all.csv.gz"]
    assert pg24["question_files"] == {
        "county": ["results/PG24/questions/county/15-kent.csv"],
        "precinct": ["results/PG24/questions/precinct/15-kent.csv.gz"],
    }
