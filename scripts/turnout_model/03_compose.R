#!/usr/bin/env Rscript
# Compute the turnout model's composition factors: how the 2026 electorate of each county x
# party group differs from its 2022 electorate because of who registered since.
#
#   Rscript scripts/turnout_model/03_compose.R          (from the repo root)
#
# For each county x party cell (DEM, REP, OTH) the change on the logit scale is
# comp = factor_age + factor_new, which the forecast adds to the cell's linear predictor:
#
# - factor_age (A): logit(R26) - logit(R22), where R is the cell's expected 2022-midterm
#   participation under its 2026 and its 2022 age mix. Rates r_cp(a) are the 2022-GG rates of
#   people now in age band a (county x age, tilted to the party with statewide party x age
#   rates); the 2026 mix is today's registrants by age, the 2022 mix the same scaled by the
#   share of each band registered in time for 2022-GG relative to 2026-GP.
# - factor_new (N): the extra pull of recent registrants beyond their age,
#   logit(R26 * (1 - (s26 - s22) * (1 - rho_gen))) - logit(R26), with s26 the share of the
#   cell registered 2023-2026, s22 the share of those registered by 2022 who registered
#   2019-2022, and rho_gen = sqrt(rho), rho the 2026-GP rate of people registered since the 2024
#   general relative to their age-expected rate.
#
# Source: skipjack.db voter_file_counts, snapshot 2026-08-12 (participation among current
# voters, used only as ratios within the snapshot). Writes
# data/turnout_model/gg26/composition_factors.csv and its provenance. Deterministic.

source("scripts/turnout_model/R/bootstrap.R")

SCRIPT <- tryCatch(script_path(), error = function(e) "scripts/turnout_model/03_compose.R")
OUT_DIR <- "data/turnout_model/gg26"
SNAPSHOT <- "2026-08-12"
SUPPRESS_MAX <- 9                       # suppressed counts are 1 to 9
RATE_CAP <- 0.99
CLAMP <- 1e-6
COMP_LIMIT <- 0.25
K_MAX <- 1.05
AGE_BANDS <- c("18-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+", "Unknown")
OLDER_BANDS <- c("35-44", "45-54", "55-64", "65-74", "75+")
# new_registrants bands (age at registration) -> turnout_by_age bands (age today).
NEW_BAND_MAP <- c("18-22" = "18-24", "23-29" = "25-34", "30+" = "35+")
NEW_YEARS <- 2023:2026                  # s26 numerator
OLD_YEARS <- 2019:2022                  # s22 numerator
PROFILE_YEARS <- 2025:2026              # new-registrant age profile

HABIT_NOTE <- paste(
  "No habit-mix factor: metric voter_types classifies each voter over the elections they were",
  "eligible for in this one snapshot, so there is no 2022-era counterpart to compare with.",
  "factor_new is the measurable part of the habit shift."
)
RHO_GEN_NOTE <- paste(
  "rho_gen = sqrt(rho) is a judgment: primaries exaggerate the gap between new and established",
  "voters, so the general-election propensity of new registrants is taken halfway (on the log",
  "scale) between rho and 1. factor_new_no_penalty uses rho_gen = 1, factor_new_full_penalty",
  "rho_gen = rho."
)
S22_NOTE <- paste(
  "Recent cohorts lose more members to moves, so in today's file the 2019-2022 cohort is",
  "understated relative to its size in 2022: s22 is understated and factor_new is biased toward",
  "a larger penalty. A known limitation, not corrected."
)

# ---- reading -----------------------------------------------------------------------------

vfc <- function(con, metric) {
  db_query(con, paste(
    "SELECT county, party_group, election, age_band, reg_year, reg_window, status,",
    "count, eligible FROM voter_file_counts WHERE snapshot_date = ? AND metric = ?"
  ), params = list(SNAPSHOT, metric))
}

