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
    count INTEGER,
    eligible INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vfc_metric ON voter_file_counts(snapshot_date, metric, county);

CREATE INDEX IF NOT EXISTS idx_vreg_date ON voter_registration(report_date);
CREATE INDEX IF NOT EXISTS idx_vreg_county ON voter_registration(county);
CREATE INDEX IF NOT EXISTS idx_vreg_party ON voter_registration(party);
CREATE INDEX IF NOT EXISTS idx_vreg_county_date ON voter_registration(county, report_date);
CREATE INDEX IF NOT EXISTS idx_activity_date ON registration_activity(report_date, measure);
CREATE INDEX IF NOT EXISTS idx_summary_date ON registration_summary(report_date, section);
"""
