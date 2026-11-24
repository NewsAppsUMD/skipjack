"""Where the data CLI reads from: the published skipjack downloads, by URL or local folder."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://newsappsumd.github.io/skipjack/downloads"
SUPPORTED_CATALOG = 1


class ClientError(Exception):
    """A failure the user can act on; the CLI prints it and exits 2."""


class NotFound(ClientError):
    """The file is not there (a missing local file, or HTTP 404)."""


class SkipjackSource:
    """``base`` is the downloads/ root: a URL, or a folder such as site/downloads."""

    def __init__(self, base: str, transport: httpx.BaseTransport | None = None):
        self.base = base.rstrip("/")
        self.local = None if base.startswith(("http://", "https://")) else Path(base)
        self._transport = transport
        self._catalog: dict | None = None

    def url(self, path: str) -> str:
        return str(self.local / path) if self.local else f"{self.base}/{path}"

    def text(self, path: str) -> str:
        if self.local:
            target = self.local / path
            if not target.is_file():
                raise NotFound(f"not found: {target}")
            return target.read_text(encoding="utf-8")
        try:
            with httpx.Client(
                transport=self._transport, timeout=30.0, follow_redirects=True
            ) as client:
                response = client.get(self.url(path))
        except httpx.HTTPError as err:
            raise ClientError(f"could not reach {self.url(path)} ({err})") from err
        if response.status_code == 404:
            raise NotFound(f"not found: {self.url(path)}")
        if response.status_code != 200:
            raise ClientError(f"{self.url(path)}: HTTP {response.status_code}")
        return response.text

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
            if catalog.get("version", 0) > SUPPORTED_CATALOG:
                raise ClientError("the site's catalog is newer than this skipjack; upgrade it")
            self._catalog = catalog
        return self._catalog
