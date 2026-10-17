# Structure-model helpers shared by the turnout-model scripts (02_fit.R, and the forecast).
#
# Sourced by R/bootstrap.R after helpers.R. The model is a binomial glmer of ballots cast over
# active registration per county x party group x election:
#
#   cbind(ballots_cast, active - ballots_cast) ~ 0 + ep + <county covariates> + <random effects>
#
# where ep is the election x party cell (a free statewide level for each). The midterm
# structure effect s_cp of a county x party cell is its covariate part plus the conditional
# modes of (1 | county:party_group) and of (1 | county:election_type) at midterm_general, on the
# logit scale: how the cell turns out relative to the statewide party level in a midterm.

suppressPackageStartupMessages(library(lme4))

MODEL_RESPONSE <- "cbind(ballots_cast, active - ballots_cast)"
MODEL_CONTROL <- glmerControl(optimizer = "bobyqa", optCtrl = list(maxfun = 2e5))
MIDTERM_TYPE <- "midterm_general"
# Random-effect terms that enter s_cp. (1 | county) would too, but the fitted model drops it.
S_TERMS <- c("county:party_group", "county:election_type")

# Key of a county x party cell, the same form lme4 uses for county:party_group levels.
cell_key <- function(county, party_group) paste(county, party_group, sep = ":")

# Name of the fixed effect for an election x party level.
alpha_name <- function(election, party_group) paste0("ep", election, ":", party_group)

# Model frame: canonical factor levels, ep = the election x party cells present, one obs id per row.
model_frame <- function(rows) {
  rows |>
    mutate(
      county = factor(county, CANONICAL_COUNTIES),
      party_group = factor(party_group, PARTY_GROUPS),
      election = factor(election, ELECTION_LEVELS),
      election_type = factor(election_type),
      ep = interaction(election, party_group, sep = ":", drop = TRUE, lex.order = TRUE),
      obs = factor(seq_len(n()))
    )
}

build_formula <- function(covariates, re_terms) {
  as.formula(paste(MODEL_RESPONSE, "~ 0 + ep +", paste(c(covariates, re_terms), collapse = " + ")))
}

formula_text <- function(f) paste(trimws(deparse(f, width.cutoff = 500L)), collapse = " ")

# Fit a binomial glmer, collecting convergence messages and warnings instead of printing them.
# The control is named as the global MODEL_CONTROL in the call, which bootMer re-evaluates.
fit_glmer <- function(f, data) {
  warns <- character()
  fit <- withCallingHandlers(
    glmer(f, data = data, family = binomial, control = MODEL_CONTROL),
    warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") }
  )
  conv <- unlist(fit@optinfo$conv$lme4$messages)
  list(fit = fit, messages = unique(c(conv, warns)), singular = isSingular(fit))
}

variance_components <- function(fit) {
  as.data.frame(VarCorr(fit)) |>
    transmute(term = grp, variance = round(vcov, 8), sd = round(sdcor, 8))
}

# Point estimate of s_cp for `cells` (columns county, party_group and the covariates).
structure_point <- function(fit, cells, covariates) {
  beta <- fixef(fit)
  re <- ranef(fit)
  if (!all(S_TERMS %in% names(re))) stop("fit lacks the random-effect terms of s_cp: ", paste(S_TERMS, collapse = ", "))
  cov_part <- as.vector(as.matrix(cells[, covariates]) %*% beta[covariates])
  u_cp <- re[["county:party_group"]][cell_key(cells$county, cells$party_group), 1]
  u_cm <- re[["county:election_type"]][cell_key(cells$county, MIDTERM_TYPE), 1]
  if (anyNA(u_cp) || anyNA(u_cm)) stop("missing conditional modes for some county x party cells")
  cov_part + u_cp + u_cm
}

# Approximate posterior draws: fixed effects from MASS::mvrnorm(fixef, vcov), the s_cp random
# effects from independent normals at their conditional modes and conditional variances. The
# alpha draws and the u draws are independent, so alpha + s carries the uncertainty of the
# population mean and is not for use uncalibrated. Returns beta (draws x coefficients) and s
# (cells x draws). The RNG order (mvrnorm, then county:party, then county:midterm) is fixed.
make_draws <- function(fit, cells, covariates, n_draws, seed) {
  set.seed(seed)
  beta <- fixef(fit)
  beta_draws <- MASS::mvrnorm(n_draws, beta, as.matrix(vcov(fit)))
  re_cv <- ranef(fit, condVar = TRUE)
  re_draw <- function(term, levels) {
    modes <- re_cv[[term]][levels, 1]
    sds <- sqrt(attr(re_cv[[term]], "postVar")[1, 1, match(levels, rownames(re_cv[[term]]))])
    if (anyNA(modes) || anyNA(sds)) stop("missing conditional variance for ", term)
    matrix(rnorm(length(levels) * n_draws, modes, sds), nrow = length(levels))  # levels x draws
  }
  counties <- unique(as.character(cells$county))
  u_cp <- re_draw("county:party_group", cell_key(cells$county, cells$party_group))
  u_cm <- re_draw("county:election_type", cell_key(counties, MIDTERM_TYPE))
  cov_part <- as.matrix(cells[, covariates]) %*% t(beta_draws[, covariates, drop = FALSE])  # cells x draws
  list(beta = beta_draws, s = cov_part + u_cp + u_cm[match(cells$county, counties), , drop = FALSE])
}

# Calibrate a set of cells to known party totals: for each party, the scalar alpha with
# sum(active * invlogit(alpha + s)) equal to that party's target, then each cell's predicted
# ballots. `election` labels the error messages.
calibrate <- function(active, s, party, target_by_party, election = "?") {
  if (anyNA(active) || anyNA(s) || any(!is.finite(s))) stop("calibrate(", election, "): active and s must be finite")
  pred <- rep(NA_real_, length(active))
  for (p in names(target_by_party)) {
    target <- target_by_party[[p]]
    i <- party == p
    if (is.na(target)) stop("calibrate(", election, ", ", p, "): target is NA")
    if (!any(i)) stop("calibrate(", election, ", ", p, "): no cells for this party")
    if (!(target > 0 && target < sum(active[i]))) {
      stop("calibrate(", election, ", ", p, "): target ", target, " is not between 0 and active ", sum(active[i]))
    }
    g <- function(a) sum(active[i] * plogis(a + s[i])) - target
    a <- tryCatch(uniroot(g, c(-15, 15), tol = 1e-12)$root,
                  error = function(e) stop("calibrate(", election, ", ", p, "): uniroot failed: ", conditionMessage(e)))
    pred[i] <- active[i] * plogis(a + s[i])
  }
  if (anyNA(pred)) stop("calibrate(", election, "): cells whose party has no target")
  pred
}
