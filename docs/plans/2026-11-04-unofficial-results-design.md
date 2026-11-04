# Unofficial results for the election being counted (GG26)

## Context

The 2026 general (GG26) was on 2026-11-03. The board publishes a running, unofficial count; the
county CSVs that `pipeline/results.py` reads appear only after certification
(`GG26_01CountyResults.csv` answers the 200-status HTML page today, which the fetch already logs as
"not published"). GG26 stays `published=False` in `RESULTS_ELECTIONS` until then.

This design adds a second, separate track for the count while it runs: snapshots of the board's
unofficial results pages, shown under the GG26 results URLs with an unofficial label and the
state of each county's canvass, and switched off automatically when the certified CSVs are
published. It follows the standing rule in `2026-10-11-unofficial-tallies-design.md`: the
election in progress may show unofficial figures, labelled as such; a certified election shows
only its figures of record.

## What the source looks like (verified 2026-11-04, "Last refreshed: 11/04/2026 09:21:22 AM")

All under `https://elections.maryland.gov/elections/2026/General_results/`, plain server-rendered
HTML (no JS needed), each page carrying `<h1> Unofficial 2026 Gubernatorial General Election
Results for …</h1>` and a `Last refreshed: MM/DD/YYYY HH:MM:SS AM` line. The board says results
update every 10 minutes.

- **24 county pages**, `gen_results_2026_by_county_{1..24}.html` (SBE county order, same as
  `COUNTY_CODES`). One `<h2>` per contest (office, with district where there is one), an `<h3>`
  "Vote for N", a "(X of Y election day precincts reported)" line, then a `table-banded` with
  `Name, Party, Early Voting, Election Day, Mail-In Ballot, Provisional, Total, Percentage` and a
  bold `Totals` row. Every contest the county votes on is there, local offices and questions
  included (56 distinct office headings across the 24 pages).
- **9 statewide pages**, `gen_results_2026_{1..9}.html`: Governor, Comptroller, Attorney
  General, Congress, State Senate, House of Delegates, Circuit Court judges, Supreme Court,
  Appellate Court at large. Statewide totals per candidate with the same columns.
- **Statewide questions**, `gen_qresults_2026_1_00_ALL.html`: For / Against / Totals for
  Questions 1–3, same vote-type columns. County question text is linked from each question as
  `gen_qtext_2026_C_<n>.html` (not fetched).
- **Canvass status**, a table on the home page `https://elections.maryland.gov/`: per county,
  "Last Updated", "# Precincts Reporting", and one cell per canvass (EV, EN, MIB 1, Prov,
  MIB 2), each an image whose `alt` is "Canvass Complete" or "Canvass Partially Complete", or
  empty, which the page's legend calls "Not Reported".

Things the parser has to get right:

- **`NR` is not zero.** "NR: not reported". On the 2026-11-04 snapshot every candidate row has
  Provisional = `NR`, and six counties (Caroline, Cecil, Dorchester, Garrett, Kent, Somerset)
  also have Mail-In Ballot = `NR`. Stored as NULL, shown as "not yet counted", never summed as 0.
- **Ballot-question tables print `0`, not `NR`,** for Provisional (e.g. Allegany Question 1:
  `…, 1,107, 0, 11,636`) even though no provisional ballot has been canvassed. A printed 0 in a
  vote type none of whose county canvasses has reported is treated like `NR`, going by the
  canvass-status table rather than the printed digit. A 0 in a canvass that has reported, even
  partly, is kept, because it can be a real count. Mail-In is fed by MIB 1 and MIB 2.
- **Judges' continuance votes are a third table kind.** The `<h3>` reads "Dan Friedman / Vote
  Yes or No / For continuance in office" (with `<br>`s), there is no Party column, and the rows
  are Yes / No.
- **Headings nest.** The `<h2>` is the office. An `<h3>` carries a district ("District 9A",
  "Circuit 2") or the vote-for line, and an `<h4>` carries the vote-for line under a district
  ("Vote for up to 3 - No more than 1 per county"). On the statewide circuit-court page an
  `<h4>` names a county instead, so each circuit is split by county there and that page has to
  be cross-checked county by county.
