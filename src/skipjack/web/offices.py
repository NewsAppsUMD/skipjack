"""A crosswalk from the SBE's office names to canonical offices that persist across elections.

The board's spelling of an office drifts between elections (``U.S. Congress`` in 2010 and
2022 on, ``Rep in Congress`` 2012-2016, ``Representative in Congress`` 2018-2020), so a
cross-election series needs a stable key. Every spelling in ``results._OFFICE_SCOPE`` has an
entry in ``OFFICE_NAMES`` and tests/test_offices.py keeps the two sets equal: a new spelling
in a new election fails until someone decides both its scope and its canonical office.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Office:
    key: str
    level: str  # "federal" | "state" | "county" | "judicial" | "municipal" | "party" | "convention"
    title: str  # display title, AP style
    districted: bool


def _office(key: str, level: str, title: str, districted: bool = False) -> tuple[str, Office]:
    return key, Office(key, level, title, districted)


# Declaration order is display order within a level.
OFFICES: dict[str, Office] = dict(
    [
        # federal
        _office("president", "federal", "President"),
        _office("us_senate", "federal", "U.S. Senate"),
        _office("us_house", "federal", "U.S. House", districted=True),
        # state
        _office("governor", "state", "Governor"),
        _office("comptroller", "state", "Comptroller"),
        _office("attorney_general", "state", "Attorney General"),
        _office("state_senate", "state", "State Senate", districted=True),
        _office("house_of_delegates", "state", "House of Delegates", districted=True),
        # county
        _office("county_executive", "county", "County Executive"),
        _office("council_president", "county", "County Council President"),
        _office("county_council", "county", "County Council", districted=True),
        _office("county_council_at_large", "county", "County Council At Large"),
        _office("commission_president", "county", "County Commission President"),
        _office("county_commissioner", "county", "County Commissioner", districted=True),
        _office("commissioner_at_large", "county", "County Commissioner At Large"),
        _office("sheriff", "county", "Sheriff"),
        _office("states_attorney", "county", "State's Attorney"),
        _office("clerk", "county", "Clerk of the Circuit Court"),
        _office("register_of_wills", "county", "Register of Wills"),
        _office("treasurer", "county", "Treasurer"),
        _office("board_of_education", "county", "Board of Education", districted=True),
        _office("board_of_education_at_large", "county", "Board of Education At Large"),
        _office("orphans_court", "county", "Orphans' Court judge"),
        # Baltimore City: the bare Mayor / City Council names occur only in the city's files
        # (the presidential-year elections), and so does its Comptroller, which `canonical`
        # tells apart from the statewide office by the contest's scope.
        _office("mayor", "county", "Mayor"),
        _office("city_council_president", "county", "City Council President"),
        _office("city_council", "county", "City Council", districted=True),
        _office("city_comptroller", "county", "City Comptroller"),
        # everything else is grouped, not tracked office by office
        _office("judge", "judicial", "Judge"),
        _office("municipal", "municipal", "Municipal office"),
        _office("party", "party", "Party central committee"),
        _office("convention", "convention", "National convention delegate"),
    ]
)

# Every spelling the SBE's files have carried -> office key.
OFFICE_NAMES: dict[str, str] = {
    # federal
    "President - Vice Pres": "president",
    "U.S. Senator": "us_senate",
    "U.S. Congress": "us_house",
    "Rep in Congress": "us_house",
    "Representative in Congress": "us_house",
    # state
    "Governor / Lt. Governor": "governor",
    "Comptroller": "comptroller",
    "Attorney General": "attorney_general",
    "State Senator": "state_senate",
    "House of Delegates": "house_of_delegates",
    # county
    "County Executive": "county_executive",
    "Pres County Council": "council_president",
    "County Council": "county_council",
    "County Council At Large": "county_council_at_large",
    "Pres County Commission": "commission_president",
    "County Commissioner": "county_commissioner",
    "County Commissioner At Large": "commissioner_at_large",
    "Cty Commission at Large": "commissioner_at_large",
    "Sheriff": "sheriff",
    "State's Attorney": "states_attorney",
    "Clerk Circuit Court": "clerk",
    "Register of Wills": "register_of_wills",
    "Treasurer": "treasurer",
    "Board of Education": "board_of_education",
    "Board of Ed at Large": "board_of_education_at_large",
    "Board of Ed At Large": "board_of_education_at_large",
    "Board of Education At Large": "board_of_education_at_large",
    "Judge Orphans' Court": "orphans_court",
    "Judge Orphans Court": "orphans_court",
    "Mayor": "mayor",
    "President City Council": "city_council_president",
    "Member City Council": "city_council",
    # judicial: statewide retention questions and the district appellate / circuit seats
    "Appeals Judge MD at Lrg": "judge",
    "Judge Spec Appeal at Lg": "judge",
    "Judge Special Appeals At Large": "judge",
    "Appeals Judge MD": "judge",
    "Appellate Judge": "judge",
    "Judge Court of Appeals": "judge",
    "Judge Spec Appeals": "judge",
    "Judge Special Appeals": "judge",
    "MD Supreme Court": "judge",
    "Judge Circuit Court": "judge",
    "Judge of the Circuit Court": "judge",
    # municipal (Cumberland and Hagerstown vote with the county in some years)
    "Council - City of Cumberland": "municipal",
    "Cumberland Council": "municipal",
    "Hagerstown Council": "municipal",
    "Mayor of Cumberland": "municipal",
    "Mayor of Hagerstown": "municipal",
    # party central committees
    "Chair REP Cent Comm": "party",
    "DEM Cent Comm": "party",
    "DEM Cent Comm Female": "party",
    "DEM Cent Comm Female At Large": "party",
    "DEM Cent Comm Male": "party",
    "DEM Cent Comm Male At Large": "party",
    "DEM Cent Comm at Large": "party",
    "DEM Cent Comm at Lg": "party",
    "DEM Cent Committee": "party",
    "Dem Cent Comm at Large": "party",
    "Dem Cent Committee": "party",
    "REP Cent Comm at Large": "party",
    "REP Cent Committee": "party",
    # presidential-primary convention delegates and alternates
    "Democratic Delegates": "convention",
    "Female DEM Del/Alt": "convention",
    "Female Delegates": "convention",
    "Male DEM Del/Alt": "convention",
    "Male Delegates": "convention",
    "REP Alternates": "convention",
    "REP Delegates": "convention",
    "Republican Alternates": "convention",
    "Republican Delegates": "convention",
}

# The levels the trends page shows, in order, and their headings.
LEVELS = ("federal", "state", "county")
LEVEL_LABELS = {"federal": "Federal", "state": "State", "county": "County"}

# District maps: the election years each districted office's map took effect. Maryland
# redrew the congressional map in 2011 (first used 2012) and again in 2022 (court-ordered),
# and the legislative map in 2012 (first used 2014) and 2022.
ERAS: dict[str, tuple[int, ...]] = {
    "us_house": (2012, 2022),
    "state_senate": (2014, 2022),
    "house_of_delegates": (2014, 2022),
}


def canonical(office: str, scope: str) -> Office:
    """The canonical office for an SBE spelling, given the contest's scope.

    `scope` is the contest's scope from results.county_page, which already applies the
    all-24-counties rule: a Comptroller contest that is not statewide is Baltimore City's.
    Raises KeyError for a spelling not in OFFICE_NAMES.
    """
    key = OFFICE_NAMES[office]
    if key == "comptroller" and scope != "statewide":
        return OFFICES["city_comptroller"]
    return OFFICES[key]


def era(key: str, year: int) -> int | None:
    """The first election year of the district map in force for `year`.

    None when the office has no map table in ERAS (a county office, or one elected at
    large) or when `year` precedes every map listed, so results from before the first map
    are not compared district by district with results under it.
    """
    starts = [s for s in ERAS.get(key, ()) if s <= year]
    return max(starts) if starts else None
