from __future__ import annotations

import json

import httpx
import pytest

from skipjack.client.sources import DEFAULT_BASE_URL, ClientError, NotFound, SkipjackSource


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
    with pytest.raises(NotFound, match="not found"):
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
    with pytest.raises(NotFound, match="index.json") as caught:
        SkipjackSource(str(tmp_path)).catalog()
    assert "No index.json there" in str(caught.value)


def test_a_catalog_that_is_not_json_is_refused(tmp_path):
    (tmp_path / "index.json").write_text("<html>oops</html>")
    with pytest.raises(ClientError, match="not a skipjack catalog"):
        SkipjackSource(str(tmp_path)).catalog()


@pytest.mark.parametrize("catalog", [{}, {"version": "2"}, {"version": True}])
def test_a_catalog_without_an_integer_version_is_refused(tmp_path, catalog):
    (tmp_path / "index.json").write_text(json.dumps(catalog))
    with pytest.raises(ClientError, match="not a skipjack catalog"):
        SkipjackSource(str(tmp_path)).catalog()


def test_one_http_client_serves_every_request(monkeypatch):
    requests: list[str] = []
    clients: list[httpx.Client] = []
    real_client = httpx.Client

    def counting_client(*args, **kwargs):
        clients.append(real_client(*args, **kwargs))
        return clients[-1]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("index.json"):
            return httpx.Response(200, json={"version": 1})
        return httpx.Response(200, text="a,b\n1,2\n")

    monkeypatch.setattr(httpx, "Client", counting_client)
    with SkipjackSource(DEFAULT_BASE_URL, transport=httpx.MockTransport(handler)) as source:
        source.text("combined/x.csv")
        source.text("combined/y.csv")
        source.catalog()
        source.catalog()
    assert len(clients) == 1
    assert clients[0].is_closed
    assert sum(path.endswith("index.json") for path in requests) == 1
    assert len(requests) == 3


def test_a_byte_order_mark_never_reaches_a_header(tmp_path):
    (tmp_path / "x.csv").write_bytes(b"\xef\xbb\xbfa,b\r\n1,2\r\n")
    assert SkipjackSource(str(tmp_path)).rows("x.csv") == [{"a": "1", "b": "2"}]
    source = SkipjackSource(
        DEFAULT_BASE_URL,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"\xef\xbb\xbfa,b\r\n1,2\r\n")
        ),
    )
    assert source.rows("x.csv") == [{"a": "1", "b": "2"}]


def test_an_unreadable_local_file_is_a_client_error(tmp_path):
    (tmp_path / "x.csv").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ClientError, match="could not read"):
        SkipjackSource(str(tmp_path)).text("x.csv")


def test_an_uppercase_scheme_is_still_a_url():
    assert SkipjackSource("HTTPS://example.org/downloads").local is None
