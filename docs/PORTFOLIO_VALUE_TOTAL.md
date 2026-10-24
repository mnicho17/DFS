# Reuse fixed scenario-value totals within selection

The greedy selector repeatedly scores candidates against changing portfolio
coverage counts. Each candidate's positive scenario-value total is unchanged
within that selection call. Cache that sum after its first use, in the private
candidate metadata, rather than scanning the same values at every selection step.

The changing numerator still visits the original scenario dictionary in its
original order. The denominator uses the original max/float/sum arithmetic.
There is no reciprocal multiplication, reassociation or approximation. Totals
are computed lazily: unused scenario evidence retains its previous handling.
Each selection call builds fresh metadata from copied evidence; nothing is
attached to input lineups or persisted across calls.

This shared selector serves Classic and Showdown. Ranking formulas, tie order,
coverage weights, exposure caps, uniqueness, retained-entry handling, repair,
refinement, cancellation checkpoints and time budgets are unchanged. SIM,
projections, ownership and saved-evidence/history policies are unchanged. Faster
wall-limited work may finish additional work under the existing budgets; this
does not promise identical portfolios from differently stopped searches.

Five regressions cover frozen original ordered portfolios and structured reports
from main 708aa15, including Classic SIM, ordinary selection, refinement,
retained rows, empty and zero/negative values. They also check unchanged inputs,
fresh calculations on later calls and lazy handling of unused evidence.
Structured report list order is normalized and derived display text/timing are
excluded from hashes; portfolio order and all structured values remain checked.
Existing recovery, cancellation and explicit-rule tests apply.

Run `python scripts/benchmark_portfolio_value_total.py --output fresh.json`.
The bounded synthetic benchmark uses 600 distinct fixture rosters scored over
500 scenarios against 100 opponents, then selects 100 entries. Three alternating
before/after trials compare merged main 708aa15 against the current selector.
Median selection time was 3.5019 to 3.0624 seconds (12.5% less); all six ordered
portfolio and structured report hashes matched. Generation and simulation are
excluded from the timed section. There is no refinement in this benchmark.
Local load affects timing; this is not a real-slate or complete-build speedup
claim. No private user inputs are included. Raw receipts are in
[the benchmark file](benchmarks/portfolio-value-total-2026-10-24.json).
