"""Opt-in: the data CLI against the real site and the real State Board. uv run pytest -m live"""

from __future__ import annotations

import json

import pytest

from skipjack.cli import main

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def default_url(monkeypatch):
    # --base-url's default reads SKIPJACK_URL; these tests are of the real published site
    monkeypatch.delenv("SKIPJACK_URL", raising=False)


def test_the_published_site_answers(capsys):
    main(["get", "registration", "--county", "Kent", "--from", "2026-01"])
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] and out["meta"]["source"] == "skipjack"
    main(["get", "county", "Kent", "--section", "turnout"])
    assert json.loads(capsys.readouterr().out)["rows"]


def test_the_state_board_answers(capsys):
    main(["get", "results", "--election", "GG22", "--county", "Kent", "--source", "sbe"])
    assert json.loads(capsys.readouterr().out)["rows"]
