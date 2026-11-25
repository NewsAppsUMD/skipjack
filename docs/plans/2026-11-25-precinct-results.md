# Precinct Results Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Load the State Board's certified candidate results by precinct, and its ballot-question
results by county and by precinct, for every election from 2010 on; check them against the
county files before anything is written; and offer them as gzipped downloads, database tables,
`skipjack get` output and one precinct page per county per election, whose contest picker
draws any contest or question from the county's published files. No maps.

**Architecture:** The results registry (`pipeline/results.py`) gains three file roles
(`precinct`, `question`, `question-precinct`, plus `question-check` for the statewide question
breakdown), archived under gitignored `archives/sbe/results-precinct/` and
`archives/sbe/results-questions/` but still recorded in `archives/manifest.csv`. The county
reader `_read_wide` learns a `level="precinct"` mode; a new `read_questions` reads both question
layouts. A generic `_check_sums` compares precinct sums with the county files (re-read from the
committed county archives, so the precinct parse never depends on the county parse having run).
Parsed precinct and question-precinct files are written as deterministic `.csv.gz` (gzip
`mtime=0`). `build-db` loads `results_precinct`/`results_precinct_votes` and
`results_questions`/`results_question_votes`. Downloads, the catalog, the data CLI and the
results pages read those.

**Tech Stack:** Python 3.13, polars, httpx, stdlib `gzip`/`csv`, SQLite, FastAPI + Jinja,
pytest. Design: `docs/plans/2026-11-25-precinct-results-design.md`.

---

## Conventions for every task

- Work on `main`; commit after each task. Commit subjects are lowercase sentence-style with a
  scope prefix (`results:`, `db:`, `web:`, `client:`, `docs:`), and the message ends with a
  blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Use
  `git commit -F - <<'EOF'` (the shell is bash 3.2: never `"$(cat <<EOF ...)"`).
- The working tree may hold unrelated edits (`src/skipjack/web/routes/home.py`,
  `src/skipjack/web/summary.py` at the time of writing). Never `git add -A`; add the paths named
  in each task only.
- Tests: `uv run pytest -q tests/<file>`. The default addopts deselect `live`. Before each
  commit run `uv run ruff check src tests` and `uv run ruff format <only the files you touched>`
  (the repo keeps `# fmt: skip` on hand-aligned blocks; leave them).
- `PYTHONPATH` in the user's shell can shadow the `tests` package: test modules import their
  helpers as `from test_results import ...` / `from client_fixture import ...`, never
  `tests.<module>`. There is no `tests/__init__.py`.
- No test touches the network. Where a step says to look at a real board file, it is a public,
  read-only GET for the implementer, done once by hand (`curl -s -A "Mozilla/5.0" URL | head`),
  never in a test.
- Philosophy carried over from the county results: nothing is written until it adds up; the
  board's figures are never corrected; a disagreement is accepted only when listed in an
  exceptions registry with exactly the two figures, and recorded in the sidecar; no silent
  fallback.

## What the board's files look like (checked 2026-11-25 against Kent's files)

Precinct candidate files (one per county and party file):

- **Coded (2022 on):** `{CODE}_{cc}PrecinctsResults.csv` (general),
  `{CODE}_{cc}Precincts{Democratic,Republican,NonPartisan}.csv` (primary). Header:
  `County, County Name, Election District - Precinct, Congressional, Legislative, Office Name,
  Office District, Candidate Name, Party, Winner, [Write-In?], <label> Votes, [<label> Votes
  Against] ...` for every vote type, **no `Total Votes`**. `County` is the code (`"15"`),
  precinct `"001-001"`. GP26's are posted at the live base
  (`https://elections.maryland.gov/elections/2026/election_data/GP26_15PrecinctsDemocratic.csv`).
- **Named 2012-2018:** `{Stem}_By_Precinct_{year}_General.csv`,
  `{Stem}_By_Precinct_{Democratic,Republican,NonPartisan}_{year}_Primary.csv`. Header:
  `County, Election District, Election Precinct, Cong, Legs, Candidate Name, Party, Office Name,
  Office District, Winner, [Write-In?], Election Night Votes, [Election Night Votes Against]`.
- **Named 2010:** as 2012-2018 without `Cong`, `Legs`.
- **PG20:** `{Stem}_By_Precinct_2020_General.csv`: `Cong`, `Legs`, all five 2020 general vote
  types with Against, plus `Total Votes`, `Total Votes Against`. PP20 has none (HTML page).
- `COUNTY_FILE_STEMS` holds for the precinct and question files too (checked:
  `Baltimore_City_`, `Baltimore_` for county 04, `Prince_Georges_`, `Queen_Annes_`, `St._Marys_`).
- Kent's 2018 and 2010 non-partisan primary precinct files come back as the HTML page.

Question files (generals only; `PP24_15QuestionResults.csv` is the HTML page):

- **Coded county** `{CODE}_{cc}QuestionResults.csv`: `County, JurisdictionName, QuestionNumber
  ("1"), How it Runs? ("State"), Winner, <label> Votes, <label> Votes Against ..., Total Votes,
  Total Votes Against`.
- **Coded precinct** `{CODE}_{cc}QuestionbyPrecinctsResults.csv`: `County, County Name, Election
  District - Precinct, Congressional, Legislative, Question, How it Runs?, Winner, <label> Votes,
  <label> Votes Against ...`, no totals.
- **Named county** `{Stem}_Questions_County_{year}.csv`: `County ("00" statewide), Question
  Number ("01"), How it Runs?, Winner, <label> Votes, <label> Votes Against, [<label> 3rd option
  (2016-2020)] ..., Total Votes, Total Votes Against, [Total 3rd option]`.
- **Named precinct** `{Stem}_Questions_Precinct_{year}.csv`: `County, Election District, Election
  Precinct, [Cong, Legs from 2012], Question Number, How it Runs?, Winner, Election Night Votes,
  Election Night Votes Against, [Election Night Votes (3rd option) 2016-2018]`. **PG20's** carries
  every 2020 vote type with `<label> 3rd option` and `Total Votes, Total Votes Against, Total 3rd
  option` in the header, but its rows stop two cells short (24 cells under 26 columns; the
  `Total Votes` cell present is blank).
- **Statewide question breakdown:** coded `{CODE}_AllQuestionsCongressionalBreakDown.csv`
  (`Vote Type (FOR=1,AGAINST=2), Vote Type Description, County, County Name, QuestionNumber, How
  it Runs?, Winner, Congressional District 1..8`; County `00` rows are statewide, other counties'
  rows are their local questions); named 2012-2020
  `Questions_State_Congressional_Districts_{year}.csv` (`Vote Type, County, Question Number, How
  it Runs?, Winner, CONG 04, ...`, with Vote Type 3 rows for the third option). **2010 posts no
  question breakdown.**

---

### Task 1: Registry, file names and fetch by level

**Files:**
- Modify: `src/skipjack/pipeline/results.py` (ResultsElection 157-180, `_election` 183-203,
  PP20 at 225, ResultsFile 241-289, after `county_files` 292-303, `results_fetch` 707-754)
- Modify: `src/skipjack/cli.py` (`cmd_results_fetch` 129-145, `results-fetch` parser 351-355)
- Modify: `.gitignore` (after the `archives/sbe/cvr/` line)
- Create: `tests/test_results_precinct.py`

**Step 1: Write the failing tests**

`tests/test_results_precinct.py`:

```python
"""Tests for the precinct results: file names, the precinct reader, the precinct-to-county check
and the gzipped output."""

from __future__ import annotations

import pytest

from skipjack.pipeline import results

# Not tests.test_results: another project's `tests` package on PYTHONPATH can shadow ours.
from test_results import BOM, GENERAL_HEAD, archive, env, fake_client  # noqa: F401 (env: fixture)

PG24 = results.RESULTS_ELECTIONS["PG24"]
ARCHIVE = "https://elections.maryland.gov/elections/archive"


def test_registry_precinct_and_question_flags():
    elections = results.RESULTS_ELECTIONS
    assert [c for c, e in elections.items() if not e.precincts] == ["PP20"]
    assert {c for c, e in elections.items() if e.questions} == {
        c for c, e in elections.items() if e.kind == "general"
    }
    assert elections["GG18"].precinct_vote_types == [("Election Night", "election_night")]
    assert elections["GP10"].precinct_vote_types == [("Election Night", "election_night")]
    assert PG24.precinct_vote_types == PG24.vote_types
    assert elections["PG20"].precinct_vote_types == results.VOTE_TYPES_2020_GENERAL
    assert elections["GG10"].question_check is False
    assert elections["PG12"].question_check is True and PG24.question_check is True


def test_precinct_file_names_and_ids():
    by_id = {f.source_id: f for f in results.precinct_files(PG24)}
    assert len(by_id) == 24
    kent = by_id["sbe-results-PG24-precinct-15-all"]
    assert kent.url == f"{ARCHIVE}/2024/election_data/PG24_15PrecinctsResults.csv"
    assert kent.local_path.parts[-4:] == ("sbe", "results-precinct", "PG24", kent.file_name)
    gp22 = {f.source_id: f for f in results.precinct_files(results.RESULTS_ELECTIONS["GP22"])}
    assert gp22["sbe-results-GP22-precinct-15-non"].file_name == "GP22_15PrecinctsNonPartisan.csv"
    assert gp22["sbe-results-GP22-precinct-15-dem"].file_name == "GP22_15PrecinctsDemocratic.csv"
    gg18 = {f.source_id: f for f in results.precinct_files(results.RESULTS_ELECTIONS["GG18"])}
    assert gg18["sbe-results-GG18-precinct-15-all"].url == (
        f"{ARCHIVE}/2018/election_data/Kent_By_Precinct_2018_General.csv"
    )
    assert gg18["sbe-results-GG18-precinct-19-all"].file_name == (
        "St._Marys_By_Precinct_2018_General.csv"
    )
    gp10 = {f.source_id: f for f in results.precinct_files(results.RESULTS_ELECTIONS["GP10"])}
    assert gp10["sbe-results-GP10-precinct-15-rep"].file_name == (
        "Kent_By_Precinct_Republican_2010_Primary.csv"
    )
    assert results.precinct_files(results.RESULTS_ELECTIONS["PP20"]) == []


def test_question_file_names_and_ids():
    by_id = {f.source_id: f for f in results.question_files(PG24)}
    assert len(by_id) == 49  # 24 county files, 24 precinct files, one statewide breakdown
    assert by_id["sbe-results-PG24-question-15"].file_name == "PG24_15QuestionResults.csv"
    assert by_id["sbe-results-PG24-question-precinct-15"].file_name == (
        "PG24_15QuestionbyPrecinctsResults.csv"
    )
    check = by_id["sbe-results-PG24-question-check"]
    assert check.file_name == "PG24_AllQuestionsCongressionalBreakDown.csv"
    assert check.local_path.parts[-3:-1] == ("results-questions", "PG24")
    gg18 = {f.source_id: f for f in results.question_files(results.RESULTS_ELECTIONS["GG18"])}
    assert gg18["sbe-results-GG18-question-15"].file_name == "Kent_Questions_County_2018.csv"
    assert gg18["sbe-results-GG18-question-precinct-15"].file_name == (
        "Kent_Questions_Precinct_2018.csv"
    )
    assert gg18["sbe-results-GG18-question-check"].file_name == (
        "Questions_State_Congressional_Districts_2018.csv"
    )
    gg10 = results.question_files(results.RESULTS_ELECTIONS["GG10"])
    assert len(gg10) == 48 and all(f.role != "question-check" for f in gg10)
    assert results.question_files(results.RESULTS_ELECTIONS["GP26"]) == []


def test_files_for_each_level():
    assert results.files_for(PG24, "county") == results.county_files(PG24)
    assert results.files_for(PG24, "precinct") == results.precinct_files(PG24)
    assert results.files_for(PG24, "questions") == results.question_files(PG24)
    assert len(results.files_for(PG24, "all")) == 25 + 24 + 49
    with pytest.raises(ValueError):
        results.files_for(PG24, "towns")


def test_fetch_precinct_level_archives_only_the_precinct_files(env, monkeypatch):
    monkeypatch.setattr(results.httpx, "Client", fake_client(lambda url: b'"County"\n"15"\n'))
    records = results.results_fetch("PG24", level="precinct")
    assert len(records) == 24
    assert all("/results-precinct/PG24/" in r.local_path for r in records)
    assert records[0].document_title == "PG24 Allegany results by precinct (all)"
    assert results.results_fetch("PG24", level="precinct") == []  # archived already
    questions = results.results_fetch("PG24", level="questions")
    assert len(questions) == 49
    assert {r.document_title for r in questions} >= {
        "PG24 Kent ballot questions",
        "PG24 Kent ballot questions by precinct",
        "PG24 ballot questions congressional breakdown",
    }
```

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_precinct.py`
Expected: FAIL, `AttributeError: 'ResultsElection' object has no attribute 'precincts'` (and
`module 'skipjack.pipeline.results' has no attribute 'precinct_files'`).

**Step 3: Implement**

In `ResultsElection` (after `published`, line 176):

```python
    # the board posts precinct files for every election but the 2020 primary
    precincts: bool = True
    # ballot questions are on general-election ballots only (set by _election)
    questions: bool = False

    @property
    def precinct_vote_types(self) -> list[tuple[str, str]]:
        """The vote types the precinct files carry: through 2018 only election-night votes are
        split by precinct; from 2020 every vote type is."""
        if self.year <= 2018:
            return [("Election Night", "election_night")]
        return list(self.vote_types)

    @property
    def question_check(self) -> bool:
        """Whether the board posts a statewide ballot-question breakdown (not for 2010)."""
        return self.questions and self.year >= 2012
```

`_election` gains `precincts=True` and sets the two flags:

```python
def _election(code, year, kind, name, day, live=False, published=True, precincts=True):
    base = (LIVE_BASE if live else ARCHIVE_BASE).format(year=year)
    e = ResultsElection(code, year, kind, name, day, base, published=published)
    e.precincts = precincts
    e.questions = kind == "general"
    ...  # the layout rules as they are
```

and PP20 (line 225) becomes
`_election("PP20", 2020, "primary", "2020 presidential primary", "2020-06-02", precincts=False),`.

`ResultsFile.role` comment becomes `# "county" | "check" | "precinct" | "question" |
"question-precinct" | "question-check"`. Add above the class:

```python
# The precinct and question archives are gitignored (about 250 MB, re-fetchable); the county
# files stay in the committed results/ folder.
ARCHIVE_FOLDERS = {
    "county": "results",
    "check": "results",
    "precinct": "results-precinct",
    "question": "results-questions",
    "question-precinct": "results-questions",
    "question-check": "results-questions",
}
DOCUMENT_TITLES = {
    "county": "{code} {county} results ({pf})",
    "check": "{code} congressional breakdown ({pf})",
    "precinct": "{code} {county} results by precinct ({pf})",
    "question": "{code} {county} ballot questions",
    "question-precinct": "{code} {county} ballot questions by precinct",
    "question-check": "{code} ballot questions congressional breakdown",
}
```

In `ResultsFile`, the first lines of `file_name` route the new roles, and the new helper names
them (the county and check branches stay as they are):

```python
    @property
    def file_name(self) -> str:
        if self.role not in ("county", "check"):
            return self._precinct_or_question_name()
        ...  # unchanged

    def _precinct_or_question_name(self) -> str:
        e, cc = self.election, self.county_code
        if e.layout == "coded":
            if self.role == "precinct":
                if e.kind == "general":
                    return f"{e.code}_{cc}PrecinctsResults.csv"
                return f"{e.code}_{cc}Precincts{PARTY_FILES_NAMED[self.party_file]}.csv"
            return {
                "question": f"{e.code}_{cc}QuestionResults.csv",
                "question-precinct": f"{e.code}_{cc}QuestionbyPrecinctsResults.csv",
                "question-check": f"{e.code}_AllQuestionsCongressionalBreakDown.csv",
            }[self.role]
        if self.role == "question-check":
            return f"Questions_State_Congressional_Districts_{e.year}.csv"
        stem = COUNTY_FILE_STEMS[cc]
        if self.role == "precinct":
            if e.kind == "general":
                return f"{stem}_By_Precinct_{e.year}_General.csv"
            return f"{stem}_By_Precinct_{PARTY_FILES_NAMED[self.party_file]}_{e.year}_Primary.csv"
        level = "County" if self.role == "question" else "Precinct"
        return f"{stem}_Questions_{level}_{e.year}.csv"
```

`source_id`, `local_path` and a new `document_title`:

```python
    @property
    def source_id(self) -> str:
        code = self.election.code
        if self.role == "check":
            return f"sbe-results-{code}-check-{self.party_file}"
        if self.role in ("county", "precinct"):
            return f"sbe-results-{code}-{self.role}-{self.county_code}-{self.party_file}"
        if self.role == "question-check":
            return f"sbe-results-{code}-question-check"
        return f"sbe-results-{code}-{self.role}-{self.county_code}"  # question, question-precinct

    @property
    def local_path(self) -> Path:
        folder = ARCHIVE_FOLDERS[self.role]
        return ARCHIVES_DIR / "sbe" / folder / self.election.code / self.file_name

    @property
    def document_title(self) -> str:
        return DOCUMENT_TITLES[self.role].format(
            code=self.election.code, county=self.county, pf=self.party_file
        )
```

After `county_files`:

```python
def precinct_files(election: ResultsElection) -> list[ResultsFile]:
    """One precinct file per county and party file (none for the 2020 primary)."""
    if not election.precincts:
        return []
    return [
        ResultsFile(election, code, name, pf, "precinct")
        for code, name in COUNTY_CODES.items()
        for pf in election.party_files
    ]


def question_files(election: ResultsElection) -> list[ResultsFile]:
    """A general's ballot-question files: each county's county and precinct files, and the
    statewide breakdown where the board posts one."""
    if not election.questions:
        return []
    files = [
        ResultsFile(election, code, name, "all", role)
        for role in ("question", "question-precinct")
        for code, name in COUNTY_CODES.items()
    ]
    if election.question_check:
        files.append(ResultsFile(election, "00", "State of Maryland", "all", "question-check"))
    return files


FETCH_LEVELS = ("county", "precinct", "questions", "all")


def files_for(election: ResultsElection, level: str = "all") -> list[ResultsFile]:
    """The files of one level ("county", "precinct", "questions") or of all three."""
    if level not in FETCH_LEVELS:
        raise ValueError(f"unknown level {level!r}; one of {', '.join(FETCH_LEVELS)}")
    files = []
    if level in ("county", "all"):
        files += county_files(election)
    if level in ("precinct", "all"):
        files += precinct_files(election)
    if level in ("questions", "all"):
        files += question_files(election)
    return files
```

`results_fetch` gains `level: str = "all"`; its loop reads `for f in files_for(e, level):`; the
unpublished branch tolerates a missing non-partisan precinct file as it does a county one:

```python
            if status == "unpublished":
                if f.party_file == "non" and f.role in ("county", "precinct"):
                    logger.info("%s: no non-partisan %s file for %s (normal)", e.code, f.role, f.county)
                else:
                    logger.warning("%s: not available: %s", e.code, f.url)
                continue
```

and `document_title=f.document_title,` replaces the inline conditional. Update the docstring's
first line to "Archive every file of the level (county, precinct, questions or all) not yet
archived".

`.gitignore`, after `archives/sbe/cvr/`:

```
# The board's precinct and ballot-question files: about 250 MB and re-fetchable with
# `skipjack results-fetch`; their sha256 stays in archives/manifest.csv
archives/sbe/results-precinct/
archives/sbe/results-questions/
```

(Why separate folders rather than a pattern inside `archives/sbe/results/`: the county files
there are committed and the names overlap in shape — `PG24_15PrecinctsResults.csv` beside
`PG24_15CountyResults.csv` — so a glob would be one typo from ignoring a county file; a folder
per kind is unambiguous and keeps `git status` honest.)

`cli.py`: the parser (after line 355)

```python
    results_fetch_p.add_argument(
        "--level",
        choices=["county", "precinct", "questions", "all"],
        default="all",
        help="Which files: the county files, the precinct files, the ballot questions, or all",
    )
```

and `cmd_results_fetch` passes it:
`records = results_fetch(election=args.election, force=args.force, failures=failures, level=args.level)`.

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_precinct.py tests/test_results.py`
Expected: PASS. Then confirm the ignore rule by hand:
`git check-ignore -v archives/sbe/results-precinct/PG24/x.csv archives/sbe/results-questions/PG24/x.csv`
prints both, and `git check-ignore archives/sbe/results/PG24/PG24_15CountyResults.csv` prints
nothing.

**Step 5: Commit**

```bash
git add src/skipjack/pipeline/results.py src/skipjack/cli.py .gitignore tests/test_results_precinct.py
git commit -F - <<'EOF'
results: the board's precinct and ballot-question files in the registry, fetched by level

The raw files are archived in their own gitignored folders and recorded in the manifest.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The coded-layout precinct reader

`_read_wide` already reads every county file by column name and checks row totals. It learns a
`level="precinct"` mode: the precinct id columns, the precinct vote types, and a total computed
from the vote types where the file prints none (the coded files never do).

**Files:**
- Modify: `src/skipjack/pipeline/results.py` (`ResultsElection` properties ~178, `OUTPUT_ID_COLUMNS`
  122-132, `_read_wide` 395-519, new `read_precinct` after `READERS` 392)
- Test: `tests/test_results_precinct.py`

**Step 1: Write the failing tests**

Append to `tests/test_results_precinct.py` (and add `import polars as pl` to its imports):

```python
# ---- the coded-layout precinct reader --------------------------------------------------------

