#!/usr/bin/env Rscript
# Forecast Maryland's November 3, 2026 general election turnout by county x party group (DEM,
# REP, OTH), summed to county and state, from the structure model's draws, the composition
# factors and the 2026 observables.
#
#   Rscript scripts/turnout_model/04_forecast.R          (from the repo root, after 01-03)
#
# Step 1, reference levels. For each of the 1,000 structure draws k, reference election Y in
# {GG18, GG22} and party p, alpha*_{Y,p,k} solves
#   sum_c active_{Y,c,p} * E_eps[invlogit(alpha + s_{c,p,k} + eps)] = actual ballots_{Y,p},
# eps ~ N(0, sigma_obs^2), the expectation by 20-node Gauss-Hermite quadrature. No composition
# factor: each reference year is reproduced with its own mix.
#
# Step 2, the level rule (verbatim in LEVEL_RULE below, in the provenance and in
# level_rule_text.csv):
LEVEL_RULE <- c(
  "Three scenarios per party p, on the logit scale, per draw k:",
  "low: alpha = alpha*_{GG22,p,k}",
  "high: alpha = alpha*_{GG18,p,k}",
  "central: alpha = (1 - w_p) * alpha*_{GG22,p,k} + w_p * alpha*_{GG18,p,k}",
  "w_p starts at 0.5 and moves by 0.1 per index vote, bounded to [0.2, 0.8]. Votes, per party p (DEM, REP, OTH), each in {-1, 0, +1} where +1 means 2018-like and -1 means 2022-like:",
  "1. Registration momentum: position = (m26 - m22) / (m18 - m22) using indices.csv reg_momentum, reg_momentum_2022, reg_momentum_2018 (statewide row for p). Vote +1 if position >= 2/3, -1 if <= 1/3, else 0. If |m18 - m22| < 0.002 the analogues cannot discriminate: vote 0. Amendment: a position outside [-0.5, 1.5] extrapolates beyond the two analogues and its sign is not interpretable: vote 0 when position < -0.5 or > 1.5; otherwise the thresholds above apply.",
  "2. Mail requests, per registrant: project GG26 final sent for p as a linear trend over the available daily snapshots extended to 2026-10-27 (the request deadline; an assumption: no ballots are sent after it). Index = (projected GG26 final sent_p / 2026 projected active_p from denominators_gg26.csv) / (GG22 final sent_p (2022-11-28 file) / GG22 active_p from training.csv), so registration growth is not read as engagement. Vote +1 if >= 1.05, -1 if <= 0.95, else 0. (No 2018 analogue exists: 2018 predates the permanent mail-in list, so this index can only say \"more or less engaged than 2022\".)",
  "3. Primary participation, per registrant: index = (GP26 ballots_p / GP26 active_p) / (GP22 ballots_p / GP22 active_p), from training.csv (DEM and REP; GP26 split estimated; OTH gets vote 0 because primaries are closed). Vote +1 if >= 1.05, -1 if <= 0.95, else 0. Caveat: GP26 had an incumbent governor and GP22 an open seat, which depresses this index for reasons unrelated to November.",
  "level_rule.csv also records the raw-count ratios (mail_count_ratio, primary_count_ratio) beside the per-registrant indices that vote."
)
MAIL_NOTE <- paste(
  "No 2018 analogue exists: 2018 predates the permanent mail-in list, so the mail index can only",
  "say more or less engaged than 2022. The rule's GG26 projection is an OLS line of sent on calendar",
  "day over every daily snapshot, extended to 2026-10-27, the mail-in request deadline (assumed to",
  "be when sending stops), floored per county x party at the latest observed count. The mail vote",
  "depends on the projection choice: the sent series is step-like, so the all-snapshot slope is",
  "much steeper than the last few days', and holding the latest count or extending the last three",
  "snapshots gives lower indices (see the _latest and _recent columns of level_rule.csv and the",
  "central_mail_latest and central_mail_recent scenarios). No GG22 trajectory of daily sent counts",
  "exists to anchor the choice."
)
#
# Step 3, forecast draws. For each draw k, scenario and cell (c, p):
#   eta = alpha_scen_{p,k} + s_{c,p,k} + comp_centred_{c,p} + eps_{c,p,k},
#   ballots = active_2026_{c,p} * invlogit(eta),
# eps N(0, sigma_obs^2) per cell per draw, antithetic across draws (EPS_NOTE), the same across
# scenarios within a draw. comp_centred is comp minus its 2026-active-weighted mean over counties
# within the party, so composition only redistributes turnout across counties. Sensitivities:
# central_comp_level (uncentred comp, central level), central_mail_latest and central_mail_recent
# (the central level with the w the rule gives under the alternative mail projections).
#
# Step 4, mode split (central scenario, county): mail = projected GG26 sent (ALL parties) x the
# mean of GG22 and PG24 received / sent, capped at 0.9 x ballots; provisional at GG22's share of
# ballots; the rest split early vs election day by GG22's early / (early + election day).
#
# Sources: data/turnout_model/gg26/structure_draws.csv.gz, fit_summary.json (sigma_obs),
# composition_factors.csv; data/turnout_model/inputs/training.csv, denominators_gg26.csv,
# indices.csv; skipjack.db turnout_counts (GG26 sent-returned daily snapshots, GG22 and PG24
# mail-voting at their latest as_of). Writes reference_levels.csv, level_rule.csv,
# level_rule_text.csv, forecast_county_party.csv, forecast_county.csv, forecast_statewide.csv and
# mode_split.csv to data/turnout_model/gg26/, each with provenance. Seeded; the CSVs are
# deterministic.

source("scripts/turnout_model/R/bootstrap.R")

SCRIPT <- tryCatch(script_path(), error = function(e) "scripts/turnout_model/04_forecast.R")
OUT_DIR <- "data/turnout_model/gg26"
STATE_LABEL <- "Maryland"
SEED <- 20261103
N_NODES <- 20
REFERENCE <- c("GG18", "GG22")
MAIN_SCENARIOS <- c("low", "central", "high", "central_comp_level")
SCENARIOS <- c(MAIN_SCENARIOS, "central_mail_latest", "central_mail_recent")
ACTUAL_SCENARIOS <- paste0("actual_", REFERENCE)
REQUEST_DEADLINE <- as.Date("2026-10-27")
N_RECENT <- 3
PROJECTIONS <- c(rule = "all", latest = "latest", recent = "recent")
GG22_SENT_AS_OF <- "2022-11-28"
W_START <- 0.5
W_STEP <- 0.1
W_BOUNDS <- c(0.2, 0.8)
POSITION_CUTS <- c(1 / 3, 2 / 3)
POSITION_RANGE <- c(-0.5, 1.5)
MOMENTUM_MIN_GAP <- 0.002
RATIO_CUTS <- c(0.95, 1.05)
MAIL_CAP <- 0.9
IDENTITY_TOL <- 0.001
Q <- c(q05 = 0.05, q50 = 0.5, q95 = 0.95)

