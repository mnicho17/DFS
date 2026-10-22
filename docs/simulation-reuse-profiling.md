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

No cache files were removed and no storage limits were raised. The bounded cache lifecycle described below addresses this pressure while protecting active readers and writers. Candidate libraries, history, saved evidence and backups must remain outside that policy. The new breakdown will also isolate remaining selection and scoring costs on production builds.

## Bounded scenario-cache recycling

Before a new recording, reserve the maximum 512 MiB artifact allowance plus 8 MiB metadata headroom inside the existing 2 GiB store bound. Retire only enough eligible artifacts to obtain that space, in oldest modification-time order with filename as a deterministic tie-breaker. Successful fully verified replay refreshes recency; files are not retired simply because an app starts or a directory is scanned.

Retirement is limited to direct, ordinary, singly linked files inside the resolved scenario-cache directory. Completed files must have the 64-hex-key filename and exact disposable scenario metadata/tables. Unknown databases, candidate libraries, history, receipts and evidence are preserved and still count toward the storage allowance. New-format partials need the application marker, owned filename, and at least one hour of age. Unmarked legacy partials are preserved.

Nonblocking operating-system leases protect readers/writers across app processes and release automatically after a crash. One writer reservation is held until publication or abort; other builds can compute without recording. A bounded 256-slot artifact-lock set can conservatively preserve extra files on a hash collision. SQLite exclusive-lock probes also preserve caches being read by older app versions. There is no interprocess wait in the build path. Scan at most 512 directory entries and retire at most 64 artifacts within a one-second maintenance budget, with bounded SQLite metadata queries. OS filesystem calls may themselves take longer on an unresponsive drive.

If files are busy, unknown, inaccessible, or insufficient, computation proceeds without a saved cache and reports the reason. Publication checks the store allowance again. The build report captures retired artifact count, reclaimed bytes, busy artifacts preserved, and remaining capacity. Cache identity, frame integrity, completion coverage and independent audits still decide reuse; recycling never authorizes an artifact.

Tests use disposable folders only. No live cache or user history was cleaned during implementation. After installation, a cache miss automatically attempts bounded recycling before recording; an already matching valid cache replays directly.
