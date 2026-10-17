#!/usr/bin/env Rscript
# Build the turnout model's input tables in data/turnout_model/inputs/ from skipjack.db and
# the Census ACS:
#
#   Rscript scripts/turnout_model/01_build_inputs.R          (from the repo root)
#
# Writes training.csv, inactive_ratio.csv, denominators_gg26.csv, covariates.csv, indices.csv
# and polls_2026.csv (header only, hand-maintained), each with a <name>_provenance.json.
#
# Sources: skipjack.db (`make build-db`) tables turnout_counts, voter_registration and
# voter_file_counts; ACS 5-year county tables via tidycensus, archived raw to
# archives/census/md_county_acs5_<vintage>.csv. The ACS archive is pulled once (newest vintage
# the API serves, 2024 then 2023) and reused on later runs so the build is deterministic and
# offline; set REFRESH_ACS=1 to pull it again. Needs CENSUS_API_KEY in ~/.Renviron for a pull.
#
# Every check stops the script when it fails; the names of the checks that passed go in the
# provenance files.

if (!file.exists("scripts/turnout_model/renv/activate.R")) {
  stop("run from the repo root: Rscript scripts/turnout_model/01_build_inputs.R")
}
Sys.setenv(RENV_PROJECT = normalizePath("scripts/turnout_model"))
source("scripts/turnout_model/renv/activate.R")
source("scripts/turnout_model/R/helpers.R")

ELECTION_DAY_GG26 <- as.Date("2026-11-03")

# Statewide ballots cast (party ALL, sum of the four modes) printed by the board.
STATEWIDE_CAST <- c(GG18 = 2335128, GG22 = 2031635, PG24 = 3045855, GP22 = 1033903, PP24 = 1016857)

# Cells where the board's own official-turnout spreadsheet prints an ALL row that differs from
# the sum of its party rows (checked against the archived xlsx: e.g. PP24 Carroll vote-by-mail
# prints ALL 10,311 against DEM 5,696 + REP 4,616 + others 0). The party-sum check accepts
# exactly these cells and fails on any other mismatch, or if one of these stops differing.
KNOWN_PARTY_SUM_DISCREPANCIES <- tibble::tribble(
  ~election, ~county, ~measure, ~all_value, ~party_sum,
  "GP22", "Montgomery", "eligible", 670472, 670467,
  "PP24", "Carroll", "vote-by-mail", 10311, 10312,
  "PP24", "Montgomery", "vote-by-mail", 80536, 80537,
  "PP24", "Prince George's", "vote-by-mail", 54004, 54006,
  "PP24", "Washington", "vote-by-mail", 8362, 8363
)

con <- db_connect()
on.exit(DBI::dbDisconnect(con), add = TRUE)

# Latest as_of for an election x family at county geography; stop if there is none.
latest_as_of <- function(election, family) {
  r <- db_query(con, "SELECT MAX(as_of) AS as_of FROM turnout_counts
                      WHERE election = ? AND family = ? AND geography = 'county'",
                params = list(election, family))
  if (is.na(r$as_of)) stop("no ", family, " county rows for ", election)
  r$as_of
}

county_rows <- function(election, family, as_of, category = "ALL") {
  db_query(con, "SELECT source_id, as_of, election, county, party, measure, value
                 FROM turnout_counts
                 WHERE election = ? AND family = ? AND geography = 'county'
                   AND category = ? AND as_of = ?",
           params = list(election, family, category, as_of))
}

registration <- function(report_date) {
  r <- db_query(con, "SELECT source_id, report_date, county, party, active_voters
                      FROM voter_registration WHERE report_date = ?",
                params = list(report_date))
  if (!nrow(r)) stop("no voter_registration rows for ", report_date)
  check_counties(r$county, paste("voter_registration", report_date))
  r
}

registration_grouped <- function(report_date) {
  registration(report_date) |>
    mutate(party_group = party_group(party)) |>
    group_by(county, party_group) |>
    summarise(active = sum(active_voters), .groups = "drop")
}

# Split integer totals into integer parts proportional to `shares` (largest remainder), so
# the parts sum exactly to the total. Ties break by position (DEM, REP, OTH order).
split_integer <- function(total, shares) {
  raw <- as.numeric(total) * as.numeric(shares) / sum(as.numeric(shares))
  base <- floor(raw)
  left <- total - sum(base)
  if (left > 0) {
    bump <- order(-(raw - base), seq_along(raw))[seq_len(left)]
    base[bump] <- base[bump] + 1
  }
  base
}

expand_grid_cp <- function() tidyr::expand_grid(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS)

# ---------------------------------------------------------------------------------------------
# a. training.csv
# ---------------------------------------------------------------------------------------------

certified <- tibble::tribble(
  ~election, ~year, ~election_type,
  "GG18", 2018L, "midterm_general",
  "GG22", 2022L, "midterm_general",
  "GP22", 2022L, "primary",
  "PP24", 2024L, "primary",
  "PG24", 2024L, "presidential_general"
)

