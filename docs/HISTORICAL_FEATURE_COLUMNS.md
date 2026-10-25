# Historical proposal feature columns

This follow-up builds on PR #158 (`9de7307`); the benchmark baseline already
includes its per-label multiplier reuse.

Store construction labels in five category columns while proposals are created,
rather than retain a five-key dictionary for every proposal. Each column preserves
proposal order. Fitting still visits categories in `CATEGORIES` order, accumulates
weighted counts in the same order, uses the same factors and normalizes the same
weight list. Missing-target reporting reads the same labels from these columns.

The data is private to one sampler call and bounded by the existing proposal
limits. Proposal generation, salary and identity validation, RNG calls, fitting
passes, cancellation checkpoints, historical qualification and acceptance guards
are unchanged. No model defaults, forecasts, ownership targets, lineup-generation
policy, ranking or artifact-qualification policy changes.

## Verification

The six inherited regressions retain exact pre-change final float-hex sampling
weights, ordered proposals/opponents, RNG states and diagnostics. Three additional
regressions cover fractional salary labels, one requested opponent backed by many
proposals, and default/specialist/default calls proving pool-specific data stays
local. All 44 focused historical-field and qualification tests passed.

## Reproduce the observations

```powershell
python scripts/benchmark_historical_fitting_factors.py --baseline 9de7307cc7e80ecd3cd8473b50f3e5b622777d70 --allocation-count 2000 --output <new-receipt.json>
```

The original benchmark default remains unchanged for reproducing PR #158. The
new baseline option isolates this follow-up. No private salary or history data
is used. All twelve timed observations matched ordered opponents, diagnostics
and final RNG state. Inputs remained unchanged, with no unexpected network calls.

| Synthetic fixture | Opponents | Before median (s) | After median (s) |
| --- | ---: | ---: | ---: |
| default | 2,000 | 2.774 | 2.446 |
| specialists | 5,000 | 7.389 | 6.306 |

These are sampler-only timings; earlier prototypes showed variable, smaller
timing differences. Hardware/load affect results. Memory reduction is the primary
claim, not a promised whole-build speedup.

The allocation observation is separate from timing: Python `tracemalloc` measures
proposal construction, stopping at the existing pre-fit cancellation checkpoint.
Both versions construct 40,000 proposals and return identical checkpoint diagnostics.
It does not measure a complete sampler, complete field setup, process RSS or app
peak memory. A paired observation after garbage collection measured:

| Proposal-bank allocation peak | Before | After |
| --- | ---: | ---: |
| Bytes | 50,797,332 | 44,842,076 |
| MiB | 48.44 | 42.76 |

This is 11.7% less Python allocation peak in that observation.
The benchmark observes the existing callback; no production instrumentation or
new cancellation checkpoints are added. Raw receipts:
[benchmark](benchmarks/historical-feature-columns-2026-10-25.json).
