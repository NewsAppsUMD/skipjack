from __future__ import annotations

import json

import httpx
import pytest

from skipjack.client.sources import DEFAULT_BASE_URL, ClientError, SkipjackSource


def test_a_local_folder_is_read_from_disk(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"version": 1}))
    (tmp_path / "combined").mkdir()
    (tmp_path / "combined" / "x.csv").write_text("a,b\n1,2\n")
    source = SkipjackSource(str(tmp_path))
    assert source.catalog() == {"version": 1}
    assert source.rows("combined/x.csv") == [{"a": "1", "b": "2"}]
    assert source.url("combined/x.csv") == str(tmp_path / "combined" / "x.csv")


def test_a_local_folder_with_a_trailing_slash_works(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"version": 1}))
    assert SkipjackSource(str(tmp_path) + "/").catalog() == {"version": 1}


def test_a_url_is_fetched_over_http():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/skipjack/downloads/index.json":
            return httpx.Response(200, json={"version": 1})
        return httpx.Response(404)

    source = SkipjackSource(DEFAULT_BASE_URL, transport=httpx.MockTransport(handler))
    assert source.catalog() == {"version": 1}
    with pytest.raises(ClientError, match="not found"):
        source.text("nope.csv")


def test_a_network_failure_says_the_site_could_not_be_reached():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    source = SkipjackSource(DEFAULT_BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(ClientError, match="could not reach") as caught:
        source.catalog()
    assert "No index.json there" not in str(caught.value)


def test_an_error_status_is_reported():
    source = SkipjackSource(
        DEFAULT_BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(ClientError, match="503"):
        source.text("combined/x.csv")


def test_a_catalog_from_a_newer_site_is_refused(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"version": 2}))
    with pytest.raises(ClientError, match="upgrade"):
        SkipjackSource(str(tmp_path)).catalog()


def test_a_missing_catalog_says_what_to_do(tmp_path):
    with pytest.raises(ClientError, match="index.json") as caught:
        SkipjackSource(str(tmp_path)).catalog()
    assert "No index.json there" in str(caught.value)


def test_a_catalog_that_is_not_json_is_refused(tmp_path):
    (tmp_path / "index.json").write_text("<html>oops</html>")
    with pytest.raises(ClientError, match="not a skipjack catalog"):
        SkipjackSource(str(tmp_path)).catalog()
