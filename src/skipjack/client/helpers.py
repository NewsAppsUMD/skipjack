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


def county_for(counties: Iterable[dict], given: str | None) -> dict | None:
    """The county ({"name", "slug", "code"}) for a name or slug, loosely matched; None when
    not asked for. ``counties`` is the catalog's list, or the pipeline's for the live source."""
    if given is None:
        return None
    counties = list(counties)
    wanted = county_key(given)
    for county in counties:
        if wanted in (county_key(county["name"]), county_key(county["slug"])):
            return county
    names = ", ".join(c["name"] for c in counties)
    raise ClientError(f"unknown county {given!r}; one of: {names}")


def choice(kind: str, given: str, choices) -> str:
    choices = sorted(choices)
    if given not in choices:
        raise ClientError(f"unknown {kind} {given!r}; one of: {', '.join(choices)}")
    return given


def file_stem(path: str) -> str:
    """A download's name without .csv or .csv.gz ("01-allegany-all")."""
    return path.rsplit("/", 1)[-1].removesuffix(".gz").removesuffix(".csv")


def precinct_id(given: str) -> str:
    """A precinct as the files print it: "1-12" and "001-012" are both "001-012"."""
    m = re.fullmatch(r"\s*(\d{1,3})-(\d{1,3})\s*", given)
    if not m:
        raise ClientError(f"{given!r} is not a precinct like 001-012")
    return f"{int(m.group(1)):03d}-{int(m.group(2)):03d}"


# The last year whose precinct files split election-night votes only. The downloads path reads
# it here (election_night_only); the sbe path asks the pipeline registry's own flag
# (ResultsElection.precinct_every_vote_type, derived from the same year in
# pipeline/results.py's precinct_vote_types): change both together.
ELECTION_NIGHT_ONLY_THROUGH = 2018

# A precinct pull of an election through that year says what its figures are
ELECTION_NIGHT_ONLY = (
    f"the board's precinct files for 2010-{ELECTION_NIGHT_ONLY_THROUGH} carry election-night "
    "votes only, so total_votes and total_against are election-night votes"
)


# The first year whose question files print a third choice (2016-2020, the named layout; the
# pipeline's question_third). A precinct pull of ballot questions from that year through
# ELECTION_NIGHT_ONLY_THROUGH carries total_third too, and it is election-night as well; earlier
# ones say ELECTION_NIGHT_ONLY.
QUESTION_THIRD_FROM = 2016

ELECTION_NIGHT_ONLY_QUESTIONS = (
    f"the board's precinct files for 2010-{ELECTION_NIGHT_ONLY_THROUGH} carry election-night "
    "votes only, so total_votes, total_against and total_third are election-night votes"
)


def election_night_only(year: str | int | None) -> bool:
    """Whether a precinct pull of an election in ``year`` (a number or its digits) carries
    election-night votes only; an unknown year does not."""
    year = str(year or "")
    return year.isdigit() and int(year) <= ELECTION_NIGHT_ONLY_THROUGH


def precinct_args(args) -> tuple[str, str | None]:
    """(geography, precinct) from get results' --geography and --precinct, the precinct as
    DDD-PPP; --precinct without --geography precinct, or without --county, is refused."""
    geography = getattr(args, "geography", "county")
    given = getattr(args, "precinct", None)
    precinct = precinct_id(given) if given is not None else None
    if precinct and geography != "precinct":
        raise ClientError("--precinct needs --geography precinct")
    if precinct and getattr(args, "county", None) is None:
        raise ClientError("--precinct needs --county: precinct numbers repeat across counties")
    return geography, precinct


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
    source_urls: Iterable[str],
    filters: dict | None = None,
    **extra,
) -> dict:
    """A handler's meta: the dataset, its own fields, the URLs read and the filters in use
    (those given as None dropped). A command that takes no filters passes none and gets no
    filters key."""
    built = {"dataset": dataset, **extra, "source_urls": list(source_urls)}
    if filters is not None:
        built["filters"] = {k: v for k, v in filters.items() if v is not None}
    return built
