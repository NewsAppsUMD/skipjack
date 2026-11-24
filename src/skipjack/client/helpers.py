"""Shared pieces of the data CLI's handlers: county matching, choices, CSV rows and meta."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from skipjack.client.sources import ClientError, SkipjackSource


def county_key(text: str) -> str:
    """Lowercase, no periods or apostrophes, spaces and hyphens as one hyphen: "St Marys",
    "st. mary's" and "ST-MARYS" all become "st-marys"."""
    text = re.sub(r"[.'’]", "", text.strip().lower())
    return re.sub(r"[\s-]+", "-", text)


def county_for(catalog: dict, given: str | None) -> dict | None:
    """The catalog's county for a name or slug, loosely matched; None when not asked for."""
    if given is None:
        return None
    wanted = county_key(given)
    for county in catalog["counties"]:
        if wanted in (county_key(county["name"]), county_key(county["slug"])):
            return county
    names = ", ".join(c["name"] for c in catalog["counties"])
    raise ClientError(f"unknown county {given!r}; one of: {names}")


def choice(kind: str, given: str, choices) -> str:
    choices = sorted(choices)
    if given not in choices:
        raise ClientError(f"unknown {kind} {given!r}; one of: {', '.join(choices)}")
    return given


def read_rows(source: SkipjackSource, path: str, needs: Iterable[str] = ()) -> list[dict]:
    """A CSV file's rows, after checking its header has the columns the caller reads.

    Checking up front names the file and column exactly; a KeyError later is our own bug."""
    reader = csv.DictReader(io.StringIO(source.text(path)))
    columns = reader.fieldnames or []
    for column in needs:
        if column not in columns:
            raise ClientError(f"{source.url(path)} has no {column!r} column")
    return list(reader)


def meta(
    dataset: str,
    source: SkipjackSource,
    paths: Iterable[str],
    filters: dict | None = None,
    **extra,
) -> dict:
    """A handler's meta: the dataset, its own fields, the files read and the filters in use
    (those given as None dropped). A command that takes no filters passes none and gets no
    filters key."""
    built = {"dataset": dataset, **extra, "source_urls": [source.url(p) for p in paths]}
    if filters is not None:
        built["filters"] = {k: v for k, v in filters.items() if v is not None}
    return built
