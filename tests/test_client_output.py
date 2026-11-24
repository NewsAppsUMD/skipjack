from __future__ import annotations

import json

from skipjack.client.output import render, typed


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
