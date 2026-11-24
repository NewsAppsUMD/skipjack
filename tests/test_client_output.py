from __future__ import annotations

import json

import pytest

from skipjack.client.output import render, typed


def test_a_non_numeric_count_stays_as_is():
    assert typed([{"value": "n/a"}]) == [{"value": "n/a"}]


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError, match="unknown format 'xml'"):
        render({}, [{"a": 1}], "xml")


def test_csv_refuses_nested_values_and_names_the_column():
    with pytest.raises(ValueError, match="'turnout'"):
        render({}, [{"a": 1, "turnout": {"x": 1}}], "csv")
    with pytest.raises(ValueError, match="'races'"):
        render({}, [{"races": [1, 2]}], "csv")


def test_count_columns_become_numbers_and_codes_stay_text():
    rows = [
        {"district": "06", "value": "12", "early_votes": "3", "total_against": "", "winner": "1"}
    ]
    assert typed(rows) == [
        {"district": "06", "value": 12, "early_votes": 3, "total_against": None, "winner": 1}
    ]


def test_json_is_an_envelope_with_meta_and_rows():
    text = render({"dataset": "results"}, [{"a": 1}], "json")
    assert json.loads(text) == {"meta": {"dataset": "results"}, "rows": [{"a": 1}]}


def test_csv_is_rows_only_with_the_union_of_columns_in_first_seen_order():
    text = render({"dataset": "x"}, [{"a": 1, "b": None}, {"a": 2, "c": "z"}], "csv")
    assert text == "a,b,c\n1,,\n2,,z\n"


def test_csv_of_no_rows_is_empty():
    assert render({}, [], "csv") == ""


def test_unofficial_results_tallies_become_numbers():
    rows = [
        {
            "district": "4A",
            "vote_for": "1",
            "precincts_reported": "49",
            "precincts_total": "49",
            "early": "6761",
            "election_day": "10862",
            "mail_in": "6769",
            "provisional": "",
            "total": "24392",
        }
    ]
    assert typed(rows) == [
        {
            "district": "4A",
            "vote_for": 1,
            "precincts_reported": 49,
            "precincts_total": 49,
            "early": 6761,
            "election_day": 10862,
            "mail_in": 6769,
            "provisional": None,
            "total": 24392,
        }
    ]
