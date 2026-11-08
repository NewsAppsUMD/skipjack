"""Tests for the office crosswalk: every SBE spelling maps to one canonical office key.

The key guard is that the crosswalk and results._OFFICE_SCOPE name exactly the same spellings,
so a new office in a new election forces both a scope and a crosswalk decision.
"""

from __future__ import annotations

import sqlite3

import pytest

from skipjack.web import offices, results
from skipjack.web.db import DB_PATH

STATEWIDE_LEVELS = {"federal", "state", "judicial"}
LOCAL_LEVELS = {"county", "judicial", "municipal", "party", "convention"}


def test_every_sbe_spelling_has_a_key_and_vice_versa():
    assert set(offices.OFFICE_NAMES) == set(results._OFFICE_SCOPE)
    assert set(offices.OFFICE_NAMES.values()) <= set(offices.OFFICES)
    for key, office in offices.OFFICES.items():
        assert office.key == key
        assert office.level in {
            "federal", "state", "county", "judicial", "municipal", "party", "convention"
        }  # fmt: skip


def test_levels_agree_with_scopes():
    expected = {
        "statewide": STATEWIDE_LEVELS,
        "congress": {"federal"},
        "senate": {"state"},
        "delegates": {"state"},
        "local": LOCAL_LEVELS,
    }
    for name, scope in results._OFFICE_SCOPE.items():
        level = offices.OFFICES[offices.OFFICE_NAMES[name]].level
        assert level in expected[scope], (name, scope, level)


def test_spellings_that_drift_share_a_key():
    congress = {offices.OFFICE_NAMES[n] for n in results.CONGRESS_OFFICES}
    assert congress == {"us_house"}
    assert len(results.CONGRESS_OFFICES) == 3
    assert (
        offices.OFFICE_NAMES["Judge Orphans' Court"] == offices.OFFICE_NAMES["Judge Orphans Court"]
    )
    assert offices.OFFICE_NAMES["Cty Commission at Large"] == "commissioner_at_large"
    assert offices.OFFICE_NAMES["County Commissioner At Large"] == "commissioner_at_large"
    assert offices.OFFICE_NAMES["Board of Ed At Large"] == "board_of_education_at_large"
    assert offices.OFFICE_NAMES["House of Delegates"] == "house_of_delegates"
    assert offices.OFFICE_NAMES["Democratic Delegates"] == "convention"


def test_canonical_demotes_the_presidential_year_comptroller():
    assert offices.canonical("Comptroller", "statewide") == offices.OFFICES["comptroller"]
    assert offices.canonical("Comptroller", "statewide").level == "state"
    city = offices.canonical("Comptroller", "local")
    assert city.key == "city_comptroller" and city.level == "county"
    assert offices.canonical("Mayor", "local").key == "mayor"
    assert offices.canonical("U.S. Congress", "congress").districted
    assert not offices.canonical("Sheriff", "local").districted
    with pytest.raises(KeyError):
        offices.canonical("Dog Catcher", "local")


def test_era():
    assert offices.era("us_house", 2010) is None
    assert offices.era("us_house", 2012) == 2012
    assert offices.era("us_house", 2020) == 2012
    assert offices.era("us_house", 2022) == 2022
    assert offices.era("us_house", 2026) == 2022
    assert offices.era("state_senate", 2012) is None
    assert offices.era("state_senate", 2014) == 2014
    assert offices.era("house_of_delegates", 2018) == 2014
    assert offices.era("sheriff", 2022) is None


def test_levels_and_labels():
    assert offices.LEVELS == ("federal", "state", "county")
    assert [offices.LEVEL_LABELS[lv] for lv in offices.LEVELS] == ["Federal", "State", "County"]
    assert set(offices.LEVEL_LABELS) == set(offices.LEVELS)


def test_display_order_within_a_level():
    federal = [k for k, o in offices.OFFICES.items() if o.level == "federal"]
    assert federal == ["president", "us_senate", "us_house"]
    state = [k for k, o in offices.OFFICES.items() if o.level == "state"]
    assert state[:3] == ["governor", "comptroller", "attorney_general"]
    county = [k for k, o in offices.OFFICES.items() if o.level == "county"]
    assert county[0] == "county_executive"
    assert offices.OFFICES["orphans_court"].title == "Orphans' Court judge"


# --- real data ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    if not DB_PATH.exists():
        pytest.skip("skipjack.db is not built")
    c = sqlite3.connect(DB_PATH)
    try:
        n = c.execute("SELECT COUNT(*) FROM results_county").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    if not n:
        c.close()
        pytest.skip("skipjack.db has no results rows")
    yield c
    c.close()


def test_real_every_office_in_the_db_is_canonical(real):
    rows = real.execute(
        "SELECT DISTINCT office, COUNT(DISTINCT county) FROM results_county GROUP BY office"
    ).fetchall()
    assert rows
    for office, n_counties in rows:
        scope = results._scope(office, n_counties)
        assert offices.canonical(office, scope).key  # no KeyError
