# Expanded seven-game zero-score diagnostic follow-up

November 7, 2026. Research only; no production/default changes. The [anonymous aggregate](zero-score-expanded-2026-11-07.json) retains the declared plan, source verification, original/new group summaries, per-game metrics, fixed reader limits and sensitivities. Player identities, raw scores, original snapshots and paths remain private.

## Coverage and preservation

The new bounded research-only reader admits all seven candidate games in the same October 17-November 6 retrospective window: the original October 18 DAL@GB and November 1 PHI@WAS, plus October 19 WAS@SF, October 22 NE@CHI, October 26 DAL@PHI, October 29 CAR@GB and November 2 CHI@SEA. There are no remaining candidate or source exclusions in this window. This is expanded retrospective coverage, not a new blind/prospective holdout or certification of complete standings.

The declared plan was saved before reading the five newly admitted games' player outcomes. Five profiles, three seeds, 2,000 scenarios per seed, representative-source selection and reconciliation bounds remain unchanged. The original two games reproduce exactly, including their snapshots, model receipts, player metrics and interactions. The original report is retained with its original hash. The scoring-model stamp and complete historical inventory agree with the prior study. The original database size and modification time are unchanged, and all original source/salary/pregame/cross-export/final checks pass. The supervised process completed in 456.797 seconds within its fixed 1,800-second deadline.

There are 87 rotation-player/game outcomes: 29 zero, 57 strictly positive and one negative. The five newly admitted games contribute 61 (19 zero, 42 positive, no negative). There are 143 modeled offensive and 14 DST game/player observations. Games are the aggregation units; repeated players, draws and profile checks do not create independent games.

All fixed-bound reconciliation plans are optimal. All normalizers remain 1. All 300 profile-specific Monte Carlo forecast-mean checks pass the existing five-standard-error bound, and every analytic mean residual passes the unchanged 0.00002-point tolerance (maximum 0.000000327). These are 143 offensive checks without DST linkage and 157 offensive/DST checks with linkage, not 300 independent players. Exact offense parity with/without DST linkage, frozen inputs, protected draws and parent-RNG checks remain enforced.

## Outcomes

Lower CRPS/Brier is better. Scores average within each game, then games equally. Positive means actual > 0; negative scores remain separate and retained in overall scores.

| Profile | Rotation CRPS | Zero Brier | Strict-positive CRPS | DST CRPS |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 2.039 | 0.329 | 2.474 | 2.502 |
| Sampled hurdle | 1.940 | 0.137 | 2.496 | 2.502 |
| Linked completion | 1.889 | 0.138 | 2.408 | 2.502 |
| Reconciled events | 1.886 | 0.136 | 2.441 | 2.502 |
| Reconciled events + DST | 1.886 | 0.136 | 2.441 | 2.802 |

Reconciled events improve overall rotation CRPS in all seven games. Average predicted zero probability rises from 0% to 30.03%, versus the equal-game observed zero proportion of 32.93%. The pooled count is 29/87; it uses a different weighting. This small-sample agreement is not general calibration evidence.

Positive-only CRPS improves in four games and worsens in three. On the five newly admitted games alone it is essentially unchanged: 2.10545 to 2.10213, improving in two and worsening in three; the omit-one-game gap spans -0.01547 to +0.01124. Overall positive bias becomes more negative (-1.186 to -1.542 points); the new-game subgroup also becomes more negative (-0.395 to -0.746). The positive-bias shift is downward in every game, though a downward shift is not necessarily worse for each individual game's starting bias. No stronger positive-outcome improvement is established.

DST CRPS worsens in six of seven games, from 2.502 to 2.802 overall. The five new games show the same aggregate harm (2.316 to 2.590), worsening in four of five. Its all-game omit-one gap remains positive (+0.225 to +0.400), as does the new-game range (+0.156 to +0.419). These are descriptive sensitivity checks, not confidence intervals or proof of causality. DST CRPS is a marginal score: it evaluates the DST outcome distribution, not its joint dependence with opposing offense. Its deterioration does not by itself identify a correlation defect; the changed DST marginal and the coupling must be isolated before tuning either.

Negative simulated QB/opposing-DST correlation is partially restored relative to reconciled events (-0.002 to -0.102), but remains weaker than baseline (-0.258). These are imposed model correlations, not empirical correlation estimates from observed game outcomes. Model offense standard deviation also falls from 31.416 to 20.530 and p90 from 126.446 to 109.321. Mean preservation does not validate those changes in dependence or tails. The DST residual remains fractional, forecast-centered linkage rather than complete DraftKings defensive event scoring. Kicker nonlinear effects remain outside the offensive mean guarantee.

## Validation and next work

All 53 focused prototype/source/outcome tests pass, including 13 new bounded-reader regressions. Independent receipt checks pass for complete status/hash, unchanged historical inventory, exact original-game reproduction, unchanged scoring stamp/profile options, optimal fixed bounds, exact retained offense parity, strict sign groups and game-average arithmetic/privacy. Six separate receipt checks also pass for complete admission/parity, count partitions, game weighting, sensitivities, mean checks/limits and privacy. The saved private aggregator reproduces the complete anonymous receipt exactly, including sensitivities. The complete isolated suite ran 2,531 tests in 912.848 seconds: 2,530 passed, one skipped because symlink creation is unavailable; no failures or unexpected network attempts. Scoring-model and production source files remain unchanged. Windows CI/package validation has not run for this stacked head; no executable or release is published.

The coverage blocker for this declared window is resolved. The model-acceptance blocker is not: zero handling improves while positive bias, team tails and DST calibration remain unresolved. Next diagnostics should isolate the offense dependence/tail change and forecast-centered opposing-DST residual before considering a new coupling. Do not tune coefficients on these results or enable the prototype from this retrospective sample. Any future alternative needs a declared comparison, preserved means and a new validation window.