- **Named write-ins** print as "Edward Shlikas (Write-In)" with party "Unaffiliated", alongside
  an "Other Write-Ins" row. The statewide questions page prints `NR` for the same column,
  which confirms that the county pages' 0 means "not counted".
- **Question tables have their own layout.** The `<h2>` sits in an `<hgroup>` with the chapter
  and a short title in `<p>` lines and a "View Question Text" link. Each choice's label ("For the
  Constitutional Amendment", "Against …") is a `<th><span>` and the numbers are `<td>`s. There is
  no Party column. There are 120 question tables across the 24 county pages (Questions 1–3 in
  every county plus lettered county questions).
- **One Mail-In column.** The certified CSVs split Mail-In Ballot 1 and 2; these pages do not.
  The unofficial track has its own vote-type list: early, election day, mail-in, provisional.
- **Office names differ from the CSVs** ("Judge of the Circuit Court" vs the CSVs' office
  names), so `STATEWIDE_OFFICES` / `LOCAL_OFFICES` in `web/results.py` do not apply as they stand.
  The unofficial track keeps its own explicit office list, with the same rule: an office it has
  not seen fails the parse.
- **No winners.** The "Denotes Winner" legend is `hidden` and no row carries the check image.
  We show none either, and compute none.
- **Cross-check holds.** Summing the 24 county pages' Governor rows gives exactly the statewide
  page's figures (Moore 1,163,909; Cox 617,114; Ellis 38,315; White 36,597; write-ins 4,751).
  The same holds for Questions 1–3 against the statewide questions page (Question 1: For
  1,303,235, Against 438,394).

## Design

### 1. Pipeline: `src/skipjack/pipeline/results_unofficial.py`

- Registry: `UNOFFICIAL_RESULTS = {"GG26": UnofficialElection(code, base_url, county_pages=24,
  statewide_pages=9, status_url)}`. It refers to `RESULTS_ELECTIONS["GG26"]` for the name and slug.
- `unofficial_fetch(election)`: fetch the 34 results pages (24 county, 9 statewide contest,
  1 statewide questions) plus the home page in one pass. The snapshot is named by the board's own
  **Last refreshed** time (`2026-11-04T092122`), not our fetch time. All 34 results pages must carry the same Last refreshed value; if one differs (the fetch straddled
  a refresh), refetch once, then give up without writing anything. A snapshot whose bytes match
  the previous one (sha256 per page) is skipped. Archive to
  `archives/sbe/results-unofficial/GG26/<snapshot>/<page>.html` with manifest rows, as
  `results_fetch` does.
- `unofficial_parse(election, snapshot=None)`: for each county page, rows of `snapshot, county,
  office, district, candidate, party, write_in, precincts_reported, precincts_total, vote_type,
  votes (NULL for NR)`, with a `kind` of `contest` or `question`. For a question, `candidate` is
  the choice label ("For the Constitutional Amendment"), `party` is empty, and the question's
  short title (the `<p>` under its `<h2>`) is kept as `office_detail`. Checks, all-or-nothing
  per snapshot:
  - every row's printed Total equals the sum of its reported vote types;
  - the printed `Totals` row equals the column sums;
  - for the nine statewide contest pages and the statewide questions page, county sums equal the
    statewide figures;
  - every office heading is in the known list.
  Writes `data/results_unofficial/GG26/<snapshot>/county/<NN>-<slug>.csv` plus
  `_provenance.json` (family `"results-unofficial"`, `checks`), and
  `data/results_unofficial/GG26/<snapshot>/canvass-status.csv` (county, last updated, precincts
  reporting, one column per canvass: `complete` / `partial` / `not_reported`).
- Snapshots stay in git like the turnout snapshots, so the count's progress over the canvass is on
  record. The site renders only the newest.

### 2. Schema and loader

- New tables `results_unofficial` (one row per candidate per county per snapshot, with the
  precincts columns) and `results_unofficial_votes` (`votes` nullable), plus
  `results_unofficial_status`. They are separate from `results_county` so nothing that reads
  certified results (the results hub, maps, downloads, the data page) can pick up an unofficial
  row by accident.
- `build_db.load_results_unofficial` loads every snapshot, requiring sidecars, like
  `load_results`.

