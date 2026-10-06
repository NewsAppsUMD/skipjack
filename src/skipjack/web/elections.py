"""Maryland statewide election dates, used to mark charts.

These are entered by hand from the State Board of Elections' election archive. The 2020
primary moved from April 28 to June 2 and the 2022 primary from June 28 to July 19; the dates
below are the days voting actually took place. ``tests/test_trends.py`` checks that each one
falls on a Tuesday and that every general election is the Tuesday after the first Monday
in November.
"""

from __future__ import annotations

PRIMARIES = [
    "2010-09-14",
    "2012-04-03",
    "2014-06-24",
    "2016-04-26",
    "2018-06-26",
    "2020-06-02",
    "2022-07-19",
    "2024-05-14",
    "2026-06-30",
]

GENERALS = [
    "2010-11-02",
    "2012-11-06",
    "2014-11-04",
    "2016-11-08",
    "2018-11-06",
    "2020-11-03",
    "2022-11-08",
    "2024-11-05",
    "2026-11-03",
]


def month_of(election_date: str) -> str:
    """ "2022-07-19" -> "2022-07"."""
    return election_date[:7]