PRECINCT_GENERAL_HEAD = (
    '"County","County Name","Election District - Precinct","Congressional","Legislative",'
    '"Office Name","Office District","Candidate Name","Party","Winner","Write-In?",'
    '"Early Votes","Early Votes Against","Election Night Votes","Election Night Votes Against",'
    '"Mail-In Ballot 1 Votes","Mail-In Ballot 1 Votes Against","Provisional Votes",'
    '"Provisional Votes Against","Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against" \n'
)
# Two precincts of one contest, as PG24's files print them (CC becomes the county's code).
# Harris 70+149+23+22+124 = 388 and 30+51+7+8+16 = 112; Trump 99+390+12+38+51 = 590 and
# 1+10+0+2+9 = 22: the county rows below are the sums.
PRECINCT_ROWS = (
    '"CC","Kent County","001-001","01","36","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","70","","149","","23","","22","","124","" \n'
    '"CC","Kent County","001-001","01","36","President - Vice Pres","","Donald J. Trump and JD Vance","REP","","","99","","390","","12","","38","","51","" \n'
    '"CC","Kent County","001-002","01","36","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","30","","51","","7","","8","","16","" \n'
    '"CC","Kent County","001-002","01","36","President - Vice Pres","","Donald J. Trump and JD Vance","REP","","","1","","10","","0","","2","","9","" \n'
)
COUNTY_ROWS = (
    '"President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","100","","200","","30","","30","","140","","500","" \n'
    '"President - Vice Pres","","Donald J. Trump and JD Vance","REP","","","100","","400","","12","","40","","60","","612","" \n'
)
PRECINCT_COLUMNS = [
    "election", "county", "party_file", "precinct", "congressional", "legislative", "office",
    "district", "candidate", "party", "winner", "write_in",
]  # fmt: skip


def precinct_csv(code: str = "15", rows: str = PRECINCT_ROWS) -> bytes:
    return (BOM + PRECINCT_GENERAL_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def county_csv(rows: str = COUNTY_ROWS) -> bytes:
    return (BOM + GENERAL_HEAD + rows).encode()


def test_read_precinct_coded_general_computes_the_totals():
    read = results.read_precinct(precinct_csv(), PG24, "Kent", "all")
    frame = read.frame
    assert frame.columns[:12] == PRECINCT_COLUMNS
    assert frame.columns[-2:] == ["total_votes", "total_against"]
    assert frame["precinct"].to_list() == ["001-001", "001-001", "001-002", "001-002"]
    assert frame["congressional"][0] == "01" and frame["legislative"][0] == "36"
    assert frame["county"].unique().to_list() == ["Kent"]
    assert frame["total_votes"].to_list() == [388, 590, 112, 22]
    assert frame["total_against"].to_list() == [None, None, None, None]
    assert frame["winner"].to_list() == [1, 0, 1, 0]
    assert read.row_total_exceptions == []


def test_read_precinct_sums_the_against_votes_of_a_retention_question():
    row = (
        '"15","Kent County","001-001","01","36","Judge","","Jane Roe","","","",'
        '"3","1","3","2","3","1","0","0","1","0" \n'
    )
    frame = results.read_precinct((PRECINCT_GENERAL_HEAD + row).encode(), PG24, "Kent", "all").frame
    assert frame["total_votes"].to_list() == [10] and frame["total_against"].to_list() == [4]


def test_read_precinct_coded_primary():
    head = (
        '"County","County Name","Election District - Precinct","Congressional","Legislative",'
        '"Office Name","Office District","Candidate Name","Party","Winner","Early Votes",'
        '"Election Night Votes","Mail-In Ballot 1 Votes","Provisional Votes",'
        '"Mail-In Ballot 2 Votes" \n'
    )
    row = '"15","Kent County","1-12","01","36","President - Vice Pres","","Joe Biden","DEM","Y","11","18","16","6","3" \n'
    e = results.RESULTS_ELECTIONS["PP24"]
    frame = results.read_precinct((head + row).encode(), e, "Kent", "dem").frame
    assert frame["precinct"].to_list() == ["001-012"]  # normalized to DDD-PPP
    assert frame["total_votes"].to_list() == [54]
    assert "total_against" not in frame.columns and frame["write_in"].to_list() == [0]


def test_read_precinct_refuses_another_countys_rows_and_a_bad_precinct():
    with pytest.raises(results.ResultsParseError, match="not Kent's code 15"):
        results.read_precinct(precinct_csv("16"), PG24, "Kent", "all")
    bad = PRECINCT_ROWS.replace('"001-002"', '"Absentee"')
    with pytest.raises(results.ResultsParseError, match="'Absentee' is not district-precinct"):
        results.read_precinct(precinct_csv("15", bad), PG24, "Kent", "all")


def test_read_precinct_names_the_precinct_file_when_columns_are_missing():
    head = PRECINCT_GENERAL_HEAD.replace('"Legislative",', "")
    rows = PRECINCT_ROWS.replace('"36",', "")
    with pytest.raises(results.ResultsParseError, match="PG24_15PrecinctsResults.csv.*Legislative"):
        results.read_precinct((head + rows.replace('"CC"', '"15"')).encode(), PG24, "Kent", "all")
```

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_precinct.py -k read_precinct`
Expected: FAIL, `AttributeError: module 'skipjack.pipeline.results' has no attribute 'read_precinct'`.

**Step 3: Implement**

After `OUTPUT_ID_COLUMNS` (line 132):

```python
PRECINCT_ID_COLUMNS = [
    "election", "county", "party_file", "precinct", "congressional", "legislative", "office",
    "district", "candidate", "party", "winner", "write_in",
]  # fmt: skip
```

In `ResultsElection`, beside `precinct_vote_types`:

```python
    @property
    def precinct_total(self) -> bool:
        """Whether the precinct files print Total Votes (only the 2020 general's do); elsewhere
        the precinct total is the sum of the vote types."""
        return self.layout == "named" and self.year == 2020
```

Two helpers before `_read_wide`:

```python
def _precinct_id_columns(election: ResultsElection) -> list[str]:
    """The columns that place a precinct row."""
    if election.layout == "coded":
        return ["County", "Election District - Precinct", "Congressional", "Legislative"]
    raise ResultsParseError(f"{election.code}: no precinct reader for the named layout yet")


def _precinct_id(r: dict, where: str) -> str:
    """The precinct as DDD-PPP, from either layout's columns."""
    if "Election District - Precinct" in r:
        value = r["Election District - Precinct"]
    else:
        value = f"{r['Election District']}-{r['Election Precinct']}"
    district, _, precinct = value.partition("-")
    if not all(p.isascii() and p.isdigit() for p in (district, precinct)):
        raise ResultsParseError(f"{where}: precinct {value!r} is not district-precinct")
    return f"{int(district):03d}-{int(precinct):03d}"
```

After `READERS` (line 392):

```python
def read_precinct(
    data: bytes, election: ResultsElection, county: str, party_file: str
) -> ReadFile:
    """One county's precinct file (a row per precinct and candidate) as the wide output frame,
    with precinct, congressional and legislative after party_file. Read like the county files of
    its layout; the total is checked where the file prints one and summed where it does not."""
    return _read_wide(
        data, election, county, party_file, blank_is_zero=election.layout == "named",
        level="precinct",
    )  # fmt: skip
```

`_read_wide` gains `level: str = "county"` as its last parameter; replace its body up to the
row loop and the parts of the loop that touch totals. The whole function becomes:

```python
def _read_wide(
    data: bytes,
    election: ResultsElection,
    county: str,
    party_file: str,
    blank_is_zero: bool,
    level: str = "county",
) -> ReadFile:
    """... (keep the existing docstring lines, then:)

    level "precinct" reads a precinct file: its id columns, the election's precinct vote types,
    and a Total Votes that is checked where the file prints one (election.precinct_total) and
    otherwise the sum of the vote types.
    """
    where = f"{election.code} {county} {party_file}" + (" precincts" if level == "precinct" else "")
    raw = _frame(data, where)
    against = election.kind == "general"
    code = next((c for c, n in COUNTY_CODES.items() if n == county), "00")
    precinct = level == "precinct"
    vote_types = election.precinct_vote_types if precinct else election.vote_types
    printed = not precinct or election.precinct_total
    need = ["Office Name", "Office District", "Candidate Name", "Party", "Winner"]
    if precinct:
        need = _precinct_id_columns(election) + need
    if printed:
        need.append("Total Votes")
    for label, _ in vote_types:
        need.append(f"{label} Votes")
        if against:
            need.append(f"{label} Votes Against")
    if against:
        need.append("Write-In?")
        if printed:
            need.append("Total Votes Against")
    missing = [c for c in need if c not in raw.columns]
    if missing:
        expected = ResultsFile(election, code, county, party_file, level).file_name
        raise ResultsParseError(
            f"{where}: expected {election.code} {election.layout} layout columns in {expected}; "
            f"missing {missing}"
        )
    ...  # the unknown-columns and header-only checks, unchanged

    rows = []
    allowed_total_mismatches = (
        0 if precinct else ROW_TOTAL_EXCEPTIONS.get((election.code, county, party_file), 0)
    )
    row_total_exceptions: list[dict] = []
    for r in raw.iter_rows(named=True):
        who = f"{where}: {r['Office Name']} / {r['Candidate Name']}"
        out = {"election": election.code, "county": county, "party_file": party_file}
        if precinct:
            if r["County"] != code:
                raise ResultsParseError(f"{who}: County {r['County']!r} is not {county}'s code {code}")
            out["precinct"] = _precinct_id(r, who)
            out["congressional"] = r.get("Congressional", r.get("Cong", ""))
            out["legislative"] = r.get("Legislative", r.get("Legs", ""))
        out |= {
            "office": r["Office Name"],
            "district": r["Office District"],
            "candidate": r["Candidate Name"],
            "party": r["Party"],
            "winner": 1 if r["Winner"].upper() in ("Y", "YES") else 0,
            "write_in": 1 if against and r["Write-In?"].upper() == "Y" else 0,
        }
        votes_sum, against_sum, against_seen = 0, 0, False
        for label, stem in vote_types:
            ...  # unchanged
        if printed:
            total = _int(r["Total Votes"], who, "Total Votes")
            if total is None and blank_is_zero:
                total = 0
            if total is None:
                raise ResultsParseError(f"{who}: blank Total Votes")
        else:
            total = votes_sum
        out["total_votes"] = total
        if total != votes_sum:
            ...  # the row-total mismatch handling, unchanged
        if against and not printed:
            out["total_against"] = against_sum if against_seen else None
        elif against:
            ...  # the printed Total Votes Against handling, unchanged
        rows.append(out)
    ...  # the exceptions-count check, unchanged

    ids = PRECINCT_ID_COLUMNS if precinct else OUTPUT_ID_COLUMNS
    schema = {c: pl.Utf8 for c in ids if c not in ("winner", "write_in")}
    schema |= {"winner": pl.Int64, "write_in": pl.Int64}
    cols = []
    for _, stem in vote_types:
        ...  # unchanged
```

Keep the existing order of the `need` list's error message in mind: tests in
`tests/test_results.py` (`test_missing_columns_message_names_election_layout_and_file`) match on
its text, so leave the wording identical.

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_precinct.py tests/test_results.py`
Expected: PASS (the county tests prove the refactor kept the county path).

**Step 5: Commit**

```bash
git add src/skipjack/pipeline/results.py tests/test_results_precinct.py
git commit -F - <<'EOF'
results: precinct files for the coded layout, read like the county files

The coded precinct files print no total, so total_votes is the sum of the vote types.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The precinct-to-county check and the gzipped output

Decision: the precinct parse re-reads the **archived county files** with the county readers
(`_read_county_files`, factored out of `_parse_election`), not the CSVs in `data/`. The county
archives are committed, so this needs nothing from a previous parse, checks against the board's
own county file rather than our output, and costs a second or two.

Sidecars of `x.csv.gz` must be `x_provenance.json` (not `x.csv_provenance.json`, which
`Path.stem` would give): a `sidecar_path()` helper in `provenance.py` becomes the one place that
decides, and `ProvenanceSidecar.write`, `_revision_updated` and `build_db.load_provenance` use it.

**Files:**
- Modify: `src/skipjack/pipeline/provenance.py` (`ProvenanceSidecar.write` 96-101; new
  `sidecar_path` after `compute_sha256` ~125)
- Modify: `src/skipjack/pipeline/build_db.py` (`load_provenance` 61-65)
- Modify: `src/skipjack/pipeline/results.py` (imports 14-35, new registry dict after
  `ROW_TOTAL_EXCEPTIONS` 150, `results_parse` 760-782, `_revision_updated` 793-799,
  `_parse_election` 824-917, new functions after it)
- Modify: `src/skipjack/cli.py` (`cmd_results_parse` 148-160, `results-parse` parser 357-361)
- Test: `tests/test_results_precinct.py`

**Step 1: Write the failing tests**

Append to `tests/test_results_precinct.py` (add `import gzip`, `import json` and
`from pathlib import Path` to its imports):

```python
# ---- the precinct-to-county check -----------------------------------------------------------

TOTAL_COLUMNS = [
    "early_votes", "early_against", "election_night_votes", "election_night_against",
    "mail_in_1_votes", "mail_in_1_against", "provisional_votes", "provisional_against",
    "mail_in_2_votes", "mail_in_2_against", "total_votes", "total_against",
]  # fmt: skip


def frames(precincts: str = PRECINCT_ROWS, county: str = COUNTY_ROWS):
    return (
        results.read_precinct(precinct_csv("15", precincts), PG24, "Kent", "all").frame,
        results._read_coded(county_csv(county), PG24, "Kent", "all").frame,
    )


def test_sidecar_path_drops_csv_and_gz():
    assert results.sidecar_path(Path("d/15-kent-all.csv.gz")) == Path("d/15-kent-all_provenance.json")
    assert results.sidecar_path(Path("d/15-kent-all.csv")) == Path("d/15-kent-all_provenance.json")


def test_precinct_check_compares_every_vote_type_and_the_totals():
    check = results._check_precinct_county(PG24, "Kent", "all", *frames())
    assert check == results.SumCheck(contests=1, rows=2, columns=TOTAL_COLUMNS, exceptions=[])


def test_precinct_check_names_the_row_and_column_that_differ():
    precincts = PRECINCT_ROWS.replace('"390"', '"389"')
    with pytest.raises(
        results.ResultsParseError,
        match=r"Trump and JD Vance \(REP\): precincts sum to 399 election_night_votes, "
        r"the county file says 400",
    ):
        results._check_precinct_county(PG24, "Kent", "all", *frames(precincts))


def test_precinct_check_needs_every_contest_on_both_sides():
    senate = '"U.S. Senator","","Some One","DEM","Y","","1","","0","","0","","0","","0","","1","" \n'
    with pytest.raises(results.ResultsParseError, match="U.S. Senator has no precinct rows"):
        results._check_precinct_county(PG24, "Kent", "all", *frames(county=COUNTY_ROWS + senate))
    senate_precinct = '"CC","Kent County","001-001","01","36","U.S. Senator","","Some One","DEM","Y","","1","","0","","0","","0","","0","" \n'
    with pytest.raises(results.ResultsParseError, match="U.S. Senator is not in the county file"):
        results._check_precinct_county(
            PG24, "Kent", "all", *frames(precincts=PRECINCT_ROWS + senate_precinct)
        )


def test_a_county_row_of_zeros_needs_no_precinct_rows():
    zero = '"President - Vice Pres","","Other Write-Ins","BOT","","Y","0","","0","","0","","0","","0","","0","" \n'
    check = results._check_precinct_county(PG24, "Kent", "all", *frames(county=COUNTY_ROWS + zero))
    assert check.rows == 3


def test_a_listed_precinct_exception_is_accepted_and_reported(monkeypatch):
    key = ("PG24", "Kent", "all", "President - Vice Pres", "", "Donald J. Trump and JD Vance", "REP")
    monkeypatch.setitem(results.PRECINCT_CHECK_EXCEPTIONS, (*key, "election_night_votes"), (399, 400))
    monkeypatch.setitem(results.PRECINCT_CHECK_EXCEPTIONS, (*key, "total_votes"), (611, 612))
    check = results._check_precinct_county(
        PG24, "Kent", "all", *frames(PRECINCT_ROWS.replace('"390"', '"389"'))
    )
    assert len(check.exceptions) == 2
    assert check.exceptions[0].endswith("election_night_votes: precincts 399, county file 400")


# ---- parse ------------------------------------------------------------------------------------


def fill_precincts(env, election=PG24, overrides=None, skip=()):
    """Archive every county's county file and precinct file: the same two-precinct fixture in each
    county under its own code; `overrides` (source id -> bytes) replaces a precinct file."""
    for f in results.county_files(election):
        if f.role == "county":
            archive(env, election, f, county_csv())
    for f in results.precinct_files(election):
        if f.source_id not in skip:
            archive(env, election, f, (overrides or {}).get(f.source_id) or precinct_csv(f.county_code))


def test_parse_precincts_writes_gzipped_csvs_and_sidecars(env):
    fill_precincts(env)
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert failed == [] and written == 24
    out = env / "data" / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    frame = pl.read_csv(out, infer_schema=False)
    assert frame.height == 4 and frame["precinct"].to_list()[:2] == ["001-001", "001-001"]
    side = json.loads((out.parent / "15-kent-all_provenance.json").read_text())
    assert side["source_id"] == "sbe-results-PG24-precinct-15-all"
    assert side["family"] == "results" and side["geography"] == "precinct"
    assert side["checks"]["rows"] == 4
    assert side["checks"]["row_totals"].startswith("computed")
    assert side["checks"]["county_check"] == (
        "ok: the precincts sum to PG24_15CountyResults.csv's 2 rows (1 contests) on "
        + ", ".join(TOTAL_COLUMNS)
    )
    assert not (env / "data" / "results" / "PG24" / "county").exists()  # precinct level only


def test_parse_precincts_writes_the_same_bytes_every_time(env):
    fill_precincts(env)
    results.results_parse("PG24", data_dir=env / "data", level="precinct")
    out = env / "data" / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    first = out.read_bytes()
    assert results.results_parse("PG24", force=True, data_dir=env / "data", level="precinct")[0] == 24
    assert out.read_bytes() == first
    assert gzip.decompress(first).startswith(b"election,county,party_file,precinct")


def test_parse_precincts_is_all_or_nothing(env):
    bad = precinct_csv("24", PRECINCT_ROWS.replace('"149"', '"150"'))
    fill_precincts(env, overrides={"sbe-results-PG24-precinct-24-all": bad})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 0 and len(failed) == 1
    assert "PG24_24PrecinctsResults.csv" in failed[0] and "election_night_votes" in failed[0]
    assert not (env / "data" / "results" / "PG24" / "precinct").exists()


def test_parse_precincts_missing_required_file_fails(env):
    fill_precincts(env, skip={"sbe-results-PG24-precinct-05-all"})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="precinct")
    assert written == 0 and "PG24_05PrecinctsResults.csv" in failed[0]


def test_current_precinct_csvs_need_no_archive(env):
    """The precinct archives are gitignored: a clone without them must not fail to parse."""
    fill_precincts(env)
    results.results_parse("PG24", data_dir=env / "data", level="precinct")
    for p in (env / "archives").rglob("*Precincts*"):
        p.unlink()
    assert results.results_parse("PG24", data_dir=env / "data", level="precinct") == (0, [])
```

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_precinct.py`
Expected: FAIL, `AttributeError: module 'skipjack.pipeline.results' has no attribute 'sidecar_path'`
(then `_check_precinct_county`, `SumCheck`, and `results_parse() got an unexpected keyword
argument 'level'`).

**Step 3: Implement**

`provenance.py`, after `compute_sha256`:

```python
def sidecar_path(data_path: Path) -> Path:
    """Where a data file's provenance sidecar sits: x.csv and x.csv.gz both give
    x_provenance.json."""
    name = data_path.name.removesuffix(".gz")
    return data_path.with_name(Path(name).stem + "_provenance.json")
```

and `ProvenanceSidecar.write` uses it (the local is named `path` so it does not shadow the
function):

```python
    def write(self, csv_path: Path) -> Path:
        path = sidecar_path(csv_path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n")
        os.replace(tmp, path)
        return path
```

(`sidecar_path` is defined at module level below the class; that is fine at call time.)

`build_db.load_provenance`: `sidecar = sidecar_path(csv_path)` with
`from skipjack.pipeline.provenance import sidecar_path`.

`results.py`: add `import gzip` and `from collections.abc import Callable`; import
`sidecar_path` from `skipjack.pipeline.provenance` (the test reaches it as
`results.sidecar_path`). `_revision_updated` uses `sidecar = sidecar_path(csv_path)`.

After `ROW_TOTAL_EXCEPTIONS`:

```python
# Precinct rows that do not sum to the board's own county file: (election code, county, party
# file, office, district, candidate, party, column) -> (what the precincts sum to, what the county
# file says). Empty until a full run finds one; a mismatch is accepted only when it equals the
# listed pair, and any other fails the election.
PRECINCT_CHECK_EXCEPTIONS: dict[tuple[str, ...], tuple[int, int]] = {}
```

Replace the archive-reading part of `_parse_election` (lines 826-852) with two helpers, and call
`county_frames, breakdowns = _read_county_files(e)` there (keep `files = county_files(e)`, which
the rest of the function reads):

```python
def _read_archived(f: ResultsFile) -> tuple[bytes, SourceRecord] | None:
    """The newest archived revision of a file and its manifest row, or None when it is not
    archived. Refuses bytes that are not the ones the manifest recorded."""
    path = _archived(f)
    if path is None:
        return None
    data = path.read_bytes()
    record = newest_source(MANIFEST_PATH, f.source_id)
    assert record is not None
    if hashlib.sha256(data).hexdigest() != record.sha256:
        raise ResultsParseError(
            f"{f.file_name}: archived bytes do not match the newest manifest sha256 "
            f"({f.source_id}); the archive and manifest are out of step, so re-fetch with "
            f"`skipjack results-fetch --force`"
        )
    return data, record


CountyFrames = dict[str, list[tuple[ResultsFile, SourceRecord, ReadFile]]]


def _read_county_files(
    e: ResultsElection, with_checks: bool = True
) -> tuple[CountyFrames, dict[str, bytes]]:
    """Every archived county file read with its layout's reader, by party file, and the
    statewide check files' bytes (skipped when with_checks is False). A missing non-partisan
    file is normal; any other missing file fails."""
    county_frames: CountyFrames = {}
    breakdowns: dict[str, bytes] = {}
    for f in county_files(e):
        if f.role == "check" and not with_checks:
            continue
        archived = _read_archived(f)
        if archived is None:
            if f.party_file == "non":
                continue  # not every county has non-partisan contests; the breakdown may be absent too
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
        data, record = archived
        if f.role == "check":
            breakdowns[f.party_file] = data
            continue
        try:
            read = READERS[e.layout](data, e, f.county, f.party_file)
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        county_frames.setdefault(f.party_file, []).append((f, record, read))
    return county_frames, breakdowns
```

The generic sum check and the candidate wrapper:

```python
class SumCheck(NamedTuple):
    contests: int  # contests (or questions) compared
    rows: int  # county-file rows compared
    columns: list[str]
    # the listed exceptions applied, as "<row> <column>: precincts N, county file M"
    exceptions: list[str]


def _check_sums(
    where: str,
    keys: list[str],
    contest_keys: int,
    columns: list[str],
    parts: pl.DataFrame,
    whole: pl.DataFrame,
    listed: Callable[[tuple, str], tuple[int, int] | None],
) -> SumCheck:
    """``parts`` (precinct rows) summed by ``keys`` must equal ``whole`` (the county file) on
    every column, blank counting as 0. The first ``contest_keys`` keys name a contest (office and
    district; a question number): a contest in one file and not the other fails. Within a
    contest, a row missing on one side counts as zeros, so a county row of zeros (a write-in line
    nobody used) needs no precinct rows. A difference passes only when ``listed`` returns exactly
    (precinct sum, county figure) for that row and column."""

    def summed(frame: pl.DataFrame) -> dict[tuple, dict[str, int]]:
        out: dict[tuple, dict[str, int]] = {}
        for r in frame.select(*keys, *columns).iter_rows(named=True):
            sums = out.setdefault(tuple(r[k] for k in keys), dict.fromkeys(columns, 0))
            for c in columns:
                sums[c] += r[c] or 0
        return out

    mine, board = summed(parts), summed(whole)
    contests_mine = {k[:contest_keys] for k in mine}
    contests_board = {k[:contest_keys] for k in board}
    if unmatched := sorted(contests_board - contests_mine):
        raise ResultsParseError(f"{where}: {' '.join(unmatched[0]).strip()} has no precinct rows")
    if unmatched := sorted(contests_mine - contests_board):
        raise ResultsParseError(
            f"{where}: {' '.join(unmatched[0]).strip()} is not in the county file"
        )
    zero = dict.fromkeys(columns, 0)
    exceptions = []
    for key in sorted(set(mine) | set(board)):
        ours, theirs = mine.get(key, zero), board.get(key, zero)
        label = " ".join(key[:contest_keys]).strip()
        if len(key) > contest_keys:
            label += f" / {key[contest_keys]}" + (f" ({key[-1]})" if len(key) > contest_keys + 1 else "")
        for c in columns:
            if ours[c] == theirs[c]:
                continue
            if listed(key, c) != (ours[c], theirs[c]):
                raise ResultsParseError(
                    f"{where}: {label}: precincts sum to {ours[c]} {c}, the county file says "
                    f"{theirs[c]}"
                )
            exceptions.append(f"{label} {c}: precincts {ours[c]}, county file {theirs[c]}")
    return SumCheck(len(contests_board), len(board), columns, exceptions)


def _precinct_check_columns(election: ResultsElection) -> list[str]:
    """The columns a precinct file can be checked on: its vote types, and the totals when it
    carries every vote type (from 2020; through 2018 only election-night votes)."""
    against = election.kind == "general"
    columns = []
    for _, stem in election.precinct_vote_types:
        columns.append(f"{stem}_votes")
        if against:
            columns.append(f"{stem}_against")
    if election.precinct_vote_types == election.vote_types:
        columns += ["total_votes", "total_against"] if against else ["total_votes"]
    return columns


def _check_precinct_county(
    election: ResultsElection,
    county: str,
    party_file: str,
    precincts: pl.DataFrame,
    county_frame: pl.DataFrame,
) -> SumCheck:
    """One county and party file's precinct rows, summed by office, district, candidate and
    party, against its county file (PRECINCT_CHECK_EXCEPTIONS aside)."""
    return _check_sums(
        f"{election.code} {county} {party_file} precincts",
        ["office", "district", "candidate", "party"],
        2,
        _precinct_check_columns(election),
        precincts,
        county_frame,
        lambda key, c: PRECINCT_CHECK_EXCEPTIONS.get(
            (election.code, county, party_file, *key, c)
        ),
    )
```

Writing:

```python
def _precinct_csv(out_dir: Path, f: ResultsFile) -> Path:
    return out_dir / f"{f.county_code}-{county_slug(f.county)}-{f.party_file}.csv.gz"


def _gzip_csv(frame: pl.DataFrame) -> bytes:
    """The frame as gzipped CSV. mtime 0 (and no file name in the header) keeps the bytes the
    same from run to run, so an unchanged re-parse leaves git nothing to commit."""
    return gzip.compress(frame.write_csv().encode(), mtime=0)


def _precinct_check_text(e: ResultsElection, county_file: str, check: SumCheck) -> str:
    text = (
        f"ok: the precincts sum to {county_file}'s {check.rows} rows ({check.contests} contests) "
        f"on {', '.join(check.columns)}"
    )
    if e.year <= 2018:
        text += " (the board's precinct files for 2010-2018 carry election-night votes only)"
    if check.exceptions:
        text += (
            "; the board's own files disagree on "
            + "; ".join(check.exceptions)
            + " (listed in PRECINCT_CHECK_EXCEPTIONS, recorded, not corrected)"
        )
    return text


def _parse_precincts(e: ResultsElection, force: bool, data_dir: Path) -> int:
    """Check each county's precinct files against its county files and write them gzipped to
    data/results/<CODE>/precinct/.

    All-or-nothing per election. A CSV written from the newest archived revision is current
    without the archive (the precinct archives are gitignored), so a clone without them parses
    nothing and fails nothing unless forced."""
    files = precinct_files(e)
    out_dir = data_dir / "results" / e.code / "precinct"
    records = {f.source_id: newest_source(MANIFEST_PATH, f.source_id) for f in files}
    for f in files:
        if records[f.source_id] is None and f.party_file != "non":
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
    present = [f for f in files if records[f.source_id] is not None]
    stale = [
        f for f in present if force or _revision_updated(_precinct_csv(out_dir, f), records[f.source_id])
    ]
    if not stale:
        return 0
    county_frames, _ = _read_county_files(e, with_checks=False)
    by_county = {
        (f.county, f.party_file): rd.frame for items in county_frames.values() for f, _, rd in items
    }
    checked = []
    for f in present:
        archived = _read_archived(f)
        if archived is None:
            raise ResultsParseError(
                f"{f.file_name} is in the manifest but not archived here; re-fetch with "
                "`skipjack results-fetch --level precinct`"
            )
        data, record = archived
        try:
            read = read_precinct(data, e, f.county, f.party_file)
            if (f.county, f.party_file) not in by_county:
                raise ResultsParseError("no county file to check it against")
            check = _check_precinct_county(
                e, f.county, f.party_file, read.frame, by_county[(f.county, f.party_file)]
            )
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        checked.append((f, record, read, check))

    out_dir.mkdir(parents=True, exist_ok=True)
    for f, record, read, check in checked:
        if f not in stale:
            continue
        county_file = ResultsFile(e, f.county_code, f.county, f.party_file, "county").file_name
        checks: dict = {
            "rows": read.frame.height,
            "row_totals": "ok"
            if e.precinct_total
            else "computed: the precinct file prints no total, so total_votes is the sum of "
            "its vote types",
            "county_check": _precinct_check_text(e, county_file, check),
        }
        if check.exceptions:
            checks["county_check_exceptions"] = check.exceptions
        sidecar = ProvenanceSidecar.from_source(
            record,
            parser_version=PARSER_VERSION,
            extraction_method="csv",
            checks=checks,
            notes=f"{e.code} {f.county} results by precinct ({f.party_file}), summed and "
            "checked against the county file.",
        )
        sidecar.election, sidecar.family, sidecar.geography = e.code, "results", "precinct"
        out = _precinct_csv(out_dir, f)
        _write_atomic(out, _gzip_csv(read.frame))  # CSV first, sidecar last (see _parse_election)
        sidecar.write(out)
    logger.info("%s: parsed %d precinct files", e.code, len(stale))
    return len(stale)
```

`results_parse` gains `level: str = "all"` and runs each level in its own try, so a failed
precinct check does not hold back the county files (each level is all-or-nothing on its own):

```python
PARSERS = {"county": _parse_election, "precinct": _parse_precincts}
```

(defined after `_parse_precincts`), and in `results_parse`:

```python
    levels = list(PARSERS) if level == "all" else [level]
    for e in _selected(election):
        if not e.published:
            logger.info("%s: not published", e.code)
            continue
        for name in levels:
            try:
                written += PARSERS[name](e, force, data_dir)
            except ResultsParseError as err:
                failures.append(f"{e.code} {name}: {err}")
```

Update its docstring: "Parse each election's archived files into data/results/<CODE>/: the
county files, then the precinct files checked against them (level picks one). Each level is
all-or-nothing per election."

The existing county tests match failures with `in failed[0]`, so the new `"{code} county: "`
prefix keeps them passing; check `tests/test_results.py` for any `failed == [...]` equality on a
failure message and update it if one exists.

`cli.py`: `results-parse` gets
`results_parse_p.add_argument("--level", choices=["county", "precinct", "all"], default="all")`
and `cmd_results_parse` passes `level=args.level`.

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_precinct.py tests/test_results.py tests/test_results_db.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/skipjack/pipeline/provenance.py src/skipjack/pipeline/build_db.py src/skipjack/pipeline/results.py src/skipjack/cli.py tests/test_results_precinct.py
git commit -F - <<'EOF'
results: precinct files checked against the county files, written gzipped

Each precinct file must sum to its county file on every vote type it carries; the parsed
files are deterministic .csv.gz with the usual sidecar.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Precinct tables in the database

**Files:**
- Modify: `src/skipjack/models.py` (after the results tables, ~166)
- Modify: `src/skipjack/pipeline/build_db.py` (`_RESULTS_TEXT` ~245, `_check_results_file`
  ~405-437, new `load_results_precinct` after `_check_results_file`, `build` ~595-611)
- Create: `tests/test_results_precinct_db.py`

**Step 1: Write the failing tests**

```python
"""Tests for the precinct results tables and their build-db loader."""

from __future__ import annotations

import csv
import gzip
import io
import json
import sqlite3

import pytest

from skipjack.models import SCHEMA_SQL
from skipjack.pipeline.build_db import ResultsLoadError, load_results_precinct
from skipjack.pipeline.results import RESULTS_ELECTIONS, county_slug


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def precinct_columns(code):
    e = RESULTS_ELECTIONS[code]
    cols = ["election", "county", "party_file", "precinct", "congressional", "legislative"]
    cols += ["office", "district", "candidate", "party", "winner", "write_in"]
    for _, stem in e.precinct_vote_types:
        cols.append(f"{stem}_votes")
        if e.kind == "general":
            cols.append(f"{stem}_against")
    cols.append("total_votes")
    if e.kind == "general":
        cols.append("total_against")
    return cols


def write_precinct(data_dir, code, number, county, party_file, rows, source_id=None, sidecar=True):
    """One gzipped precinct CSV (rows after the election, county and party_file columns)."""
    d = data_dir / "results" / code / "precinct"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{number}-{county_slug(county)}-{party_file}"
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(precinct_columns(code))
    for r in rows:
        w.writerow([code, county, party_file, *r])
    (d / f"{stem}.csv.gz").write_bytes(gzip.compress(text.getvalue().encode()))
    if sidecar:
        (d / f"{stem}_provenance.json").write_text(
            json.dumps(
                {
                    "source_id": source_id or f"sbe-results-{code}-precinct-{number}-{party_file}",
                    "election": code, "family": "results", "geography": "precinct",
                    "source_url": "https://elections.maryland.gov/x.csv",
                    "source_name": "Maryland State Board of Elections",
                    "document_title": "test", "fetch_date": "2026-11-25T12:00:00+00:00",
                    "parse_date": "2026-11-25T12:00:01+00:00", "sha256_original": "0" * 64,
                    "parser_version": "skipjack 0.4.0", "notes": "test",
                    "extraction_method": "csv", "checks": {"rows": len(rows)},
                }
            )  # fmt: skip
        )


# GG18: election-night votes only; [precinct, cong, legs, office, district, candidate, party,
# winner, write_in, election_night_votes, election_night_against, total_votes, total_against]
GG18_ROWS = [
    ["001-001", "01", "36", "Governor / Lt. Governor", "", "Larry Hogan", "REP", 1, 0, 420, "", 420, ""],
    ["001-001", "01", "36", "State Senator", "06", "Sen Six", "DEM", 1, 0, 5, "", 5, ""],
]


def test_load_precinct_rows_and_votes(conn, tmp_path):
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS)
    assert load_results_precinct(conn, tmp_path) == 2
    rows = conn.execute(
        "SELECT precinct, congressional, legislative, district, candidate, total_votes, "
        "total_against FROM results_precinct ORDER BY id"
    ).fetchall()
    assert rows[0] == ("001-001", "01", "36", "", "Larry Hogan", 420, None)
    assert rows[1][3] == "6"  # numeric districts drop their zeros, as in results_county
    votes = conn.execute("SELECT vote_type, votes FROM results_precinct_votes").fetchall()
    assert votes == [("election_night", 420), ("election_night", 5)]
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)


