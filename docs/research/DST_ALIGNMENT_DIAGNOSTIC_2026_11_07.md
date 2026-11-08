# DST distribution versus alignment diagnostic — November 7, 2026

Research only; no forecasts, model draws, production coupling or defaults changed. The [declared plan](DST_ALIGNMENT_DIAGNOSTIC_PLAN.md) precedes this comparison, and the [anonymous receipt](dst-alignment-2026-11-07.json) records support, each fixed event, per-game/per-seed diagnostics and sensitivities. These seven games were already studied; this is a mechanical replay, not a new validation window.

## Checks and scope

All seven games and fourteen exact opposing QB/DST pairs qualify. Both exact selected-source base-player actuals are available for every pair; no cross-export score filling occurs. The two examined profiles are unchanged reconciled events (with inherited production DST draws) and reconciled events plus the existing forecast-centered DST residual.

For each profile, a fixed nonzero cyclic shift changes DST/offense scenario alignment within each seed. The same shift is used for both DSTs, retaining their block relationship. All 28 pair/profile marginal comparisons and 84 seed-specific comparisons preserve exact values, means, standard deviations, biases, CRPS and p10/p90. QB/offense samples remain unchanged. The shift does not guarantee independence or zero correlation, and no offset is selected from outcomes.

Every previous seven-game result, model receipt, profile summary and forecast check reproduces exactly after removing only the newly added diagnostic field. All 300 original profile-specific mean checks still pass; the maximum analytic residual remains 0.000000327 points. The scoring-model stamp, original database size/mtime and complete historical inventory are unchanged. All original source/salary/pregame, bounded reader, conflict and final source/snapshot/model checks pass. The supervised replay completed in 665.359 seconds within its fixed 1,800-second deadline. Original reports remain unchanged.

## Distribution and alignment are different questions

Values average across the two pairs within each game, then games equally. SD is the average per-DST simulated SD, not the SD of a pooled mixture. Correlations describe simulated samples, not empirical real-game correlation estimates. Both DST distributions are paired with the same reconciled QB draws. The inherited row therefore measures that diagnostic pairing; its near-zero correlation does not describe the full production baseline coupling.

| DST distribution | Alignment | Mean DST SD | DST CRPS | QB/DST correlation | Mean of three joint-event Briers |
| --- | --- | ---: | ---: | ---: | ---: |
| Inherited DST | Original | 4.457 | 2.502 | -0.0024 | 0.089320 |
| Inherited DST | Fixed shift | 4.457 | 2.502 | +0.0017 | 0.089705 |
| Existing residual | Original | 1.730 | 2.802 | -0.1023 | 0.099946 |
| Existing residual | Fixed shift | 1.730 | 2.802 | +0.0011 | 0.100414 |

Marginal CRPS is exactly unchanged by alignment, as required. Its earlier deterioration therefore cannot be used as a correlation score. The existing residual substantially narrows the DST distribution: average p10/p90 changes from -0.723/10.933 to 2.773/7.542, while average mean changes only from 5.144 to 5.159. This identifies a substantial spread/tail change to investigate; it does not by itself prove that all inherited variance is correct or identify a replacement distribution.

The existing constructor replaces inherited DST draws with a forecast-centered opposing-QB signal plus bounded uniform noise. Its noise cap and floor-bound coefficient are fixed in the existing prototype; they are not tuned in this diagnostic. The comparison does not change them.

## Joint-event support and limits

The fixed events use inclusive DK-point boundaries. Lower Brier is better; these three events are targeted diagnostics, not a complete joint-distribution score.

| Event | Observed true pairs / 14 | Games with event / 7 | Residual original Brier | Residual shifted Brier |
| --- | ---: | ---: | ---: | ---: |
| QB <= 10, DST >= 8 | 1 | 1 | 0.071961 | 0.071779 |
| QB >= 20, DST >= 8 | 0 | 0 | 0.008468 | 0.010204 |
| QB >= 20, DST <= 4 | 3 | 2 | 0.219408 | 0.219261 |

The residual's original alignment has a 0.000469 lower mean Brier than its shifted control, lower in four games and higher in three; its omit-one-game difference remains positive when expressed as shifted minus original (+0.000130 to +0.000650). That small average difference is driven by the high-QB/high-DST event, which never occurred among these fourteen observations. Both events that did occur have slightly worse average scores under the residual's original alignment. This is not persuasive validation of the coupling.

Inherited DST original versus shifted differences are also small (mean Brier gap +0.000386 for shifted minus original). Observed support is sparse, pairs within a game are related, all seven games have already been examined, and the fixed shift is only one mechanical control. Omit-one ranges are descriptive sensitivity checks, not confidence intervals. Do not tune offsets, thresholds or coupling coefficients from these results.

## Validation and next work

Thirteen new regressions cover exact marginals and shared block permutations, unchanged inputs/RNG, deterministic offsets independent of actuals, inclusive event/CRPS oracles, constant-series unavailability, exact draw caps, invalid/partial data, cancellation and CLI mode prerequisites. All 51 focused diagnostic/reader/prototype/outcome tests passed in 21.863 seconds with no unexpected network attempts. Six independent receipt checks also passed for source hashes/exact prior results, forecast means, fixed offsets, event support/scoring, game weighting/sensitivity and privacy. The complete isolated suite ran 2,544 tests in 1198.358 seconds: 2,543 passed, one skipped because symlink creation is unavailable; no failures or unexpected network attempts. No Python source changed after these tests began. Windows CI/package status will be reported for the new PR head; earlier package results do not validate it.

The first distribution/alignment isolation pass is complete; prototype acceptance remains blocked. Next define a forecast-mean-preserving DST marginal comparison with explicit spread/tail assumptions before adjusting dependence strength. Preserve legal score bounds and offense invariants. Then test any proposed alternative on a genuinely new validation window. Positive-outcome bias and team-offense dispersion/tails remain separate unfinished work. No production defaults or grouping rules change.
