# Refinement counter arithmetic

The scenario-swap kernel repeatedly called int and max for each scenario in every considered swap. Replace the conversions with Boolean integer arithmetic and the clamps with comparisons. Keep the original set-union traversal, subtraction, summation and weighting order. No search limits, constraints, cancellation checks, candidate eligibility or refinement budgets change.

Three alternating paired synthetic selection trials against combined PR #192 (89bb1c0) measured median 11.271s before and 5.569s after (50.6% lower). Each trial used the same 100 simulated candidates, 20 requested entries, 2,500 scenarios and 1,000 field lineups. All six results matched the exact selected-order/report digest, eight refinement attempts and six swaps. Candidate inputs, hit sets, metrics and scenario values were unchanged. This is selection-only local timing, not a full-build or 150-entry speed claim.

Validation: 74 focused portfolio/refinement/recovery tests passed in 14.101s, zero unexpected network attempts. A new deterministic edge-case check extracts the actual nested kernel and compares exact float hex values with the original expression for 200 count/hit cases and four weights, including negative/zero/missing counts, overlapping/new hits and empty sets. Existing fixed baseline receipts cover Classic, retained rosters and empty scenario hits. Full suite and Windows packaging for this follow-up remain pending.


## Full 150-entry Showdown check

Four baseline/native cold/warm builds completed all 150 entries from one frozen 996-candidate library. All stage scores, ordered lineups, portfolio evidence and independent audit diagnostics matched exactly. Screening (250 scenarios) and validation (2,500) replayed completely on warm builds; audits completed 2,000 fresh scenarios each time. Original player inputs and library bytes remained unchanged.

The single preliminary warm pair measured 80.403s on #192 versus 55.202s on #194 (31.3% lower). Selection measured 64.180s versus 39.642s. Cold wall times were 83.128s versus 59.512s. These are synthetic single-pair timings, not stable medians or production-slate guarantees, and must not be combined with timings on earlier differently sized candidate banks.

The measurement ran the benchmark source from #193 commit 3d357b1, overriding its baseline to #192 commit 89bb1c0 and measuring native committed #194 head 79ad4d6. See adjacent refinement-150-showdown-2026-11-06.json for source hashes and full receipts. No measured application files changed during the run. #194 now targets main because the workflow does not run for PRs targeting the #192 branch; main remains unchanged.