def test_load_precinct_refuses_a_total_that_is_not_the_sum(conn, tmp_path):
    bad = [[*GG18_ROWS[0][:11], 421, ""]]
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", bad)
    with pytest.raises(ResultsLoadError, match="total votes 421"):
        load_results_precinct(conn, tmp_path)


def test_load_precinct_refuses_a_file_without_its_sidecar_or_with_the_wrong_source(conn, tmp_path):
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS, sidecar=False)
    with pytest.raises(ResultsLoadError, match="no provenance sidecar"):
        load_results_precinct(conn, tmp_path)
    write_precinct(tmp_path, "GG18", "15", "Kent", "all", GG18_ROWS, source_id="sbe-results-GG18-county-15-all")
    with pytest.raises(ResultsLoadError, match="expected 'sbe-results-GG18-precinct-15-all'"):
        load_results_precinct(conn, tmp_path)


def test_no_precinct_files_load_nothing(conn, tmp_path):
    assert load_results_precinct(conn, tmp_path) == 0
```

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_precinct_db.py`
Expected: FAIL, `ImportError: cannot import name 'load_results_precinct'`.

**Step 3: Implement**

`models.py`, after `idx_results_votes_result`:

```sql
-- One candidate's certified result in one precinct (a row of the board's precinct results
-- files), checked by the parser against the county file it sums to. precinct is 'DDD-PPP';
-- congressional and legislative are the board's district codes for the precinct ('' in 2010).
-- The 2010-2018 files carry election-night votes only, so total_votes there is the precinct's
-- election-night votes, not its whole vote. district is normalized as in results_county.
CREATE TABLE IF NOT EXISTS results_precinct (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    election TEXT NOT NULL,
    county TEXT NOT NULL,
    party_file TEXT NOT NULL,
    precinct TEXT NOT NULL,
    congressional TEXT NOT NULL,
    legislative TEXT NOT NULL,
    office TEXT NOT NULL,
    district TEXT NOT NULL,
    candidate TEXT NOT NULL,
    party TEXT,
    winner INTEGER NOT NULL,
    write_in INTEGER NOT NULL,
    total_votes INTEGER NOT NULL,
    total_against INTEGER
);

-- A results_precinct row's votes by vote type, as results_votes is for results_county.
CREATE TABLE IF NOT EXISTS results_precinct_votes (
    result_id INTEGER NOT NULL REFERENCES results_precinct(id),
    vote_type TEXT NOT NULL,
    votes INTEGER NOT NULL,
    votes_against INTEGER
);
CREATE INDEX IF NOT EXISTS idx_results_precinct_contest
    ON results_precinct (election, county, party_file, office, district);
CREATE INDEX IF NOT EXISTS idx_results_precinct_votes_result
    ON results_precinct_votes (result_id);
```

`build_db.py`: generalize the file check to both levels (`load_results` keeps calling it with
the default):

```python
def _check_results_file(
    df: pl.DataFrame, prov: dict, csv_path: Path, code: str, level: str = "county"
) -> None:
    """The CSV must agree with its sidecar, its directory and its file name."""
    ...  # the election check, unchanged
    name = csv_path.name.removesuffix(".gz").removesuffix(".csv")
    number, _, rest = name.partition("-")
    ...  # county and party_file checks unchanged, reading `rest`
    expected_id = f"sbe-results-{code}-{level}-{number}-{party_file}"
    ...
```

(Replace `csv_path.stem` by `name`; everything else stays.)

The loader, after `_check_results_file`:

```python
_PRECINCT_TEXT = [
    "election", "county", "party_file", "precinct", "congressional", "legislative",
    "office", "district", "candidate", "party",
]  # fmt: skip


def load_results_precinct(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> int:
    """Load every data/results/<CODE>/precinct/*.csv.gz, melting the vote-type columns as
    load_results does. Each row's total must be the sum of its vote types (the parser computed
    or checked it). Returns the number of results_precinct rows loaded."""
    root = data_dir / "results"
    if not root.exists():
        return 0
    next_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM results_precinct").fetchone()[0] + 1
    total = 0
    for path in sorted(root.glob("*/precinct/*.csv.gz")):
        prov = load_provenance(path)
        if not prov:
            raise ResultsLoadError(
                f"{path}: no provenance sidecar; refusing to load an unattributable file"
            )
        code = path.parent.parent.name
        election = RESULTS_ELECTIONS.get(code)
        if election is None:
            raise ResultsLoadError(f"{path}: unknown election code {code!r}")
        stems = [stem for _, stem in election.precinct_vote_types]
        against = election.kind == "general"
        overrides = {c: pl.String for c in _PRECINCT_TEXT}
        overrides |= {"winner": pl.Int64, "write_in": pl.Int64, "total_votes": pl.Int64}
        overrides |= {f"{stem}_votes": pl.Int64 for stem in stems}
        if against:
            overrides |= {f"{stem}_against": pl.Int64 for stem in stems}
            overrides["total_against"] = pl.Int64
        try:
            df = pl.read_csv(path, schema_overrides=overrides, missing_utf8_is_empty_string=True)
        except PolarsError as e:
            raise ResultsLoadError(f"{path}: {e}") from e
        missing = [c for c in overrides if c not in df.columns]
        if missing:
            raise ResultsLoadError(f"{path}: missing columns {missing} for {code}")
        if df.height == 0:
            raise ResultsLoadError(f"{path}: no rows")
        _check_results_file(df, prov, path, code, level="precinct")
        _check_results_cells(df, path)
        df = df.with_columns(
            pl.col("district").map_elements(_normalize_district, return_dtype=pl.String)
        )
        _upsert_source(conn, prov)
        precinct_rows, vote_rows = [], []
        for row in df.with_row_index("_line", offset=2).iter_rows(named=True):
            vote_sum = 0
            for stem in stems:
                votes = row[f"{stem}_votes"]
                if votes is None:
                    raise ResultsLoadError(f"{path}: blank {stem}_votes (row {row['_line']})")
                vote_sum += votes
                vote_rows.append((next_id, stem, votes, row[f"{stem}_against"] if against else None))
            if vote_sum != row["total_votes"]:
                raise ResultsLoadError(
                    f"{path}: row {row['_line']} ({row['candidate']}): total votes "
                    f"{row['total_votes']} != sum of vote types {vote_sum}"
                )
            precinct_rows.append(
                (
                    next_id,
                    prov["source_id"],
                    *(row[c] for c in _PRECINCT_TEXT),
                    row["winner"],
                    row["write_in"],
                    row["total_votes"],
                    row["total_against"] if against else None,
                )
            )
            next_id += 1
        conn.executemany(
            """INSERT INTO results_precinct
               (id, source_id, election, county, party_file, precinct, congressional,
                legislative, office, district, candidate, party, winner, write_in, total_votes,
                total_against)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            precinct_rows,
        )
        conn.executemany(
            "INSERT INTO results_precinct_votes (result_id, vote_type, votes, votes_against) "
            "VALUES (?, ?, ?, ?)",
            vote_rows,
        )
        total += len(precinct_rows)
    logger.info("Loaded precinct results: %d rows", total)
    return total
```

`build()`: after `counts["results"] = ...` add
`counts["results_precinct"] = load_results_precinct(conn, data_dir)`.

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_precinct_db.py tests/test_results_db.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/skipjack/models.py src/skipjack/pipeline/build_db.py tests/test_results_precinct_db.py
git commit -F - <<'EOF'
db: results_precinct and its votes, loaded from the gzipped precinct files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Downloads, the Data page and the catalog accept .csv.gz

`Path.suffix` of `x.csv.gz` is `.gz`, so every `p.suffix in DOWNLOAD_SUFFIXES` test and every
`csv.stem` needs a look. Places: `web/downloads.py:68`, `web/routes/data.py:75, 86, 108, 117,
172, 219`, `web/catalog.py:31, 41`. `web/static_site.py:258-261` copies whatever
`download_files()` returns, so it needs no change. The live app must serve a `.csv.gz` as
`application/gzip`: Starlette's `FileResponse` would otherwise guess `text/csv` (mimetypes gives
`("text/csv", "gzip")` and Starlette keeps only the first) and a browser would show compressed
bytes as text.

**Files:**
- Modify: `src/skipjack/web/downloads.py` (`DOWNLOAD_SUFFIXES` 32, `download_files` 60-79)
- Modify: `src/skipjack/web/routes/data.py` (routes 63-110, `_file` 113-120, `results_listing`
  155-180)
- Modify: `src/skipjack/web/templates/data.html` (results sections 137-146)
- Modify: `src/skipjack/web/catalog.py` (`build_catalog` 24-50, `_results_entry` 53-67)
- Test: `tests/test_catalog.py`, `tests/test_results_downloads.py`

**Step 1: Write the failing tests**

`tests/test_catalog.py`:

