"""Where the data CLI reads from: the published skipjack downloads, by URL or local folder."""

from __future__ import annotations

import csv
import gzip
import io
import json
import zlib
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://newsappsumd.github.io/skipjack/downloads"
SUPPORTED_CATALOG = 1
GZIP_MAGIC = b"\x1f\x8b"


class ClientError(Exception):
    """A failure the user can act on; the CLI prints it and exits 2."""


class NotFound(ClientError):
    """The file is not there (a missing local file, or HTTP 404)."""


class SkipjackSource:
    """``base`` is the downloads/ root: a URL, or a folder such as site/downloads."""

    def __init__(self, base: str, transport: httpx.BaseTransport | None = None):
        self.base = base.rstrip("/")
        is_url = base.lower().startswith(("http://", "https://"))
        self.local = None if is_url else Path(base)
        self._transport = transport
        self._client: httpx.Client | None = None
        self._catalog: dict | None = None

    def __enter__(self) -> SkipjackSource:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                transport=self._transport, timeout=30.0, follow_redirects=True
            )
        return self._client

    def url(self, path: str) -> str:
        return str(self.local / path) if self.local is not None else f"{self.base}/{path}"

    def _bytes(self, path: str) -> bytes:
        if self.local is not None:
            target = self.local / path
            if not target.is_file():
                raise NotFound(f"not found: {target}")
            try:
                return target.read_bytes()
            except OSError as err:
                raise ClientError(f"could not read {target} ({err})") from err
        try:
            response = self._http().get(self.url(path))
        except httpx.HTTPError as err:
            raise ClientError(f"could not reach {self.url(path)} ({err})") from err
        if response.status_code == 404:
            raise NotFound(f"not found: {self.url(path)}")
        if response.status_code != 200:
            raise ClientError(f"{self.url(path)}: HTTP {response.status_code}")
        return response.content

    def text(self, path: str) -> str:
        """The file's text, decoded as UTF-8 with any byte-order mark dropped. A .gz file is
        decompressed, unless the server already undid the compression (a host that sends
        Content-Encoding: gzip for .gz, which httpx decodes): only bytes that start with the
        gzip magic are decompressed."""
        data = self._bytes(path)
        if path.endswith(".gz") and data[:2] == GZIP_MAGIC:
            try:
                data = gzip.decompress(data)
            except (OSError, EOFError, zlib.error) as err:
                # a valid header over a corrupt body raises zlib.error, not OSError
                raise ClientError(f"could not decompress {self.url(path)} ({err})") from err
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as err:
            raise ClientError(f"could not read {self.url(path)} ({err})") from err

    def rows(self, path: str) -> list[dict]:
        return list(csv.DictReader(io.StringIO(self.text(path))))

    def catalog(self) -> dict:
        if self._catalog is None:
            try:
                text = self.text("index.json")
            except NotFound as err:
                raise NotFound(
                    f"{err}. No index.json there: point --base-url at a skipjack downloads/ "
                    "folder or URL built after the data CLI was added"
                ) from err
            try:
                catalog = json.loads(text)
            except json.JSONDecodeError as err:
                raise ClientError(f"{self.url('index.json')} is not a skipjack catalog") from err
            if not isinstance(catalog, dict):
                raise ClientError(f"{self.url('index.json')} is not a skipjack catalog")
            version = catalog.get("version")
            if not isinstance(version, int) or isinstance(version, bool):
                raise ClientError(f"{self.url('index.json')} is not a skipjack catalog")
            if version > SUPPORTED_CATALOG:
                raise ClientError("the site's catalog is newer than this skipjack; upgrade it")
            self._catalog = catalog
        return self._catalog