PRIMARY_CAVEAT <- paste(
  "GP26 had an incumbent governor and GP22 an open seat, which depresses the primary index for",
  "reasons unrelated to November. GP26 party ballots are estimated (training.csv party_split_estimated)."
)
EPS_NOTE <- paste(
  "Antithetic cell noise: for draws 1-500 eps_cpk is a fresh N(0, sigma_obs^2) per cell, and draws",
  "501-1000 reuse the negated eps of draws 1-500 (eps_{c,p,k+500} = -eps_{c,p,k}); each draw's eps is",
  "still marginally N(0, sigma_obs^2) and shared by every scenario within the draw. With 1,000",
  "independent eps the Monte Carlo SE of the statewide OTH mean is about 250 ballots (0.077%), so",
  "the 0.1% identity check passed or failed by seed; the antithetic pairs cancel the linear part of",
  "that error (identity errors at most about 6e-5 across six seeds tested)."
)
INTERVAL_NOTE <- paste(
  "Each draw's level is calibrated to a reference year, so the intervals within a scenario carry",
  "only the cell noise eps and the shift of the 2026 cells' active registration and comp relative",
  "to the reference mix; the spread between low, central and high is the level uncertainty. The",
  "range to quote is headline_range: low's q05 to high's q95, around the central mean."
)
COMP_NOTES <- list(
  centring = paste(
    "Every scenario uses comp_centred_cp = comp_cp minus the weighted mean over counties of comp for",
    "party p (weights: 2026 projected active registration, denominators_gg26.csv). composition_factors.csv's",
    "comp is positive in every cell (statewide DEM +0.051, REP +0.080, OTH +0.034), but its statewide",
    "level is not identified: the 2022 mix comes from today's voter file, which lacks the ~13% of the",
    "2022 electorate who have since died or moved, and that bias is as large as the factor. Centred,",
    "composition only redistributes turnout across counties within a party and the level comes from",
    "the calibrated scenario."
  ),
  sensitivity = paste(
    "central_comp_level uses the uncentred comp with the central level; its statewide difference from",
    "central is the measure ballots_minus_central in forecast_statewide.csv."
  ),
  structure = paste(
    "The midterm structure effect s_cp is treated as describing the 2022 composition, and comp is a change from it."
  )
)
MAIL_VOTING_NOTE <- paste(
  "GG22 and PG24 mail-voting are read at MAX(as_of) per election, which is the board's 2026-04-21",
  "re-fetch of each certified file (it differs from the original 2022-12-08 and 2024-12-11 files by",
  "about 100 ballots sent and a few received)."
)
ACTUAL_ROWS_NOTE <- paste(
  "Rows with scenario actual_GG18 and actual_GG22 are the certified figures from training.csv: active",
  "is that election's active registration and every interval column equals the point value (not NA).",
  "In forecast_county.csv their eligible, cvap, rate_eligible_* and rate_cvap_* are empty, since no",
  "active + inactive or CVAP denominator of that year is in the inputs."
)

# ---- inputs -------------------------------------------------------------------------------

# The 72 county x party cells in canonical order.
cell_frame <- function() {
  tidyr::expand_grid(county = CANONICAL_COUNTIES, party_group = PARTY_GROUPS) |>
    mutate(cell = cell_key(county, party_group))
}

read_inputs <- function(cells) {
  checks <- character()
  draws <- readr::read_csv(file.path(OUT_DIR, "structure_draws.csv.gz"), show_col_types = FALSE)
  checks <- c(checks, check(nrow(draws) == 72000 && !anyNA(draws), "structure_draws has 72,000 complete rows"))
  wide <- draws |>
    mutate(cell = cell_key(county, party_group)) |>
    select(cell, draw, s) |>
    tidyr::pivot_wider(names_from = draw, values_from = s)
  s <- as.matrix(wide[match(cells$cell, wide$cell), -1])
  checks <- c(checks, check(!anyNA(s) && ncol(s) == 1000 && identical(colnames(s), as.character(1:1000)),
                            "every cell has draws 1 to 1,000"))
  sigma <- jsonlite::fromJSON(file.path(OUT_DIR, "fit_summary.json"))$sigma_obs
  checks <- c(checks, check(is.numeric(sigma) && length(sigma) == 1 && sigma > 0 && sigma < 0.5,
                            "fit_summary.json has sigma_obs"))

  comp <- readr::read_csv(file.path(OUT_DIR, "composition_factors.csv"), show_col_types = FALSE) |>
    filter(county != STATE_LABEL)
  comp <- comp[match(cells$cell, cell_key(comp$county, comp$party_group)), ]
  checks <- c(checks, check(nrow(comp) == 72 && !anyNA(comp$comp) && !anyNA(comp$county),
                            "composition_factors has comp for the 72 cells (Maryland rows dropped)"))

  training <- readr::read_csv(file.path(INPUTS_DIR, "training.csv"), show_col_types = FALSE)
  den <- readr::read_csv(file.path(INPUTS_DIR, "denominators_gg26.csv"), show_col_types = FALSE)
  den <- den[match(cells$cell, cell_key(den$county, den$party_group)), ]
  checks <- c(checks, check(nrow(den) == 72 && !anyNA(den$county) && !anyNA(den$active) &&
                              !anyNA(den$eligible) && !anyNA(den$cvap) && all(den$active > 0),
                            "denominators_gg26 has active, eligible and cvap for the 72 cells"))
  cvap_ok <- den |> group_by(county) |> summarise(n = n_distinct(cvap), .groups = "drop")
  checks <- c(checks, check(all(cvap_ok$n == 1), "cvap is one county figure repeated on each party row"))

  ref <- lapply(setNames(REFERENCE, REFERENCE), function(y) {
    r <- training |> filter(election == y)
    r <- r[match(cells$cell, cell_key(r$county, r$party_group)), ]
    if (nrow(r) != 72 || anyNA(r$county) || anyNA(r$active) || anyNA(r$ballots_cast)) stop(y, " training rows incomplete")
    r
  })
  checks <- c(checks, check(sum(ref$GG18$ballots_cast) == 2335128 && sum(ref$GG22$ballots_cast) == 2031635,
                            "GG18 and GG22 training ballots sum to the certified statewide totals"))

  indices <- readr::read_csv(file.path(INPUTS_DIR, "indices.csv"), show_col_types = FALSE)
  checks <- c(checks, check(sum(indices$county == STATE_LABEL) == 4, "indices.csv has the four Maryland (statewide) rows"))
  list(s = s, sigma = sigma, comp = comp, training = training, den = den, ref = ref,
       indices = indices, checks = checks)
}