# Model party groups from file party groups: DEM and REP as they are, OTH = ALL - DEM - REP so
# that suppressed UNA and minor-party cells do not matter. `vals` are value columns. Run on the
# output of fill_absent(), so absent rows are already 0; a NULL (suppressed, 1-9) stays NA and
# makes the derived OTH NA too.
to_model_groups <- function(df, keys, vals) {
  wide <- df |>
    filter(party_group %in% c("ALL", "DEM", "REP")) |>
    select(all_of(c(keys, "party_group", vals))) |>
    pivot_longer(all_of(vals), names_to = ".value_name", values_to = ".v") |>
    mutate(.col = paste0(.value_name, "__", party_group)) |>
    select(-party_group, -.value_name) |>
    pivot_wider(names_from = .col, values_from = .v)
  out <- list()
  for (p in c("DEM", "REP", "OTH")) {
    d <- wide[keys]
    d$party_group <- p
    for (v in vals) {
      g <- function(pg) {
        x <- wide[[paste0(v, "__", pg)]]
        if (is.null(x)) stop("to_model_groups(): no ", pg, " rows for ", v)
        x
      }
      d[[v]] <- if (p == "OTH") g("ALL") - g("DEM") - g("REP") else g(p)
    }
    out[[p]] <- d
  }
  bind_rows(out)
}

# Add the ALL/DEM/REP rows a group lacks, as 0: the pipeline's count_by only emits groups with
# members, so an absent row is a true zero. Suppressed rows (NULL) stay NA.
fill_absent <- function(df, keys, vals, groups = c("ALL", "DEM", "REP")) {
  grid <- df |> distinct(across(all_of(setdiff(keys, "party_group"))))
  full <- tidyr::crossing(grid, party_group = groups)
  full |>
    left_join(df |> filter(party_group %in% groups) |> mutate(.present = TRUE),
              by = keys) |>
    mutate(across(all_of(vals), ~ ifelse(is.na(.present), 0, .x))) |>
    select(-.present)
}

# Lower and upper bounds of a value that may be suppressed (NA -> 1..9).
lo_of <- function(x) ifelse(is.na(x), 1, x)
hi_of <- function(x) ifelse(is.na(x), SUPPRESS_MAX, x)

# ---- factor A: age mix ------------------------------------------------------------------

age_rates <- function(con) {
  tba <- vfc(con, "turnout_by_age") |> filter(age_band %in% AGE_BANDS)
  check_counties(setdiff(tba$county, "Maryland"), "turnout_by_age counties")
  rate_c <- function(el) {
    tba |> filter(election == el) |>
      transmute(county, age_band, count, eligible,
                # count 0 is observed even when eligible is suppressed: the rate is then 0
                rate = ifelse(!is.na(count) & count == 0, 0, count / eligible))
  }
  gg22 <- rate_c("2022-GG"); gp26 <- rate_c("2026-GP")
  k <- gg22 |> select(county, age_band, elig22 = eligible) |>
    inner_join(gp26 |> select(county, age_band, elig26 = eligible), by = c("county", "age_band")) |>
    mutate(k = elig22 / elig26)
  list(gg22 = gg22, gp26 = gp26, k = k, tba = tba)
}

# Statewide party tilt t_p(a) = party rate / all-party rate, per election (OTH = ALL - DEM - REP).
party_tilt <- function(con, el) {
  tap <- vfc(con, "turnout_by_age_party") |> filter(election == el, age_band %in% AGE_BANDS)
  stopifnot(all(is.na(tap$county)))
  tap$county <- "Maryland"
  m <- to_model_groups(fill_absent(tap, c("county", "age_band", "party_group"), c("count", "eligible")),
                       c("county", "age_band"), c("count", "eligible"))
  all <- tap |> filter(party_group == "ALL") |> transmute(age_band, all_rate = count / eligible)
  check(!anyNA(m$count) && !anyNA(m$eligible), paste("turnout_by_age_party", el, "DEM/REP/ALL unsuppressed"))
  m |> transmute(party_group, age_band, party_rate = count / eligible) |>
    left_join(all, by = "age_band") |>
    mutate(tilt = ifelse(all_rate == 0, NA_real_, party_rate / all_rate))
}

# ---- main -------------------------------------------------------------------------------