training_checks <- character()
training_ids <- character()
training_asof <- list()
mode_cols <- c("election-day" = "election_day", "early-voting" = "early_voting",
               "vote-by-mail" = "vote_by_mail", "provisional" = "provisional")

ot_all <- list()
for (el in certified$election) {
  as_of <- latest_as_of(el, "official-turnout")
  training_asof[[el]] <- as_of
  rows <- county_rows(el, "official-turnout", as_of)
  training_ids <- c(training_ids, rows$source_id)
  check_counties(rows$county, paste(el, "official-turnout"))
  training_checks <- c(training_checks, check(
    setequal(unique(rows$measure), c(names(mode_cols), "eligible")),
    paste(el, "official-turnout has the four modes and eligible")))
  training_checks <- c(training_checks, check(!anyNA(rows$value), paste(el, "no missing values")))
  ot_all[[el]] <- rows
}
ot_all <- bind_rows(ot_all)

# Party groups sum to the ALL row per county and measure, apart from the board's known cells.
party_sums <- ot_all |>
  group_by(election, county, measure) |>
  summarise(all_value = sum(value[party == "ALL"]), party_sum = sum(value[party != "ALL"]),
            n_all = sum(party == "ALL"), .groups = "drop")
training_checks <- c(training_checks, check(all(party_sums$n_all == 1),
                                            "one ALL row per election x county x measure"))
mismatch <- party_sums |> filter(all_value != party_sum) |> select(-n_all) |>
  arrange(election, county, measure)
known <- KNOWN_PARTY_SUM_DISCREPANCIES |> arrange(election, county, measure)
training_checks <- c(training_checks, check(
  identical(do.call(paste, mismatch), do.call(paste, known)),
  "party groups sum to the ALL row per county and measure, except the board's 5 printed discrepancies",
  paste(capture.output(print(as.data.frame(mismatch))), collapse = "\n")))

# Counties sum (ALL rows, four modes) to the statewide ballots cast.
statewide_all <- ot_all |> filter(party == "ALL", measure != "eligible") |>
  group_by(election) |> summarise(cast = sum(value), .groups = "drop")
for (el in names(STATEWIDE_CAST)) {
  training_checks <- c(training_checks, check(
    statewide_all$cast[statewide_all$election == el] == STATEWIDE_CAST[[el]],
    sprintf("%s counties sum to statewide ballots cast %s", el, format(STATEWIDE_CAST[[el]], big.mark = ","))))
}

training_certified <- ot_all |>
  filter(party != "ALL") |>
  mutate(party_group = party_group(party),
         measure = ifelse(measure == "eligible", "active", mode_cols[measure])) |>
  group_by(election, county, party_group, measure) |>
  summarise(value = sum(value), .groups = "drop") |>
  pivot_wider(names_from = measure, values_from = value) |>
  mutate(ballots_cast = election_day + early_voting + vote_by_mail + provisional,
         party_split_estimated = FALSE)

# Party-row ballots differ from the ALL statewide figure only by the known discrepancies.
known_cast_diff <- KNOWN_PARTY_SUM_DISCREPANCIES |> filter(measure != "eligible") |>
  group_by(election) |> summarise(d = sum(party_sum - all_value), .groups = "drop")
for (el in names(STATEWIDE_CAST)) {
  d <- sum(known_cast_diff$d[known_cast_diff$election == el])
  training_checks <- c(training_checks, check(
    sum(training_certified$ballots_cast[training_certified$election == el]) == STATEWIDE_CAST[[el]] + d,
    sprintf("%s party-group ballots sum to the statewide figure%s", el,
            if (d != 0) sprintf(" plus the board's printed discrepancy of %+d", d) else "")))
}

# GP26: county canvass totals split by the 2026-08-12 voter file's 2026-GP participation shares.
gp26_as_of <- latest_as_of("GP26", "county-canvass")
gp26_canvass <- county_rows("GP26", "county-canvass", gp26_as_of)
check_counties(gp26_canvass$county, "GP26 county-canvass")
gp26_wide <- gp26_canvass |> filter(party == "ALL") |>
  select(county, measure, value) |> pivot_wider(names_from = measure, values_from = value)
training_checks <- c(training_checks,
  check(nrow(gp26_wide) == 24 && !anyNA(gp26_wide), "GP26 canvass has 24 complete county rows"),
  check(with(gp26_wide, `election-day` + `early-voting` + `vote-by-mail` + provisional == total),
        "GP26 canvass modes sum to the county total"))