```python
def test_the_catalog_lists_precinct_and_question_files_apart(tmp_path):
    _touch(tmp_path, "results/PG24/county/15-kent-all.csv")
    _touch(tmp_path, "results/PG24/precinct/15-kent-all.csv.gz")
    _touch(tmp_path, "results/PG24/precinct/15-kent-all_provenance.json")
    _touch(tmp_path, "results/PG24/questions/county/15-kent.csv")
    _touch(tmp_path, "results/PG24/questions/precinct/15-kent.csv.gz")
    pg24 = build_catalog(tmp_path)["results"]["PG24"]
    assert pg24["files"] == ["results/PG24/county/15-kent-all.csv"]  # county only: version 1
    assert pg24["precinct_files"] == ["results/PG24/precinct/15-kent-all.csv.gz"]
    assert pg24["question_files"] == {
        "county": ["results/PG24/questions/county/15-kent.csv"],
        "precinct": ["results/PG24/questions/precinct/15-kent.csv.gz"],
    }
```

`tests/test_results_downloads.py` (add `import gzip` and
`from skipjack.web.downloads import download_files` and
`from skipjack.web.routes import data as data_route`):

```python
def test_gzipped_files_are_offered_with_their_sidecars_only_when_published(tmp_path, monkeypatch):
    monkeypatch.setattr(RESULTS_ELECTIONS["GG26"], "published", False)
    for code in ("PG24", "GG26"):
        d = tmp_path / "results" / code / "precinct"
        d.mkdir(parents=True)
        (d / "15-kent-all.csv.gz").write_bytes(gzip.compress(b"x\n"))
        (d / "15-kent-all_provenance.json").write_text("{}")
        (d / "15-kent-all.csv.gz.tmp").write_bytes(b"")
    assert [p.as_posix() for p in download_files(tmp_path)] == [
        "results/PG24/precinct/15-kent-all.csv.gz",
        "results/PG24/precinct/15-kent-all_provenance.json",
    ]


def test_the_listing_offers_precinct_and_question_files(tmp_path):
    base = tmp_path / "results" / "PG24"
    for relative in ("county/15-kent-all.csv", "precinct/15-kent-all.csv.gz",
                     "questions/county/15-kent.csv", "questions/precinct/15-kent.csv.gz"):  # fmt: skip
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        (base / relative).write_text("x")
    block = results_listing(tmp_path)[0]
    assert block["precinct_files"] == [
        {
            "name": "15-kent-all",
            "path": "results/PG24/precinct/15-kent-all.csv.gz",
            "provenance": "results/PG24/precinct/15-kent-all_provenance.json",
        }
    ]
    assert [f["path"] for f in block["question_files"]] == [
        "results/PG24/questions/county/15-kent.csv",
        "results/PG24/questions/precinct/15-kent.csv.gz",
    ]


def test_a_gzipped_file_is_served_as_gzip(tmp_path, monkeypatch):
    target = tmp_path / "results" / "PG24" / "precinct" / "15-kent-all.csv.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(gzip.compress(b"election\nPG24\n", mtime=0))
    monkeypatch.setattr(data_route, "DATA_DIR", tmp_path)
    resp = TestClient(create_app()).get("/downloads/results/PG24/precinct/15-kent-all.csv.gz")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert gzip.decompress(resp.content) == b"election\nPG24\n"
```

(`RESULTS_ELECTIONS` from `skipjack.pipeline.results`.)

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_catalog.py tests/test_results_downloads.py`
Expected: FAIL (`KeyError: 'precinct_files'`, an empty `download_files`, a 404 for the `.gz`).

**Step 3: Implement**

`downloads.py`:

```python
DOWNLOAD_SUFFIXES = {".csv", ".json"}
GZIP_SUFFIX = ".csv.gz"  # the precinct and question-precinct files


def downloadable(path: Path) -> bool:
    """Whether a file's type is offered: a CSV, a JSON sidecar, or a gzipped CSV."""
    return path.name.endswith(GZIP_SUFFIX) or path.suffix in DOWNLOAD_SUFFIXES


def media_type(path: Path) -> str | None:
    """The type to serve a download as: gzip for .csv.gz (left to guess, it would be text/csv
    over compressed bytes), otherwise the server's guess."""
    return "application/gzip" if path.name.endswith(GZIP_SUFFIX) else None


def data_stem(path: Path) -> str:
    """A data file's name without .csv or .csv.gz."""
    return path.name.removesuffix(".gz").removesuffix(".csv")
```

and in `download_files`: `if not (p.is_file() and downloadable(p)):`. Update the module docstring:
"... results files (county CSVs, and the precinct and question-precinct files as .csv.gz) are
gated per election".

`routes/data.py`: import `downloadable`, `media_type`, `data_stem` (drop `DOWNLOAD_SUFFIXES`
if unused) and `sidecar_path` from `skipjack.pipeline.provenance`; the three routes test
`target.is_file() and downloadable(target)` and return
`FileResponse(target, media_type=media_type(target))`. `_file` becomes

```python
def _file(data_dir: Path, path: Path, name: str | None = None) -> dict:
    return {
        "name": name or data_stem(path),
        "path": path.relative_to(data_dir).as_posix(),
        "provenance": sidecar_path(path).relative_to(data_dir).as_posix(),
    }
```

and `results_listing` builds its blocks with it:

```python
        files = [_file(data_dir, csv) for csv in sorted(county_dir.glob("*.csv"))]
        if files:
            blocks.append(
                {
                    "code": code,
                    "name": election.name,
                    "files": files,
                    "precinct_files": [
                        _file(data_dir, p) for p in sorted((root / code / "precinct").glob("*.csv.gz"))
                    ],
                    "question_files": [
                        _file(data_dir, p)
                        for p in sorted((root / code / "questions").glob("*/*.csv*"))
                        if downloadable(p)
                    ],
                }
            )
```

(The turnout listing's `csv.stem` at 219 is for `.csv` only and may stay.)

`data.html`, inside each results section after the county `<p>`:

```jinja
    {% if section.precinct_files %}
    <details>
        <summary>By precinct ({{ section.precinct_files|length }} gzipped CSVs, one per county and party file)</summary>
        <p>{% for f in section.precinct_files %}
        <a href="{{ base }}/downloads/{{ f.path }}" style="white-space:nowrap">{{ f.name }}</a>
        (<a href="{{ base }}/downloads/{{ f.provenance }}">provenance</a>)&nbsp;
        {% endfor %}</p>
    </details>
    {% endif %}
    {% if section.question_files %}
    <details>
        <summary>Ballot questions ({{ section.question_files|length }} files, by county and by precinct)</summary>
        <p>{% for f in section.question_files %}
        <a href="{{ base }}/downloads/{{ f.path }}" style="white-space:nowrap">{{ f.path.split("/")[3] }}/{{ f.name }}</a>
        (<a href="{{ base }}/downloads/{{ f.provenance }}">provenance</a>)&nbsp;
        {% endfor %}</p>
    </details>
    {% endif %}
```

and add to the section intro paragraph: "Precinct files are gzipped (`.csv.gz`); most tools read
them as they are."

`catalog.py`:

```python
    for relative in download_files(data_dir):
        if not relative.name.endswith((".csv", ".csv.gz")):
            continue
        parts = relative.parts
        path = relative.as_posix()
        if parts[:2] == ("voter_registration", "monthly") and len(parts) == 3:
            ...
        elif parts[0] == "results" and len(parts) == 4 and parts[2] == "county":
            ...
        elif parts[0] == "results" and len(parts) == 4 and parts[2] == "precinct":
            _results_entry(results, parts[1], official=True, as_of=None)["precinct_files"].append(path)
        elif parts[0] == "results" and len(parts) == 5 and parts[2] == "questions":
            entry = _results_entry(results, parts[1], official=True, as_of=None)
            entry["question_files"][parts[3]].append(path)
        ...
```

and `_results_entry` adds `"precinct_files": []` and
`"question_files": {"county": [], "precinct": []}`. `CATALOG_VERSION` stays 1: the keys are
new, and `files` is unchanged, so version-1 clients keep working. Update the module docstring to
say so.

**Step 4: Run the tests**

`uv run pytest -q tests/test_catalog.py tests/test_results_downloads.py tests/test_static_site.py`
Expected: PASS (the static-site tests skip without a database).

**Step 5: Commit**

```bash
git add src/skipjack/web/downloads.py src/skipjack/web/routes/data.py src/skipjack/web/templates/data.html src/skipjack/web/catalog.py tests/test_catalog.py tests/test_results_downloads.py
git commit -F - <<'EOF'
web: gzipped precinct and question files on the Data page, the catalog and the downloads

The catalog keeps files county-only and adds precinct_files and question_files, so version 1
clients keep working.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: The data CLI reads precinct results

`skipjack get results --geography precinct --county Kent [--precinct 001-001]`. The client reads
CSVs with the stdlib `csv` module (not polars), so `SkipjackSource.text` decompresses a `.gz`
path itself with `gzip`. A server may already have undone the compression (a host that sends
`Content-Encoding: gzip` for `.gz`, which httpx then decodes), so it decompresses only bytes that
start with the gzip magic `1f 8b`. A statewide precinct pull is about 100,000 rows: `--county` is
required unless `-o FILE` is given. `--source sbe` reads one county's precinct files live.

`get questions` arrives with the question files in Task 13.

**Files:**
- Modify: `src/skipjack/client/sources.py` (`text` 56-81)
- Modify: `src/skipjack/client/datasets.py` (`results` 76-121)
- Modify: `src/skipjack/client/commands.py` (results parser 70-76)
- Modify: `src/skipjack/client/sbe.py` (`results` 36-95)
- Modify: `tests/client_fixture.py`
- Test: `tests/test_client_results.py`, `tests/test_client_sources.py`, `tests/test_client_sbe.py`

**Step 1: Write the failing tests**

`tests/client_fixture.py`: add `import gzip`, then

```python
PRECINCT_HEADER = (
    "election,county,party_file,precinct,congressional,legislative,office,district,candidate,"
    "party,winner,write_in,early_votes,early_against,total_votes,total_against\n"
)
ALLEGANY_PRECINCTS = PRECINCT_HEADER + (
    'GG22,Allegany,all,001-001,06,01,Governor / Lt. Governor,"",Wes Moore and Aruna Miller,DEM,1,0,500,,4000,\n'
    'GG22,Allegany,all,001-002,06,01,Governor / Lt. Governor,"",Wes Moore and Aruna Miller,DEM,1,0,283,,2796,\n'
)
```

in `write_downloads` write it gzipped after the text files:

```python
    gzipped = {"results/GG22/precinct/01-allegany-all.csv.gz": ALLEGANY_PRECINCTS}
    for relative, text in gzipped.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(text.encode(), mtime=0))
```

and the GG22 catalog entry gains
`"precinct_files": ["results/GG22/precinct/01-allegany-all.csv.gz"],`.

`tests/test_client_results.py`:

```python
def test_precinct_results_for_a_county(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "Allegany")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-001", "001-002"]
    assert out["rows"][0]["total_votes"] == 4000 and out["rows"][0]["congressional"] == "06"
    assert out["meta"]["filters"] == {"county": "Allegany", "geography": "precinct"}
    assert out["meta"]["source_urls"][0].endswith("01-allegany-all.csv.gz")


def test_one_precinct_matches_loosely(capsys, base):
    out = json.loads(
        run(capsys, "get", "results", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "Allegany", "--precinct", "1-2")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-002"]
    assert out["meta"]["filters"]["precinct"] == "001-002"


def test_every_countys_precincts_need_an_output_file(capsys, base, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography", "precinct"])
    assert exit_info.value.code == 2
    assert "add --county, or -o FILE" in capsys.readouterr().err
    out = tmp_path / "all.json"
    main(["get", "results", "--election", "GG22", "--base-url", base, "--geography", "precinct",
          "-o", str(out)])  # fmt: skip
    assert len(json.loads(out.read_text())["rows"]) == 2


def test_precinct_needs_the_precinct_geography(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG22", "--base-url", base, "--precinct", "001-001"])
    assert "--precinct needs --geography precinct" in capsys.readouterr().err


def test_a_bad_precinct_is_refused(capsys, base):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Allegany", "--precinct", "north"])  # fmt: skip
    assert "'north' is not a precinct like 001-012" in capsys.readouterr().err


def test_a_catalog_without_precinct_files_says_so(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    del catalog["results"]["GG22"]["precinct_files"]
    index.write_text(json.dumps(catalog))
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "GG22", "--base-url", base, "--geography",
              "precinct", "--county", "Allegany"])  # fmt: skip
    assert "GG22 has no precinct files on this site" in capsys.readouterr().err
```

`tests/test_client_sources.py`:

```python
@pytest.mark.parametrize("already_decoded", [False, True])
def test_a_gz_file_is_decompressed_unless_the_server_did_it(already_decoded):
    body = b"a,b\n1,2\n"
    sent = body if already_decoded else gzip.compress(body)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sent)

    source = SkipjackSource("https://example.org/downloads", transport=httpx.MockTransport(handler))
    assert source.rows("results/x.csv.gz") == [{"a": "1", "b": "2"}]


def test_a_broken_gz_file_is_a_client_error(tmp_path):
    (tmp_path / "x.csv.gz").write_bytes(b"\x1f\x8b not really gzip")
    with pytest.raises(ClientError, match="could not decompress"):
        SkipjackSource(str(tmp_path)).text("x.csv.gz")
```

(add `import gzip`.)

`tests/test_client_sbe.py` (it already has `archived(...)` and monkeypatches
`results.download_status`):

```python
def test_results_live_precincts_need_a_county(capsys):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "PG24", "--source", "sbe", "--geography", "precinct"])
    assert "one county's precinct files at a time; add --county" in capsys.readouterr().err


def test_results_live_precincts_for_a_county(capsys, monkeypatch):
    head = (
        '"County","County Name","Election District - Precinct","Congressional","Legislative",'
        '"Office Name","Office District","Candidate Name","Party","Winner","Write-In?",'
        '"Early Votes","Early Votes Against","Election Night Votes","Election Night Votes Against",'
        '"Mail-In Ballot 1 Votes","Mail-In Ballot 1 Votes Against","Provisional Votes",'
        '"Provisional Votes Against","Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against"\n'
    )
    row = '"15","Kent County","001-001","01","36","President - Vice Pres","","Kamala D. Harris and Tim Walz","DEM","Y","","70","","149","","23","","22","","124",""\n'
    seen = []

    def fake(url, timeout=30.0):
        seen.append(url)
        return "ok", (head + row).encode(), ""

    monkeypatch.setattr(results, "download_status", fake)
    out = json.loads(
        run(capsys, "get", "results", "--election", "PG24", "--source", "sbe",
            "--geography", "precinct", "--county", "Kent")
    )  # fmt: skip
    assert seen == ["https://elections.maryland.gov/elections/archive/2024/election_data/PG24_15PrecinctsResults.csv"]
    assert out["rows"][0]["precinct"] == "001-001" and out["rows"][0]["total_votes"] == 388
    assert "precinct-to-county check not run" in out["meta"]["checks"]


def test_results_live_pp20_has_no_precinct_files(capsys):
    with pytest.raises(SystemExit):
        main(["get", "results", "--election", "PP20", "--source", "sbe", "--geography",
              "precinct", "--county", "Kent"])  # fmt: skip
    assert "the board posted no precinct files for PP20" in capsys.readouterr().err
```

