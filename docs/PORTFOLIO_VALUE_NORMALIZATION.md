# Normalize private scenario values once per selection call

After the first use of a candidate's scenario evidence, the selector now retains
its original positive-value normalization in the private copied dictionary. It
keeps every original key and its insertion order, including zero and negative
entries. The changing coverage numerator still divides each normalized value by
the current scenario count in the original order. The total uses the same ordered
positive floats and summation. No reciprocal multiplication or reassociation is
introduced. No input lineup or persisted evidence is modified.

Normalization remains lazy, so unused evidence keeps its original handling and
invalid used values still fail. Each call builds fresh private metadata. The
shared optimization applies to Classic and Showdown without changing ranking
formulas, tie order, coverage weights, caps, uniqueness, retained entries,
repair/refinement, cancellation checkpoints or budgets. SIM, projections,
ownership, cache identity algorithms and saved-history/evidence gates remain
unchanged. Ordinary source fingerprints continue to gate reuse after edits.
Faster wall-limited work may finish more work under the same budgets; differently
stopped runs are not promised identical final portfolios.

Two new regression methods cover six frozen boundary cases in both formats,
including numeric strings, signed zero, negative evidence, small/large finite
values, retained entries and refinement. They compare every observed greedy
ranking key bit-for-bit against main ae1698e, the ordered portfolio and structured
report, and unchanged original evidence. Invalid used evidence must still fail
without input mutation. Existing lazy-evidence, fresh-call, recovery, cancellation
and primary-pipeline parity tests remain applicable.

Run `python scripts/benchmark_portfolio_value_normalization.py --output fresh.json`.
The bounded benchmark uses 600 distinct synthetic fixture rosters, 100 opponents,
seed 90210, and three alternating before/after trials per workload. Only selection
is timed; generation, SIM and refinement are excluded. Each trial checks the
ordered portfolio, structured report and unchanged selector inputs. Report list
order is normalized; derived text/timing are excluded from report hashes.

| Scenarios | Entries | Before (s) | After (s) | Less time |
| --- | --- | --- | --- | --- |
| 100 | 20 | 0.3108 | 0.2992 | 3.8% |
| 100 | 150 | 1.8042 | 1.5864 | 12.1% |
| 500 | 20 | 0.6977 | 0.6060 | 13.1% |
| 500 | 150 | 3.9605 | 3.2824 | 17.1% |

All 24 trials matched their workload's original ordered portfolio and structured
report hash. Local load affects timing, especially short selections. These are
synthetic selection measurements, not real-slate, complete-build or model-quality
claims. Raw [receipts](benchmarks/portfolio-value-normalization-2026-10-24.json)
contain all trials. No private user inputs are included.