vf_gp26 <- db_query(con, "SELECT county, party_group, count FROM voter_file_counts
                          WHERE snapshot_date = '2026-08-12' AND metric = 'turnout_by_election'
                            AND election = '2026-GP' AND county != 'Maryland'")
check_counties(vf_gp26$county, "voter file 2026-GP turnout_by_election")
# OTH = ALL - DEM - REP: the minor-party and UNA cells are often suppressed (<10), the
# ALL/DEM/REP cells never are, so the difference keeps every voter without zero-filling.
vf_shares <- vf_gp26 |> filter(party_group %in% c("ALL", "DEM", "REP")) |>
  pivot_wider(names_from = party_group, values_from = count)
training_checks <- c(training_checks, check(
  nrow(vf_shares) == 24 && !anyNA(vf_shares[c("ALL", "DEM", "REP")]),
  "voter file 2026-GP ALL, DEM and REP counts present for all 24 counties"))
vf_shares <- vf_shares |>
  mutate(OTH = ALL - DEM - REP) |>
  select(county, DEM, REP, OTH) |>
  pivot_longer(c(DEM, REP, OTH), names_to = "party_group", values_to = "vf_voted")
training_checks <- c(training_checks, check(all(vf_shares$vf_voted >= 0),
                                            "voter file 2026-GP OTH (ALL - DEM - REP) is non-negative"))

gp26_active <- registration_grouped("2026-05")
gp26 <- vf_shares |>
  left_join(gp26_wide |> select(county, total), by = "county") |>
  mutate(party_group = factor(party_group, PARTY_GROUPS)) |>
  arrange(county, party_group) |>
  group_by(county) |>
  mutate(ballots_cast = split_integer(first(total), vf_voted)) |>
  ungroup() |>
  mutate(party_group = as.character(party_group), election = "GP26",
         election_day = NA_real_, early_voting = NA_real_, vote_by_mail = NA_real_,
         provisional = NA_real_, party_split_estimated = TRUE) |>
  left_join(gp26_active, by = c("county", "party_group"))
training_checks <- c(training_checks,
  check(gp26 |> group_by(county) |> summarise(ok = sum(ballots_cast) == first(total)) |> pull(ok),
        "GP26 party-group ballots sum to the county canvass total"),
  check(sum(gp26$ballots_cast) == sum(gp26_wide$total),
        sprintf("GP26 counties sum to the canvass statewide total %s", format(sum(gp26_wide$total), big.mark = ","))),
  check(!anyNA(gp26$active), "GP26 active registration (2026-05) present for every row"))

training <- bind_rows(training_certified, gp26 |> select(-total, -vf_voted)) |>
  left_join(bind_rows(certified, tibble(election = "GP26", year = 2026L, election_type = "primary")),
            by = "election") |>
  mutate(closed_primary_party = election_type == "primary" & party_group == "OTH",
         party_group = factor(party_group, PARTY_GROUPS),
         election = factor(election, c("GG18", "GG22", "GP22", "PP24", "PG24", "GP26"))) |>
  arrange(election, county, party_group) |>
  mutate(party_group = as.character(party_group), election = as.character(election)) |>
  select(election, year, election_type, county, party_group, ballots_cast, election_day,
         early_voting, vote_by_mail, provisional, active, party_split_estimated, closed_primary_party)

training_checks <- c(training_checks,
  check(all(training |> count(election) |> pull(n) == 72) && n_distinct(training$election) == 6,
        "every election has 24 counties x 3 party groups"),
  check(!any(duplicated(training[c("election", "county", "party_group")])), "no duplicate rows"),
  check(all(training$ballots_cast <= training$active | training$closed_primary_party),
        "ballots cast do not exceed active registration outside closed-primary OTH rows"))

write_input_csv(training, file.path(INPUTS_DIR, "training.csv"))
write_provenance(
  file.path(INPUTS_DIR, "training.csv"),
  "Ballots cast and active registration by election x county x party group (DEM, REP, OTH = all other parties and unaffiliated).",
  sources = list(
    certified = sprintf("turnout_counts: family 'official-turnout', geography 'county', category 'ALL', latest as_of per election (%s); ballots_cast = election-day + early-voting + vote-by-mail + provisional; active = the 'eligible' measure",
                        paste(sprintf("%s %s", names(training_asof), unlist(training_asof)), collapse = ", ")),
    gp26_totals = sprintf("turnout_counts: family 'county-canvass', election GP26, geography 'county', party ALL, measure total, as_of %s", gp26_as_of),
    gp26_party_split = "voter_file_counts: snapshot 2026-08-12, metric turnout_by_election, election 2026-GP, county x party_group count; OTH = ALL - DEM - REP",
    gp26_active = "voter_registration: report_date 2026-05, active_voters grouped to DEM/REP/OTH"
  ),
  source_ids = c(training_ids, gp26_canvass$source_id, registration("2026-05")$source_id),
  checks = training_checks,
  notes = c(
    "GP26 party-group ballots are an estimate (party_split_estimated = TRUE): each county's canvass total is split by the shares of 2026-GP voters by party group among voters still on the 2026-08-12 voter file, rounded by largest remainder so the parts sum to the county total. GP26 mode columns are blank because the split is not available by mode.",
    "closed_primary_party marks OTH rows in primaries: unaffiliated voters cannot vote in Maryland's partisan primaries, so their ballots (nonpartisan contests only) are tiny.",
    "The board's own official-turnout sheets print five cells whose ALL row differs from the sum of the party rows (GP22 Montgomery eligible -5; PP24 vote-by-mail in Carroll +1, Montgomery +1, Prince George's +2, Washington +1). Party-group rows are summed from the party rows, so PP24 party-group ballots total 1,016,862 against the printed 1,016,857, and GP22 Montgomery active sums to 670,467 against 670,472."
  ),
  extra = list(known_party_sum_discrepancies = KNOWN_PARTY_SUM_DISCREPANCIES)
)

# ---------------------------------------------------------------------------------------------
# b. inactive_ratio.csv
# ---------------------------------------------------------------------------------------------

inactive_checks <- character()
inactive_ids <- character()
inactive_parts <- list()
for (el in c("GG22", "PG24")) {
  as_of_i <- latest_as_of(el, "eligible-inactive")
  as_of_a <- latest_as_of(el, "eligible-active")
  ina <- county_rows(el, "eligible-inactive", as_of_i) |> filter(measure == "inactive")
  act <- county_rows(el, "eligible-active", as_of_a) |> filter(measure == "active")
  inactive_ids <- c(inactive_ids, ina$source_id, act$source_id)
  check_counties(ina$county, paste(el, "eligible-inactive"))
  check_counties(act$county, paste(el, "eligible-active"))
  for (d in list(list(ina, "inactive"), list(act, "active"))) {
    s <- d[[1]] |> group_by(county) |>
      summarise(ok = sum(value[party == "ALL"]) == sum(value[party != "ALL"]) & sum(party == "ALL") == 1)
    inactive_checks <- c(inactive_checks, check(s$ok, sprintf("%s %s party rows sum to the ALL row per county", el, d[[2]])))
  }
  ot_elig <- ot_all |> filter(election == el, party == "ALL", measure == "eligible")
  inactive_checks <- c(inactive_checks, check(
    sum(act$value[act$party == "ALL"]) == sum(ot_elig$value),
    sprintf("%s eligible-active statewide equals official-turnout eligible", el)))
  tag <- tolower(el)
  inactive_parts[[el]] <- full_join(
    ina |> filter(party != "ALL") |> mutate(party_group = party_group(party)) |>
      group_by(county, party_group) |> summarise(inactive = sum(value), .groups = "drop"),
    act |> filter(party != "ALL") |> mutate(party_group = party_group(party)) |>
      group_by(county, party_group) |> summarise(active = sum(value), .groups = "drop"),
    by = c("county", "party_group")) |>
    mutate(ratio = inactive / active) |>
    rename_with(~ paste0(.x, "_", tag), c(inactive, active, ratio))
  inactive_parts[[paste0(el, "_asof")]] <- c(inactive = as_of_i, active = as_of_a)
}
inactive_ratio <- expand_grid_cp() |>
  left_join(inactive_parts$GG22, by = c("county", "party_group")) |>
  left_join(inactive_parts$PG24, by = c("county", "party_group")) |>
  transmute(county, party_group,
            active_gg22, inactive_gg22, inactive_ratio_gg22 = round(ratio_gg22, 6),
            active_pg24, inactive_pg24, inactive_ratio_pg24 = round(ratio_pg24, 6),
            inactive_ratio_mean = round((ratio_gg22 + ratio_pg24) / 2, 6))
inactive_checks <- c(inactive_checks,
  check(nrow(inactive_ratio) == 72 && !anyNA(inactive_ratio), "24 counties x 3 party groups, no missing values"),
  check(all(inactive_ratio$inactive_ratio_mean > 0 & inactive_ratio$inactive_ratio_mean < 1),
        "inactive ratios between 0 and 1"))
write_input_csv(inactive_ratio, file.path(INPUTS_DIR, "inactive_ratio.csv"))
write_provenance(
  file.path(INPUTS_DIR, "inactive_ratio.csv"),
  "Inactive / active registration by county x party group for GG22 and PG24, and their mean.",
  sources = list(
    inactive = sprintf("turnout_counts: family 'eligible-inactive', geography 'county', category 'ALL', measure inactive; GG22 as_of %s, PG24 as_of %s",
                       inactive_parts$GG22_asof[["inactive"]], inactive_parts$PG24_asof[["inactive"]]),
    active = sprintf("turnout_counts: family 'eligible-active', geography 'county', category 'ALL', measure active; GG22 as_of %s, PG24 as_of %s",
                     inactive_parts$GG22_asof[["active"]], inactive_parts$PG24_asof[["active"]])
  ),
  source_ids = inactive_ids,
  checks = inactive_checks,
  notes = "inactive_ratio_mean is the unweighted mean of the GG22 and PG24 ratios."
)

# ---------------------------------------------------------------------------------------------
# d. Census ACS 5-year covariates (built before c, which carries the CVAP column)
# ---------------------------------------------------------------------------------------------

acs_vars <- c(
  "B29001_001", "B01002_001", "B01001_001",
  sprintf("B01001_%03d", c(20:25, 44:49)),
  "B15003_001", sprintf("B15003_%03d", 22:25),
  "B19013_001", "B25003_001", "B25003_002", "B07003_001", "B07003_004"
)
acs_path <- function(v) sprintf("archives/census/md_county_acs5_%s.csv", v)
acs_vintage <- NULL
if (Sys.getenv("REFRESH_ACS") != "1") {
  for (v in c(2024, 2023)) if (file.exists(acs_path(v))) { acs_vintage <- v; break }
}
if (is.null(acs_vintage)) {
  suppressPackageStartupMessages(library(tidycensus))
  if (!nzchar(Sys.getenv("CENSUS_API_KEY"))) stop("CENSUS_API_KEY is not set (put it in ~/.Renviron)")
  for (v in c(2024, 2023)) {
    raw <- tryCatch(
      suppressMessages(get_acs(geography = "county", state = "MD", variables = acs_vars,
                               year = v, survey = "acs5", cache_table = FALSE)),
      error = function(e) { message("ACS 5-year ", v, " not available: ", conditionMessage(e)); NULL })
    if (!is.null(raw) && nrow(raw) > 0) { acs_vintage <- v; break }
  }
  if (is.null(acs_vintage)) stop("neither the 2024 nor the 2023 ACS 5-year county tables could be pulled")
  raw <- raw |> select(GEOID, NAME, variable, estimate, moe) |> arrange(GEOID, variable)
  dir.create("archives/census", showWarnings = FALSE, recursive = TRUE)
  write_csv(raw, acs_path(acs_vintage), na = "")
  writeLines(toJSON(list(
    file = acs_path(acs_vintage),
    api = "U.S. Census Bureau API (api.census.gov), American Community Survey 5-year detailed tables, via tidycensus::get_acs(geography = 'county', state = 'MD', survey = 'acs5')",
    vintage = acs_vintage,
    period = sprintf("%d-%d", acs_vintage - 4, acs_vintage),
    tables = c("B29001", "B01002", "B01001", "B15003", "B19013", "B25003", "B07003"),
    variables = acs_vars,
    pull_date = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
    generated_by = "scripts/turnout_model/01_build_inputs.R",
    rows = nrow(raw)
  ), auto_unbox = TRUE, pretty = TRUE), sub("\\.csv$", "_provenance.json", acs_path(acs_vintage)))
}
acs <- read_csv(acs_path(acs_vintage), col_types = cols(GEOID = "c", NAME = "c", variable = "c",
                                                       estimate = "d", moe = "d"))
cov_checks <- c(
  check(setequal(unique(acs$variable), acs_vars), "ACS archive has every requested variable"),
  check(n_distinct(acs$GEOID) == 24 && nrow(acs) == 24 * length(acs_vars), "ACS archive has 24 counties x every variable"),
  check(!anyNA(acs$estimate), "no missing ACS estimates")
)
acs <- acs |> mutate(county = normalize_census_county(NAME))
check_counties(acs$county, "ACS")
cov_checks <- c(cov_checks, "every Census county name maps to one of the 24 canonical names")
w <- acs |> select(county, variable, estimate) |> pivot_wider(names_from = variable, values_from = estimate)
e <- function(...) rowSums(w[, c(...), drop = FALSE])
covariates <- tibble(
  county = w$county,
  cvap = w$B29001_001,
  median_age = w$B01002_001,
  share_65plus = e(sprintf("B01001_%03d", c(20:25, 44:49))) / w$B01001_001,
  share_ba_plus = e(sprintf("B15003_%03d", 22:25)) / w$B15003_001,
  median_hh_income = w$B19013_001,
  share_owner_occupied = w$B25003_002 / w$B25003_001,
  share_moved_past_year = 1 - w$B07003_004 / w$B07003_001
)
zcols <- c("median_age", "share_65plus", "share_ba_plus", "median_hh_income",
           "share_owner_occupied", "share_moved_past_year")
for (z in zcols) covariates[[paste0("z_", z)]] <- as.numeric(scale(covariates[[z]]))
covariates <- covariates |>
  mutate(across(c(starts_with("share_"), starts_with("z_")), ~ round(.x, 6))) |>
  arrange(match(county, CANONICAL_COUNTIES))
cov_checks <- c(cov_checks,
  check(nrow(covariates) == 24 && !anyNA(covariates), "24 counties, no missing covariates"),
  check(all(covariates |> select(starts_with("share_")) |> as.matrix() |> (\(m) m > 0 & m < 1)()),
        "shares strictly between 0 and 1"))
write_input_csv(covariates, file.path(INPUTS_DIR, "covariates.csv"))
write_provenance(
  file.path(INPUTS_DIR, "covariates.csv"),
  "County covariates from the ACS 5-year estimates, with z-scores across the 24 counties (unweighted, sample sd).",
  sources = list(
    acs_archive = acs_path(acs_vintage),
    vintage = sprintf("ACS 5-year %d-%d", acs_vintage - 4, acs_vintage),
    cvap = "B29001_001 (citizens 18 and over)",
    median_age = "B01002_001",
    share_65plus = "B01001_020..025 + B01001_044..049 over B01001_001",
    share_ba_plus = "B15003_022..025 (bachelor's, master's, professional, doctorate) over B15003_001 (population 25 and over)",
    median_hh_income = sprintf("B19013_001 (%d inflation-adjusted dollars)", acs_vintage),
    share_owner_occupied = "B25003_002 over B25003_001 (occupied housing units)",
    share_moved_past_year = "1 - B07003_004 (same house 1 year ago) over B07003_001 (population 1 year and over)"
  ),
  checks = cov_checks
)

# ---------------------------------------------------------------------------------------------
# c. denominators_gg26.csv
# ---------------------------------------------------------------------------------------------

den_checks <- character()
elig_ratio <- function(el, report_date) {
  ot <- ot_all |> filter(election == el, party != "ALL", measure == "eligible") |>
    mutate(party_group = party_group(party)) |>
    group_by(county, party_group) |> summarise(eligible = sum(value), .groups = "drop")
  vr <- registration_grouped(report_date)
  full_join(ot, vr, by = c("county", "party_group"))
}
r18 <- elig_ratio("GG18", "2018-09")
r22 <- elig_ratio("GG22", "2022-09")
state_r18 <- sum(r18$eligible) / sum(r18$active)
state_r22 <- sum(r22$eligible) / sum(r22$active)
den_checks <- c(den_checks,
  check(abs(state_r18 - 3954027 / 3975309) < 1e-12, sprintf("statewide GG18 eligible / 2018-09 active = %.4f", state_r18)),
  check(abs(state_r22 - 4124156 / 4161637) < 1e-12, sprintf("statewide GG22 eligible / 2022-09 active = %.4f", state_r22)))
active_2609 <- registration_grouped("2026-09")
denominators <- expand_grid_cp() |>
  left_join(active_2609 |> rename(active_2026_09 = active), by = c("county", "party_group")) |>
  left_join(r18 |> transmute(county, party_group, eligible_ratio_2018 = eligible / active), by = c("county", "party_group")) |>
  left_join(r22 |> transmute(county, party_group, eligible_ratio_2022 = eligible / active), by = c("county", "party_group")) |>
  left_join(inactive_ratio |> select(county, party_group, inactive_ratio_mean), by = c("county", "party_group")) |>
  left_join(covariates |> select(county, cvap), by = "county") |>
  mutate(eligible_ratio_mean = (eligible_ratio_2018 + eligible_ratio_2022) / 2,
         active = round(active_2026_09 * eligible_ratio_mean),
         eligible = round(active * (1 + inactive_ratio_mean)),
         cvap_note = "county-level CVAP (ACS B29001_001), repeated on each party row; not a party-level figure") |>
  mutate(across(c(eligible_ratio_2018, eligible_ratio_2022, eligible_ratio_mean), ~ round(.x, 6))) |>
  select(county, party_group, active_2026_09, eligible_ratio_2018, eligible_ratio_2022,
         eligible_ratio_mean, active, inactive_ratio_mean, eligible, cvap, cvap_note)
den_checks <- c(den_checks,
  check(nrow(denominators) == 72 && !anyNA(denominators), "24 counties x 3 party groups, no missing values"),
  check(all(denominators$eligible_ratio_mean > 0.5 & denominators$eligible_ratio_mean < 1.5),
        "county x party eligible / September active ratios between 0.5 and 1.5"),
  check(sum(denominators$active_2026_09) == 4333854, "2026-09 active registration sums to 4,333,854"))
write_input_csv(denominators, file.path(INPUTS_DIR, "denominators_gg26.csv"))
write_provenance(
  file.path(INPUTS_DIR, "denominators_gg26.csv"),
  "Projected GG26 active registration (the election-day 'eligible' measure) and active + inactive by county x party group, with county CVAP.",
  sources = list(
    active_2026_09 = "voter_registration: report_date 2026-09, active_voters grouped to DEM/REP/OTH",
    eligible_ratio_2018 = sprintf("turnout_counts official-turnout GG18 eligible (as_of %s) / voter_registration 2018-09 active, per county x party group", training_asof$GG18),
    eligible_ratio_2022 = sprintf("turnout_counts official-turnout GG22 eligible (as_of %s) / voter_registration 2022-09 active, per county x party group", training_asof$GG22),
    active = "round(active_2026_09 x eligible_ratio_mean), eligible_ratio_mean = unweighted mean of the 2018 and 2022 ratios",
    eligible = "round(active x (1 + inactive_ratio_mean)), inactive_ratio_mean from inactive_ratio.csv",
    cvap = sprintf("covariates.csv (ACS 5-year %d, B29001_001)", acs_vintage)
  ),
  source_ids = c(registration("2026-09")$source_id, registration("2018-09")$source_id,
                 registration("2022-09")$source_id,
                 ot_all$source_id[ot_all$election %in% c("GG18", "GG22")], inactive_ids),
  checks = den_checks,
  notes = "CVAP is county-level only; it is repeated on each party row and must not be summed across party rows."
)

# ---------------------------------------------------------------------------------------------
# e. indices.csv
# ---------------------------------------------------------------------------------------------

idx_checks <- character()
gg26_as_of <- latest_as_of("GG26", "sent-returned")
gg22_sr <- county_rows("GG22", "sent-returned", "2022-11-28")
idx_checks <- c(idx_checks, check(nrow(gg22_sr) > 0, "GG22 sent-returned final snapshot (as_of 2022-11-28) present"),
                check(latest_as_of("GG22", "sent-returned") == "2022-11-28", "2022-11-28 is the latest GG22 sent-returned snapshot"))
gg26_sr <- county_rows("GG26", "sent-returned", gg26_as_of)
mail <- function(rows, tag) {
  r <- rows |> filter(measure %in% c("sent", "received"))
  check_counties(r$county, paste(tag, "sent-returned"))
  s <- r |> group_by(county, measure) |>
    summarise(ok = sum(party == "ALL") == 1 & sum(value[party == "ALL"]) == sum(value[party != "ALL"]), .groups = "drop")
  idx_checks <<- c(idx_checks, check(s$ok, paste(tag, "sent-returned DEM + REP + OTH sum to ALL per county")))
  idx_checks <<- c(idx_checks, check(setequal(unique(r$party), c("DEM", "REP", "OTH", "ALL")),
                                     paste(tag, "sent-returned parties are DEM, REP, OTH, ALL")))
  r |> select(county, party_group = party, measure, value) |>
    pivot_wider(names_from = measure, values_from = value, names_prefix = paste0(tag, "_"))
}
mail_tab <- full_join(mail(gg26_sr, "gg26"), mail(gg22_sr, "gg22"), by = c("county", "party_group"))

gp22_county <- ot_all |> filter(election == "GP22", party == "ALL", measure != "eligible") |>
  group_by(county) |> summarise(county_gp22_cast = sum(value), .groups = "drop")
gp26_county <- gp26_wide |> transmute(county, county_gp26_cast = total)

reg_tab <- function(a, b, name) {
  x <- bind_rows(
    registration_grouped(a),
    registration_grouped(a) |> group_by(county) |> summarise(active = sum(active), party_group = "ALL")
  ) |> rename(!!paste0("active_", gsub("-", "_", a)) := active)
  y <- bind_rows(
    registration_grouped(b),
    registration_grouped(b) |> group_by(county) |> summarise(active = sum(active), party_group = "ALL")
  ) |> rename(!!paste0("active_", gsub("-", "_", b)) := active)
  full_join(x, y, by = c("county", "party_group"))
}
reg <- reg_tab("2026-09", "2025-09") |>
  full_join(reg_tab("2022-09", "2021-09"), by = c("county", "party_group")) |>
  full_join(reg_tab("2018-09", "2017-09"), by = c("county", "party_group"))

index_counts <- mail_tab |>
  full_join(reg, by = c("county", "party_group")) |>
  left_join(gp26_county, by = "county") |>
  left_join(gp22_county, by = "county")
statewide <- index_counts |> select(-county) |> group_by(party_group) |>
  summarise(across(everything(), sum), .groups = "drop") |> mutate(county = "Statewide")
pg_levels <- c(PARTY_GROUPS, "ALL")
indices <- bind_rows(index_counts, statewide) |>
  mutate(gg26_as_of = gg26_as_of,
         days_to_election = as.integer(ELECTION_DAY_GG26 - as.Date(gg26_as_of)),
         mail_sent_index = round(gg26_sent / gg22_sent, 6),
         mail_received_index = round(gg26_received / gg22_received, 6),
         primary_index = round(county_gp26_cast / county_gp22_cast, 6),
         reg_momentum = round(active_2026_09 / active_2025_09, 6),
         reg_momentum_2022 = round(active_2022_09 / active_2021_09, 6),
         reg_momentum_2018 = round(active_2018_09 / active_2017_09, 6)) |>
  arrange(match(county, c(CANONICAL_COUNTIES, "Statewide")), match(party_group, pg_levels)) |>
  select(county, party_group, gg26_as_of, days_to_election,
         gg26_sent, gg22_sent, mail_sent_index, gg26_received, gg22_received, mail_received_index,
         county_gp26_cast, county_gp22_cast, primary_index,
         active_2026_09, active_2025_09, reg_momentum,
         active_2022_09, active_2021_09, reg_momentum_2022,
         active_2018_09, active_2017_09, reg_momentum_2018)
idx_checks <- c(idx_checks,
  check(nrow(indices) == 25 * 4 && !anyNA(indices), "24 counties + Statewide x DEM/REP/OTH/ALL, no missing values"),
  check(all(is.finite(as.matrix(indices |> select(ends_with("index"), starts_with("reg_momentum"))))), "every index finite"),
  check(sum(gp26_county$county_gp26_cast) == sum(gp26_wide$total) && sum(gp22_county$county_gp22_cast) == STATEWIDE_CAST[["GP22"]],
        "primary_index county totals reconcile to the GP26 canvass and GP22 statewide cast"))
write_input_csv(indices, file.path(INPUTS_DIR, "indices.csv"))
write_provenance(
  file.path(INPUTS_DIR, "indices.csv"),
  "Observable 2026 environment indices by county x party group (DEM, REP, OTH, and ALL for the county total), with a Statewide row per party group.",
  sources = list(
    mail = sprintf("turnout_counts: family 'sent-returned', geography 'county', category 'ALL', measures sent and received; GG26 as_of %s (latest) over GG22 as_of 2022-11-28 (post-election final)", gg26_as_of),
    primary_index = sprintf("GP26 county-canvass total (as_of %s) over GP22 official-turnout ALL ballots cast (four modes, as_of %s); county level, repeated across party rows",
                            gp26_as_of, training_asof$GP22),
    reg_momentum = "voter_registration active_voters grouped to DEM/REP/OTH: 2026-09 / 2025-09; reg_momentum_2022 = 2022-09 / 2021-09; reg_momentum_2018 = 2018-09 / 2017-09"
  ),
  source_ids = c(gg26_sr$source_id, gg22_sr$source_id, gp26_canvass$source_id,
                 ot_all$source_id[ot_all$election == "GP22"],
                 unlist(lapply(c("2026-09", "2025-09", "2022-09", "2021-09", "2018-09", "2017-09"),
                               function(d) registration(d)$source_id))),
  checks = idx_checks,
  notes = c(
    sprintf("The GG26 mail figures are a running count %d days before the election; the GG22 denominators are the post-election final, so the mail indices rise as GG26 approaches.", as.integer(ELECTION_DAY_GG26 - as.Date(gg26_as_of))),
    "primary_index, county_gp26_cast and county_gp22_cast are county totals repeated on each party row.",
    "Statewide rows are sums of the county counts, with indices computed from those sums."
  )
)

# ---------------------------------------------------------------------------------------------
# f. polls_2026.csv (header only; filled by hand)
# ---------------------------------------------------------------------------------------------

polls <- tibble(pollster = character(), field_start = character(), field_end = character(),
                population = character(), sample_size = integer(), dem_share_lv = double(),
                rep_share_lv = double(), una_share_lv = double(), governor_dem = double(),
                governor_rep = double(), source_url = character(), notes = character())
polls_path <- file.path(INPUTS_DIR, "polls_2026.csv")
if (!file.exists(polls_path)) {
  write_input_csv(polls, polls_path)
  write_provenance(
    polls_path,
    "Public 2026 Maryland general election polls, used only as a cross-check on the forecast.",
    sources = list(polls = "hand-maintained; rows are added by hand from published poll releases, one row per poll, with source_url"),
    checks = character(),
    notes = "Created header-only by 01_build_inputs.R; the script never overwrites an existing file, so hand-entered rows are kept."
  )
} else {
  existing <- read_csv(polls_path, col_types = cols(.default = "c"))
  check(identical(names(existing), names(polls)), "polls_2026.csv keeps the expected header")
}

# ---------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------

options(width = 200, pillar.width = 200)
cat("\nTraining, statewide by election:\n")
print(as.data.frame(training |>
  group_by(election) |>
  summarise(ballots_cast = sum(ballots_cast), active = sum(active), .groups = "drop") |>
  mutate(rate = round(ballots_cast / active, 4)) |>
  arrange(match(election, c("GG18", "GG22", "GP22", "PP24", "PG24", "GP26")))))
cat("\nGG26 denominators, statewide:\n")
print(as.data.frame(denominators |> summarise(active_2026_09 = sum(active_2026_09), active = sum(active), eligible = sum(eligible))))
cat("\nIndices, statewide:\n")
print(as.data.frame(indices |> filter(county == "Statewide") |>
  select(party_group, gg26_as_of, days_to_election, mail_sent_index, mail_received_index,
         primary_index, reg_momentum, reg_momentum_2022, reg_momentum_2018)))
cat(sprintf("\nCVAP total (ACS 5-year %d): %s\n", acs_vintage, format(sum(covariates$cvap), big.mark = ",")))