(`run` comes from `client_fixture`; check the file's existing imports and add what is missing.)

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_client_results.py tests/test_client_sources.py tests/test_client_sbe.py`
Expected: FAIL, `error: unrecognized arguments: --geography precinct` (argparse exits 2) and the
`.gz` read returns compressed text.

**Step 3: Implement**

`sources.py` (add `import gzip`):

```python
GZIP_MAGIC = b"\x1f\x8b"

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
        decompressed, unless the server already undid the compression."""
        data = self._bytes(path)
        if path.endswith(".gz") and data[:2] == GZIP_MAGIC:
            try:
                data = gzip.decompress(data)
            except (OSError, EOFError) as err:
                raise ClientError(f"could not decompress {self.url(path)} ({err})") from err
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as err:
            raise ClientError(f"could not read {self.url(path)} ({err})") from err
```

`helpers.py`, two small helpers the results and questions handlers share:

```python
def file_stem(path: str) -> str:
    """A download's name without .csv or .csv.gz ("01-allegany-all")."""
    return path.rsplit("/", 1)[-1].removesuffix(".gz").removesuffix(".csv")


def precinct_id(given: str) -> str:
    """A precinct as the files print it: "1-12" and "001-012" are both "001-012"."""
    m = re.fullmatch(r"\s*(\d{1,3})-(\d{1,3})\s*", given)
    if not m:
        raise ClientError(f"{given!r} is not a precinct like 001-012")
    return f"{int(m.group(1)):03d}-{int(m.group(2)):03d}"
```

`datasets.results` (import both helpers; replace each `Path(f).stem` by `file_stem(f)`):

```python
    geography = getattr(args, "geography", "county")
    precinct = precinct_id(args.precinct) if getattr(args, "precinct", None) else None
    if precinct and geography != "precinct":
        raise ClientError("--precinct needs --geography precinct")
    county = county_for(catalog["counties"], args.county)
    if geography == "precinct":
        files = election.get("precinct_files") or []
        if not files:
            raise ClientError(f"{code} has no precinct files on this site")
        if county is None and not getattr(args, "output", None):
            raise ClientError(
                "precinct results are large (every county's is about 100,000 rows): add "
                "--county, or -o FILE to write them all"
            )
    else:
        files = election["files"]
    ...  # the county and party-file filters as they are, on `files`
    needs = (["office"] if office else []) + (["precinct"] if precinct else [])
    rows = []
    for f in files:
        rows += [
            row
            for row in read_rows(source, f, needs)
            if (office is None or office in (row["office"] or "").lower())
            and (precinct is None or row["precinct"] == precinct)
        ]
    filters = {"county": county["name"] if county else None, "office": args.office,
               "party_file": args.party_file,
               "geography": geography if geography != "county" else None,
               "precinct": precinct}  # fmt: skip
```

(The party-file error's list of party files reads `election["files"]`; keep it reading the
`files` list chosen above so a precinct request lists precinct party files.)

`commands.py`, the results parser:

```python
    res.add_argument("--geography", choices=["county", "precinct"], default="county",
                     help="county (the default) or precinct: a row per precinct")  # fmt: skip
    res.add_argument("--precinct", metavar="DDD-PPP",
                     help="One precinct (with --geography precinct), e.g. 001-012")  # fmt: skip
```

and change the subparser help to "An election's results by county or precinct".

`sbe.results` (import `precinct_id`):

```python
    geography = getattr(args, "geography", "county")
    precinct = precinct_id(args.precinct) if getattr(args, "precinct", None) else None
    if precinct and geography != "precinct":
        raise ClientError("--precinct needs --geography precinct")
    county = county_for(_counties(), args.county)
    if geography == "precinct":
        if not e.precincts:
            raise ClientError(f"the board posted no precinct files for {code}. {_FALLBACK}")
        if county is None:
            raise ClientError(
                "--source sbe reads one county's precinct files at a time; add --county"
            )
        files, reader = pr.precinct_files(e), pr.read_precinct
    else:
        files = [f for f in pr.county_files(e) if f.role == "county"]
        reader = pr.READERS[e.layout]
    for f in files:
        if county and f.county != county["name"]:
            continue
        ...  # as now, with `read = reader(body, e, f.county, f.party_file)` and the precinct
        #      filter beside the office filter
    checks = "row totals checked"
    ...
    if geography == "precinct":
        checks += "; precinct-to-county check not run (skipjack results-parse runs it)"
    else:
        checks += "; statewide cross-check not run (it needs every county's file)"
```

Add `geography` and `precinct` to its `filters` the same way as `datasets.results`.

**Step 4: Run the tests**

`uv run pytest -q tests/test_client_results.py tests/test_client_sources.py tests/test_client_sbe.py tests/test_client_commands.py tests/test_light_install.py`
Expected: PASS (`test_light_install` proves `gzip` costs the light install nothing).

**Step 5: Commit**

```bash
git add src/skipjack/client/sources.py src/skipjack/client/helpers.py src/skipjack/client/datasets.py src/skipjack/client/commands.py src/skipjack/client/sbe.py tests/client_fixture.py tests/test_client_results.py tests/test_client_sources.py tests/test_client_sbe.py
git commit -F - <<'EOF'
client: get results --geography precinct, from the gzipped files or live from the board

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: One precinct page per county, with a contest picker

**Decision (2026-11-25):** not a page per contest (about 10,000 pages), but one page per county
per certified election, about 400 in all: `/results/{election}/county/{county}/precincts/`. The
page lists the county's contests that have precinct rows in a `<select>`; picking one draws its
table in the browser from the county's published precinct file
(`downloads/results/{CODE}/precinct/{cc}-{slug}-{pf}.csv.gz`, one per party file), so the HTML
stays small and the site carries no second copy of the figures. A contest is deep-linked as
`precincts/#{anchor}`, the anchor being the contest's anchor on the county page. Unofficial
elections get no page: the running count has no precinct figures.

GitHub Pages serves `.csv.gz` as `application/gzip` without `Content-Encoding`, so the browser
receives gzip bytes; a server that adds `Content-Encoding: gzip` hands `fetch` plain text. The
script checks the first two bytes (`1f 8b`) and decompresses only then, with
`DecompressionStream("gzip")`, as the CLI does in Task 6.

**Files:**
- Modify: `src/skipjack/web/results.py` (`county_page` 506-573; new `precinct_picker` after it)
- Modify: `src/skipjack/web/routes/results.py` (docstring 1-8, new route after `county` 261-287)
- Create: `src/skipjack/web/templates/results_precincts.html`
- Create: `src/skipjack/web/static/precincts.js`
- Modify: `src/skipjack/web/templates/results_county.html` (a "Results by precinct" link under
  the heading, and "By precinct" under each contest card that has precinct rows, ~50-53)
- Modify: `src/skipjack/web/static_site.py` (`page_urls` 98-110: factor out `results_urls`)
- Test: `tests/test_results_routes.py`

**Step 1: Write the failing tests**

Append to `tests/test_results_routes.py`:

```python
import json
import re


def add_precinct(conn, election, county, party_file, office, district, precinct, name, party, total):
    code = {"Allegany": "01", "Garrett": "11"}[county]
    conn.execute(
        """INSERT INTO results_precinct (source_id, election, county, party_file, precinct,
           congressional, legislative, office, district, candidate, party, winner, write_in,
           total_votes, total_against)
           VALUES (?, ?, ?, ?, ?, '06', '01', ?, ?, ?, ?, 0, 0, ?, NULL)""",
        (f"sbe-results-{election}-precinct-{code}-{party_file}", election, county, party_file,
         precinct, office, district, name, party, total),
    )  # fmt: skip


def governor_precincts(db):
    for precinct, name, n in (("001-002", "Wes Moore", 41), ("001-001", "Wes Moore", 60),
                              ("001-001", "Eric Felber", 30)):  # fmt: skip
        add_precinct(db, "GP26", "Allegany", "dem", "Governor / Lt. Governor", "", precinct, name, "DEM", n)


def picker_contests(html):
    """The contests the page hands its script, from the embedded JSON."""
    m = re.search(r'<script type="application/json" id="precinct-contests">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_county_page_links_its_precinct_page_and_each_contest(client, db):
    governor_precincts(db)
    slug = results.contest_slug("Governor / Lt. Governor", "", "dem")
    html = client.get("/results/gp26/county/allegany/").text
    assert "/results/gp26/county/allegany/precincts/" in html
    assert f"/results/gp26/county/allegany/precincts/#{slug}" in html
    garrett = client.get("/results/gp26/county/garrett/").text  # no precinct rows there
    assert "/precincts/" not in garrett


def test_precinct_page_lists_contests_with_their_file_and_candidates(client, db):
    governor_precincts(db)
    slug = results.contest_slug("Governor / Lt. Governor", "", "dem")
    resp = client.get("/results/gp26/county/allegany/precincts/")
    assert resp.status_code == 200
    html = resp.text
    assert f'<option value="{slug}"' in html
    assert "/static/precincts.js" in html
    [contest] = picker_contests(html)
    assert contest["anchor"] == slug
    assert contest["file"] == "results/GP26/precinct/01-allegany-dem.csv.gz"
    assert (contest["office"], contest["district"]) == ("Governor / Lt. Governor", "")
    assert contest["candidates"][:2] == ["Wes Moore", "Eric Felber"]  # the county page's order
    assert contest["kind"] == "contest"
    assert "election-day votes only" not in html  # GP26 counts every way of voting by precinct
    assert "/downloads/results/GP26/precinct/01-allegany-dem.csv.gz" in html  # the noscript list


def test_precinct_picker_leaves_out_contests_without_precinct_rows(db, data):
    governor_precincts(db)
    picker = results.precinct_picker(db, "GP26", "allegany")
    assert [c["office"] for c in picker["contests"]] == ["Governor / Lt. Governor"]
    assert picker["files"] == ["results/GP26/precinct/01-allegany-dem.csv.gz"]
    assert picker["election_night_only"] is False
    assert results.precinct_picker(db, "GP26", "garrett") is None


@pytest.mark.parametrize(
    "url",
    [
        "/results/gp26/county/nowhere/precincts/",
        "/results/gp26/county/garrett/precincts/",  # a county without precinct rows
    ],
)
def test_unknown_precinct_pages_are_404(client, db, url):
    governor_precincts(db)
    assert client.get(url).status_code == 404


def test_2018_precinct_pages_say_election_day_votes_only(client, db):
    add(db, "GG18", "Allegany", "all", "Governor / Lt. Governor", "",
        [("Larry Hogan", "REP", True, {"election_night": 50})])  # fmt: skip
    add_precinct(db, "GG18", "Allegany", "all", "Governor / Lt. Governor", "", "001-001", "Larry Hogan", "REP", 50)
    html = client.get("/results/gg18/county/allegany/precincts/").text
    assert "election-day votes only" in html


def test_results_urls_include_one_precinct_page_per_county(db, data):
    from skipjack.web.static_site import results_urls

    governor_precincts(db)
    urls = results_urls(db)
    assert "/results/gp26/county/allegany/precincts/" in urls
    assert "/results/gp26/county/garrett/precincts/" not in urls
    under = [u for u in urls if u.startswith("/results/gp26/county/allegany/")]
    assert under == ["/results/gp26/county/allegany/", "/results/gp26/county/allegany/precincts/"]
```

(Check first that the `data` fixture's GP26 rows include Allegany's "Governor / Lt. Governor"
DEM contest with Wes Moore ahead of Eric Felber, and a Garrett county page; adjust the names to
the fixture if not. If GP26 is unofficial in the fixture, use a certified election the fixture
has instead, as the route 404s for an election being counted.)

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_routes.py -k "precinct"`
Expected: FAIL: no precinct link on the county page, the precinct URL is a 404, and
`AttributeError: module 'skipjack.web.results' has no attribute 'precinct_picker'`.

**Step 3: Implement**

`web/results.py`. In `county_page`, before the loop over `grouped`:

```python
    with_precincts = set(
        conn.execute(
            "SELECT DISTINCT party_file, office, district FROM results_precinct "
            "WHERE election = ? AND county = ?",
            (code, county),
        ).fetchall()
    )
```

(guard it as the module guards other optional tables if an older database may lack
`results_precinct`), and in each contest dict add
`"by_precinct": (party_file, office, district) in with_precincts,`. The page dict gains
`"by_precinct": any(...)` over its contests, for the link under the heading.

After `county_page`:

```python
_CODE_FOR_COUNTY = {name: code for code, name in COUNTY_CODES.items()}


def precinct_file(code: str, county: str, party_file: str) -> str:
    """The county's published precinct file for one party file, relative to downloads/
    (as Task 3 writes it under data/)."""
    return f"results/{code}/precinct/{_CODE_FOR_COUNTY[county]}-{slugify(county)}-{party_file}.csv.gz"


def precinct_picker(conn: sqlite3.Connection, code: str, county_slug: str) -> dict | None:
    """One county's contests that have precinct rows, for the precinct page's picker: each
    with its anchor on the county page, its published file, the office and district its rows
    carry in that file, and its candidates in the county page's order. The figures themselves
    stay in the files; the page's script reads them. None for an unknown county or one with no
    precinct rows. Through 2018 the precinct figures are election-night votes only."""
    page = county_page(conn, code, county_slug)
    if page is None:
        return None
    contests = [
        {
            "kind": "contest",
            "anchor": c["anchor"],
            "title": c["title"],
            "group": g["label"],
            "file": precinct_file(code, page["county"], c["party_file"]),
            "office": c["office"],
            "district": c["district"],
            "candidates": [cand["name"] for cand in c["candidates"]],
            "has_against": c["has_against"],
        }
        for g in page["groups"]
        for c in g["contests"]
        if c["by_precinct"]
    ]
    if not contests:
        return None
    return {
        "election": page["election"],
        "county": page["county"],
        "county_slug": county_slug,
        "contests": contests,
        "files": sorted({c["file"] for c in contests}),
        "election_night_only": RESULTS_ELECTIONS[code].year <= 2018,
    }
```

(Use the real key names of `county_page`'s groups and contests: read lines 506-573 and match
`label`, `title`, `party_file`, `candidates[].name`, `has_against` to what it returns. The
office and district must be the values as written in the precinct CSV, which Task 3 copies from
the reader, so the script's filter matches; if `county_page` normalizes the district, as the
loader does "01" to "1", carry the raw value or normalize in the script the same way, and say
which in a comment.)

`routes/results.py`: the docstring gains "``/{slug}/county/{county_slug}/precincts/`` is a
county's results by precinct (a fixed last segment, three segments in all, so it never meets
``/{slug}/{contest_slug}/``)." New route, right after `county`:

```python
@router.get("/{slug}/county/{county_slug}/precincts/")
async def county_precincts(request: Request, slug: str, county_slug: str):
    """A county's results by precinct: a contest picker over its published precinct files
    (certified results only)."""
    with get_db() as conn:
        code = _code(conn, slug)
        if qu.is_unofficial(code):
            raise HTTPException(status_code=404)  # the running count is by county only
        data = qr.precinct_picker(conn, code, county_slug)
        if data is None:
            raise HTTPException(status_code=404)
        context = _page_context(conn, code)
    return _render(request, "results_precincts.html", {**data, **context})
```

`templates/results_precincts.html`:

```jinja
{% extends "base.html" %}
{% import "_results.html" as r with context %}
{% block title %}{{ county }} by precinct: {{ election.name.title() }}{% endblock %}

{% block content %}
<div class="viz-root">
{{ r.breadcrumb(election, [("By county", base ~ "/results/" ~ election.slug ~ "/county/"), (county, base ~ "/results/" ~ election.slug ~ "/county/" ~ county_slug ~ "/"), ("By precinct", none)]) }}
<h1>{{ county }} by precinct</h1>
<p class="secondary">{{ election.name.title() }}.
    <a href="{{ base }}/results/{{ election.slug }}/county/{{ county_slug }}/">{{ county }}'s totals</a>.</p>
{% if election_night_only %}
{# keep "election-day votes only" on one line: the tests look for the phrase #}
<p class="note">The State Board's precinct files for {{ election.year }} count election-day votes only:
    early, absentee and provisional ballots were counted by county, not by precinct. These
    figures add up to {{ county }}'s election-day votes, not its total.</p>
{% else %}
<p class="note">Every way of voting is counted by precinct, so these figures add up to
    {{ county }}'s totals.</p>
{% endif %}
<label for="precinct-contest">Contest</label>
<select id="precinct-contest">
    {% for g in contests|groupby("group", sort=false) %}
    <optgroup label="{{ g.grouper }}">
        {% for c in g.list %}<option value="{{ c.anchor }}">{{ c.title }}</option>{% endfor %}
    </optgroup>
    {% endfor %}
</select>
<p id="precinct-status" class="secondary" aria-live="polite"></p>
<div class="overflow-auto">
<table id="precinct-table" data-sortable class="results-table compact" hidden></table>
</div>
<noscript><p>The table needs JavaScript. The same figures are in the county's precinct
    files:</p></noscript>
<ul class="secondary">
    {% for f in files %}<li><a href="{{ base }}/downloads/{{ f }}"><code>{{ f.split("/")[-1] }}</code></a></li>{% endfor %}
</ul>
<script type="application/json" id="precinct-contests">{{ contests|tojson }}</script>
<script>window.SKIPJACK_DOWNLOADS = "{{ base }}/downloads/";</script>
<script src="{{ base }}/static/precincts.js"></script>
</div>
{% endblock %}
```

(Jinja's `groupby` with `sort=false` needs Jinja 3.1; check the locked version, and fall back to
grouping in `precinct_picker` if it is older. Check how other templates load scripts and the
`base` prefix, e.g. the map pages with `maps.js`, and match them, including any `defer`.
`sortable.js` enables tables at load, before this table has rows: check whether it needs
calling again, and if so call its `enable` from `precincts.js` after drawing, exporting it from
`sortable.js` if it is not already reachable.)

`static/precincts.js`:

```javascript
// The precinct page: draw the picked contest's table from the county's published precinct
// file. The files are gzipped CSV; a server may already have decompressed one (Content-Encoding),
// so decompress only when the bytes still start with the gzip magic number.
(function () {
  const contests = JSON.parse(document.getElementById("precinct-contests").textContent);
  const select = document.getElementById("precinct-contest");
  const table = document.getElementById("precinct-table");
  const status = document.getElementById("precinct-status");
  const files = {};  // file -> Promise of parsed rows, fetched once per page

  async function text(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
    const bytes = new Uint8Array(await resp.arrayBuffer());
    if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return new TextDecoder().decode(bytes);
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return await new Response(stream).text();
  }

  // RFC 4180: quoted fields may hold commas, quotes ("") and newlines.
  function parse(csv) {
    const rows = [];
    let row = [], field = "", quoted = false;
    for (let i = 0; i < csv.length; i++) {
      const ch = csv[i];
      if (quoted) {
        if (ch === '"' && csv[i + 1] === '"') { field += '"'; i++; }
        else if (ch === '"') quoted = false;
        else field += ch;
      } else if (ch === '"') quoted = true;
      else if (ch === ",") { row.push(field); field = ""; }
      else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && csv[i + 1] === "\n") i++;
        row.push(field); rows.push(row); row = []; field = "";
      } else field += ch;
    }
    if (field || row.length) { row.push(field); rows.push(row); }
    const [header, ...body] = rows;
    return body.map((r) => Object.fromEntries(header.map((h, j) => [h, r[j]])));
  }

  function load(file) {
    if (!files[file]) files[file] = text(window.SKIPJACK_DOWNLOADS + file).then(parse);
    return files[file];
  }

  const num = (s) => (s === undefined || s === "" ? null : Number(s));
  const fmt = (n) => (n === null ? "—" : n.toLocaleString("en-US"));

  // kind: true for a number column, "row" for the precinct heading of a row
  function cell(tag, value, kind) {
    const el = document.createElement(tag);
    if (tag === "th") el.scope = kind === "row" ? "row" : "col";
    if (kind === true) el.className = "num";
    el.textContent = value;
    return el;
  }

  // Candidate contests: a row per precinct, a column per candidate in the county page's order.
  function contestTable(c, rows) {
    const mine = rows.filter((r) => r.office === c.office && r.district === c.district);
    const byPrecinct = new Map();
    for (const r of mine) {
      if (!byPrecinct.has(r.precinct)) byPrecinct.set(r.precinct, { r, votes: {}, against: {} });
      const p = byPrecinct.get(r.precinct);
      p.votes[r.candidate] = num(r.total_votes);
      p.against[r.candidate] = num(r.total_against);
    }
    const head = ["Precinct", "Congressional district", "Legislative district"];
    for (const name of c.candidates) {
      head.push(name);
      if (c.has_against) head.push(`${name}: against`);
    }
    if (!c.has_against) head.push("Votes cast");
    const body = [...byPrecinct.values()].sort((a, b) => a.r.precinct.localeCompare(b.r.precinct)).map((p) => {
      const cells = [];
      for (const name of c.candidates) {
        cells.push(p.votes[name] ?? null);
        if (c.has_against) cells.push(p.against[name] ?? null);
      }
      if (!c.has_against) cells.push(c.candidates.reduce((s, n) => s + (p.votes[n] || 0), 0));
      return [p.r.precinct, p.r.congressional, p.r.legislative, cells];
    });
    return { head, body };
  }

  // Ballot questions (Task 12): For, Against and the For share per precinct.
  function questionTable(c, rows) {
    const mine = rows.filter((r) => String(Number(r.question)) === String(Number(c.question)));
    const body = mine.sort((a, b) => a.precinct.localeCompare(b.precinct)).map((r) => {
      const yes = num(r.total_votes), no = num(r.total_against);
      const share = yes + no ? `${((100 * yes) / (yes + no)).toFixed(1)}%` : "—";
      return [r.precinct, r.congressional, r.legislative, [yes, no], share];
    });
    return { head: ["Precinct", "Congressional district", "Legislative district", "For", "Against", "For share"], body };
  }

  function draw(c, { head, body }) {
    const withDistricts = body.some((b) => b[1]);
    table.replaceChildren();
    const caption = document.createElement("caption");
    caption.className = "sr-only";
    caption.textContent = `${c.title}, by precinct`;
    const thead = document.createElement("thead"), tr = document.createElement("tr");
    head.forEach((h, i) => {
      if (!withDistricts && (i === 1 || i === 2)) return;
      tr.append(cell("th", h, i > 2));
    });
    thead.append(tr);
    const tbody = document.createElement("tbody");
    for (const [precinct, cong, legs, cells, extra] of body) {
      const row = document.createElement("tr");
      row.append(cell("th", precinct, "row"));
      if (withDistricts) row.append(cell("td", cong), cell("td", legs));
      for (const n of cells) row.append(cell("td", fmt(n), true));
      if (extra !== undefined) row.append(cell("td", extra, true));
      tbody.append(row);
    }
    table.append(caption, thead, tbody);
    table.hidden = false;
    status.textContent = `${body.length} precincts.`;
  }

  async function show(anchor) {
    const c = contests.find((x) => x.anchor === anchor) || contests[0];
    select.value = c.anchor;
    status.textContent = "Loading…";
    try {
      const rows = await load(c.file);
      if (select.value !== c.anchor) return;  // picked another contest while this one loaded
      draw(c, c.kind === "question" ? questionTable(c, rows) : contestTable(c, rows));
    } catch (err) {
      table.hidden = true;
      status.textContent = `Could not load ${c.file}: ${err.message}`;
    }
  }

  select.addEventListener("change", () => {
    history.replaceState(null, "", `#${select.value}`);
    show(select.value);
  });
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  show(location.hash.slice(1));
})();
```

(Read how `results_county.html` shows a contest with Against columns
(retention judges) and match its column wording. Match the site's number format for a missing
value, `r.votes` in `_results.html`.)

`results_county.html`: under the `<h1>`,

```jinja
{% if by_precinct %}<p class="secondary"><a href="{{ base }}/results/{{ election.slug }}/county/{{ county|slug }}/precincts/">Results by precinct</a></p>{% endif %}
```

and inside the contest `<article>` after the candidate table:

```jinja
    {% if c.by_precinct %}<p class="note"><a href="{{ base }}/results/{{ election.slug }}/county/{{ county|slug }}/precincts/#{{ c.anchor }}">By precinct</a></p>{% endif %}
```

(`by_precinct` is undefined on the unofficial county page, which Jinja reads as false.)

`static_site.py`: move the results block of `page_urls` (lines 98-110) into a function the test
can call, adding one precinct page per county:

```python
def results_urls(conn: sqlite3.Connection) -> list[str]:
    """The results pages: each election's hub, county index, county pages and their precinct
    pages (certified elections only), contest pages, and the county histories."""
    urls: list[str] = []
    for e in site_elections(conn):
        base = f"/results/{e['slug']}/"
        urls += [base, base + "county/"]
        for c in county_slugs_for(conn, e["code"]):
            urls.append(f"{base}county/{c}/")
            if not e.get("unofficial") and qr.precinct_picker(conn, e["code"], c):
                urls.append(f"{base}county/{c}/precincts/")
        urls += [f"{base}{c}/" for c in contest_slugs_for(conn, e["code"])]
    if urls:
        urls.append("/results/county/")
        urls += [f"/results/county/{c}/" for c in county_slugs(conn)]
    return urls
```

with `from skipjack.web import results as qr`, and in `page_urls`:

```python
        try:
            results_urls_found = results_urls(conn)
        except sqlite3.OperationalError:  # no results tables in an older database
            results_urls_found = []
```

(rename the local consistently below it; check how `site_elections` marks an unofficial
election and use that key.) `precinct_picker` builds the whole county page; if the site build
slows noticeably, replace the call with one `SELECT DISTINCT county FROM results_precinct WHERE
election = ?` per election.

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_routes.py tests/test_results_web.py tests/test_static_site.py`
Expected: PASS.

**Step 5: Check the script in a browser**

The tests cover the page, not the script. Build a small site the way the tests' fixtures do, or
wait for Task 13's real build, and open a precinct page in the preview browser: pick two
contests, check the hash updates, a deep link `precincts/#<anchor>` opens that contest, the
console has no errors, and the table's sums for one contest equal the county page's totals. If
waiting for Task 13, say so in the task's report so the controller checks it there.

**Step 6: Commit**

```bash
git add src/skipjack/web/results.py src/skipjack/web/routes/results.py src/skipjack/web/templates/results_precincts.html src/skipjack/web/static/precincts.js src/skipjack/web/templates/results_county.html src/skipjack/web/static_site.py tests/test_results_routes.py
git commit -F - <<'EOF'
web: one precinct page per county, with a contest picker over its precinct files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: Precinct readers for the named layout (2010-2020)

**Note from the Task 2 review:** when a precinct file prints no total, `total_against` is the
sum of the Against cells and the county reader's lone-zero rule does not apply. The named
layout reads blanks as zero (`blank_is_zero=True`), so a general precinct row with a lone "0"
in Election Night Votes Against gets `total_against = 0` where the county reader gives None
(the 2012 county files have that pattern on 835 rows). Apply the same lone-zero rule to
precinct rows, or make Task 3's check treat None and 0 against-totals alike for such rows, and
test it with a 2012-style fixture.

**Files:**
- Modify: `src/skipjack/pipeline/results.py` (`_precinct_id_columns`)
- Test: `tests/test_results_precinct.py`

**Step 1: Look at the real files**

Confirm the headers the plan records (they were read on 2026-11-25; check nothing moved):

```bash
for u in archive/2010/election_data/Kent_By_Precinct_2010_General.csv \
         archive/2018/election_data/Kent_By_Precinct_Democratic_2018_Primary.csv \
         archive/2020/election_data/Kent_By_Precinct_2020_General.csv \
         archive/2018/election_data/Kent_By_Precinct_NonPartisan_2018_Primary.csv; do
  curl -s -A "Mozilla/5.0" "https://elections.maryland.gov/elections/$u" | head -2; echo; done
```

The last one must be the board's HTML page (a missing non-partisan file).

**Step 2: Write the failing tests**

```python
# ---- the named layout -------------------------------------------------------------------------

GG10 = results.RESULTS_ELECTIONS["GG10"]
GG18 = results.RESULTS_ELECTIONS["GG18"]
GP18 = results.RESULTS_ELECTIONS["GP18"]
PG20 = results.RESULTS_ELECTIONS["PG20"]

NAMED_2018_GENERAL_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Candidate Name","Party",'
    '"Office Name","Office District","Winner","Write-In?","Election Night Votes",'
    '"Election Night Votes Against"\n'
)
NAMED_2018_PRIMARY_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Candidate Name","Party",'
    '"Office Name","Office District","Winner","Election Night Votes"\n'
)
NAMED_2010_GENERAL_HEAD = NAMED_2018_GENERAL_HEAD.replace('"Cong","Legs",', "")
PG20_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Candidate Name","Party",'
    '"Office Name","Office District","Winner","Write-In?","Early Voting Votes",'
    '"Early Voting Votes Against","Election Day Votes","Election Day Votes Against",'
    '"By Mail Votes","By Mail Votes Against","Prov. Votes","Prov. Votes Against",'
    '"By Mail 2 Votes","By Mail 2 Votes Against","Total Votes","Total Votes Against"\n'
)


