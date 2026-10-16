"""SQLite schema definitions for skipjack.db."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    document_title TEXT,
    fetch_date TEXT NOT NULL,
    sha256 TEXT,
    extraction_method TEXT,
    checks_json TEXT,
    notes TEXT
);

-- TOTAL ACTIVE REGISTRATION, one row per county x party.
CREATE TABLE IF NOT EXISTS voter_registration (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    report_date TEXT NOT NULL,
    county TEXT NOT NULL,
    party TEXT NOT NULL,
    party_name TEXT NOT NULL,
    active_voters INTEGER NOT NULL
);

-- County-level monthly activity: address_changes, name_changes, confirmation_mailings,
-- inactive (party is NULL), and party_affiliation_changes_from (party = the old party).
CREATE TABLE IF NOT EXISTS registration_activity (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    report_date TEXT NOT NULL,
    county TEXT NOT NULL,
    measure TEXT NOT NULL,
    party TEXT,
    value INTEGER NOT NULL
);

-- Statewide summary: section is new_registration (category = method, party may be
-- DUPS for duplicate applications) or removal (category = reason).
-- value is NULL where the report prints NA.
CREATE TABLE IF NOT EXISTS registration_summary (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    report_date TEXT NOT NULL,
    section TEXT NOT NULL,
    category TEXT NOT NULL,
    party TEXT NOT NULL,
    value INTEGER
);

-- Aggregates from a statewide voter file snapshot (see pipeline/voterfile.py).
CREATE TABLE IF NOT EXISTS voter_file_snapshots (
    snapshot_date TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    meta_json TEXT NOT NULL
);

-- One row per cell of every metric. Columns a metric does not use are NULL.
-- count is NULL where the cell was suppressed (1 to suppress_below - 1 voters).
-- eligible is the number of registrants eligible for the election (turnout metrics).
-- race is an estimate (BISG); count is then an expected number and voters the registrants in
-- the cell (race metrics).
CREATE TABLE IF NOT EXISTS voter_file_counts (
    id INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL REFERENCES voter_file_snapshots(snapshot_date),
    metric TEXT NOT NULL,
    county TEXT,
    party_group TEXT,
    party TEXT,
    status TEXT,
    election TEXT,
    age_band TEXT,
    generation TEXT,
    reg_year INTEGER,
    district_type TEXT,
    district TEXT,
    gender TEXT,
    voter_type TEXT,
    eligible_elections INTEGER,
    reg_window TEXT,
    reg_week INTEGER,
    race TEXT,
    count INTEGER,
    eligible INTEGER,
    voters INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vfc_metric ON voter_file_counts(snapshot_date, metric, county);

CREATE INDEX IF NOT EXISTS idx_vreg_date ON voter_registration(report_date);
CREATE INDEX IF NOT EXISTS idx_vreg_county ON voter_registration(county);
CREATE INDEX IF NOT EXISTS idx_vreg_party ON voter_registration(party);
CREATE INDEX IF NOT EXISTS idx_vreg_county_date ON voter_registration(county, report_date);
CREATE INDEX IF NOT EXISTS idx_activity_date ON registration_activity(report_date, measure);
CREATE INDEX IF NOT EXISTS idx_summary_date ON registration_summary(report_date, section);

-- A press-room turnout stat file snapshot (one row per archived revision).
CREATE TABLE IF NOT EXISTS turnout_sources (
    source_id TEXT PRIMARY KEY,
    election TEXT NOT NULL,
    family TEXT NOT NULL,
    geography TEXT NOT NULL,
    as_of TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    document_title TEXT,
    fetch_date TEXT NOT NULL,
    sha256 TEXT,
    extraction_method TEXT,
    checks_json TEXT,
    notes TEXT
);

-- One cell of one turnout snapshot: mail-in requests by delivery method, or
-- ballots sent / received / accepted by party, per county or district.
CREATE TABLE IF NOT EXISTS turnout_counts (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES turnout_sources(source_id),
    as_of TEXT NOT NULL,
    election TEXT NOT NULL,
    family TEXT NOT NULL,
    geography TEXT NOT NULL,
    county TEXT,
    district TEXT,
    category TEXT NOT NULL,
    party TEXT NOT NULL,
    measure TEXT NOT NULL,
    value INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turnout_counts_asof
    ON turnout_counts (election, family, geography, as_of);

-- One candidate's county-level result in one contest (a row of the SBE's county results CSV).
-- party_file is dem / rep / non for a primary and all for a general. district is text ('' when
-- the contest has none), with leading zeros dropped from numeric districts. winner and write_in
-- are 0/1, winner always the SBE's own Y flag. total_against is set only for judges'
-- retention questions.
CREATE TABLE IF NOT EXISTS results_county (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    election TEXT NOT NULL,
    county TEXT NOT NULL,
    party_file TEXT NOT NULL,
    office TEXT NOT NULL,
    district TEXT NOT NULL,
    candidate TEXT NOT NULL,
    party TEXT,
    winner INTEGER NOT NULL,
    write_in INTEGER NOT NULL,
    total_votes INTEGER NOT NULL,
    total_against INTEGER
);

-- A results_county row's votes by vote type (stem as the registry names it, e.g. early,
-- election_night, mail_in_1). votes_against is NULL where the SBE left it blank (all primaries).
CREATE TABLE IF NOT EXISTS results_votes (
    result_id INTEGER NOT NULL REFERENCES results_county(id),
    vote_type TEXT NOT NULL,
    votes INTEGER NOT NULL,
    votes_against INTEGER
);
CREATE INDEX IF NOT EXISTS idx_results_county_contest
    ON results_county (election, office, district, party_file);
CREATE INDEX IF NOT EXISTS idx_results_county_county ON results_county (election, county);
CREATE INDEX IF NOT EXISTS idx_results_votes_result ON results_votes (result_id);
"""
