# Simulation reuse profiling

Copy Last Build Report now records wall-time components for screening, primary validation and the independent ranking audit. Each phase reports calls, completed/reused scenarios and candidate evaluations, with separate setup, cache verification, outcome draws, opponent scoring, frame I/O, candidate scoring/metrics, finalization and result assembly. Other/progress accounts for the remaining simulation wall time. Phase sums are inside SIM calls, not additional time to add to the existing Generate/SIM/Select totals.

Measurements are observational. RNG consumption, arithmetic order, cancellation checks, cache identity/integrity gates, saved evidence, ranking audits and portfolio rules remain unchanged. Worker-local sessions prevent cross-build accumulation. Captured reports preserve their own measurements; older reports do not acquire invented timings. Profiling delivery failure does not reject a completed simulation.

## Controlled comparison

Run in the repository with the development environment:

```
python scripts/benchmark_reuse_stages.py --kind both --candidates 80 --field 200 --scenarios 2500 --output benchmark.json
```

The runner uses disposable app data and one fixed candidate bank per format. It compares cache disabled, first recording, and exact-input replay through complete Deep workers. All scored metrics, hit sets, scenario payouts, candidate identities and final selections must match. Distribution timestamps and additive timing/cache receipts are excluded from equality. Independent audits still compute fresh scenarios. Candidate library creation is outside the measured build. These are synthetic fixtures, not production latency predictions.

Measured locally on 2026-10-22:

| Format | Fresh | First recording | Warm replay |
| --- | ---: | ---: | ---: |
| Classic | 14.70 s | 15.87 s | 8.71 s |
| Showdown | 8.53 s | 9.74 s | 4.72 s |

Both formats passed exact result comparisons. Warm replay skipped all screening/validation outcome draws and opponent scoring; verification, candidate metrics and independent audits remained. One sequence per format is diagnostic evidence, not a statistical performance guarantee.

## Production report investigation

Build 117340b26f90c0af2bc514386e7ee2308184fea3093e1e75ae3be2c9886b5a58 completed 150 Showdown lineups in 477.17 seconds: candidate discovery/load 0.53 seconds, generation 0.29 seconds, SIM 358.36 seconds and selection 118.52 seconds. Scenario replay was blocked by the cache storage limit. A read-only inspection found 27 cache SQLite files totaling 2,043.73 MiB, leaving less than the minimum 8 MiB recording allowance under the 2 GiB bound.

No cache files were removed and no storage limits were raised. Bounded retirement of disposable scenario artifacts needs a separate lifecycle fix, including protection for active readers/writers. Candidate libraries, history, saved evidence and backups must remain outside that policy. The new breakdown will also isolate remaining selection and scoring costs on production builds.
