# Exact opponent roster score reuse

Showdown's compiled-scoring path builds a per-bank index of exact ordered
Captain/FLEX player keys. Each scenario scores each distinct ordered roster once,
then expands the scores to every original occurrence before sorting and ranking.
Repeated opponents retain full weight, field size, duplicate counts, ties and
payout settlement. Captain assignment and FLEX order are part of the key; sorted
athlete signatures cannot be used because they discard arithmetic order.

All score arithmetic still uses the original compiled roster scorer. No values
are rounded, aggregated or reused across scenarios/banks. Outcome draws, seeds,
field sampling, bootstrap draws, candidate scoring, distribution capture, payout
rules, ranking, selection and cancellation checkpoints are unchanged. The cache
continues to identify the full original bank and stores full expanded scores;
all existing verification, coverage and storage gates apply.

The standalone precompiled_rosters default remains false. Existing callers that
enable compiled scoring use the indexed banks; this change does not enable it at
new call sites. In the current main branch that includes the held-out Showdown
ranking audit; primary Deep call-site enablement is a separate review change.
Classic is unchanged. Faster wall-limited execution can complete more scenarios
within the same budget; differently stopped samples need not select the same output.

Seven new regressions cover multiplicity and reduced scorer calls, Captain swaps,
FLEX order/exact arithmetic, primary/audit seeds, qualified cache replay without
opponent scoring, cancellation parity, and full tie-split payout weighting.

Three alternating synthetic full simulation trials (1,000 scenarios, 5,000
opponents, 50 candidates, seed 90210) measured median seconds before/after:

| Mode | Before | After |
| --- | ---: | ---: |
| Uncached | 8.990 | 8.344 |
| Cold cache | 10.402 | 9.565 |
| Warm replay | 5.161 | 5.087 |

Uncached/cold execution used about 7%/8% less time; warm replay was essentially
unchanged. All 18 float-hex metric/hit/scenario-value digests matched, and all warm
runs replayed 1,000 frames with new outcome generation prohibited. Setup, cache
verification and candidate scoring are included; independent audit and portfolio
selection are excluded. These synthetic fallback-ownership timings depend on local
hardware/load and fixed within-mode uncached/cold/warm ordering. They do not
establish whole-build or real-slate speedups. Raw [receipts](benchmarks/opponent-score-reuse-2026-10-24.json)
also include the smaller constant-outcome score-only prototype.