### 3. When it publishes

One rule, beside `may_publish`: **unofficial results publish for an election only while
`RESULTS_ELECTIONS[code].published` is False.** When the certified CSVs arrive and GG26 is changed
to `published=True`, the unofficial pages and downloads drop out with no other change, matching
how the turnout pages treat the eight-day tallies. Archives and snapshots stay.

### 4. Web

- **Same URLs as the certified pages will use:** `/results/gg26/`, `/results/gg26/county/…/`,
  `/results/gg26/<contest>/`. The routes take the unofficial tables when the election is
  unpublished, so links people share now keep working after certification.
- Every page opens with a banner: "Unofficial and incomplete. The board's count as of
  <Last refreshed>. Provisional ballots and some mail-in ballots are not yet counted." Below it, a
  compact canvass-status table: 24 counties × 5 canvasses, with the precincts reporting.
- Vote-type cells that are `NR` (or a 0 in a canvass that has not finished) render as "not yet
  counted" in muted text. Totals carry a "so far" label.
- No winner marks. Percentages are shown, labelled as shares of votes counted so far.
- Maps: the contest page keeps the D − R margin map with a caption giving the as-of time.
- `/results/` lists GG26 at the top with an "Unofficial, count in progress" tag.
- **Ballot questions are included.** The hub gets a "Ballot questions" group: Questions 1–3,
  each with its own page (`/results/gg26/question-1/`) showing the For / Against shares
  statewide, a county table, and a For-share map. County questions are listed on each county's
  page and are not given a page of their own. Each question links to the board's question text
  rather than copying it. This is new ground: the certified results tab still leaves questions
  out, so either the certified pipeline gains the `…QuestionResults.csv` files before GG26 is
  certified, or the questions drop off when the certified results replace the unofficial pages.
  The implementation plan takes the first path as a follow-up and notes the second as the fallback.
- Downloads: the newest snapshot's CSVs, under a "GG26 unofficial" heading on the data page,
  gated by the rule in §3.

### 5. Cadence

The canvasses run over several days after the election, and the pages change only when a county
reports. Add `unofficial-fetch` + `unofficial-parse` to `turnout-daily.yml`, guarded so the step
does nothing once GG26 is published. Before the provisional and second mail-in canvass days, run
`make results-unofficial` by hand. A snapshot that has not changed is skipped (§1), so extra runs
cost nothing.

### 6. CLI, Makefile, docs

- `skipjack unofficial-fetch|unofficial-parse [--election GG26]`; `make results-unofficial`.
- README: one line under Results describing the unofficial track and when it disappears.
- `docs/plans/2026-11-04-unofficial-results.md`, the implementation plan, after this design is
  agreed.

## Testing

- Parser fixtures from the saved 2026-11-04 pages: Allegany (questions printing 0 provisional),
  Caroline (mail-in `NR`), Montgomery (the largest page), the statewide questions page.
- `NR` → NULL; the question-table 0 → NULL when the status table says Prov has not reported;
  question rows parse with their choice labels and short titles; totals check; statewide
  cross-checks (the Governor and Question 1 sums above); an unknown office fails; mixed Last
  refreshed values fail.
- Web: GG26 hub renders the banner, the as-of time and "not yet counted" cells, with no ✓;
  no unofficial row leaks into the certified elections' pages.
- Publish gate: with `published=True`, unofficial pages, downloads and static-site URLs are gone
  and the certified pages render.
- Full non-slow suite green.

## Rejected alternatives

- **Load the unofficial count into `results_county`.** It would blur the one guarantee those
  tables carry (certified, cross-checked against the board's CSVs), and NR has no place in them.
- **Wait for certification.** Simplest, but leaves the results tab blank for a month while the
  board's own count is public.
- **Calling races.** Not skipjack's job, and the board isn't marking winners yet either.

## Decisions (with the user, 2026-11-04)

1. Margin maps stay during the count, captioned with the board's last-refreshed time.
2. Ballot questions are included: statewide Questions 1–3 get their own pages, and county
   questions appear on county pages.
3. The newest unofficial snapshot is offered for download, labelled unofficial and gated by §3.
