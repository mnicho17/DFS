# Independent later-game zero-score/team/DST prototype check

November 7, 2026. Diagnostic only: no app code, forecasts, production SIM, optimizer, ownership, ranking, generation or defaults changed. The [aggregate receipt](zero-score-independent-2026-11-07.json) contains the declared plan, verification hashes, exclusions, game-level metrics and mean checks. Raw snapshots, player rows and local paths remain private.

## Declared window and admission

The earlier prototype development evaluated games through October 11, and its October 12-16 holdout admitted no games. Before reading new player-outcome values, this pass declared October 17-November 6 (evaluation-after October 16, completed-before November 7). These date/game groups do not occur in either earlier prototype report, including their exclusions. It is a retrospective later-game check, not a blind or prospective experiment: contest constructions had already been reviewed separately.

The unchanged auditor used its five fixed profiles, three existing seeds (90210, 481516, 730001), 2,000 scenarios per seed and fixed reconciliation bounds. One representative result per game is chosen by known construction coverage before outcome reads. Original source/salary association, exact frozen pregame forecast/ownership/QB-eligibility evidence, player/role scores and cross-export conflicts retain their existing gates. There is no current-depth substitution or reconstruction of forecasts from results. Captain observations are not counted twice.

Seven later game groups were candidates. Two were admitted: October 18 DAL@GB and November 1 PHI@WAS. Five were excluded by the existing outcome CSV row limit, with no partial evaluation: October 19 WAS@SF, October 22 NE@CHI, October 26 DAL@PHI, October 29 CAR@GB and November 2 CHI@SEA. The limit was not raised or bypassed. These are capacity exclusions, not evidence of infeasible event priors or bad forecasts.

The admitted sample contains 26 rotation-player/game outcomes: ten zeros, fifteen strictly positive scores and one negative score; 42 modeled offensive and four DST game/player observations are also recorded. Games are the evaluation units; neither players nor simulated draws establish independent sample sizes. Both games passed the fixed-bound reconciliation. No infeasibility conclusion is available for the five excluded groups.

The original auditor completed its final source, snapshot and scoring-model rechecks. An outer read-only supervisor additionally verified the complete historical index inventory and original database size/mtime before and after; they were unchanged. It enforced a 1,800-second deadline and withheld incomplete runs. No mappings, qualification rules or original history files changed.

## Mean preservation and invariants

Both reconciled models returned optimal fixed-bound plans, without per-player point normalizers: all normalizers are 1. The largest analytic offensive forecast-mean residual is 0.000000327 points, below the unchanged 0.00002 tolerance. All 88 profile-specific Monte Carlo mean checks are within five standard errors; these include repeated offensive observations with and without DST linkage, not 88 independent players. There are 42 modeled offensive and four DST game/player means.

These checks establish consistency with the declared modeled forecast means, not forecast accuracy or complete DraftKings event scoring. DST remains a fractional forecast-centered opposing-QB-event residual. Kicker nonlinear feedback is outside the offensive mean-equality guarantee. Preservation of a supplied mean also does not imply equality to the baseline simulator's sampled mean.

For admitted games, the unchanged auditor checks frozen-input equality, parent-RNG alignment, unchanged protected-player draws and exact offensive-draw parity between reconciled-events and reconciled-DST profiles. Independent aggregation also verifies equality of all retained offensive outcome-metric rows between the two reconciled modes. No constraints, priors or forecasts were adjusted after reading outcomes.

## Results

Lower CRPS and zero Brier are better. Player scores average within each game, then games equally. Strict-positive CRPS uses actual score greater than zero, not all nonzero scores.

| Diagnostic profile | Rotation CRPS | Zero Brier | Strict-positive CRPS | DST CRPS | Simulated QB/opposing-DST correlation |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 2.378 | 0.385 | 3.396 | 2.966 | -0.256 |
| Sampled hurdle | 2.238 | 0.140 | 3.383 | 2.966 | -0.256 |
| Linked completion | 2.213 | 0.141 | 3.329 | 2.966 | -0.010 |
| Reconciled events | 2.114 | 0.110 | 3.288 | 2.966 | -0.006 |
| Reconciled events + DST link | 2.114 | 0.110 | 3.288 | 3.330 | -0.068 |

Reconciled events improve zero-outcome CRPS from 1.007 to 0.504 and increase average predicted zero probability from 0% to 36.42%; the observed zero proportion is 10/26, or 38.46%. This agreement in two games is not proof of general calibration.

Strict-positive CRPS improves modestly in both admitted games relative to baseline: 4.886 to 4.729 for DAL@GB and 1.906 to 1.846 for PHI@WAS. However, positive-outcome bias becomes more negative overall, from -3.163 to -3.534 points. Improved zero scores do not resolve the positive-outcome issue.

Opposing-DST linkage restores a negative simulated correlation relative to reconciled events alone, but weakly compared with baseline. DST CRPS worsens in both games: 2.093 to 2.332 and 3.839 to 4.327. Four defensive observations cannot qualify a production coupling choice, and negative model correlation alone is not evidence of correct joint behavior. These are imposed model correlations, not empirical within-game correlation estimates from two real games.

Team dispersion also changes substantially: mean offensive standard deviation falls from 31.46 to 21.98 and mean offensive p90 from 125.71 to 110.26. QB/receiver model correlation rises from 0.539 to 0.852. Those are unresolved tail/dependence changes, not established improvements. Average kicker mean changes slightly (7.8595 to 7.8638), but there is no general kicker-mean preservation guarantee.

## Outcome-sign reporting defect

The existing audit tool labels its nonzero group `actual_positive`, using actual != 0. That includes one negative score in this sample and reports sixteen nonzero observations. The original report is retained unchanged and its checksum is recorded. This receipt recomputes actual == 0, actual > 0 and actual < 0 groups from the same per-player metric rows, without changing any draws, fitting or excluding the negative observation from overall scores. Unsupported groups remain unavailable, not zero.

The strict-positive sample is fifteen, and the single negative outcome remains explicit. The tool's legacy subgroup labeling needs a reporting-only correction before reuse; this documentation PR does not ship that fix. Future reviews must not interpret its old nonzero subgroup as positive-only performance.

## Validation and remaining work

All 53 existing isolated prototype/source/outcome regressions passed in 51.193 seconds with no unexpected network attempts. Five private strict-sign aggregation checks passed; they cover negative-versus-positive separation, unknown outcomes, unavailable groups, equal-game weighting and nonmutation. Independent checks passed for report/plan hashes, date independence, fixed bounds/optimality, exact retained offensive parity, category counts, game-average arithmetic and analytic/Monte Carlo means. No Python files changed in the repository, so no complete runtime suite was rerun. Runtime/package validation remains the inherited #200 result: all 2,512 Windows tests and executable/guide/checksum/artifact checks passed; there is no new package or release for this report.

The first later-game pass is complete. Production acceptance is not: coverage is only two of seven candidates; large-export admission must be addressed through a separately reviewed bounded reader path, preserving full-source identity/conflict checks and cancellation. The legacy outcome-sign label also needs correction. Positive bias, tails and opposing-DST calibration remain unresolved. Do not enable the prototype or tune bounds/coefficient defaults from this sample.