def test_read_precinct_2018_general_is_election_night_only():
    rows = (
        '"15","001","001","01","36","Larry Hogan","REP","Governor / Lt. Governor","","Y","","420",""\n'
        '"15","001","002","01","36","Judge Jane","","Judge Court of Appeals","","","","3","1"\n'
    )
    frame = results.read_precinct((NAMED_2018_GENERAL_HEAD + rows).encode(), GG18, "Kent", "all").frame
    assert frame["precinct"].to_list() == ["001-001", "001-002"]
    assert frame.columns[12:] == [
        "election_night_votes", "election_night_against", "total_votes", "total_against",
    ]  # fmt: skip
    assert frame["total_votes"].to_list() == [420, 3]
    assert frame["total_against"].to_list() == [None, 1]


def test_read_precinct_2018_primary():
    row = '"15","001","001","01","36","Ben Jealous","DEM","Governor / Lt. Governor","","Y","17"\n'
    frame = results.read_precinct((NAMED_2018_PRIMARY_HEAD + row).encode(), GP18, "Kent", "dem").frame
    assert frame["congressional"].to_list() == ["01"] and frame["total_votes"].to_list() == [17]


def test_read_precinct_2010_has_no_districts():
    row = '"15","001","001","Martin O\'Malley","DEM","Governor / Lt. Governor","","Y","","195",""\n'
    frame = results.read_precinct((NAMED_2010_GENERAL_HEAD + row).encode(), GG10, "Kent", "all").frame
    assert frame["congressional"].to_list() == [""] and frame["legislative"].to_list() == [""]
    assert frame["total_votes"].to_list() == [195]


def test_read_precinct_pg20_checks_its_printed_total():
    row = '"15","001","001","01","36","Joe Biden","DEM","President - Vice Pres","","Y","","76","","89","","55","","26","","78","","324",""\n'
    frame = results.read_precinct((PG20_HEAD + row).encode(), PG20, "Kent", "all").frame
    assert frame["total_votes"].to_list() == [324] and frame["by_mail_2_votes"].to_list() == [78]
    with pytest.raises(results.ResultsParseError, match="total votes 325 != sum of vote types 324"):
        results.read_precinct((PG20_HEAD + row.replace('"324"', '"325"')).encode(), PG20, "Kent", "all")


def test_named_precinct_check_compares_election_night_only():
    assert results._precinct_check_columns(GG18) == ["election_night_votes", "election_night_against"]
    assert results._precinct_check_columns(GP18) == ["election_night_votes"]
    assert results._precinct_check_columns(PG20)[-2:] == ["total_votes", "total_against"]


def test_parse_gp18_precincts_tolerates_a_missing_non_partisan_file(env):
    county_head = (
        '"Candidate Name","Party","Office Name","Office District","Winner","Early Votes",'
        '"Election Night Votes","Absentees Votes","Provisional Votes","2nd Absentees Votes",'
        '"Total Votes"\n'
    )
    county = county_head + '"Ben Jealous","DEM","Governor / Lt. Governor","","Y","5","17","1","0","0","23"\n'
    precincts = '"CC","001","001","01","36","Ben Jealous","DEM","Governor / Lt. Governor","","Y","17"\n'
    for f in results.county_files(GP18):
        if f.role == "county" and f.party_file != "non":
            archive(env, GP18, f, county.encode())
    for f in results.precinct_files(GP18):
        if f.party_file != "non":
            archive(env, GP18, f, (NAMED_2018_PRIMARY_HEAD + precincts.replace('"CC"', f'"{f.county_code}"')).encode())
    written, failed = results.results_parse("GP18", data_dir=env / "data", level="precinct")
    assert failed == [] and written == 48
    side = json.loads(
        (env / "data" / "results" / "GP18" / "precinct" / "15-kent-dem_provenance.json").read_text()
    )
    assert side["checks"]["county_check"].endswith(
        "on election_night_votes (the board's precinct files for 2010-2018 carry election-night "
        "votes only)"
    )
```

(Both party files read the same fixture rows; the `rep` file's "DEM" candidate is harmless to
the check, which compares by name.)

**Step 3: Run it to see it fail**

`uv run pytest -q tests/test_results_precinct.py -k "named or 2018 or 2010 or pg20 or gp18"`
Expected: FAIL, `ResultsParseError: GG18: no precinct reader for the named layout yet`.

**Step 4: Implement**

```python
def _precinct_id_columns(election: ResultsElection) -> list[str]:
    """The columns that place a precinct row: the coded layout's combined district-precinct
    column, or the named layout's two, with Cong and Legs from 2012 (2010's files have none)."""
    if election.layout == "coded":
        return ["County", "Election District - Precinct", "Congressional", "Legislative"]
    columns = ["County", "Election District", "Election Precinct"]
    return columns + (["Cong", "Legs"] if election.year >= 2012 else [])
```

Nothing else: `_read_wide` already picks the precinct vote types, the printed total for PG20,
and blank-as-zero for the named layout.

**Step 5: Run the tests**

`uv run pytest -q tests/test_results_precinct.py tests/test_results.py`
Expected: PASS.

**Step 6: Commit**

```bash
git add src/skipjack/pipeline/results.py tests/test_results_precinct.py
git commit -F - <<'EOF'
results: precinct files for the named layout, election-night votes only through 2018

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: The ballot-question readers (county and precinct files, both layouts)

> **Third choice (decided 2026-11-25, supersedes the blank rule below):** the readers carry the
> "3rd option" columns as published: `<stem>_third` per vote type, after `<stem>_against`, and
> `total_third` where the county file prints `Total 3rd option`; blank reads as None, and a
> layout without the columns has none. A printed total is checked for the third choice too.
> Wicomico 2016 and 2018 (question A) fill it. `How it Runs?` is kept as printed (`Ed` occurs).

**Files:**
- Modify: `src/skipjack/pipeline/results.py` (`_frame` 331-349; new `QUESTION_*` columns and
  `read_questions` after `read_precinct`)
- Create: `tests/test_results_questions.py`

**Step 1: Look at the real files**

```bash
A=https://elections.maryland.gov/elections/archive
for u in 2024/election_data/PG24_16QuestionResults.csv 2024/election_data/PG24_16QuestionbyPrecinctsResults.csv \
         2018/election_data/Montgomery_Questions_County_2018.csv 2018/election_data/Montgomery_Questions_Precinct_2018.csv \
         2020/election_data/Montgomery_Questions_Precinct_2020.csv 2010/election_data/Montgomery_Questions_Precinct_2010.csv; do
  curl -s -A "Mozilla/5.0" "$A/$u" | head -4; echo; done
```

Montgomery has local questions: check what `County` and `How it Runs?` say on a local question
row in the named county file (the reader below assumes `County` is `"00"` exactly when `How it
Runs?` is `"State"`, and the county's own code otherwise). If a local row says something else,
change the rule in `read_questions` and the test to what the file says, and note it in the
docstring. Also confirm that the 2020 precinct file's rows stop short of the header (count the
cells: `python3 -I -c "import csv,sys; r=list(csv.reader(open(sys.argv[1]))); print(len(r[0]), {len(x) for x in r[1:]})" FILE`).

**Step 2: Write the failing tests**

`tests/test_results_questions.py`:

```python
"""Tests for the ballot questions: readers, checks, parse."""

from __future__ import annotations

import pytest

from skipjack.pipeline import results

PG24 = results.RESULTS_ELECTIONS["PG24"]
GG18 = results.RESULTS_ELECTIONS["GG18"]
GG10 = results.RESULTS_ELECTIONS["GG10"]
PG20 = results.RESULTS_ELECTIONS["PG20"]

CODED_COUNTY_HEAD = (
    '"County","JurisdictionName","QuestionNumber","How it Runs?","Winner","Early Votes",'
    '"Early Votes Against","Election Night Votes","Election Night Votes Against",'
    '"Mail-In Ballot 1 Votes","Mail-In Ballot 1 Votes Against","Provisional Votes",'
    '"Provisional Votes Against","Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against",'
    '"Total Votes","Total Votes Against" \n'
)
# Kent, PG24: For 2454+3243+801+272+1003 = 7773, Against 1047+1534+132+70+154 = 2937
CODED_COUNTY_ROWS = '"CC","Kent County","1","State","Y","2454","1047","3243","1534","801","132","272","70","1003","154","7773","2937" \n'
CODED_PRECINCT_HEAD = (
    '"County","County Name","Election District - Precinct","Congressional","Legislative",'
    '"Question","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Election Night Votes","Election Night Votes Against","Mail-In Ballot 1 Votes",'
    '"Mail-In Ballot 1 Votes Against","Provisional Votes","Provisional Votes Against",'
    '"Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against" \n'
)
# two precincts that sum to the county row above
CODED_PRECINCT_ROWS = (
    '"CC","Kent County","001-001","01","36","1","State","Y","2000","1000","3000","1500","800","130","270","70","1000","150" \n'
    '"CC","Kent County","001-002","01","36","1","State","Y","454","47","243","34","1","2","2","0","3","4" \n'
)
NAMED_COUNTY_HEAD = (
    '"County","Question Number","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Early 3rd option","Election Night Votes","Election Night Votes Against",'
    '"Election Night 3rd option","Absentees Votes","Absentees Votes Against",'
    '"Absentees 3rd option","Provisional Votes","Provisional Votes Against",'
    '"Provisional 3rd option","2nd Absentees Votes","2nd Absentees Votes Against",'
    '"2nd Absentees 3rd option","Total Votes","Total Votes Against","Total 3rd option"\n'
)
# Kent, GG18: For 2409+4078+178+80+112 = 6857, Against 290+628+31+6+9 = 964
NAMED_COUNTY_ROWS = '"00","01","State","Y","2409","290","","4078","628","","178","31","","80","6","","112","9","","6857","964",""\n'
NAMED_PRECINCT_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Question Number",'
    '"How it Runs?","Winner","Election Night Votes","Election Night Votes Against",'
    '"Election Night Votes (3rd option)"\n'
)
NAMED_PRECINCT_ROWS = (
    '"CC","001","001","01","36","01","State","","4000","600",""\n'
    '"CC","001","002","01","36","01","State","","78","28",""\n'
)


def coded_county(code="15", rows=CODED_COUNTY_ROWS) -> bytes:
    return (CODED_COUNTY_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def coded_precincts(code="15", rows=CODED_PRECINCT_ROWS) -> bytes:
    return (CODED_PRECINCT_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def named_county(rows=NAMED_COUNTY_ROWS) -> bytes:
    return (NAMED_COUNTY_HEAD + rows).encode()


def named_precincts(code="15", rows=NAMED_PRECINCT_ROWS) -> bytes:
    return (NAMED_PRECINCT_HEAD + rows.replace('"CC"', f'"{code}"')).encode()


def test_read_coded_county_questions():
    frame = results.read_questions(coded_county(), PG24, "Kent").frame
    assert frame.columns[:5] == ["election", "county", "question", "how_it_runs", "winner"]
    assert frame.columns[-2:] == ["total_votes", "total_against"]
    row = frame.row(0, named=True)
    assert row["question"] == "1" and row["how_it_runs"] == "State" and row["winner"] == 1
    assert row["early_votes"] == 2454 and row["early_against"] == 1047
    assert (row["total_votes"], row["total_against"]) == (7773, 2937)


def test_county_question_totals_must_be_the_sum():
    bad = CODED_COUNTY_ROWS.replace('"7773"', '"7774"')
    with pytest.raises(results.ResultsParseError, match="Total Votes 7774 != sum of vote types 7773"):
        results.read_questions(coded_county(rows=bad), PG24, "Kent")


def test_read_named_county_questions_with_a_blank_third_option():
    frame = results.read_questions(named_county(), GG18, "Kent").frame
    assert frame["question"].to_list() == ["01"]
    assert (frame["total_votes"][0], frame["total_against"][0]) == (6857, 964)
    assert "absentees_2_votes" in frame.columns


def test_a_third_option_figure_is_refused():
    filled = NAMED_COUNTY_ROWS.replace('"628","",', '"628","5",')
    with pytest.raises(results.ResultsParseError, match="'Election Night 3rd option' holds '5'"):
        results.read_questions(named_county(filled), GG18, "Kent")


def test_named_county_column_follows_how_it_runs():
    local = NAMED_COUNTY_ROWS.replace('"00","01","State"', '"15","A","County"')
    assert results.read_questions(named_county(local), GG18, "Kent").frame["question"].to_list() == ["A"]
    wrong = NAMED_COUNTY_ROWS.replace('"00","01","State"', '"15","01","State"')
    with pytest.raises(results.ResultsParseError, match="County '15' for a question that runs 'State'"):
        results.read_questions(named_county(wrong), GG18, "Kent")


def test_read_coded_precinct_questions_computes_the_totals():
    frame = results.read_questions(coded_precincts(), PG24, "Kent", level="precinct").frame
    assert frame.columns[:8] == [
        "election", "county", "precinct", "congressional", "legislative", "question",
        "how_it_runs", "winner",
    ]  # fmt: skip
    assert frame["precinct"].to_list() == ["001-001", "001-002"]
    assert frame["total_votes"].to_list() == [7070, 703]
    assert frame["total_against"].to_list() == [2850, 87]


def test_read_named_precinct_questions_election_night_only():
    frame = results.read_questions(named_precincts(), GG18, "Kent", level="precinct").frame
    assert frame.columns[-4:] == [
        "election_night_votes", "election_night_against", "total_votes", "total_against",
    ]  # fmt: skip
    assert frame["total_votes"].to_list() == [4000, 78]


def test_read_2010_precinct_questions_without_districts():
    head = NAMED_PRECINCT_HEAD.replace('"Cong","Legs",', "").replace(',"Election Night Votes (3rd option)"', "")
    rows = '"15","001","001","01","State","","251","202"\n'
    frame = results.read_questions((head + rows).encode(), GG10, "Kent", level="precinct").frame
    assert frame["congressional"].to_list() == [""] and frame["total_votes"].to_list() == [251]


PG20_PRECINCT_HEAD = (
    '"County","Election District","Election Precinct","Cong","Legs","Question Number",'
    '"How it Runs?","Winner","Early Voting Votes","Early Voting Votes Against",'
    '"Early Voting 3rd option","Election Day Votes","Election Day Votes Against",'
    '"Election Day 3rd option","By Mail Votes","By Mail Votes Against","By Mail 3rd option",'
    '"Prov. Votes","Prov. Votes Against","Prov. 3rd option","By Mail 2 Votes",'
    '"By Mail 2 Votes Against","By Mail 2 3rd option","Total Votes","Total Votes Against",'
    '"Total 3rd option"\n'
)


def test_pg20_precinct_question_rows_stop_short_of_the_total_columns():
    # 24 cells under 26 columns, as the board posts them; Total Votes blank
    row = '"15","001","001","01","36","01","State","Y","130","65","","259","93","","49","11","","50","10","","73","34","",""\n'
    frame = results.read_questions((PG20_PRECINCT_HEAD + row).encode(), PG20, "Kent", level="precinct").frame
    assert frame["total_votes"].to_list() == [561] and frame["total_against"].to_list() == [213]
    short = row.replace(',"73","34","",""', ',"73"')  # cut into the vote columns: refused
    with pytest.raises(results.ResultsParseError, match="rows do not all have 26 columns"):
        results.read_questions((PG20_PRECINCT_HEAD + short).encode(), PG20, "Kent", level="precinct")
```

(For the PG20 row: For 130+259+49+50+73 = 561, Against 65+93+11+10+34 = 213.)

**Step 3: Run it to see it fail**

`uv run pytest -q tests/test_results_questions.py`
Expected: FAIL, `AttributeError: module 'skipjack.pipeline.results' has no attribute 'read_questions'`.

**Step 4: Implement**

`_frame` gains `optional_tail: tuple[str, ...] = ()`:

```python
def _frame(data: bytes, where: str, optional_tail: tuple[str, ...] = ()) -> pl.DataFrame:
    """The file as a frame of stripped strings (BOM, quotes, and the stray trailing space).

    A row may stop short of trailing columns named in ``optional_tail`` (PG20's precinct
    question files leave off their last Total columns); those cells read as blank. A row short
    by more than that, or long, fails."""
    ...  # decoding, rows, header as now
    width = len(header)
    tail = 0
    while tail < min(len(optional_tail), width) and header[width - 1 - tail] in optional_tail:
        tail += 1
    body = []
    for r in rows[1:]:
        cells = [c.strip() for c in r]
        if width - tail <= len(cells) < width:
            cells += [""] * (width - len(cells))
        body.append(cells)
    if any(len(r) != width for r in body):
        raise ResultsParseError(f"{where}: rows do not all have {width} columns")
    return pl.DataFrame(body, schema={c: pl.Utf8 for c in header}, orient="row")
```

After `read_precinct`:

```python
QUESTION_ID_COLUMNS = ["election", "county", "question", "how_it_runs", "winner"]
QUESTION_PRECINCT_ID_COLUMNS = [
    "election", "county", "precinct", "congressional", "legislative", "question",
    "how_it_runs", "winner",
]  # fmt: skip
QUESTION_TOTALS = ("Total Votes", "Total Votes Against", "Total 3rd option")


def read_questions(
    data: bytes, election: ResultsElection, county: str, level: str = "county"
) -> ReadFile:
    """One county's ballot-question file as a wide frame: For votes in <stem>_votes, Against in
    <stem>_against, by vote type, then total_votes and total_against.

    level "county" reads the county question file, "precinct" its precinct file (the
    election's precinct vote types, and the precinct id columns). Columns are matched by name:
    the question number is "Question Number" in the named layout ("01"), "QuestionNumber" in a
    coded county file and "Question" in a coded precinct file ("1"), kept as printed. The named
    county file's County column is "00" for a statewide question (How it Runs? "State") and the
    county's code for a local one; every other file prints the county's code. A "3rd option"
    column (2016-2020) must be blank in every row. A printed total must equal the sum of the vote
    types; the county files must print one, the precinct files print none (PG20's Total cells
    are blank or missing), so there the totals are the sums.
    """
    precinct = level == "precinct"
    where = f"{election.code} {county} questions" + (" by precinct" if precinct else "")
    raw = _frame(data, where, optional_tail=QUESTION_TOTALS if precinct else ())
    code = next(c for c, n in COUNTY_CODES.items() if n == county)
    blank_is_zero = election.layout == "named"
    vote_types = election.precinct_vote_types if precinct else election.vote_types
    if election.layout == "named":
        number = "Question Number"
    else:
        number = "Question" if precinct else "QuestionNumber"
    need = (_precinct_id_columns(election) if precinct else ["County"]) + [
        number, "How it Runs?", "Winner",
    ]  # fmt: skip
    for label, _ in vote_types:
        need += [f"{label} Votes", f"{label} Votes Against"]
    if not precinct:
        need += ["Total Votes", "Total Votes Against"]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        role = "question-precinct" if precinct else "question"
        expected = ResultsFile(election, code, county, "all", role).file_name
        raise ResultsParseError(
            f"{where}: expected {election.code} {election.layout} layout columns in {expected}; "
            f"missing {missing}"
        )
    unknown = sorted(
        c
        for c in raw.columns
        if c.endswith((" Votes", " Votes Against")) and c not in need and not c.startswith("Total")
    )
    if unknown:
        raise ResultsParseError(f"{where}: vote columns not in the registry: {unknown}")
    for c in raw.columns:
        if "3rd option" in c:
            filled = raw.filter(pl.col(c) != "")
            if filled.height:
                raise ResultsParseError(
                    f"{where}: {c!r} holds {filled[c][0]!r}; the reader requires the third "
                    "option to stay blank"
                )
    if raw.height == 0:
        raise ResultsParseError(f"{where}: no data rows (header only)")

    rows = []
    for r in raw.iter_rows(named=True):
        who = f"{where}: question {r[number]}"
        out: dict = {"election": election.code, "county": county}
        runs = r["How it Runs?"]
        if precinct or election.layout == "coded":
            if r["County"] != code:
                raise ResultsParseError(f"{who}: County {r['County']!r} is not {county}'s code {code}")
        elif r["County"] != ("00" if runs == "State" else code):
            raise ResultsParseError(f"{who}: County {r['County']!r} for a question that runs {runs!r}")
        if precinct:
            out["precinct"] = _precinct_id(r, who)
            out["congressional"] = r.get("Congressional", r.get("Cong", ""))
            out["legislative"] = r.get("Legislative", r.get("Legs", ""))
        out |= {
            "question": r[number],
            "how_it_runs": runs,
            "winner": 1 if r["Winner"].upper() in ("Y", "YES") else 0,
        }
        sums = {"votes": 0, "against": 0}
        for label, stem in vote_types:
            for side, column in (("votes", f"{label} Votes"), ("against", f"{label} Votes Against")):
                v = _int(r[column], who, column)
                if v is None and blank_is_zero:
                    v = 0
                if v is None:
                    raise ResultsParseError(f"{who}: blank {column}")
                out[f"{stem}_{side}"] = v
                sums[side] += v
        for side, column in (("votes", "Total Votes"), ("against", "Total Votes Against")):
            printed = _int(r[column], who, column) if column in r else None
            if printed is None and not precinct:
                raise ResultsParseError(f"{who}: blank {column}")
            if printed is not None and printed != sums[side]:
                raise ResultsParseError(
                    f"{who}: {column} {printed} != sum of vote types {sums[side]}"
                )
            out[f"total_{side}"] = sums[side]
        rows.append(out)

    ids = QUESTION_PRECINCT_ID_COLUMNS if precinct else QUESTION_ID_COLUMNS
    schema = {c: pl.Utf8 for c in ids if c != "winner"} | {"winner": pl.Int64}
    for _, stem in vote_types:
        schema |= {f"{stem}_votes": pl.Int64, f"{stem}_against": pl.Int64}
    schema |= {"total_votes": pl.Int64, "total_against": pl.Int64}
    return ReadFile(pl.DataFrame(rows, schema=schema), [])
```

**Step 5: Run the tests**