main <- function() {
  con <- db_connect()
  on.exit(DBI::dbDisconnect(con), add = TRUE)
  checks <- character()
  ck <- function(ok, name, detail = NULL) checks <<- c(checks, check(ok, name, detail))
  cells <- tidyr::crossing(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS)

  ar <- age_rates(con)
  ck(sum(is.na(ar$gg22$rate)) == 0, "every county x age band has a 2022-GG rate (count 0 rates are 0)")
  # Unknown age: statewide 2022-GG eligible equals 2026-GP eligible, so every county's does
  # (each county's 2022 count is at most its 2026 count): k = 1 where suppressed.
  st_unk <- ar$k |> filter(county == "Maryland", age_band == "Unknown")
  ck(st_unk$elig22 == st_unk$elig26, "statewide Unknown-age eligible is equal for 2022-GG and 2026-GP")
  k <- ar$k |> filter(county != "Maryland") |>
    mutate(k = ifelse(age_band == "Unknown" & is.na(elig22) & is.na(elig26), 1, k))
  tilt22 <- party_tilt(con, "2022-GG")
  tilt26 <- party_tilt(con, "2026-GP")

  # Cell rates r_cp(a): county x age rate times the party tilt, capped below RATE_CAP.
  cell_rate <- function(rates, tilt) {
    tidyr::crossing(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS, age_band = AGE_BANDS) |>
      left_join(rates |> select(county, age_band, r_c = rate), by = c("county", "age_band")) |>
      left_join(tilt |> select(party_group, age_band, tilt), by = c("party_group", "age_band")) |>
      mutate(r = ifelse(r_c == 0, 0, pmin(r_c * tilt, RATE_CAP)))
  }
  r22 <- cell_rate(ar$gg22, tilt22)
  r26gp <- cell_rate(ar$gp26, tilt26)
  # A county with no Unknown-age registrants has no Unknown row (absent = no members); every other
  # band must have a rate.
  ck(!anyNA(r22$r[r22$age_band != "Unknown"]) && !anyNA(r26gp$r[r26gp$age_band != "Unknown"]),
     "every cell x age band 18-24 to 75+ has a tilted rate")

  # 2026 mix: age_by_party, all statuses.
  abp_raw <- vfc(con, "age_by_party") |> filter(county != "Maryland")
  abp <- to_model_groups(fill_absent(abp_raw, c("county", "age_band", "party_group"), "count"),
                         c("county", "age_band"), "count")
  # bounds for the reconciliation check
  abp_b <- fill_absent(abp_raw, c("county", "age_band", "party_group"), "count") |>
    mutate(lo = lo_of(count), hi = hi_of(count)) |>
    select(county, age_band, party_group, lo, hi) |>
    pivot_wider(names_from = party_group, values_from = c(lo, hi))
  abp_bounds <- bind_rows(
    abp_b |> transmute(county, age_band, party_group = "DEM", lo = lo_DEM, hi = hi_DEM),
    abp_b |> transmute(county, age_band, party_group = "REP", lo = lo_REP, hi = hi_REP),
    abp_b |> transmute(county, age_band, party_group = "OTH",
                       lo = pmax(0, lo_ALL - hi_DEM - hi_REP), hi = hi_ALL - lo_DEM - lo_REP)
  )
  pbc_raw <- vfc(con, "party_by_county") |> filter(county != "Maryland")
  pbc <- to_model_groups(
    fill_absent(pbc_raw |> group_by(county, party_group) |>
                  summarise(count = if (anyNA(count)) NA_real_ else sum(count), .groups = "drop"),
                c("county", "party_group"), "count"),
    "county", "count") |> rename(cell_total = count)
  ck(!anyNA(pbc$cell_total), "party_by_county totals (active + inactive) are unsuppressed for every cell")
  recon <- abp_bounds |> group_by(county, party_group) |>
    summarise(lo = sum(lo), hi = sum(hi), .groups = "drop") |>
    inner_join(pbc, by = c("county", "party_group"))
  ck(nrow(recon) == 72 && all(recon$cell_total >= recon$lo & recon$cell_total <= recon$hi),
     "age bands of age_by_party (with Under 18; suppressed bands as 1-9) sum to the cell's party_by_county total")
  exact <- abp |> group_by(county, party_group) |>
    summarise(s = sum(count), .groups = "drop") |> inner_join(pbc, by = c("county", "party_group"))
  ck(all(is.na(exact$s) | exact$s == exact$cell_total),
     "where no band is suppressed, age_by_party bands sum exactly to the party_by_county total")

  e26 <- abp |> filter(age_band %in% AGE_BANDS) |> rename(e26 = count)
  unknown_dropped <- e26 |> filter(is.na(e26)) |> select(county, party_group, age_band)
  ck(all(unknown_dropped$age_band == "Unknown"), "only Unknown-age bands of e26 are suppressed")
  e26 <- e26 |> filter(!is.na(e26))

  mix <- e26 |>
    left_join(k |> select(county, age_band, k), by = c("county", "age_band")) |>
    left_join(r22 |> select(county, party_group, age_band, r), by = c("county", "party_group", "age_band")) |>
    mutate(e22 = e26 * k)
  used_k <- mix$k[mix$e26 > 0]
  ck(!anyNA(used_k) && all(used_k > 0 & used_k <= K_MAX), "k_c(a) in (0, 1.05] for every band in use")
  ck(!anyNA(mix$r), "every band in use has a rate")

  age_cells <- mix |> group_by(county, party_group) |>
    summarise(n26 = sum(e26), v26 = sum(e26 * r), n22 = sum(e22), v22 = sum(e22 * r), .groups = "drop") |>
    mutate(R26 = v26 / n26, R22 = v22 / n22)

  # ---- factor N: shares --------------------------------------------------------------------
  rc_raw <- vfc(con, "registration_cohorts") |> filter(county != "Maryland")
  ck(!anyNA(rc_raw$reg_year), "registration_cohorts carries no NULL reg_year rows (the metric excludes them)")
  recent <- rc_raw |> filter(reg_year %in% c(OLD_YEARS, NEW_YEARS)) |>
    fill_absent(c("county", "party_group", "reg_year"), "count")
  rec <- to_model_groups(recent, c("county", "reg_year"), "count") |>
    mutate(window = ifelse(reg_year %in% NEW_YEARS, "new", "old"))
  ck(!anyNA(rec$count), "registration_cohorts 2019-2026 counts are unsuppressed for DEM, REP and ALL")
  shares <- rec |> group_by(county, party_group) |>
    summarise(n_new = sum(count[window == "new"]), n_old = sum(count[window == "old"]), .groups = "drop") |>
    inner_join(pbc, by = c("county", "party_group")) |>
    mutate(by_2022 = cell_total - n_new, s26 = n_new / cell_total, s22 = n_old / by_2022)
  ck(nrow(shares) == 72, "shares for 72 cells")
  # registration_cohorts vs party_by_county: statewide rows are computed before suppression, so
  # their difference is exactly the number of registrants with a NULL reg_year. In the county
  # rows the gap is that plus suppressed old years (1-9 each).
  st_tot <- function(m) db_query(con, "SELECT SUM(count) AS n FROM voter_file_counts WHERE snapshot_date = ?
    AND metric = ? AND county = 'Maryland' AND party_group = 'ALL'", params = list(SNAPSHOT, m))$n
  null_reg_year <- st_tot("party_by_county") - st_tot("registration_cohorts")
  ck(null_reg_year >= 0 && null_reg_year <= 0.001 * st_tot("party_by_county"),
     "statewide NULL reg_year registrants (party_by_county minus registration_cohorts) are under 0.1% of registrants")
  gap <- rc_raw |> filter(party_group %in% c("ALL", "DEM", "REP")) |> group_by(county, party_group) |>
    summarise(known = sum(count, na.rm = TRUE), n_supp = sum(is.na(count)), .groups = "drop") |>
    inner_join(pbc_raw |> group_by(county, party_group) |> summarise(total = sum(count), .groups = "drop"),
               by = c("county", "party_group")) |>
    mutate(gap = total - known)
  ck(all(gap$gap >= gap$n_supp & gap$gap <= SUPPRESS_MAX * gap$n_supp + null_reg_year),
     "county registration_cohorts sums fall short of party_by_county totals by suppressed old years plus at most the statewide NULL reg_year count")

  # ---- factor N: rho ---------------------------------------------------------------------
  nvv_raw <- vfc(con, "new_voters_voted")
  nvv_c <- nvv_raw |> filter(county != "Maryland")
  nvv <- to_model_groups(fill_absent(nvv_c, c("county", "party_group"), c("count", "eligible")),
                         "county", c("count", "eligible"))
  nvv_all <- nvv_c |> filter(party_group == "ALL") |> select(county, count, eligible)
  ck(all(nvv_raw$eligible[nvv_raw$party_group %in% c("DEM", "REP", "ALL")] > 0, na.rm = TRUE),
     "new_voters_voted eligible is positive for DEM, REP and ALL")
  st_nvv <- nvv_raw |> filter(county == "Maryland", party_group %in% c("DEM", "REP")) |> arrange(party_group)
  sum_nvv <- nvv |> filter(party_group %in% c("DEM", "REP")) |> group_by(party_group) |>
    summarise(count = sum(count), eligible = sum(eligible)) |> arrange(party_group)
  ck(!anyNA(sum_nvv$count) && all(sum_nvv$count == st_nvv$count & sum_nvv$eligible == st_nvv$eligible),
     "county new_voters_voted DEM and REP sum to the statewide rows")

  # Age profile of reg_year 2025-2026 first-time registrants (age at registration).
  nr_raw <- vfc(con, "new_registrants") |> filter(county != "Maryland", reg_year %in% PROFILE_YEARS)
  nr_sum <- nr_raw |> filter(party_group %in% c("ALL", "DEM", "REP")) |>
    group_by(county, party_group, age_band) |>
    summarise(count = if (anyNA(count)) NA_real_ else sum(count), .groups = "drop") |>
    fill_absent(c("county", "party_group", "age_band"), "count")
  prof_cell <- nr_sum |> filter(party_group %in% c("DEM", "REP")) |>
    group_by(county, party_group) |> mutate(ok = !anyNA(count), w = count / sum(count)) |> ungroup()
  prof_all <- nr_sum |> filter(party_group == "ALL") |> group_by(county) |>
    mutate(w_all = count / sum(count)) |> ungroup() |> select(county, age_band, w_all)
  ck(!anyNA(prof_all$w_all), "county ALL new-registrant age profiles are unsuppressed")
  profile <- prof_cell |> left_join(prof_all, by = c("county", "age_band")) |>
    mutate(w_use = ifelse(ok, w, w_all)) |>
    group_by(county, party_group) |> mutate(profile_source = ifelse(all(ok), "cell", "county ALL")) |> ungroup()
  profile_all <- prof_all |> rename(w_use = w_all) |> mutate(party_group = "ALL", profile_source = "county ALL")

  # Age-expected 2026-GP rate per new-registrant band: 18-22 -> 18-24, 23-29 -> 25-34, 30+ -> the
  # cell's 35+ average (weighted by the cell's current registrants in those bands).
  band_rate <- function(rates_cell, weights) {
    young <- rates_cell |> filter(age_band %in% c("18-24", "25-34")) |>
      transmute(county, party_group, nr_band = names(NEW_BAND_MAP)[match(age_band, NEW_BAND_MAP)], rate = r)
    older <- rates_cell |> filter(age_band %in% OLDER_BANDS) |>
      inner_join(weights, by = c("county", "party_group", "age_band")) |>
      group_by(county, party_group) |> summarise(rate = sum(w * r) / sum(w), .groups = "drop") |>
      mutate(nr_band = "30+")
    bind_rows(young, older)
  }
  w_cell <- e26 |> select(county, party_group, age_band, w = e26)
  br_cell <- band_rate(r26gp |> filter(party_group %in% c("DEM", "REP")), w_cell)
  # County ALL: untilted county rate, weighted by all registrants.
  r_all <- ar$gp26 |> filter(county != "Maryland") |> transmute(county, party_group = "ALL", age_band, r = rate)
  w_all <- abp_raw |> filter(party_group == "ALL", age_band %in% OLDER_BANDS) |>
    transmute(county, party_group = "ALL", age_band, w = count)
  br_all <- band_rate(r_all, w_all)

  expect <- function(prof, br, obs) {
    prof |> select(county, party_group, nr_band = age_band, w_use, profile_source) |>
      inner_join(br, by = c("county", "party_group", "nr_band")) |>
      group_by(county, party_group, profile_source) |>
      summarise(exp_rate = sum(w_use * rate), .groups = "drop") |>
      inner_join(obs, by = c("county", "party_group")) |>
      mutate(obs_rate = count / eligible, exp_count = exp_rate * eligible, rho = obs_rate / exp_rate)
  }
  rho_dr <- expect(profile, br_cell, nvv |> filter(party_group %in% c("DEM", "REP")))
  rho_all <- expect(profile_all, br_all, nvv_all |> mutate(party_group = "ALL"))
  ck(nrow(rho_dr) == 48 && nrow(rho_all) == 24, "rho inputs for 48 DEM/REP cells and 24 county ALL rows")
  ck(!anyNA(rho_all$rho), "county ALL rho is computable everywhere")
  pooled <- rho_dr |> filter(!is.na(count))
  rho_pooled <- sum(pooled$count) / sum(pooled$exp_count)
  # statewide ALL rho (last fallback): summed county ALL rows
  rho_state_all <- sum(rho_all$count) / sum(rho_all$exp_count)

  rho_tbl <- cells |>
    left_join(rho_dr |> select(county, party_group, rho_cell = rho, profile_source), by = c("county", "party_group")) |>
    left_join(rho_all |> select(county, rho_county_all = rho), by = "county") |>
    mutate(
      rho_source = case_when(
        party_group == "OTH" ~ "statewide DEM/REP pooled",
        !is.na(rho_cell) ~ "cell",
        !is.na(rho_county_all) ~ "county ALL",
        TRUE ~ "statewide ALL"
      ),
      rho = case_when(
        rho_source == "statewide DEM/REP pooled" ~ rho_pooled,
        rho_source == "cell" ~ rho_cell,
        rho_source == "county ALL" ~ rho_county_all,
        TRUE ~ rho_state_all
      ),
      profile_source = ifelse(party_group == "OTH", NA_character_, profile_source)
    )

  # ---- assemble ----------------------------------------------------------------------------
  factor_new <- function(R26, s26, s22, rho_gen) {
    arg <- R26 * (1 - (s26 - s22) * (1 - rho_gen))
    qlogis(pmin(pmax(arg, CLAMP), 1 - CLAMP)) - qlogis(R26)
  }
  out <- cells |>
    inner_join(age_cells, by = c("county", "party_group")) |>
    inner_join(shares |> select(county, party_group, n_new, n_old, by_2022, cell_total, s26, s22),
               by = c("county", "party_group")) |>
    inner_join(rho_tbl |> select(county, party_group, rho, rho_source), by = c("county", "party_group")) |>
    mutate(
      factor_age = qlogis(R26) - qlogis(R22),
      rho_gen = sqrt(rho),
      factor_new = factor_new(R26, s26, s22, rho_gen),
      factor_new_no_penalty = factor_new(R26, s26, s22, 1),
      factor_new_full_penalty = factor_new(R26, s26, s22, rho),
      comp = factor_age + factor_new
    )

  # Statewide rows per party from the summed cell counts (report only, not used in the forecast).
  exp_dr <- rho_dr |> group_by(party_group) |>
    summarise(rho = sum(count) / sum(exp_count), .groups = "drop")
  state <- out |> group_by(party_group) |>
    summarise(v26 = sum(v26), n26 = sum(n26), v22 = sum(v22), n22 = sum(n22),
              n_new = sum(n_new), n_old = sum(n_old), by_2022 = sum(by_2022), cell_total = sum(cell_total),
              .groups = "drop") |>
    mutate(county = "Maryland", R26 = v26 / n26, R22 = v22 / n22, s26 = n_new / cell_total, s22 = n_old / by_2022) |>
    left_join(exp_dr, by = "party_group") |>
    mutate(rho = ifelse(party_group == "OTH", rho_pooled, rho),
           rho_source = ifelse(party_group == "OTH", "statewide DEM/REP pooled", "statewide summed cells"),
           factor_age = qlogis(R26) - qlogis(R22),
           rho_gen = sqrt(rho),
           factor_new = factor_new(R26, s26, s22, rho_gen),
           factor_new_no_penalty = factor_new(R26, s26, s22, 1),
           factor_new_full_penalty = factor_new(R26, s26, s22, rho),
           comp = factor_age + factor_new)

  cols <- c("county", "party_group", "R22", "R26", "factor_age", "rho", "rho_gen", "s22", "s26",
            "factor_new", "factor_new_no_penalty", "factor_new_full_penalty", "comp", "rho_source")
  cell_rows <- out |>
    mutate(county = factor(county, CANONICAL_COUNTIES), party_group = factor(party_group, PARTY_GROUPS)) |>
    arrange(county, party_group) |> mutate(across(c(county, party_group), as.character))
  state_rows <- state |> mutate(party_group = factor(party_group, PARTY_GROUPS)) |>
    arrange(party_group) |> mutate(party_group = as.character(party_group))

  ck(nrow(cell_rows) == 72 && !anyNA(cell_rows$comp), "72 cell rows with no NA in comp")
  big <- cell_rows |> filter(abs(comp) > COMP_LIMIT)
  ck(nrow(big) == 0, "|comp| <= 0.25 for every cell",
     paste(big$county, big$party_group, round(big$comp, 3), collapse = "; "))
  ck(all(cell_rows$s26 > 0 & cell_rows$s26 < 1 & cell_rows$s22 > 0 & cell_rows$s22 < 1), "s22 and s26 in (0, 1)")
  ck(all(cell_rows$rho > 0), "rho > 0")

  final <- bind_rows(cell_rows, state_rows) |> select(all_of(cols)) |>
    mutate(across(where(is.double), ~ round(.x, 6)))
  path <- file.path(OUT_DIR, "composition_factors.csv")
  write_input_csv(final, path)

  write_provenance(
    path,
    generated_by = SCRIPT,
    description = paste(
      "Composition change factors per county x party group (DEM, REP, OTH = ALL - DEM - REP), logit scale:",
      "comp = factor_age + factor_new, added by the forecast to each cell's linear predictor. R22 and R26 are",
      "the cell's expected 2022-midterm participation under its 2022 and 2026 age mix; s22, s26 the recent-registrant",
      "shares; rho the relative 2026-primary propensity of people registered since the 2024 general,",
      "rho_gen = sqrt(rho) its general-election version. county = Maryland rows are statewide per party from",
      "summed cell counts, for reporting only."
    ),
    sources = list(
      table = "skipjack.db voter_file_counts",
      snapshot_date = SNAPSHOT,
      metrics = list(
        turnout_by_age = "county x age band (age today): 2022-GG and 2026-GP count / eligible; k_c(a) = 2022-GG eligible / 2026-GP eligible",
        turnout_by_age_party = "statewide party x age band: party tilt t_p(a) for 2022-GG (factor_age) and 2026-GP (rho)",
        age_by_party = "county x party x age band, all statuses: the 2026 mix e26",
        party_by_county = "county x party x status: cell totals (active + inactive) for the e26 reconciliation and as the share denominators",
        registration_cohorts = "county x party x reg_year: 2019-2022 and 2023-2026 counts",
        new_voters_voted = "county x party: registered after 2024-11-05 and eligible for 2026-GP, count voted / eligible",
        new_registrants = "county x party x age at registration (18-22, 23-29, 30+) x reg_year, first-time registrants: reg_year 2025-2026 age profile"
      )
    ),
    checks = checks,
    notes = list(
      method = list(
        factor_age = "r_cp(a) = r_c(a) * t_p(a), capped at 0.99; e22 = e26 * k_c(a); R = sum(e r) / sum(e); A = logit(R26) - logit(R22). Under 18 dropped, Unknown kept as its own band.",
        factor_new = "N = logit(R26 * (1 - (s26 - s22) * (1 - rho_gen))) - logit(R26), argument clamped to [1e-6, 1 - 1e-6].",
        rho = paste(
          "DEM and REP: observed 2026-GP rate of new_voters_voted over the age-expected rate, sum over bands of the",
          "cell's 2025-2026 new-registrant age profile times the cell's 2026-GP rate (county x age rate times the",
          "statewide 2026-GP party tilt): 18-22 -> 18-24, 23-29 -> 25-34, 30+ -> the cell's 35+ average weighted by",
          "its current registrants. OTH: statewide DEM/REP pooled rho (sum of observed over sum of expected counts",
          "across the 48 DEM/REP cells), since closed primaries make OTH primary rates meaningless. Fallbacks if a",
          "cell's count were suppressed: county ALL (untilted rates), then statewide ALL; rho_source records which was used."
        ),
        rho_gen = RHO_GEN_NOTE,
        habit_mix = HABIT_NOTE
      ),
      limitations = list(
        s22 = S22_NOTE,
        participation_among_current_voters = "All rates and shares are ratios within the 2026-08-12 snapshot, which holds about 87% of the 2022 electorate; never levels.",
        profile_mismatch = paste(
          "new_voters_voted counts everyone with a state registration date after 2024-11-05 (including 16-17 year-old",
          "preregistrants and November-December 2024 registrants), while the age profile comes from new_registrants,",
          "first-time registrants aged 18+ at registration in reg_year 2025-2026, whose bands are age at registration",
          "rather than age today. The profile is an approximation of the age mix of the rho population."
        )
      ),
      deviations = list(
        registration_cohorts_null_reg_year = paste(
          "registration_cohorts excludes NULL reg_year rows (the pipeline filters them), and its oldest years are",
          "sometimes suppressed. Share denominators therefore use the cell's party_by_county total (active + inactive),",
          "which includes NULL reg_year registrants; s22's denominator is that total minus the 2023-2026 counts, so",
          "NULL reg_year registrants count as registered by 2022. Statewide, NULL reg_year registrants number",
          null_reg_year, "(party_by_county minus registration_cohorts, both unsuppressed statewide)."
        ),
        rho_party_tilt = paste(
          "The brief's age-expected 2026-GP rate for a county x party cell uses turnout_by_age, which has no party; the",
          "county x age rate is tilted to the party with the statewide 2026-GP party x age rates, as factor_age does",
          "for 2022-GG, so that DEM and REP rho compare like with like (all-party primary rates include unaffiliated",
          "voters who cannot vote in them)."
        ),
        e26_reconciliation = paste(
          "age_by_party has no total row; its bands (with Under 18) are reconciled to party_by_county totals, exactly",
          "where no band is suppressed and within the 1-9 bounds of suppressed bands otherwise."
        ),
        unknown_age = paste(
          "The Unknown age band has a 2022-GG count of 0 in every county (rate 0, observed even where eligible is",
          "suppressed) and, statewide, equal 2022-GG and 2026-GP eligible counts, so k = 1 in every county. Where a",
          "cell's Unknown e26 is suppressed (1-9 people) or not derivable, the band is left out of that cell's mixes;",
          "with rate 0 and equal weight in both mixes its effect on factor_age is under 1e-4."
        ),
        unknown_age_dropped_cells = paste(unknown_dropped$county, unknown_dropped$party_group, sep = ":")
      )
    ),
    extra = list(
      statewide_pooled_dem_rep_rho = round(rho_pooled, 6),
      statewide_all_rho = round(rho_state_all, 6),
      statewide_null_reg_year_registrants = null_reg_year,
      new_registrant_profile_fallbacks = profile |> filter(profile_source != "cell") |>
        distinct(county, party_group) |> mutate(cell = paste(county, party_group, sep = ":")) |> pull(cell),
      columns = list(
        R22 = "expected 2022-GG participation under the cell's 2022 age mix (ratio within the snapshot)",
        R26 = "the same under the 2026 age mix",
        factor_age = "logit(R26) - logit(R22)",
        rho = "2026-GP propensity of registrants since the 2024 general relative to their age-expected rate",
        rho_gen = "sqrt(rho): judged general-election propensity (see notes)",
        s22 = "share of registrants by end of 2022 who registered 2019-2022",
        s26 = "share of current registrants who registered 2023-2026",
        factor_new = "new-registrant factor with rho_gen = sqrt(rho)",
        factor_new_no_penalty = "with rho_gen = 1 (sensitivity)",
        factor_new_full_penalty = "with rho_gen = rho (sensitivity)",
        comp = "factor_age + factor_new",
        rho_source = "where rho came from"
      )
    )
  )

  options(width = 200)
  cat("Statewide composition factors (summed cells):\n")
  print(as.data.frame(final |> filter(county == "Maryland")), row.names = FALSE)
  cat("\nFive largest |comp| cells:\n")
  print(as.data.frame(final |> filter(county != "Maryland") |> arrange(desc(abs(comp))) |> head(5)), row.names = FALSE)
  cat("\nStatewide pooled DEM/REP rho:", round(rho_pooled, 6), "\n")
  cat("Unknown-age bands left out:", nrow(unknown_dropped), "cells\n")
  cat("Wrote", path, "\n")
}

main()
