# Historical-field fitting factors

The historical construction sampler applies twelve fitting passes to freshly
generated legal proposals. Within one category update, the observed weighted
totals and target proportions stay fixed. Calculate the original clamped
multiplier once per observed label, then multiply each proposal weight in the
same order. Keep weighted-count accumulation, scale summation and normalization
in their original order. The factor dictionary lasts for one category update.

Proposal generation, draw weights, RNG calls, selected opponent order,
diagnostics, cancellation checkpoints, proposal bounds and all historical
qualification and calibration acceptance guards remain unchanged. Independent
screening, validation and ranking-audit fields still use their original seeds
and fresh sampling. No forecast, ownership target, optimizer or SIM default
changes. Existing artifact qualification checks remain authoritative.

## Verification

Six regressions freeze evidence from merged main `b93853f`: exact final sampling
weights as float hex digests, ordered proposal and output identities, RNG states
and diagnostics. Cover default and specialist pools, zero target proportions,
a custom salary cap, ownership draw multipliers, unavailable targets, cancellation
during proposals and fitting, and an empty request. Inputs remain unchanged.

## Reproducible synthetic benchmark

Run from the repository root:

```powershell
python scripts/benchmark_historical_fitting_factors.py --output <new-receipt.json>
```

The script loads the trusted baseline source from Git as UTF-8. It runs three
alternating before/after pairs per fixture with seed 90211, checks exact ordered
rosters, diagnostics and final RNG state, and rejects mutated inputs or unexpected
network attempts. The 36-player synthetic fixture contains no private history.

| Fixture | Opponents | Before median (s) | After median (s) | Less time |
| --- | ---: | ---: | ---: | ---: |
| default | 2,000 | 3.584 | 2.493 | 30.4% |
| specialists | 5,000 | 9.450 | 6.533 | 30.9% |

All twelve observations matched ordered outputs, diagnostics and final RNG
state. These measure the construction sampler alone, not complete field setup
or whole builds. Timing depends on hardware and load. Raw synthetic receipts:
[benchmark](benchmarks/historical-fitting-factors-2026-10-25.json).

A separate private frozen-input prototype comparison measured full historical
field setup for 10,000 opponents in three alternating pairs. Median time was
67.910 seconds before and 50.940 seconds after (25.0% less). Ordered opponents
and all diagnostics matched in every pair, and captured inputs stayed unchanged.
The prototype uses the same factor calculation implemented here. This is a
field-only diagnostic, not reconstruction of the original timed build, evidence
of historical predictive quality, or a whole-build speedup estimate. Private
salary records, history profiles and roster identities remain outside Git.
