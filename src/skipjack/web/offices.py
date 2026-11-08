"""A crosswalk from the SBE's office names to canonical offices that persist across elections.

The board's spelling of an office drifts between elections (``U.S. Congress`` in 2010 and
2022 on, ``Rep in Congress`` 2012-2016, ``Representative in Congress`` 2018-2020), so a
cross-election series needs a stable key. Every spelling in ``results._OFFICE_SCOPE`` has an
entry in ``OFFICE_NAMES`` and tests/test_offices.py keeps the two sets equal: a new spelling
in a new election fails until someone decides both its scope and its canonical office.
"""

from __future__ import annotations

from dataclasses import dataclass


ALL_LEVELS = ("federal", "state", "county", "judicial", "municipal", "party", "convention")


@dataclass(frozen=True)
class Office:
    key: str
    level: str  # one of ALL_LEVELS
    title: str  # display title, AP style
    districted: bool = False


# Declaration order is display order within a level.
OFFICES: dict[str, Office] = {
    o.key: o
    for o in (
        # federal
        Office("president", "federal", "President"),
        Office("us_senate", "federal", "U.S. Senate"),
        Office("us_house", "federal", "U.S. House", districted=True),
        # state
        Office("governor", "state", "Governor"),
        Office("comptroller", "state", "Comptroller"),
        Office("attorney_general", "state", "Attorney General"),
        Office("state_senate", "state", "State Senate", districted=True),
        Office("house_of_delegates", "state", "House of Delegates", districted=True),
        # county
        Office("county_executive", "county", "County Executive"),
        Office("council_president", "county", "County Council President"),
        Office("county_council", "county", "County Council", districted=True),
        Office("county_council_at_large", "county", "County Council, at large"),
        Office("commission_president", "county", "County Commission President"),
        Office("county_commissioner", "county", "County Commissioner", districted=True),
        Office("commissioner_at_large", "county", "County Commissioner, at large"),
        Office("sheriff", "county", "Sheriff"),
        Office("states_attorney", "county", "State's Attorney"),
        Office("clerk", "county", "Clerk of the Circuit Court"),
        Office("register_of_wills", "county", "Register of Wills"),
        Office("treasurer", "county", "Treasurer"),
        Office("board_of_education", "county", "Board of Education", districted=True),
        Office("board_of_education_at_large", "county", "Board of Education, at large"),
        Office("orphans_court", "county", "Orphans' Court judge"),
        # Baltimore City: the bare Mayor / City Council names occur only in the city's files
        # (the presidential-year elections), and so does its Comptroller, which `canonical`
        # tells apart from the statewide office by the contest's scope.
        Office("mayor", "county", "Mayor"),
        Office("city_council_president", "county", "City Council President"),
        Office("city_council", "county", "City Council", districted=True),
        Office("city_comptroller", "county", "City Comptroller"),
        # everything else is grouped, not tracked office by office
        Office("judge", "judicial", "Judge"),
        Office("municipal", "municipal", "Municipal office"),
        Office("party", "party", "Party central committee"),
        Office("convention", "convention", "National convention delegate"),
    )
}

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

# The levels the trends page shows (a subset of ALL_LEVELS), in order, and their headings.
LEVELS = ("federal", "state", "county")
LEVEL_LABELS = {"federal": "Federal", "state": "State", "county": "County"}

# District maps: the election years each districted office's map took effect. The 2002 maps
# (drawn after the 2000 census) cover the earliest elections in the archive, 2010. Maryland
# redrew the congressional map in 2011 (first used 2012) and again in 2022 (court-ordered),
# and the legislative map in 2012 (first used 2014) and 2022.
ERAS: dict[str, tuple[int, ...]] = {
    "us_house": (2002, 2012, 2022),
    "state_senate": (2002, 2014, 2022),
    "house_of_delegates": (2002, 2014, 2022),
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
    large). Every election in the archive (2010 on) falls under a listed map, so for a
    districted office None would only mean a `year` before 2002.
    """
    starts = [s for s in ERAS.get(key, ()) if s <= year]
    return max(starts) if starts else None
