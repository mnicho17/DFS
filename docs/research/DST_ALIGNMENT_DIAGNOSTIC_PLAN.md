# DST alignment diagnostic plan

Declared November 7, 2026 before running this comparison. Research-only mechanical replay of the previously studied October 17-November 6 seven-game window; outcomes from this window have already been analyzed. This is not a fresh blind holdout, coefficient fit or production coupling proposal. No defaults change.

## Fixed comparison

Retain all five existing profiles, three seeds (90210, 481516, 730001), 2,000 scenarios per seed, forecast means, reconciliation bounds, source selection and evidence guards. Compare the reconciled-events profile's inherited DST distribution with the reconciled-DST profile's forecast-centered residual distribution. Their offensive draws must remain exactly identical.

For each profile, compare its original QB/DST alignment with a fixed nonzero cyclic shift of DST outcomes within each seed block. Derive each shift from SHA-256 of `dst-alignment-diagnostic-v1:<seed>`, using `1 + first-eight-digest-bytes-as-big-endian-integer % (block_size - 1)`. Apply the same shift to both DST series. This preserves each seed's exact DST values and the full two-DST block relationship, plus all unchanged offense draws. It does not guarantee empirical independence or zero correlation. Never select shifts from observed outcomes or from whichever produces a preferred correlation.

Require exact equality of marginal mean, standard deviation, bias, CRPS and p10/p90 before/after shifting, plus exact sample multisets. Report original/shifted correlation and covariance; constant-series correlation is unavailable. Report each seed separately as well as the full sample. This postprocessing consumes no simulation or global RNG and edits no samples, forecasts, model priors or protected draws.

The three fixed joint events are: QB <= 10 and DST >= 8; QB >= 20 and DST >= 8; QB >= 20 and DST <= 4 (DK points, inclusive boundaries). For each, report simulated probability, observed event and Brier `(probability - observed)^2`; also report their equal-weight average. These are exploratory targeted threshold scores, not a complete joint-distribution score or an independence test. Thresholds are fixed for this replay and must not be revised to improve its results. A production proposal requires separate prospective/unseen validation.

Use only both exact selected-source base-player actual scores on a verified opposing QB/DST pair. Missing either leaves the pair unavailable; other exports never fill selected-source outcomes. Existing source/salary association, frozen pregame identity/role evidence, cross-export conflicts, bounded full-file reads, cancellation and final source/snapshot/model checks remain enforced. Pair selection comes from the existing verified model links, not outcomes.

## Bounds and evidence

The helper accepts at most three distinct nonnegative integer seed blocks and 6,000 total draws, at least two per block. Reject incomplete/mismatched/nonfinite/bool samples, invalid actuals or unsupported budgets. Cancellation withholds the result. No source-reader limit is enlarged: research reads retain 1M records, 128 MiB/file, 60 seconds/operation and 384 MiB + 64 KiB cumulative bytes. A separate 1,800-second process deadline withholds incomplete runs.

Preserve the prior expanded report/hash. Require exact original seven-game profile/receipt/mean-check reproduction after removing only the newly added diagnostic field, unchanged scoring-model stamp and unchanged full historical inventory/database attributes. Record unavailable pairs and exclusions; never promote partial attempts. Games, not the fourteen correlated pairs or simulated draws, are the evaluation units. Report both original/inherited and residual marginal results, all three events and game sensitivity without selecting favorable scores. No joint calibration/default conclusion is justified by this retrospective control.
