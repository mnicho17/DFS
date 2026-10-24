# Showdown candidate metric updates

Each scenario now resolves its field size, top-two-percent threshold and winning
score once. The original top-one/top-five/win hit comparisons update their three
sets directly, without rebuilding a tuple and zip iterator for every candidate.

Percentiles retain the same integer-index arithmetic and division. Comparisons
remain inclusive at the original boundaries. Mean scores, hits, top-two/cash/bust
counts, return proxies, exact payout values, tie splitting and scenario values
are unchanged. There is no reciprocal multiplication or floating-point reordering.
Original reference and compiled scoring paths both use the same metric update.

Field generation, outcome draws, seeds, candidate/portfolio ranking, selection,
scenarios, time budgets, cancellation checkpoints, cache identity algorithm and
full saved-evidence validation remain unchanged. The original code fingerprint
continues to gate reuse after a source change. Classic is unchanged. Faster
wall-limited execution may complete more scenarios under unchanged budgets;
differently stopped samples need not select identical portfolios.

Seven regressions freeze exact original metric/hit/scenario-value digests from
merged main 08a44ad for primary/audit seeds, small fields, all contest objectives
and cancellation. Separate manual cases check zero/negative-score ties and
qualified replay without new outcomes or opponent scoring. All cases preserve
input records. Existing payout, cache-integrity and real Deep parity tests apply.

Run the bounded developer benchmark from a Git checkout:

```
python scripts/benchmark_candidate_metrics.py --output candidate-metrics.json
```

Three alternating before/after trials used 2,000 candidate occurrences from 50
legal synthetic fixture rosters, 1,000 scenarios and 100 opponents. Median metric
update time was 4.726 to 3.800 seconds (19.6% less); complete load-test simulation
was 6.478 to 5.509 seconds (15.0% less). All float-hex result digests matched.
These repeated-roster samples stress the metric loop; they do not assess candidate
quality, real-slate speedups or complete builds. The baseline file runs against
shared current dependencies to isolate this execution change. Local load and
hardware affect timing. Generation, selection and independent audit are excluded.
Raw [receipts](benchmarks/candidate-metrics-2026-10-24.json) include all trials.
