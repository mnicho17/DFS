# Bounded Showdown construction-state reuse

The fast Showdown sampler repeatedly reaches the same ordered partial roster,
particularly when a narrow Captain pool and prior candidates concentrate draws.
Reuse the computed feasible-player list and its raw scores for that prefix.
Each key preserves Captain identity and FLEX selection order. Salary arithmetic,
pool order, raw float scores, weight calculation and random draws retain their
original execution order. Duplicate and final legality checks still run.

The cache belongs to one optimizer call and holds at most 512 entries. It is
cleared immediately after every accepted output, before the next sampling step
can observe changed Captain/FLEX exposure counts. It retains no state across
calls, slates or workers. It neither changes the number of trials nor skips RNG
draws. Cancellation checkpoints, failure bounds, candidate-batch sizes, styles,
seeds, locks/fades, salary bounds and downstream validation remain unchanged.
The small exact-CBC path and Classic generation are unchanged. Saved libraries,
scenario-cache storage/compatibility, SIM, forecasts and ranking formulas are
unchanged. Ordinary source/model fingerprints still gate prepared reuse.
Faster timed searches may complete additional work within unchanged deadlines;
this does not promise identical portfolios from differently stopped builds.

Five regressions freeze ordered FLEX output, every weighted pool/draw weight and
final RNG state from main 4eab227. They cover all five styles, changing exposure
counts, locks/fades, an excluded complete roster, duplicate exhaustion,
cancellation and consecutive calls. Existing primary-worker and safety tests
remain applicable. Private saved-slate diagnostic data stays outside Git.

Run `python scripts/benchmark_showdown_construction_cache.py --output fresh.json`.
The bounded synthetic benchmark uses the existing 36-player fixture, five Captain
locks, a $47,500 minimum, seed 35764, twelve requested rows, and three alternating
before/after trials per style. The second case excludes 100 prior original
fixture outputs. Each trial checks ordered FLEX outputs, final RNG state and
unchanged player inputs. It times only the fast constructor, not a complete build.

| Style | Prior outputs excluded | Before (s) | After (s) | Less time |
| --- | --- | --- | --- | --- |
| Strategic | 0 | 0.5328 | 0.4238 | 20.5% |
| Strategic | 100 | 0.7666 | 0.5679 | 25.9% |
| Balanced | 0 | 0.5188 | 0.4050 | 21.9% |
| Balanced | 100 | 0.7624 | 0.5507 | 27.8% |
| Randomized | 0 | 0.4580 | 0.3450 | 24.7% |
| Randomized | 100 | 0.6958 | 0.4901 | 29.6% |

All 36 trials retained exact ordered outputs and RNG state. Hardware/load affect
timing; these measurements do not establish real-slate or whole-build speedups,
search completeness or lineup quality. Raw [receipts](benchmarks/showdown-construction-cache-2026-10-24.json)
contain the synthetic results. No private salary/result inputs are included.