`uv run pytest -q tests/test_results_questions.py tests/test_results.py tests/test_results_precinct.py`
Expected: PASS (the county tests cover `_frame`'s unchanged default).

**Step 6: Commit**

```bash
git add src/skipjack/pipeline/results.py tests/test_results_questions.py
git commit -F - <<'EOF'
results: ballot-question files by county and by precinct, both layouts

The third-option columns must stay blank, printed totals must add up, and PG20's precinct
question rows that stop short of their blank total columns are read as blank.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 10: The question checks and the parsed question files

Checks (all-or-nothing per election, questions as one level):

- **Row totals** in each county question file (in the reader).
- **Statewide:** the statewide questions' county figures summed must equal the breakdown file's
  County `00` rows (Vote Type 1 = For, 2 = Against, 3 = the third choice, compared with the
  `_third` column, None counting as 0; the coded breakdown has no Vote Type 3 rows). The named
  breakdown also lists local questions (Vote Type 3 for Wicomico A, 2016 and 2018); they are not
  compared. The county column compared is the election's `check_votes`, as for the candidates
  (election-night votes for 2012-2018, totals otherwise) — **verify this in step 1**. 2010 has no
  breakdown: recorded as n/a.
- **Precinct to county:** each county's precinct question rows summed per question must equal
  its county question file on `_precinct_check_columns(e)`.
  The `_third` columns are compared too, on the precinct file's vote types (plus
  `total_third` from 2020); the code below predates this and must add them. The 2016-2018
  precinct files' `total_third` (equal to `election_night_third`, the only vote type they carry)
  is **not** compared: the county file's `total_third` covers every vote type, so as for the
  candidates the totals take part only when the precinct file carries every vote type (2020 on).

Output: `data/results/{CODE}/questions/county/{cc}-{slug}.csv` (plain) and
`data/results/{CODE}/questions/precinct/{cc}-{slug}.csv.gz`, with sidecars (`family`
`"questions"`, `geography` `"county"` / `"precinct"`).

**Files:**
- Modify: `src/skipjack/pipeline/results.py` (new registry dict beside
  `PRECINCT_CHECK_EXCEPTIONS`; new functions after `_parse_precincts`; `PARSERS`)
- Modify: `src/skipjack/cli.py` (`results-parse --level` choices)
- Test: `tests/test_results_questions.py`

**Step 1: Confirm what the statewide breakdown sums to**

Fetch one county question file and the breakdown for 2018 and for 2024 and compare by hand,
or after Task 9 run the reader against all 24 fetched files (`uv run skipjack results-fetch
--election GG18 --level questions`, then in `uv run python`):

```python
from skipjack.pipeline import results as r
e = r.RESULTS_ELECTIONS["GG18"]
frames = [r.read_questions(f.local_path.read_bytes(), e, f.county).frame
          for f in r.question_files(e) if f.role == "question"]
import polars as pl
s = pl.concat(frames).filter(pl.col("how_it_runs") == "State").group_by("question").agg(
    pl.col("election_night_votes").sum(), pl.col("total_votes").sum())
print(s.sort("question"))
```

and compare with the County 00, Vote Type 1 rows of
`archives/sbe/results-questions/GG18/Questions_State_Congressional_Districts_2018.csv` summed
across the `CONG` columns. If 2012-2018 match `total_votes` rather than election night, set the
question check's column to `"total"` for every election in `_check_questions_statewide` below
and change the test to say so. Do the same for PG20 (`total` expected) and PG24.

**Step 2: Write the failing tests**

Append to `tests/test_results_questions.py` (imports: `import json`, `import polars as pl`, and
`from test_results import archive, env  # noqa: F401 (env: fixture)`):

```python
CODED_BREAKDOWN_HEAD = (
    '"Vote Type (FOR=1,AGAINST=2)","Vote Type Description","County","County Name",'
    '"QuestionNumber","How it Runs?","Winner","Congressional District 1",'
    '"Congressional District 2" \n'
)


def coded_breakdown(for_total: int, against_total: int) -> bytes:
    rows = (
        f'"1","For Response","00","State of Maryland","1","State","Y","{for_total - 100}","100" \n'
        f'"2","Against Response","00","State of Maryland","1","State","","{against_total}","0" \n'
        '"1","For Response","02","Anne Arundel County","A","County","Y","5","0" \n'
    )
    return (CODED_BREAKDOWN_HEAD + rows).encode()


def test_statewide_question_check_passes_and_fails():
    frames = [results.read_questions(coded_county(), PG24, "Kent").frame] * 2
    check = results._check_questions_statewide(PG24, frames, coded_breakdown(15546, 5874))
    assert check == 1  # one statewide question compared; Anne Arundel's local question is not
    with pytest.raises(results.ResultsParseError, match="question 1: counties sum to 15546 for, the breakdown says 15547"):
        results._check_questions_statewide(PG24, frames, coded_breakdown(15547, 5874))


def test_question_precinct_check():
    county = results.read_questions(coded_county(), PG24, "Kent").frame
    precincts = results.read_questions(coded_precincts(), PG24, "Kent", level="precinct").frame
    check = results._check_question_precincts(PG24, "Kent", precincts, county)
    assert check.contests == 1 and check.rows == 1
    bad = results.read_questions(
        coded_precincts(rows=CODED_PRECINCT_ROWS.replace('"3000"', '"3001"')), PG24, "Kent", level="precinct"
    ).frame
    with pytest.raises(results.ResultsParseError, match="precincts sum to 3244 election_night_votes"):
        results._check_question_precincts(PG24, "Kent", bad, county)


def fill_questions(env, election=PG24, overrides=None):
    for f in results.question_files(election):
        data = {
            "question": lambda: coded_county(f.county_code),
            "question-precinct": lambda: coded_precincts(f.county_code),
            "question-check": lambda: coded_breakdown(7773 * 24, 2937 * 24),
        }[f.role]()
        archive(env, election, f, (overrides or {}).get(f.source_id) or data)


def test_parse_questions_writes_county_csvs_and_gzipped_precinct_files(env):
    fill_questions(env)
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert failed == [] and written == 48
    base = env / "data" / "results" / "PG24" / "questions"
    county = pl.read_csv(base / "county" / "15-kent.csv", infer_schema=False)
    assert county["total_votes"].to_list() == ["7773"]
    side = json.loads((base / "county" / "15-kent_provenance.json").read_text())
    assert side["family"] == "questions" and side["geography"] == "county"
    assert side["source_id"] == "sbe-results-PG24-question-15"
    assert side["checks"]["statewide_check"].startswith(
        "ok: 1 statewide questions match PG24_AllQuestionsCongressionalBreakDown.csv"
    )
    precinct = base / "precinct" / "15-kent.csv.gz"
    assert pl.read_csv(precinct).height == 2
    side = json.loads((base / "precinct" / "15-kent_provenance.json").read_text())
    assert side["geography"] == "precinct"
    assert side["checks"]["county_check"].startswith("ok: the precincts sum to PG24_15QuestionResults.csv")


def test_parse_questions_is_all_or_nothing(env):
    fill_questions(env, overrides={"sbe-results-PG24-question-check": coded_breakdown(1, 1)})
    written, failed = results.results_parse("PG24", data_dir=env / "data", level="questions")
    assert written == 0 and "PG24 questions:" in failed[0]
    assert not (env / "data" / "results" / "PG24" / "questions").exists()


```

And a 2010 case, built from the real header (2010 has the third absentee canvass, no
3rd-option columns and no statewide breakdown):

```python
GG10_COUNTY_HEAD = (
    '"County","Question Number","How it Runs?","Winner","Early Votes","Early Votes Against",'
    '"Election Night Votes","Election Night Votes Against","Absentees Votes",'
    '"Absentees Votes Against","Provisional Votes","Provisional Votes Against",'
    '"2nd Absentees Votes","2nd Absentees Votes Against","3rd Absentees Votes",'
    '"3rd Absentees Votes Against","Total Votes","Total Votes Against"\n'
)
GG10_COUNTY_ROW = '"00","01","State","","669","747","2670","2323","147","179","48","28","22","32","8","13","3564","3322"\n'
GG10_PRECINCT = (
    '"County","Election District","Election Precinct","Question Number","How it Runs?",'
    '"Winner","Election Night Votes","Election Night Votes Against"\n'
    '"CC","001","001","01","State","","2670","2323"\n'
)


def test_gg10_has_no_statewide_question_breakdown(env):
    for f in results.question_files(GG10):
        data = (GG10_COUNTY_HEAD + GG10_COUNTY_ROW) if f.role == "question" else GG10_PRECINCT.replace('"CC"', f'"{f.county_code}"')
        archive(env, GG10, f, data.encode())
    written, failed = results.results_parse("GG10", data_dir=env / "data", level="questions")
    assert failed == [] and written == 48
    side = json.loads(
        (env / "data" / "results" / "GG10" / "questions" / "county" / "15-kent_provenance.json").read_text()
    )
    assert side["checks"]["statewide_check"] == "n/a: the board posted no statewide question breakdown for 2010"
```

**Step 3: Run it to see it fail**

`uv run pytest -q tests/test_results_questions.py`
Expected: FAIL, `AttributeError: ... has no attribute '_check_questions_statewide'`.

**Step 4: Implement**

Beside `PRECINCT_CHECK_EXCEPTIONS`:

```python
# Question figures the board's own files disagree on: (election code, county or "statewide",
# question, column) -> (what the parts sum to, what the whole says). Empty until a full run
# finds one; accepted only as listed.
QUESTION_CHECK_EXCEPTIONS: dict[tuple[str, str, str, str], tuple[int, int]] = {}
```

After `_parse_precincts`:

```python
def _check_questions_statewide(
    election: ResultsElection, frames: list[pl.DataFrame], breakdown: bytes
) -> int:
    """The statewide questions (How it Runs? "State") summed over the county files must equal
    the breakdown file's County 00 rows, For and Against, on the election's check column, and
    a third-option row (Vote Type 3) against the summed <column>_third (None counting as 0).
    Local questions are not compared. Returns how many questions matched."""
    where = f"{election.code} question breakdown"
    raw = _frame(breakdown, where)
    vt = "Vote Type (FOR=1,AGAINST=2)"
    number = "QuestionNumber" if election.layout == "coded" else "Question Number"
    if vt not in raw.columns or number not in raw.columns:
        raise ResultsParseError(f"{where}: no {vt!r} or {number!r} column")
    district_cols = [
        c for c in raw.columns if c.startswith("Congressional District") or re.fullmatch(r"CONG \S+", c)
    ]
    if not district_cols:
        raise ResultsParseError(f"{where}: no district columns")
    state: dict[tuple[str, str], int] = {}
    for r in raw.filter(pl.col("County") == "00").iter_rows(named=True):
        n = sum(_int(r[c], where, c) or 0 for c in district_cols)
        side = {"1": "for", "2": "against", "3": "third"}.get(r[vt])
        if side is None:
            raise ResultsParseError(f"{where}: unknown vote type {r[vt]!r}")
        state[(r[number], side)] = state.get((r[number], side), 0) + n
    column = election.check_votes
    sums: dict[tuple[str, str], int] = {}
    for frame in frames:
        for r in frame.filter(pl.col("how_it_runs") == "State").iter_rows(named=True):
            sides = [("for", "votes"), ("against", "against")]
            if f"{column}_third" in r:
                sides.append(("third", "third"))  # None counts as 0
            for side, suffix in sides:
                key = (r["question"], side)
                sums[key] = sums.get(key, 0) + (r[f"{column}_{suffix}"] or 0)
    for key in sorted(set(sums) | set(state)):
        ours, theirs = sums.get(key), state.get(key)
        if ours is None or theirs is None:
            raise ResultsParseError(
                f"{where}: question {key[0]} {key[1]} is "
                + ("in the breakdown but no county file" if ours is None else "in the county files but not the breakdown")
            )
        if ours != theirs and QUESTION_CHECK_EXCEPTIONS.get(
            (election.code, "statewide", key[0], f"{column}_{key[1]}")
        ) != (ours, theirs):
            raise ResultsParseError(
                f"{where}: question {key[0]}: counties sum to {ours} {key[1]}, the breakdown "
                f"says {theirs}"
            )
    return len({k[0] for k in sums})


def _check_question_precincts(
    election: ResultsElection, county: str, precincts: pl.DataFrame, county_frame: pl.DataFrame
) -> SumCheck:
    """One county's precinct question rows summed per question against its county question
    file, on the vote types the precinct file carries (and the totals from 2020). Questions are
    on general ballots only, so _precinct_check_columns gives the For and Against columns."""
    return _check_sums(
        f"{election.code} {county} questions by precinct",
        ["question"],
        1,
        _precinct_check_columns(election),
        precincts,
        county_frame,
        lambda key, c: QUESTION_CHECK_EXCEPTIONS.get((election.code, county, key[0], c)),
    )
```

The parse:

```python
def _question_out(data_dir: Path, f: ResultsFile) -> Path:
    level = "county" if f.role == "question" else "precinct"
    suffix = ".csv" if level == "county" else ".csv.gz"
    return data_dir / "results" / f.election.code / "questions" / level / (
        f"{f.county_code}-{county_slug(f.county)}{suffix}"
    )


def _parse_questions(e: ResultsElection, force: bool, data_dir: Path) -> int:
    """Check a general's question files and write them: the county files to
    questions/county/ (plain CSV, small) and the precinct files to questions/precinct/
    (gzipped). All-or-nothing per election; current files need no archive, as for precincts."""
    files = question_files(e)
    if not files:
        return 0
    records = {f.source_id: newest_source(MANIFEST_PATH, f.source_id) for f in files}
    for f in files:
        if records[f.source_id] is None:
            raise ResultsParseError(f"missing required file {f.file_name} ({f.source_id})")
    data_files = [f for f in files if f.role != "question-check"]
    stale = [
        f for f in data_files if force or _revision_updated(_question_out(data_dir, f), records[f.source_id])
    ]
    if not stale:
        return 0

    def read(f: ResultsFile) -> tuple[bytes, SourceRecord]:
        archived = _read_archived(f)
        if archived is None:
            raise ResultsParseError(
                f"{f.file_name} is in the manifest but not archived here; re-fetch with "
                "`skipjack results-fetch --level questions`"
            )
        return archived

    counties: dict[str, tuple[ResultsFile, SourceRecord, ReadFile]] = {}
    for f in (f for f in data_files if f.role == "question"):
        data, record = read(f)
        try:
            counties[f.county] = (f, record, read_questions(data, e, f.county))
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
    check_file = next((f for f in files if f.role == "question-check"), None)
    if check_file is None:
        statewide = f"n/a: the board posted no statewide question breakdown for {e.year}"
    else:
        n = _check_questions_statewide(
            e, [rd.frame for _, _, rd in counties.values()], read(check_file)[0]
        )
        statewide = f"ok: {n} statewide questions match {check_file.file_name}"
        if e.check_votes == "election_night":
            statewide += " on election-night votes (the board's district files for 2010-2018 carry election-day votes only)"
    precincts = []
    for f in (f for f in data_files if f.role == "question-precinct"):
        data, record = read(f)
        try:
            rd = read_questions(data, e, f.county, level="precinct")
            check = _check_question_precincts(e, f.county, rd.frame, counties[f.county][2].frame)
        except ResultsParseError as err:
            raise ResultsParseError(f"{f.file_name}: {err}") from None
        precincts.append((f, record, rd, check))

    written = 0
    for f, record, rd, check in [(*c, None) for c in counties.values()] + precincts:
        if f not in stale:
            continue
        out = _question_out(data_dir, f)
        out.parent.mkdir(parents=True, exist_ok=True)
        if check is None:
            checks = {"rows": rd.frame.height, "row_totals": "ok", "statewide_check": statewide}
            geography, data = "county", rd.frame.write_csv().encode()
        else:
            county_file = counties[f.county][0].file_name
            checks = {
                "rows": rd.frame.height,
                "row_totals": "computed: the precinct file prints no total",
                "county_check": _precinct_check_text(e, county_file, check),
            }
            geography, data = "precinct", _gzip_csv(rd.frame)
        sidecar = ProvenanceSidecar.from_source(
            record, parser_version=PARSER_VERSION, extraction_method="csv", checks=checks,
            notes=f"{e.code} {f.county} ballot questions ({geography}), checked.",
        )  # fmt: skip
        sidecar.election, sidecar.family, sidecar.geography = e.code, "questions", geography
        _write_atomic(out, data)
        sidecar.write(out)
        written += 1
    logger.info("%s: parsed %d question files", e.code, written)
    return written
```

`_precinct_check_text` says "contests" — fine for questions too ("1 contests" reads as the
question count); if the wording grates, pass a noun parameter.

`PARSERS = {"county": _parse_election, "precinct": _parse_precincts, "questions": _parse_questions}`
and `cli.py` `results-parse --level` choices become `["county", "precinct", "questions", "all"]`.

**Step 5: Run the tests**

`uv run pytest -q tests/test_results_questions.py tests/test_results_precinct.py tests/test_results.py`
Expected: PASS.

**Step 6: Commit**

```bash
git add src/skipjack/pipeline/results.py src/skipjack/cli.py tests/test_results_questions.py
git commit -F - <<'EOF'
results: ballot questions checked statewide and by precinct, then written

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 11: Question tables in the database

> **Third choice:** `results_questions` gains `total_third INTEGER` and `results_question_votes`
> gains `votes_third INTEGER`, both nullable (NULL where the file prints none); the loader checks
> `total_third` as the sum of the `_third` columns where any is printed.

**Files:**
- Modify: `src/skipjack/models.py` (after the precinct tables)
- Modify: `src/skipjack/pipeline/build_db.py` (new `load_results_questions`, `build`)
- Test: `tests/test_results_precinct_db.py`

**Step 1: Write the failing tests**

```python
def write_questions(data_dir, code, number, county, geography, rows, sidecar=True):
    """A question CSV: county (plain) or precinct (gzipped); rows after election and county."""
    e = RESULTS_ELECTIONS[code]
    types = e.vote_types if geography == "county" else e.precinct_vote_types
    cols = ["election", "county"]
    cols += ["precinct", "congressional", "legislative"] if geography == "precinct" else []
    cols += ["question", "how_it_runs", "winner"]
    for _, stem in types:
        cols += [f"{stem}_votes", f"{stem}_against"]
    cols += ["total_votes", "total_against"]
    d = data_dir / "results" / code / "questions" / geography
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{number}-{county_slug(county)}"
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(cols)
    for r in rows:
        w.writerow([code, county, *r])
    path = d / (f"{stem}.csv" if geography == "county" else f"{stem}.csv.gz")
    data = text.getvalue().encode()
    path.write_bytes(data if geography == "county" else gzip.compress(data))
    role = "question" if geography == "county" else "question-precinct"
    if sidecar:
        (d / f"{stem}_provenance.json").write_text(json.dumps({
            "source_id": f"sbe-results-{code}-{role}-{number}", "election": code,
            "family": "questions", "geography": geography,
            "source_url": "https://elections.maryland.gov/x.csv",
            "source_name": "Maryland State Board of Elections", "document_title": "test",
            "fetch_date": "2026-11-25T12:00:00+00:00", "parse_date": "2026-11-25T12:00:01+00:00",
            "sha256_original": "0" * 64, "parser_version": "skipjack 0.4.0", "notes": "test",
            "extraction_method": "csv", "checks": {"rows": len(rows)},
        }))  # fmt: skip


def test_load_questions_at_both_geographies(conn, tmp_path):
    # GG18 county: five vote types For/Against then totals; precinct: election night only
    write_questions(tmp_path, "GG18", "15", "Kent", "county",
                    [["01", "State", 1, 1, 1, 2, 2, 0, 0, 0, 0, 0, 0, 3, 3]])  # fmt: skip
    write_questions(tmp_path, "GG18", "15", "Kent", "precinct",
                    [["001-001", "01", "36", "01", "State", 0, 2, 2, 2, 2]])  # fmt: skip
    assert load_results_questions(conn, tmp_path) == 2
    rows = conn.execute(
        "SELECT geography, precinct, question, total_votes, total_against FROM results_questions ORDER BY id"
    ).fetchall()
    assert rows == [("county", None, "1", 3, 3), ("precinct", "001-001", "1", 2, 2)]
    assert conn.execute("SELECT COUNT(*) FROM results_question_votes").fetchone() == (6,)


def test_load_questions_refuses_totals_that_do_not_add_up(conn, tmp_path):
    write_questions(tmp_path, "GG18", "15", "Kent", "county",
                    [["01", "State", 1, 1, 1, 2, 2, 0, 0, 0, 0, 0, 0, 4, 3]])  # fmt: skip
    with pytest.raises(ResultsLoadError, match="total votes 4 != sum of vote types 3"):
        load_results_questions(conn, tmp_path)
```

(import `load_results_questions`.)

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_precinct_db.py -k questions`
Expected: FAIL, `ImportError: cannot import name 'load_results_questions'`.

**Step 3: Implement**

`models.py`:

```sql
-- A ballot question's certified result in one county (geography 'county', precinct NULL) or
-- one precinct (geography 'precinct'). question is the board's number with leading zeros dropped
-- ('1'; a county's own questions are lettered, 'A'); how_it_runs is the board's 'State' or the
-- local kind. total_votes is For, total_against Against; through 2018 the precinct rows are
-- election-night votes only. winner is the board's flag as printed (its meaning, statewide or
-- county outcome, is not documented; the pages do not show it).
CREATE TABLE IF NOT EXISTS results_questions (
    id INTEGER PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    election TEXT NOT NULL,
    geography TEXT NOT NULL,
    county TEXT NOT NULL,
    precinct TEXT,
    congressional TEXT,
    legislative TEXT,
    question TEXT NOT NULL,
    how_it_runs TEXT NOT NULL,
    winner INTEGER NOT NULL,
    total_votes INTEGER NOT NULL,
    total_against INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS results_question_votes (
    question_id INTEGER NOT NULL REFERENCES results_questions(id),
    vote_type TEXT NOT NULL,
    votes INTEGER NOT NULL,
    votes_against INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_questions_county
    ON results_questions (election, county, geography);
CREATE INDEX IF NOT EXISTS idx_results_question_votes ON results_question_votes (question_id);
```

`build_db.py`:

```python
def load_results_questions(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> int:
    """Load data/results/<CODE>/questions/county/*.csv and questions/precinct/*.csv.gz, melting
    the vote types. Returns the number of results_questions rows loaded."""
    root = data_dir / "results"
    if not root.exists():
        return 0
    next_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM results_questions").fetchone()[0] + 1
    total = 0
    paths = sorted(root.glob("*/questions/county/*.csv")) + sorted(
        root.glob("*/questions/precinct/*.csv.gz")
    )
    for path in paths:
        geography = path.parent.name
        prov = load_provenance(path)
        if not prov:
            raise ResultsLoadError(f"{path}: no provenance sidecar; refusing to load an unattributable file")
        code = path.parents[2].name
        election = RESULTS_ELECTIONS.get(code)
        if election is None:
            raise ResultsLoadError(f"{path}: unknown election code {code!r}")
        number = path.name.partition("-")[0]
        role = "question" if geography == "county" else "question-precinct"
        expected = f"sbe-results-{code}-{role}-{number}"
        if prov.get("source_id") != expected or prov.get("election") != code:
            raise ResultsLoadError(f"{path}: sidecar source_id {prov.get('source_id')!r}, expected {expected!r}")
        types = election.vote_types if geography == "county" else election.precinct_vote_types
        stems = [s for _, s in types]
        text = ["election", "county", "question", "how_it_runs"]
        text += ["precinct", "congressional", "legislative"] if geography == "precinct" else []
        overrides = {c: pl.String for c in text} | {"winner": pl.Int64}
        overrides |= {f"{s}_{side}": pl.Int64 for s in stems for side in ("votes", "against")}
        overrides |= {"total_votes": pl.Int64, "total_against": pl.Int64}
        try:
            df = pl.read_csv(path, schema_overrides=overrides, missing_utf8_is_empty_string=True)
        except PolarsError as e:
            raise ResultsLoadError(f"{path}: {e}") from e
        missing = [c for c in overrides if c not in df.columns]
        if missing or df.height == 0:
            raise ResultsLoadError(f"{path}: missing columns {missing} or no rows")
        if df["county"].unique().to_list() != [COUNTY_CODES.get(number)]:
            raise ResultsLoadError(f"{path}: county column does not match the file name")
        _upsert_source(conn, prov)
        q_rows, v_rows = [], []
        for row in df.iter_rows(named=True):
            sums = {"votes": 0, "against": 0}
            for s in stems:
                for side in sums:
                    n = row[f"{s}_{side}"]
                    if n is None:
                        raise ResultsLoadError(f"{path}: blank {s}_{side} on question {row['question']}")
                    sums[side] += n
                v_rows.append((next_id, s, row[f"{s}_votes"], row[f"{s}_against"]))
            for side, column in (("votes", "total_votes"), ("against", "total_against")):
                if sums[side] != row[column]:
                    raise ResultsLoadError(
                        f"{path}: question {row['question']}: total {side} {row[column]} != "
                        f"sum of vote types {sums[side]}"
                    )
            q_rows.append((
                next_id, prov["source_id"], code, geography, row["county"], row.get("precinct"),
                row.get("congressional"), row.get("legislative"),
                _normalize_district(row["question"]), row["how_it_runs"], row["winner"],
                row["total_votes"], row["total_against"],
            ))  # fmt: skip
            next_id += 1
        conn.executemany(
            """INSERT INTO results_questions (id, source_id, election, geography, county,
               precinct, congressional, legislative, question, how_it_runs, winner, total_votes,
               total_against) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            q_rows,
        )
        conn.executemany(
            "INSERT INTO results_question_votes (question_id, vote_type, votes, votes_against) "
            "VALUES (?, ?, ?, ?)",
            v_rows,
        )
        total += len(q_rows)
    logger.info("Loaded ballot questions: %d rows", total)
    return total
```

(The test's error text "total votes 4 != sum of vote types 3" corresponds to
`f"total {side}"` with side "votes": keep the message as written so it reads "total votes 4".)

`_normalize_district` drops "01" to "1" for the question number so the named ("01") and coded
("1") layouts agree. `build()`: `counts["results_questions"] = load_results_questions(conn, data_dir)`.

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_precinct_db.py tests/test_results_db.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/skipjack/models.py src/skipjack/pipeline/build_db.py tests/test_results_precinct_db.py
git commit -F - <<'EOF'
db: results_questions by county and by precinct, with their votes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 12: Question pages, `get questions`

> **Third choice:** the county page's question table and the precinct picker's question table
> (`precincts.js`) show a third-choice column only for a question that has one (any `_third`
> figure); `get questions` passes the `_third` and `total_third` columns through.

The certified county pages show no ballot questions today (the `questions` section of
`results_county.html` is filled only by the unofficial count, `results_unofficial.question`).
This task adds them to the certified county page from `results_questions` (county rows), each
with a "By precinct" link to `/results/{election}/county/{county}/precincts/#question-{n}`: the
questions join the contests in Task 7's picker, read from the county's question precinct file
(`results/{CODE}/questions/precinct/{cc}-{slug}.csv.gz`). No page per question. It does not add certified statewide question pages (the unofficial
`question-*` contest pages stay unofficial-only); that is a separate design question.

**Files:**
- Modify: `src/skipjack/web/results.py` (`county_page`, new `_county_questions`,
  `precinct_picker`)
- Modify: `src/skipjack/web/templates/_results.html` (new `question_table` macro)
- Modify: `src/skipjack/web/templates/results_county.html` (questions section ~111-122)
- Modify: `src/skipjack/client/datasets.py` (new `questions` after `results`),
  `src/skipjack/client/commands.py` (new `questions` parser, `_GET`), `tests/client_fixture.py`
  (the catalog's `question_files` already come from Task 5)
- Test: `tests/test_results_routes.py`, `tests/test_client_results.py`

**Step 1: Write the failing tests**

`tests/test_results_routes.py`:

```python
def add_question(conn, election, county, geography, question, for_votes, against, precinct=None):
    code = {"Allegany": "01", "Garrett": "11"}[county]
    role = "question" if geography == "county" else "question-precinct"
    cur = conn.execute(
        """INSERT INTO results_questions (source_id, election, geography, county, precinct,
           congressional, legislative, question, how_it_runs, winner, total_votes, total_against)
           VALUES (?, ?, ?, ?, ?, '06', '01', ?, 'State', 1, ?, ?)""",
        (f"sbe-results-{election}-{role}-{code}", election, geography, county, precinct, question,
         for_votes, against),
    )  # fmt: skip
    conn.execute(
        "INSERT INTO results_question_votes VALUES (?, 'early', ?, ?)",
        (cur.lastrowid, for_votes, against),
    )


def test_certified_county_page_lists_its_questions_with_a_precinct_link(client, db):
    # the data fixture already has PG24 rows in Allegany, so its county page exists
    add_question(db, "PG24", "Allegany", "county", "1", 70, 30)
    add_question(db, "PG24", "Allegany", "precinct", "1", 70, 30, precinct="001-001")
    html = client.get("/results/pg24/county/allegany/").text
    assert 'id="question-1"' in html and "70.0%" in html
    assert "/results/pg24/county/allegany/precincts/#question-1" in html
    page = client.get("/results/pg24/county/allegany/precincts/")
    assert page.status_code == 200
    question = next(c for c in picker_contests(page.text) if c["kind"] == "question")
    assert question == {
        "kind": "question", "anchor": "question-1", "title": "Question 1",
        "group": "Ballot questions", "question": "1",
        "file": "results/PG24/questions/precinct/01-allegany.csv.gz",
    }  # fmt: skip


def test_a_county_with_only_question_precincts_gets_a_precinct_page(db, data):
    from skipjack.web.static_site import results_urls

    add_question(db, "PG24", "Allegany", "county", "1", 70, 30)
    add_question(db, "PG24", "Allegany", "precinct", "1", 70, 30, precinct="001-001")
    assert "/results/pg24/county/allegany/precincts/" in results_urls(db)
```

(Both rely on the `data` fixture's PG24 rows in Allegany; confirm them in the fixture first.)

`tests/client_fixture.py`: add

```python
QUESTIONS_HEADER = "election,county,question,how_it_runs,winner,early_votes,early_against,total_votes,total_against\n"
QUESTION_PRECINCT_HEADER = (
    "election,county,precinct,congressional,legislative,question,how_it_runs,winner,"
    "early_votes,early_against,total_votes,total_against\n"
)
```

files `"results/GG22/questions/county/01-allegany.csv": QUESTIONS_HEADER + "GG22,Allegany,1,State,1,300,100,3000,1000\n"`,
gzipped `"results/GG22/questions/precinct/01-allegany.csv.gz": QUESTION_PRECINCT_HEADER +
"GG22,Allegany,001-001,06,01,1,State,1,300,100,3000,1000\n"`, and the GG22 catalog entry's
`"question_files": {"county": [...the csv...], "precinct": [...the gz...]}`.

`tests/test_client_results.py`:

```python
def test_questions_by_county_and_by_precinct(capsys, base):
    out = json.loads(run(capsys, "get", "questions", "--election", "GG22", "--base-url", base))
    assert out["rows"][0]["question"] == "1" and out["rows"][0]["total_against"] == 1000
    assert out["meta"]["dataset"] == "questions"
    out = json.loads(
        run(capsys, "get", "questions", "--election", "GG22", "--base-url", base,
            "--geography", "precinct", "--county", "allegany", "--precinct", "1-1")
    )  # fmt: skip
    assert [r["precinct"] for r in out["rows"]] == ["001-001"]


def test_questions_for_an_election_without_them(capsys, base, tmp_path):
    index = tmp_path / "downloads" / "index.json"
    catalog = json.loads(index.read_text())
    del catalog["results"]["GG22"]["question_files"]
    index.write_text(json.dumps(catalog))
    with pytest.raises(SystemExit):
        main(["get", "questions", "--election", "GG22", "--base-url", base])
    assert "GG22 has no ballot-question files on this site" in capsys.readouterr().err
```

**Step 2: Run it to see it fail**

`uv run pytest -q tests/test_results_routes.py -k question tests/test_client_results.py -k questions`
Expected: FAIL (no question section on the certified page; `invalid choice: 'questions'`).

**Step 3: Implement**

`web/results.py`:

```python
def _county_questions(conn: sqlite3.Connection, code: str, county: str) -> list[dict]:
    """The county's ballot questions as certified, statewide first, each with For and Against
    by vote type and a link to its precinct table when it has one."""
    stems = [s for _, s in RESULTS_ELECTIONS[code].vote_types]
    rows = conn.execute(
        """SELECT id, question, how_it_runs, total_votes, total_against FROM results_questions
           WHERE election = ? AND county = ? AND geography = 'county'
           ORDER BY how_it_runs <> 'State', id""",
        (code, county),
    ).fetchall()
    votes: dict[int, dict[str, tuple[int, int]]] = {}
    for qid, vote_type, n, against in conn.execute(
        """SELECT v.question_id, v.vote_type, v.votes, v.votes_against
           FROM results_question_votes v JOIN results_questions q ON q.id = v.question_id
           WHERE q.election = ? AND q.county = ? AND q.geography = 'county'""",
        (code, county),
    ):
        votes.setdefault(qid, {})[vote_type] = (n, against)
    with_precincts = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT question FROM results_questions "
            "WHERE election = ? AND county = ? AND geography = 'precinct'",
            (code, county),
        )
    }
    out = []
    for qid, number, runs, for_votes, against in rows:
        cast = for_votes + against
        anchor = f"question-{slugify(number)}"
        out.append(
            {
                "question": number,
                "title": f"Question {number}",
                "anchor": anchor,
                "statewide": runs == "State",
                "choices": [
                    {"label": label, "total": total,
                     "votes": {s: votes.get(qid, {}).get(s, (0, 0))[i] for s in stems},
                     "share": 100 * total / cast if cast else None}
                    for i, (label, total) in enumerate((("For", for_votes), ("Against", against)))
                ],  # fmt: skip
                "by_precinct": number in with_precincts,
            }
        )
    return out


```

`county_page` returns `"questions": _county_questions(conn, code, county)`, and its
`by_precinct` becomes true when any contest or question has precinct rows. `precinct_picker`
(Task 7) appends the questions after the contests, and keeps the page when only questions have
rows: move its `if not contests: return None` after this addition.

```python
    county_code = _CODE_FOR_COUNTY[page["county"]]
    contests += [
        {
            "kind": "question",
            "anchor": q["anchor"],
            "title": q["title"],
            "group": "Ballot questions",
            "question": q["question"],
            "file": f"results/{code}/questions/precinct/{county_code}-{slugify(page['county'])}.csv.gz",
        }
        for q in page["questions"]
        if q["by_precinct"]
    ]
```

The script's `questionTable` (Task 7) already handles `kind: "question"`, comparing question
numbers numerically ("01" in the named files, "1" in the coded ones). Its For share column
should match `share` in `_results.html` (one decimal and a percent sign). Check it in the
browser with the contests (Task 7, step 5).

`_results.html`:

```jinja
{# A certified ballot question in one county: For and Against by way of voting, with shares. #}
{% macro question_table(q, caption, vote_types=[], compact=false) -%}
<div class="overflow-auto">
<table class="results-table{% if compact %} compact{% endif %}">
    <caption class="sr-only">{{ caption }}</caption>
    <thead>
        <tr>
            <th scope="col">Choice</th>
            {% for v in vote_types %}<th scope="col" class="num">{{ v.label }}</th>{% endfor %}
            <th scope="col" class="num">Votes</th>
            <th scope="col" class="num">Share</th>
        </tr>
    </thead>
    <tbody>
        {% for c in q.choices %}
        <tr>
            <th scope="row">{{ c.label }}</th>
            {% for v in vote_types %}<td class="num">{{ votes(c.votes[v.stem]) }}</td>{% endfor %}
            <td class="num">{{ votes(c.total) }}</td>
            <td class="num">{{ share(c) }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
</div>
{%- endmacro %}
```

`results_county.html`, the question `<article>` body:

```jinja
<article class="contest-card" id="{{ q.anchor }}">
    {% if unofficial %}
    <h3>{% if q.slug %}<a href="{{ base }}/results/{{ election.slug }}/{{ q.slug }}/">{{ q.title }}</a>{% else %}{{ q.title }} <span class="tag">county question</span>{% endif %}</h3>
    {{ u.question_detail(q) }}
    {{ u.question_table(q, q.title ~ " in " ~ county, vote_types, compact=true) }}
    {% else %}
    <h3>{{ q.title }}{% if not q.statewide %} <span class="tag">county question</span>{% endif %}</h3>
    {{ r.question_table(q, q.title ~ " in " ~ county, vote_types, compact=true) }}
    {% if q.by_precinct %}<p class="note"><a href="{{ base }}/results/{{ election.slug }}/county/{{ county|slug }}/precincts/#{{ q.anchor }}">By precinct</a></p>{% endif %}
    {% endif %}
</article>
```

`client/commands.py`:

```python
    qs = get_sub.add_parser("questions", help="An election's ballot questions by county or precinct")
    qs.add_argument("--election", required=True, help="An election code, e.g. PG24")
    qs.add_argument("--geography", choices=["county", "precinct"], default="county")
    qs.add_argument("--county")
    qs.add_argument("--precinct", metavar="DDD-PPP")
    _shared(qs)
```

and `_GET["questions"] = datasets.questions`. `--source` is not offered (questions are not parsed
live); `getattr(args, "source", "skipjack")` already defaults.

`client/datasets.py`:

```python
def questions(args: argparse.Namespace, source: SkipjackSource) -> tuple[dict, list[dict]]:
    """Ballot questions: questions/county/NN-<slug>.csv, questions/precinct/NN-<slug>.csv.gz."""
    catalog = source.catalog()
    code = choice("election", args.election.upper(), catalog["results"])
    election = catalog["results"][code]
    files = (election.get("question_files") or {}).get(args.geography) or []
    if not files:
        raise ClientError(f"{code} has no ballot-question files on this site")
    precinct = precinct_id(args.precinct) if args.precinct else None
    if precinct and args.geography != "precinct":
        raise ClientError("--precinct needs --geography precinct")
    county = county_for(catalog["counties"], args.county)
    if county:
        files = [f for f in files if file_stem(f) == f"{county['code']}-{county['slug']}"]
    rows = []
    for f in files:
        rows += [
            r for r in read_rows(source, f, ["precinct"] if precinct else [])
            if precinct is None or r["precinct"] == precinct
        ]
    filters = {"county": county["name"] if county else None,
               "geography": args.geography if args.geography != "county" else None,
               "precinct": precinct}  # fmt: skip
    built = meta(
        "questions", [source.url(f) for f in files], filters,
        election=code, name=election["name"], election_day=election["election_day"],
        official=election["official"],
    )  # fmt: skip
    return built, rows
```

**Step 4: Run the tests**

`uv run pytest -q tests/test_results_routes.py tests/test_results_web.py tests/test_results_unofficial_web.py tests/test_client_results.py tests/test_client_commands.py`
Expected: PASS (the unofficial county page still renders its own question section).

**Step 5: Commit**

```bash
git add src/skipjack/web/results.py src/skipjack/web/templates/_results.html src/skipjack/web/templates/results_county.html src/skipjack/client/datasets.py src/skipjack/client/commands.py tests/client_fixture.py tests/test_results_routes.py tests/test_client_results.py
git commit -F - <<'EOF'
web: certified ballot questions on the county pages, and in the precinct picker

The data cli gains skipjack get questions, by county or by precinct.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 13: The real files, every election, and the docs

**Files:**
- Modify: `src/skipjack/pipeline/results.py` (exception registries, only as the runs require)
- Modify: `README.md` (Results bullet ~64-77, the `make results` paragraph ~130, "Pulling the
  data from the command line" ~137-185)
- Commit: `data/results/*/precinct/`, `data/results/*/questions/`, `archives/manifest.csv`

**Step 1: One election end to end**

```bash
uv run skipjack results-fetch --election PG24 --level precinct
uv run skipjack results-fetch --election PG24 --level questions
uv run skipjack results-parse --election PG24
git status --short archives | grep -v manifest.csv   # must print nothing: the raw files are ignored
```

Expected: 24 precinct, 24 + 24 + 1 question files fetched; `Parsed 72 results files` (24
precinct + 48 question; the county files are current). If a check fails, read the message: it
names the file, the row and the column. Decide from the board's own files whether it is a
reading problem (fix the reader, with a test built from the offending rows) or the board's files
disagreeing with themselves (add the exact pair to `PRECINCT_CHECK_EXCEPTIONS` /
`QUESTION_CHECK_EXCEPTIONS` with a comment saying which files and how far apart, and a test
that the listed pair is accepted and a different one is not). Things to watch for, not seen in
Kent's files: pseudo-precincts (absentee, provisional) in the coded files, blank vote cells in
coded precinct files (the coded reader refuses them), a local question whose `County` column
breaks the "00 iff State" rule.

Then `uv run skipjack build-db`, `uv run skipjack serve`, and look at
`/results/pg24/county/kent/` and one precinct page; and
`uv run skipjack get results --election PG24 --geography precinct --county Kent --base-url site/downloads`
after `make site`.

**Step 2: Every election**

```bash
uv run skipjack results-fetch            # all levels, every published election
uv run skipjack results-parse
```

Expect PP20 to fetch no precinct files and the 2012/2014/2018 primaries (and 2010) to log
"no non-partisan precinct file for ..." for some counties. Every failure goes through the step-1
decision. Record every exception you add, with the reason, in the commit message.

**Step 3: Measure**

```bash
du -sh archives/sbe/results-precinct archives/sbe/results-questions data/results
du -sh data/results/*/precinct | sort -h | tail -3
uv run skipjack build-db && ls -lh skipjack.db
time make site && du -sh site && find site/results -name index.html | wc -l
```

Before this work: `archives` 147M, `data` 121M, `site` 259M, `skipjack.db` 195M. Write the new
figures into the README paragraph below. If the site build time or size is a problem for GitHub
Pages (1 GB site limit; the Pages workflow builds the database and the site), stop and raise it
with the user rather than trimming pages silently.

**Step 4: Docs**

README, the Results bullet: replace "Precinct and ballot-question files are not loaded yet."
with:

```markdown
  Each contest on a county page links to a table by precinct, and each ballot question to its
  own; the precinct files are checked to sum to the county files before they are written. The
  board's precinct files for 2010 through 2018 count election-day votes only (early, absentee
  and provisional ballots were counted by county), so those tables say so; from 2020 every way
  of voting is split by precinct. The 2020 primary has no precinct files. No precinct maps.
```

The `make results` paragraph gains: "`results-fetch` and `results-parse` take `--level
county|precinct|questions|all` (default all). The board's precinct and question files (about
250 MB) are archived under `archives/sbe/results-precinct/` and `results-questions/`, recorded in
`archives/manifest.csv`, and gitignored: `skipjack results-fetch` gets them again, and a parse
skips files already written from the same revision, so a clone without them still builds."

The command table gains:

```markdown
| `skipjack get results --election PG24 --geography precinct --county Kent [--precinct 001-001]` | Results by precinct; without `--county`, every county's needs `-o FILE` |
| `skipjack get questions --election PG24 [--geography county\|precinct] [--county] [--precinct]` | Ballot questions, For and Against by way of voting |
```

and the columns paragraph: "Precinct rows add `precinct` (`DDD-PPP`), `congressional` and
`legislative` (blank in 2010). Ballot-question rows have `question`, `how_it_runs`, and `*_votes`
(For) and `*_against` (Against) per way of voting. The precinct files are published gzipped
(`.csv.gz`); `skipjack get` decompresses them."

**Step 5: Full suite and lint**

```bash
uv run pytest -q
uv run ruff check src tests
```

Expected: PASS, no lint errors.

**Step 6: Commit**

```bash
git add README.md src/skipjack/pipeline/results.py tests/test_results_precinct.py tests/test_results_questions.py
git commit -F - <<'EOF'
docs: precinct results and ballot questions in the readme and the data cli

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git add archives/manifest.csv data/results
git diff --cached --stat | tail -3   # only the manifest and data/results; nothing from archives/sbe
git commit -F - <<'EOF'
results: every election's precinct and ballot-question files, parsed and checked

<list each exception added, with the files and figures, or "no exceptions were needed">

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

(Only commit `src/skipjack/pipeline/results.py` in the first commit if exceptions were added;
leave out the test files if unchanged.)