read_mail <- function() {
  con <- db_connect()
  on.exit(DBI::dbDisconnect(con))
  sent <- db_query(con, "
    SELECT as_of, county, party, value, source_id FROM turnout_counts
    WHERE election = 'GG26' AND family = 'sent-returned' AND geography = 'county'
      AND category = 'ALL' AND measure = 'sent' AND party IN ('DEM', 'REP', 'OTH', 'ALL')")
  mv <- db_query(con, "
    SELECT t.election, t.as_of, t.county, t.measure, t.value, t.source_id FROM turnout_counts t
    WHERE t.election IN ('GG22', 'PG24') AND t.family = 'mail-voting' AND t.geography = 'county'
      AND t.category = 'ALL' AND t.party = 'ALL' AND t.measure IN ('sent', 'received')
      AND t.as_of = (SELECT MAX(as_of) FROM turnout_counts u WHERE u.election = t.election
                     AND u.family = 'mail-voting' AND u.geography = 'county')")
  checks <- character()
  dup <- sent |> count(as_of, county, party) |> filter(n > 1)
  checks <- c(checks, check(nrow(dup) == 0, "one GG26 sent value per snapshot x county x party"))
  check_counties(sent$county, "GG26 sent-returned")
  per_day <- sent |> count(as_of)
  checks <- c(checks, check(all(per_day$n == 96) && nrow(per_day) >= N_RECENT,
                            "every GG26 snapshot has 24 counties x DEM/REP/OTH/ALL, and there are at least three"))
  recon <- sent |>
    group_by(as_of, county) |>
    summarise(gap = sum(value[party != "ALL"]) - value[party == "ALL"], .groups = "drop")
  checks <- c(checks, check(all(recon$gap == 0), "GG26 sent DEM + REP + OTH = ALL in every snapshot and county"))
  check_counties(mv$county, "mail-voting")
  checks <- c(checks, check(nrow(mv) == 2 * 24 * 2 && !anyNA(mv$value) && all(mv$value > 0),
                            "GG22 and PG24 mail-voting sent and received at the latest as_of for 24 counties"))
  mv_as_of <- mv |> distinct(election, as_of)
  list(sent = sent, mail_voting = mv, mail_voting_as_of = mv_as_of, checks = checks,
       source_ids = ids_of(sent, mv))
}

# Project each county x party (DEM/REP/OTH) sent series to `to`: method "all" is an OLS line over
# every snapshot, "recent" an OLS line over the last N_RECENT snapshots, "latest" holds the latest
# count. Every projection is floored at the latest observed count; ALL is the sum of the three
# parties' projections.
project_sent <- function(sent, to = REQUEST_DEADLINE, method = "all") {
  if (!method %in% PROJECTIONS) stop("project_sent(): unknown method ", method)
  days <- sort(unique(as.Date(sent$as_of)))
  use <- if (method == "recent") tail(days, N_RECENT) else days
  parties <- sent |>
    filter(party %in% PARTY_GROUPS) |>
    mutate(day = as.numeric(as.Date(as_of) - to)) |>   # day = 0 at `to`
    group_by(county, party) |>
    group_modify(function(d, key) {
      latest <- d$value[which.max(d$day)]
      fit <- if (method == "latest") NULL else coef(lm(value ~ day, data = d[as.Date(d$as_of) %in% use, ]))
      raw <- if (is.null(fit)) latest else unname(fit[1])
      tibble(n_snapshots = sum(as.Date(d$as_of) %in% use), latest = latest,
             slope = if (is.null(fit)) 0 else unname(fit[2]), unfloored = raw, projected = max(raw, latest))
    }) |>
    ungroup()
  all_rows <- parties |> group_by(county) |>
    summarise(party = "ALL", n_snapshots = first(n_snapshots), latest = sum(latest), slope = sum(slope),
              unfloored = sum(unfloored), projected = sum(projected), .groups = "drop")
  bind_rows(parties, all_rows) |> mutate(method = method)
}

# ---- step 1: reference levels ------------------------------------------------------------

reference_levels <- function(inp, cells, gh) {
  K <- ncol(inp$s)
  alpha <- list()
  for (y in REFERENCE) {
    r <- inp$ref[[y]]
    for (p in PARTY_GROUPS) {
      i <- cells$party_group == p
      target <- sum(r$ballots_cast[i])
      alpha[[y]][[p]] <- vapply(seq_len(K), function(k) {
        calibrate_alpha(r$active[i], inp$s[i, k], target, inp$sigma, gh, label = paste(y, p, "draw", k))
      }, numeric(1))
    }
  }
  # Each solved alpha reproduces its target (expected ballots, by quadrature) to 1e-6 relative.
  worst <- 0
  for (y in REFERENCE) for (p in PARTY_GROUPS) {
    r <- inp$ref[[y]]
    i <- cells$party_group == p
    target <- sum(r$ballots_cast[i])
    for (k in c(1, K %/% 2, K)) {
      e <- sum(r$active[i] * expected_rate(alpha[[y]][[p]][k] + inp$s[i, k], inp$sigma, gh))
      worst <- max(worst, abs(e / target - 1))
    }
  }
  list(alpha = alpha, checks = check(worst < 1e-6,
    "calibrated alphas reproduce GG18 and GG22 party ballots (expected, by quadrature) to 1e-6"))
}

reference_table <- function(alpha, inp, cells) {
  rows <- list()
  for (y in REFERENCE) for (p in PARTY_GROUPS) {
    a <- alpha[[y]][[p]]
    i <- cells$party_group == p
    act <- sum(inp$ref[[y]]$active[i])
    bal <- sum(inp$ref[[y]]$ballots_cast[i])
    rows[[length(rows) + 1]] <- tibble(
      election = y, party_group = p,
      alpha_mean = mean(a), alpha_q05 = unname(quantile(a, 0.05)), alpha_q95 = unname(quantile(a, 0.95)),
      active = act, ballots = bal, rate_statewide = bal / act, rate_at_alpha_mean = plogis(mean(a))
    )
  }
  bind_rows(rows) |>
    mutate(across(starts_with("alpha"), ~ round(.x, 6)), across(starts_with("rate"), ~ round(.x, 6)))
}

# ---- step 2: the level rule --------------------------------------------------------------

vote_ratio <- function(x) ifelse(x >= RATIO_CUTS[2], 1L, ifelse(x <= RATIO_CUTS[1], -1L, 0L))
weight_of <- function(votes) round(pmin(W_BOUNDS[2], pmax(W_BOUNDS[1], W_START + W_STEP * votes)), 6)
party_vec <- function(df, col) df[[col]][match(PARTY_GROUPS, df$party)]

level_rule <- function(inp, projs) {
  st <- inp$indices |> filter(county == STATE_LABEL)
  st <- st[match(PARTY_GROUPS, st$party_group), ]
  if (anyNA(st$county)) stop("indices.csv lacks Maryland rows for DEM, REP and OTH")
  sent_state <- lapply(projs, function(pr) pr |> filter(party %in% PARTY_GROUPS) |> group_by(party) |>
                         summarise(projected = sum(projected), latest = sum(latest), .groups = "drop"))
  checks <- check(all(party_vec(sent_state$rule, "latest") == st$gg26_sent),
                  "projection's latest GG26 sent matches indices.csv per party")
  prim <- inp$training |> filter(election %in% c("GP22", "GP26")) |>
    group_by(election, party_group) |> summarise(b = sum(ballots_cast), a = sum(active), .groups = "drop")
  gp <- function(e, col = "b") prim[[col]][prim$election == e][match(PARTY_GROUPS, prim$party_group[prim$election == e])]
  by_party <- function(x) as.vector(tapply(x$active, factor(x$party_group, PARTY_GROUPS), sum))
  active26 <- by_party(inp$den)
  active22 <- by_party(inp$ref$GG22)

  m26 <- st$reg_momentum; m22 <- st$reg_momentum_2022; m18 <- st$reg_momentum_2018
  gap <- m18 - m22
  position <- (m26 - m22) / gap
  vote_reg <- ifelse(abs(gap) < MOMENTUM_MIN_GAP | position < POSITION_RANGE[1] | position > POSITION_RANGE[2], 0L,
                     ifelse(position >= POSITION_CUTS[2], 1L, ifelse(position <= POSITION_CUTS[1], -1L, 0L)))
  mail_index_of <- function(projected) (projected / active26) / (st$gg22_sent / active22)
  is_oth <- PARTY_GROUPS == "OTH"
  primary_count_ratio <- ifelse(is_oth, NA_real_, gp("GP26") / gp("GP22"))
  primary_index <- ifelse(is_oth, NA_real_, (gp("GP26") / gp("GP26", "a")) / (gp("GP22") / gp("GP22", "a")))
  vote_primary <- ifelse(is_oth, 0L, vote_ratio(primary_index))
  proj_rule <- party_vec(sent_state$rule, "projected")
  mail_index <- mail_index_of(proj_rule)
  votes <- vote_reg + vote_ratio(mail_index) + vote_primary
  rule <- tibble(
    party_group = PARTY_GROUPS,
    reg_momentum_2026 = m26, reg_momentum_2022 = m22, reg_momentum_2018 = m18,
    reg_position = round(position, 6), vote_reg = vote_reg,
    gg26_sent_latest = party_vec(sent_state$rule, "latest"), gg26_sent_projected = round(proj_rule, 1),
    gg22_sent_final = st$gg22_sent, active_2026 = active26, active_gg22 = active22,
    mail_count_ratio = round(proj_rule / st$gg22_sent, 6), mail_index = round(mail_index, 6), vote_mail = vote_ratio(mail_index),
    gp26_ballots = gp("GP26"), gp26_active = gp("GP26", "a"), gp22_ballots = gp("GP22"), gp22_active = gp("GP22", "a"),
    primary_count_ratio = round(primary_count_ratio, 6), primary_index = round(primary_index, 6),
    vote_primary = vote_primary,
    vote_sum = votes,
    w = weight_of(votes)
  )
  # Sensitivities: the same rule with the alternative mail projections.
  for (alt in c("latest", "recent")) {
    pa <- party_vec(sent_state[[alt]], "projected")
    mi <- mail_index_of(pa)
    v <- vote_reg + vote_ratio(mi) + vote_primary
    rule[[paste0("gg26_sent_projected_", alt)]] <- round(pa, 1)
    rule[[paste0("mail_index_", alt)]] <- round(mi, 6)
    rule[[paste0("vote_mail_", alt)]] <- vote_ratio(mi)
    rule[[paste0("w_", alt)]] <- weight_of(v)
  }
  list(rule = rule, checks = checks)
}

# ---- step 3: forecast draws --------------------------------------------------------------

centre_comp <- function(comp, den, cells) {
  w <- den$active
  m <- tapply(comp$comp * w, cells$party_group, sum) / tapply(w, cells$party_group, sum)
  comp$comp - as.vector(m[cells$party_group])
}

# Ballots per cell x draw for level matrix alpha_cell (cells x draws), offset per cell, eps.
cell_ballots <- function(active, alpha_cell, s, offset, eps) {
  active * plogis(alpha_cell + s + offset + eps)
}

alpha_cells <- function(alpha_by_party, cells) {
  do.call(rbind, lapply(cells$party_group, function(p) alpha_by_party[[p]]))
}

mix_alpha <- function(alpha, w) {
  setNames(lapply(PARTY_GROUPS, function(p) (1 - w[[p]]) * alpha$GG22[[p]] + w[[p]] * alpha$GG18[[p]]), PARTY_GROUPS)
}

summ <- function(x) c(mean = mean(x), quantile(x, Q, names = FALSE) |> setNames(names(Q)))

identity_check <- function(alpha, inp, cells, eps) {
  res <- list()
  checks <- character()
  for (y in REFERENCE) {
    r <- inp$ref[[y]]
    b <- cell_ballots(r$active, alpha_cells(alpha[[y]], cells), inp$s, 0, eps)
    for (p in PARTY_GROUPS) {
      tot <- colSums(b[cells$party_group == p, , drop = FALSE])
      act <- sum(r$ballots_cast[cells$party_group == p])
      q <- quantile(tot, c(0.05, 0.95), names = FALSE)
      res[[length(res) + 1]] <- list(election = y, party_group = p, actual = act, mean = round(mean(tot), 1),
                                     q05 = round(q[1], 1), q95 = round(q[2], 1),
                                     rel_error = round(mean(tot) / act - 1, 7))
      checks <- c(checks,
        check(abs(mean(tot) / act - 1) <= IDENTITY_TOL,
              paste("identity", y, p, "mean within 0.1% of actual"), paste(round(mean(tot)), "vs", act)),
        check(act >= q[1] && act <= q[2], paste("identity", y, p, "actual inside the 90% interval")))
    }
  }
  list(result = res, checks = checks)
}

forecast_draws <- function(alpha, rule, inp, cells, eps) {
  comp_c <- centre_comp(inp$comp, inp$den, cells)
  w <- function(col) setNames(rule[[col]], rule$party_group)
  run <- function(a, offset = comp_c) cell_ballots(inp$den$active, alpha_cells(a, cells), inp$s, offset, eps)
  central <- mix_alpha(alpha, w("w"))
  list(
    low = run(alpha$GG22),
    central = run(central),
    high = run(alpha$GG18),
    central_comp_level = run(central, inp$comp$comp),
    central_mail_latest = run(mix_alpha(alpha, w("w_latest"))),
    central_mail_recent = run(mix_alpha(alpha, w("w_recent")))
  )
}

# ---- outputs -----------------------------------------------------------------------------

county_party_table <- function(ballots, inp, cells, as_of) {
  fc <- bind_rows(lapply(MAIN_SCENARIOS, function(sc) {
    b <- ballots[[sc]]
    sb <- t(apply(b, 1, summ))
    sr <- t(apply(b / inp$den$active, 1, summ))
    tibble(scenario = sc, county = cells$county, party_group = cells$party_group, active = inp$den$active,
           ballots_mean = round(sb[, "mean"]), ballots_q05 = round(sb[, "q05"]),
           ballots_q50 = round(sb[, "q50"]), ballots_q95 = round(sb[, "q95"]),
           rate_active_mean = round(sr[, "mean"], 6), rate_active_q05 = round(sr[, "q05"], 6),
           rate_active_q95 = round(sr[, "q95"], 6))
  }))
  actual <- bind_rows(lapply(REFERENCE, function(y) {
    r <- inp$ref[[y]]
    rate <- round(r$ballots_cast / r$active, 6)
    tibble(scenario = paste0("actual_", y), county = cells$county, party_group = cells$party_group, active = r$active,
           ballots_mean = r$ballots_cast, ballots_q05 = r$ballots_cast, ballots_q50 = r$ballots_cast,
           ballots_q95 = r$ballots_cast, rate_active_mean = rate, rate_active_q05 = rate, rate_active_q95 = rate)
  }))
  bind_rows(fc, actual) |> mutate(gg26_mail_as_of = as_of)
}

county_denoms <- function(inp) {
  inp$den |>
    group_by(county) |>
    summarise(active = sum(active), eligible = sum(eligible), cvap = first(cvap), .groups = "drop") |>
    mutate(county = factor(county, CANONICAL_COUNTIES)) |> arrange(county) |>
    mutate(county = as.character(county))
}

county_sums <- function(b, cells) {
  rowsum(b, factor(cells$county, CANONICAL_COUNTIES), reorder = TRUE)[CANONICAL_COUNTIES, , drop = FALSE]
}

county_table <- function(ballots, inp, cells, as_of) {
  cd <- county_denoms(inp)
  fc <- bind_rows(lapply(MAIN_SCENARIOS, function(sc) {
    cb <- county_sums(ballots[[sc]], cells)
    f <- function(m) t(apply(m, 1, summ))
    sb <- f(cb); ra <- f(cb / cd$active); re <- f(cb / cd$eligible); rc <- f(cb / cd$cvap)
    sh <- f(sweep(cb, 2, colSums(cb), "/"))
    tibble(scenario = sc, county = cd$county, active = cd$active, eligible = cd$eligible, cvap = cd$cvap,
           ballots_mean = round(sb[, "mean"]), ballots_q05 = round(sb[, "q05"]),
           ballots_q50 = round(sb[, "q50"]), ballots_q95 = round(sb[, "q95"]),
           rate_active_mean = ra[, "mean"], rate_active_q05 = ra[, "q05"], rate_active_q95 = ra[, "q95"],
           rate_eligible_mean = re[, "mean"], rate_eligible_q05 = re[, "q05"], rate_eligible_q95 = re[, "q95"],
           rate_cvap_mean = rc[, "mean"], rate_cvap_q05 = rc[, "q05"], rate_cvap_q95 = rc[, "q95"],
           share_of_state_mean = sh[, "mean"], share_of_state_q05 = sh[, "q05"], share_of_state_q95 = sh[, "q95"])
  }))
  actual <- bind_rows(lapply(REFERENCE, function(y) {
    r <- inp$ref[[y]] |> group_by(county) |> summarise(active = sum(active), b = sum(ballots_cast), .groups = "drop")
    r <- r[match(CANONICAL_COUNTIES, r$county), ]
    rate <- r$b / r$active
    share <- r$b / sum(r$b)
    tibble(scenario = paste0("actual_", y), county = r$county, active = r$active,
           eligible = NA_real_, cvap = NA_real_,
           ballots_mean = r$b, ballots_q05 = r$b, ballots_q50 = r$b, ballots_q95 = r$b,
           rate_active_mean = rate, rate_active_q05 = rate, rate_active_q95 = rate,
           rate_eligible_mean = NA_real_, rate_eligible_q05 = NA_real_, rate_eligible_q95 = NA_real_,
           rate_cvap_mean = NA_real_, rate_cvap_q05 = NA_real_, rate_cvap_q95 = NA_real_,
           share_of_state_mean = share, share_of_state_q05 = share, share_of_state_q95 = share)
  }))
  bind_rows(fc, actual) |>
    mutate(across(starts_with("rate_") | starts_with("share_"), ~ round(.x, 6)), gg26_mail_as_of = as_of)
}

long_rows <- function(scenario, measure, x) {
  s <- summ(x)
  tibble(scenario = scenario, measure = measure, mean = s[["mean"]], q05 = s[["q05"]], q50 = s[["q50"]], q95 = s[["q95"]])
}

statewide_table <- function(ballots, inp, cells, as_of) {
  cd <- county_denoms(inp)
  den_state <- c(active = sum(cd$active), eligible = sum(cd$eligible), cvap = sum(cd$cvap))
  act_p <- vapply(PARTY_GROUPS, function(p) sum(inp$den$active[cells$party_group == p]), numeric(1))
  rows <- list()
  for (sc in SCENARIOS) {
    b <- ballots[[sc]]
    tot <- colSums(b)
    party <- lapply(setNames(PARTY_GROUPS, PARTY_GROUPS), function(p) colSums(b[cells$party_group == p, , drop = FALSE]))
    rows <- c(rows, list(
      long_rows(sc, "ballots", tot),
      long_rows(sc, "rate_active", tot / den_state[["active"]]),
      long_rows(sc, "rate_eligible", tot / den_state[["eligible"]]),
      long_rows(sc, "rate_cvap", tot / den_state[["cvap"]])
    ))
    for (p in PARTY_GROUPS) rows <- c(rows, list(long_rows(sc, paste0("ballots_", p), party[[p]])))
    for (p in PARTY_GROUPS) rows <- c(rows, list(long_rows(sc, paste0("rate_active_", p), party[[p]] / act_p[[p]])))
    for (p in PARTY_GROUPS) rows <- c(rows, list(long_rows(sc, paste0("share_", p), party[[p]] / tot)))
    if (!sc %in% c("low", "central", "high")) {
      rows <- c(rows, list(long_rows(sc, "ballots_minus_central", tot - colSums(ballots$central))))
    }
  }
  # The range to quote: low's q05 to high's q95, around the central mean and median.
  for (m in c("ballots", "rate_active", "rate_eligible", "rate_cvap")) {
    g <- function(sc) bind_rows(rows) |> filter(scenario == sc, measure == m)
    rows <- c(rows, list(tibble(scenario = "headline_range", measure = m, mean = g("central")$mean,
                                q05 = g("low")$q05, q50 = g("central")$q50, q95 = g("high")$q95)))
  }
  for (y in REFERENCE) {
    r <- inp$ref[[y]]
    tot <- sum(r$ballots_cast)
    one <- function(m, v) tibble(scenario = paste0("actual_", y), measure = m, mean = v, q05 = v, q50 = v, q95 = v)
    rows <- c(rows, list(one("ballots", tot), one("rate_active", tot / sum(r$active))))
    for (p in PARTY_GROUPS) rows <- c(rows, list(one(paste0("ballots_", p), sum(r$ballots_cast[cells$party_group == p]))))
    for (p in PARTY_GROUPS) {
      i <- cells$party_group == p
      rows <- c(rows, list(one(paste0("rate_active_", p), sum(r$ballots_cast[i]) / sum(r$active[i]))))
    }
    for (p in PARTY_GROUPS) rows <- c(rows, list(one(paste0("share_", p), sum(r$ballots_cast[cells$party_group == p]) / tot)))
  }
  bind_rows(rows) |>
    mutate(across(c(mean, q05, q50, q95), ~ ifelse(grepl("^ballots", measure), round(.x), round(.x, 6))),
           gg26_mail_as_of = as_of)
}

mode_split <- function(ballots, sw, inp, cells, proj, mail_voting, as_of) {
  # County ballots: the central county means, rounded by largest remainder so they sum to the
  # statewide central total in forecast_statewide.csv.
  state_total <- sw$mean[sw$scenario == "central" & sw$measure == "ballots"]
  county_mean <- rowMeans(county_sums(ballots$central, cells))
  central <- tibble(county = CANONICAL_COUNTIES, ballots_mean = split_integer(state_total, county_mean))
  rr <- mail_voting |>
    select(election, county, measure, value) |>
    tidyr::pivot_wider(names_from = measure, values_from = value) |>
    group_by(county) |>
    summarise(return_rate = mean(received / sent), n = n(), .groups = "drop")
  if (!all(rr$n == 2)) stop("return rate needs GG22 and PG24 for every county")
  g22 <- inp$ref$GG22 |> group_by(county) |>
    summarise(across(c(ballots_cast, election_day, early_voting, vote_by_mail, provisional), sum), .groups = "drop")
  g22 <- bind_rows(g22, g22 |> summarise(county = STATE_LABEL, across(-county, sum))) |>
    mutate(prov_share = provisional / ballots_cast, early_ratio = early_voting / (early_voting + election_day),
           gg22_share_mail = round(vote_by_mail / ballots_cast, 6), gg22_share_early = round(early_voting / ballots_cast, 6),
           gg22_share_election_day = round(election_day / ballots_cast, 6),
           gg22_share_provisional = round(provisional / ballots_cast, 6))
  ps <- proj |> filter(party == "ALL") |> select(county, projected_sent = projected)
  out <- central |>
    left_join(ps, by = "county") |> left_join(select(rr, county, return_rate), by = "county") |>
    left_join(select(g22, county, prov_share, early_ratio), by = "county") |>
    mutate(
      mail_raw = projected_sent * return_rate,
      mail_capped = mail_raw > MAIL_CAP * ballots_mean,
      mail = round(pmin(mail_raw, MAIL_CAP * ballots_mean)),
      provisional = round(prov_share * ballots_mean),
      early = round((ballots_mean - mail - provisional) * early_ratio),
      election_day = ballots_mean - mail - provisional - early
    )
  if (anyNA(out)) stop("mode split has missing inputs")
  state <- out |> summarise(county = STATE_LABEL, ballots_mean = sum(ballots_mean), projected_sent = sum(projected_sent),
                            mail_raw = sum(mail_raw), mail_capped = any(mail_capped), mail = sum(mail),
                            provisional = sum(provisional), early = sum(early), election_day = sum(election_day)) |>
    mutate(return_rate = mail_raw / projected_sent)
  bind_rows(out, state) |>
    left_join(select(g22, county, starts_with("gg22_share_")), by = "county") |>
    transmute(county, ballots_mean, projected_sent = round(projected_sent, 1), return_rate = round(return_rate, 6),
              mail, early, election_day, provisional,
              share_mail = round(mail / ballots_mean, 6), share_early = round(early / ballots_mean, 6),
              share_election_day = round(election_day / ballots_mean, 6),
              share_provisional = round(provisional / ballots_mean, 6),
              gg22_share_mail, gg22_share_early, gg22_share_election_day, gg22_share_provisional,
              mail_capped, gg26_mail_as_of = as_of)
}

# ---- checks on outputs -------------------------------------------------------------------

output_checks <- function(cp, ct, sw, ms, alpha, ballots, cells) {
  out <- character()
  fc_ct <- ct |> filter(!scenario %in% ACTUAL_SCENARIOS)
  out <- c(out, check(!anyNA(cp) && !anyNA(fc_ct) && !anyNA(sw) && !anyNA(ms),
                      "no NA in any output (actual-year eligible and CVAP columns of forecast_county excepted)"))
  agg <- cp |> group_by(scenario, county) |> summarise(b = sum(ballots_mean), .groups = "drop") |>
    inner_join(select(ct, scenario, county, ballots_mean), by = c("scenario", "county"))
  out <- c(out, check(nrow(agg) == 24 * 6 && all(abs(agg$b - agg$ballots_mean) <= 3),
                      "party ballots_mean sum to county within 1 ballot per cell (forecast and actual rows)"))
  st <- ct |> group_by(scenario) |> summarise(b = sum(ballots_mean), .groups = "drop") |>
    inner_join(sw |> filter(measure == "ballots") |> select(scenario, mean), by = "scenario")
  out <- c(out, check(nrow(st) == 6 && all(abs(st$b - st$mean) <= 72),
                      "county ballots_mean sum to statewide within 1 ballot per cell"))
  stp <- cp |> group_by(scenario, party_group) |> summarise(b = sum(ballots_mean), .groups = "drop") |>
    inner_join(sw |> filter(grepl("^ballots_(DEM|REP|OTH)$", measure)) |>
                 transmute(scenario, party_group = sub("ballots_", "", measure), mean), by = c("scenario", "party_group"))
  out <- c(out, check(nrow(stp) == 18 && all(abs(stp$b - stp$mean) <= 24),
                      "party cells sum to statewide party ballots within 1 ballot per cell"))
  shares <- sw |> filter(grepl("^share_", measure)) |> group_by(scenario) |> summarise(t = sum(mean), .groups = "drop")
  out <- c(out, check(all(abs(shares$t - 1) < 1e-5), "statewide party shares sum to 1"))
  rates <- c(unlist(select(cp, starts_with("rate_"))), na.omit(unlist(select(ct, starts_with("rate_")))),
             sw$mean[grepl("^rate_", sw$measure)], sw$q05[grepl("^rate_", sw$measure)], sw$q95[grepl("^rate_", sw$measure)])
  out <- c(out, check(all(rates > 0 & rates < 1), "every rate in (0, 1)"))
  ms_c <- ms |> filter(county != STATE_LABEL)
  ms_s <- ms |> filter(county == STATE_LABEL)
  out <- c(out, check(all(ms_c$mail + ms_c$early + ms_c$election_day + ms_c$provisional == ms_c$ballots_mean) &&
                        all(select(ms, mail, early, election_day, provisional) >= 0),
                      "modes sum to ballots in every county and are non-negative"))
  out <- c(out, check(ms_s$ballots_mean == sw$mean[sw$scenario == "central" & sw$measure == "ballots"],
                      "mode_split Maryland ballots equal the forecast_statewide central total"))
  ct_c <- ct |> filter(scenario == "central")
  out <- c(out, check(all(abs(ms_c$ballots_mean - ct_c$ballots_mean[match(ms_c$county, ct_c$county)]) <= 1),
                      "mode_split county ballots within 1 of forecast_county central means"))
  # low <= central <= high per party, unless a party's GG18 level is below its GG22 level.
  order_notes <- list()
  for (p in PARTY_GROUPS) {
    m <- sapply(c("low", "central", "high"), function(sc) mean(colSums(ballots[[sc]][cells$party_group == p, , drop = FALSE])))
    if (mean(alpha$GG18[[p]]) < mean(alpha$GG22[[p]])) {
      order_notes[[p]] <- paste0(p, ": GG18 level below GG22 level; low/high refer to reference years (GG22/GG18)")
      message("NOTE ", order_notes[[p]])
    } else {
      out <- c(out, check(m[["low"]] <= m[["central"]] && m[["central"]] <= m[["high"]],
                          paste("low <= central <= high statewide mean for", p)))
    }
  }
  list(checks = out, order_notes = order_notes)
}

# ---- main --------------------------------------------------------------------------------

SOURCES <- list(
  structure_draws = file.path(OUT_DIR, "structure_draws.csv.gz"),
  fit_summary = file.path(OUT_DIR, "fit_summary.json"),
  composition_factors = file.path(OUT_DIR, "composition_factors.csv"),
  training = file.path(INPUTS_DIR, "training.csv"),
  denominators = file.path(INPUTS_DIR, "denominators_gg26.csv"),
  indices = file.path(INPUTS_DIR, "indices.csv"),
  mail = "skipjack.db turnout_counts: GG26 family 'sent-returned' county ALL measure sent, every daily as_of; GG22 and PG24 family 'mail-voting' county ALL sent and received at the latest as_of"
)

main <- function() {
  t0 <- proc.time()[["elapsed"]]
  set.seed(SEED)
  cells <- cell_frame()
  gh <- gauss_hermite_normal(N_NODES)
  inp <- read_inputs(cells)
  mail <- read_mail()
  as_of <- max(mail$sent$as_of)
  projs <- lapply(PROJECTIONS, function(m) project_sent(mail$sent, REQUEST_DEADLINE, m))
  proj <- projs$rule
  ols_all <- proj |> filter(party == "ALL") |>
    left_join(mail$sent |> filter(party == "ALL") |> mutate(day = as.numeric(as.Date(as_of) - REQUEST_DEADLINE)) |>
                group_by(county) |> summarise(direct = unname(coef(lm(value ~ day))[1]), .groups = "drop"), by = "county")
  checks <- c(inp$checks, mail$checks,
              "gauss_hermite_normal(20) integrates Z^2 under N(0, 1) to 1 within 1e-10 (checked when model.R loads)",
              check(all(abs(ols_all$unfloored - ols_all$direct) < 1e-6),
                    "OLS projections of DEM + REP + OTH sum to the OLS projection of ALL per county"))
  floors <- bind_rows(projs) |> filter(party != "ALL", unfloored < latest) |> transmute(method, county, party)

  # eps: N(0, sigma_obs^2) per cell per draw, drawn once and shared by every scenario. Antithetic:
  # draws 1-500 are fresh and draws 501-1000 reuse their negation (see EPS_NOTE).
  K <- ncol(inp$s)
  if (K %% 2 != 0) stop("antithetic eps needs an even number of draws")
  half <- matrix(rnorm(nrow(cells) * K / 2, 0, inp$sigma), nrow = nrow(cells))
  eps <- cbind(half, -half)

  ref <- reference_levels(inp, cells, gh)
  alpha <- ref$alpha
  checks <- c(checks, ref$checks)
  ref_tab <- reference_table(alpha, inp, cells)

  lr <- level_rule(inp, projs)
  rule <- lr$rule
  checks <- c(checks, lr$checks)
  cat("\nLevel rule:\n"); print(as.data.frame(rule), row.names = FALSE)

  ident <- identity_check(alpha, inp, cells, eps)
  checks <- c(checks, ident$checks)

  ballots <- forecast_draws(alpha, rule, inp, cells, eps)
  cp <- county_party_table(ballots, inp, cells, as_of)
  ct <- county_table(ballots, inp, cells, as_of)
  sw <- statewide_table(ballots, inp, cells, as_of)
  ms <- mode_split(ballots, sw, inp, cells, proj, mail$mail_voting, as_of)
  oc <- output_checks(cp, ct, sw, ms, alpha, ballots, cells)
  checks <- c(checks, oc$checks)

  diffs <- sw |> filter(measure == "ballots_minus_central") |> select(scenario, mean, q05, q95)
  centring_means <- tapply(inp$comp$comp * inp$den$active, cells$party_group, sum) / tapply(inp$den$active, cells$party_group, sum)
  text_tab <- tibble(rule = paste(LEVEL_RULE, collapse = " "), mail_note = MAIL_NOTE, interval_note = INTERVAL_NOTE,
                     comp_note = paste(unlist(COMP_NOTES), collapse = " "))

  # Write.
  common <- list(level_rule = LEVEL_RULE, mail_note = MAIL_NOTE, composition = COMP_NOTES, eps = EPS_NOTE,
                 interval_note = INTERVAL_NOTE, mail_voting_note = MAIL_VOTING_NOTE,
                 mail_voting_as_of = mail$mail_voting_as_of, seed = SEED, draws = K, sigma_obs = inp$sigma,
                 quadrature = paste(N_NODES, "Gauss-Hermite nodes (Golub-Welsch, model.R gauss_hermite_normal)"),
                 gg26_mail_as_of = as_of, state_label = "county = Maryland marks statewide rows")
  write_out <- function(df, name, description, extra = list()) {
    f <- file.path(OUT_DIR, name)
    write_input_csv(df, f)
    write_provenance(f, description, sources = SOURCES, generated_by = SCRIPT, source_ids = mail$source_ids,
                     checks = checks, extra = c(common, extra))
  }
  write_out(ref_tab, "reference_levels.csv",
            "Calibrated reference levels alpha*_{Y,p} per draw for GG18 and GG22 (logit scale), summarised over the 1,000 draws: each draw's alpha reproduces the year's actual party ballots with its own active registration and structure draw, eps ~ N(0, sigma_obs^2) integrated by quadrature. rate_statewide is ballots / active, which every draw reproduces; rate_at_alpha_mean is invlogit(alpha_mean), a cell with s = 0 and no eps.")
  write_out(rule, "level_rule.csv",
            "The 2026 level rule: per party, the three index values and votes (+1 2018-like, -1 2022-like) and the central weight w on alpha*_GG18, with sensitivity columns for the alternative mail projections (_latest: the latest count held; _recent: OLS over the last three snapshots).",
            list(primary_caveat = PRIMARY_CAVEAT,
                 oth_primary = "OTH's primary_index and primary_count_ratio are empty and its vote is fixed at 0: unaffiliated voters cannot vote in Maryland's partisan primaries, and training.csv's GP22 OTH active counts only voters eligible for some contest, so the ratio is meaningless.",
                 gg22_sent = paste("gg22_sent_final is indices.csv gg22_sent, from the", GG22_SENT_AS_OF, "sent-returned file"),
                 reg_position_note = "positions in [1, 1.5] count as 2018-like (+1) and in [-0.5, 0] as 2022-like (-1); outside [-0.5, 1.5] the vote is 0 (extrapolation beyond both analogues)",
                 per_registrant_note = "mail_index and primary_index are per-registrant ratios (they vote); mail_count_ratio and primary_count_ratio are the raw-count ratios, shown for comparison. active_2026 is denominators_gg26.csv active, active_gg22 and gp*_active are training.csv active.",
                 projection_floors = if (nrow(floors)) floors else "the latest-count floor binds for no county x party under any method"))
  write_out(text_tab, "level_rule_text.csv",
            "The level rule, mail note, interval note and composition note as text, one row, so a report can read them from CSV.")
  write_out(cp, "forecast_county_party.csv",
            "GG26 forecast ballots per county x party group and scenario (low = GG22 level, high = GG18 level, central = level rule, central_comp_level = central with uncentred comp), summaries over 1,000 draws, plus the certified GG18 and GG22 figures (actual_ rows). active is projected GG26 active registration (the election's own active on actual_ rows).",
            list(actual_rows = ACTUAL_ROWS_NOTE, identity_check = ident$result))
  write_out(ct, "forecast_county.csv",
            "GG26 forecast ballots per county and scenario, with rates on projected active registration, on active + inactive (eligible) and on county CVAP, and the county's share of statewide ballots; summaries over 1,000 draws (quantiles are per column and do not sum), plus the certified GG18 and GG22 figures (actual_ rows).",
            list(actual_rows = ACTUAL_ROWS_NOTE))
  write_out(sw, "forecast_statewide.csv",
            "GG26 statewide forecast per scenario in long form (scenario, measure, mean, q05, q50, q95): ballots, rates on active/eligible/CVAP, party ballots, party rates on active and party shares of ballots. Sensitivities central_comp_level, central_mail_latest and central_mail_recent add ballots_minus_central (paired per draw). headline_range is the range to quote: low's q05 and high's q95 as its q05/q95, with central's mean and median. actual_GG18 and actual_GG22 rows give the certified figures (all four columns equal); rates on eligible and CVAP are not given for them.",
            list(format_note = "Long rather than wide so that the actual rows, which have no eligible or CVAP denominator, need no NA.",
                 differences_from_central = diffs,
                 mail_sensitivity_w = rule |> select(party_group, w, w_latest, w_recent),
                 comp_weighted_mean_removed = as.list(round(centring_means, 6)),
                 identity_check = ident$result,
                 order_notes = oc$order_notes))
  capped <- ms$county[ms$mail_capped & ms$county != STATE_LABEL]
  write_out(ms, "mode_split.csv",
            "Central-scenario GG26 ballots by mode per county and statewide (county = Maryland): mail = projected GG26 sent (ALL parties, the rule's projection) x return rate (mean of GG22 and PG24 received / sent, mail-voting at its latest as_of), capped at 0.9 x ballots; provisional at GG22's share of the county's ballots; early vs election day by GG22's early / (early + election day). County ballots are the central means rounded by largest remainder to sum to the statewide central total (each within 1 of forecast_county.csv). gg22_share_* are GG22's actual mode shares.",
            list(capped_counties = as.list(capped),
                 sent_projection = proj |> filter(party == "ALL") |>
                   transmute(county, latest, slope_per_day = round(slope, 3), projected = round(projected, 1))))

  runtime <- round(proc.time()[["elapsed"]] - t0, 1)
  cat("\nReference levels:\n"); print(as.data.frame(ref_tab), row.names = FALSE)
  cat("\nIdentity check:\n"); print(bind_rows(ident$result) |> as.data.frame(), row.names = FALSE)
  cat("\nStatewide ballots:\n"); print(as.data.frame(filter(sw, measure %in% c("ballots", "rate_active", "ballots_minus_central"))), row.names = FALSE)
  cen <- ct |> filter(scenario == "central") |> arrange(desc(rate_active_mean))
  cat("\nCentral county rates, highest:\n"); print(as.data.frame(head(select(cen, county, ballots_mean, rate_active_mean), 5)), row.names = FALSE)
  cat("\nCentral county rates, lowest:\n"); print(as.data.frame(tail(select(cen, county, ballots_mean, rate_active_mean), 5)), row.names = FALSE)
  cat("\nMode split statewide:\n"); print(as.data.frame(filter(ms, county == STATE_LABEL)), row.names = FALSE)
  cat("Capped counties:", if (length(capped)) paste(capped, collapse = ", ") else "none", "\n")
  cat("Projection floors binding:", nrow(floors), "\n")
  cat("Runtime:", runtime, "s\n")
}

main()
