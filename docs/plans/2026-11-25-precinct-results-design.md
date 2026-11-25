# Precinct results and ballot questions

**Date:** 2026-11-25

## What

Load the State Board's certified results by precinct, and its ballot-question results by county
and by precinct, for every election skipjack covers from 2010 on. Check them against the county
figures before anything is written, and surface them as downloads, database tables, `skipjack
get` output and one precinct page per county per election. No maps.

## Decisions

- **Elections:** all of them, 2010 on. PP20 has no precinct files (the board posted the general
  only); GP26 and GG26 get them once posted, behind the existing `published` flag.
- **Surfaces:** data, CLI and county tables. No precinct boundaries.
- **Ballot questions:** included, at county and precinct level.
- **Storage:** parsed precinct and question CSVs are written and published gzipped
  (`.csv.gz`). The board's raw precinct and question files are archived locally and recorded in
  `archives/manifest.csv` with their sha256, but gitignored: about 250 MB, and anyone can
  re-fetch them. County results stay as they are.

## What the board publishes

Surveyed on 2026-11-25 from Kent County's files for every election, plus statewide file sizes.

**Candidate results by precinct, one file per county and party file:**

| Elections | File name | Precinct columns | Vote types |
|---|---|---|---|
| 2010 | `{Stem}_By_Precinct_{Party}_2010_Primary.csv`, `{Stem}_By_Precinct_2010_General.csv` | `County`, `Election District`, `Election Precinct` (no Cong/Legs) | Election Night only |
| 2012-2018 | the same names | adds `Cong`, `Legs` | Election Night only |
| PG20 | `{Stem}_By_Precinct_2020_General.csv` | `Cong`, `Legs` | all five 2020 types, plus `Total Votes` |
| 2022 on | `{CODE}_{cc}PrecinctsResults.csv`, `{CODE}_{cc}Precincts{Democratic,Republican,NonPartisan}.csv` | `County`, `County Name`, `Election District - Precinct` (`001-001`), `Congressional`, `Legislative` | all five, **no** `Total Votes` |

- Column order differs between eras: the named layout puts `Candidate Name`, `Party` before
  `Office Name`. Columns are matched by name, as the county readers already do.
- Missing non-partisan files come back as the board's HTML page, as with the county files.
- Precinct ids are normalized to `DDD-PPP` in both layouts.

**Ballot questions:**

| Elections | County file | Precinct file |
|---|---|---|
| 2010-2020 | `{Stem}_Questions_County_{year}.csv` | `{Stem}_Questions_Precinct_{year}.csv` |
| 2022 on | `{CODE}_{cc}QuestionResults.csv` | `{CODE}_{cc}QuestionbyPrecinctsResults.csv` |

- County files carry every vote type with For/Against pairs and totals. In the named layout
  the `County` column is `00` for a statewide question and the county code for a local one.
- 2016-2020 add a "3rd option" column per vote type, blank in every row seen so far; the
  reader requires it to stay blank.
- Precinct question files carry Election Night only through 2018, every vote type from 2020.
- The coded county question file names the question `QuestionNumber`; its precinct file
  `Question`.

**Sizes:** the statewide precinct and question files total 231 MB for 2012-2024; 2010 adds
roughly 20 MB. PG24 alone is 117,876 precinct rows.

## Checks

All-or-nothing per election, as for county results; listed exceptions only.

- **Precinct to county:** per county and party file, precinct rows summed by office, district
  and candidate must equal the county file, vote type by vote type. For 2010-2018 only
  election-night votes exist at the precinct level, so only that column is compared. Every
  precinct contest must exist in the county file and the reverse.
- **Question county to statewide:** statewide questions' county totals summed must match the
  board's statewide question breakdown file where one is posted.
- **Question precinct to county:** precinct rows summed per question must equal the county
  question file on the vote types the precinct file carries.
- **Row totals:** where a file prints a total (PG20 precinct, county question files), it must
  equal the sum of the vote types.

## Output

- `data/results/{CODE}/precinct/{cc}-{slug}-{pf}.csv.gz`
- `data/results/{CODE}/questions/county/{cc}-{slug}.csv` (small; plain CSV)
- `data/results/{CODE}/questions/precinct/{cc}-{slug}.csv.gz`
- each with the usual `_provenance.json` sidecar (`geography`, `checks`)

## Downstream

- **Database:** `results_precinct` plus `results_precinct_votes`; `results_questions` with a
  `geography` column and a nullable precinct.
- **Downloads:** `download_files()` accepts `.csv.gz`; the existing `results_published` gate
  covers the new folders.
- **Catalog:** `results[CODE]` gains `precinct_files` and `question_files`; `files` stays
  county-only, so version 1 clients keep working.
- **CLI:** `skipjack get results --geography precinct`, `--precinct DDD-PPP`, and
  `skipjack get questions`. Gzipped files are decompressed on read.
- **Pages:** one page per county per certified election,
  `/results/{election}/county/{county}/precincts/`, about 400 in all rather than about 10,000
  pages per contest. A picker lists the county's contests and questions; the page's script
  reads the picked one from the county's published `.csv.gz` file and draws a row per
  precinct, so the site carries no second copy of the figures. Each contest and question on
  the county page links to `precincts/#<anchor>`.

## Left out

- Precinct maps and boundaries.
- The statewide `All…Precincts` files (the same rows as the county files).
- Precinct reference and polling-place workbooks.
- Council and commissioner district breakdown files.
