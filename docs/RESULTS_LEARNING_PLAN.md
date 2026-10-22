# Results & Learning: staged implementation

## Current development stage: contest objectives and QB early-exit coverage

Queued by the user on October 4, 2026, after the v1.24.0 release. This is
an accepted staged plan. The user subsequently prioritized single-entry contest
intent and authorized CO-02 alongside QB coverage. This branch implements an
initial review slice: exact-profile objective ranking, hard starting-QB fade
counts, occurrence/receiver-dependence reporting and paired conditional point
comparisons. It is a development preview, not a published release.

**Remaining QB stage:** optional backup allocation with fresh replacement-role
evidence and validated conditional playing-time/opportunity assumptions; RB
receiving and K/DST effects; supported conditional contest-money comparisons.
The initial point review has an explicitly defined portfolio point threshold,
not a paid cutoff. No automatic injury probabilities or allocations are added.
The requirements below remain the acceptance plan for these later slices.
RL-07B generation explanation and DC-02B measured overnight improvements remain
queued; unrelated backlog work is excluded from this branch.

**Goal:** help review a 150-entry portfolio's dependence on each starting QB
and deliberately build coverage for an early exit. Showdown is the first build
workflow; Classic initially emphasizes diversification among starting QBs.
Reuse the existing Portfolio Risk concentration calculations where suitable,
while keeping its historical read-only evidence contract intact.

- Show exact counts and percentages for both starting QBs, QB A only, QB B
  only and neither, with separate Captain/FLEX exposure. Include shared
  dependence on each team's receivers; a QB fade can still depend on his
  passing offense. Count repeated entries as portfolio occurrences.
- Compare the normal build with explicit "QB A exits early" and "QB B exits
  early" assumptions on the same frozen inputs and paired scenario draws.
  Disclose exit-time/remaining-production assumptions and point changes,
  including affected receivers. Define any "competitive entries" threshold
  visibly. These are conditional stress cases, not estimated injury
  probabilities or guaranteed cash coverage. Money metrics require an explicit
  supported contest/payout model.
- Offer opt-in coverage targets for coherent QB A fades, QB B fades and
  neither-QB constructions. Keep ordinary builds unchanged when disabled.
  Show requested versus achieved counts and any shortage; do not silently
  relax roster eligibility, salary, uniqueness, exposures, Core Plans, Captain
  safeguards, locks/fades, or retained-entry protections.
- Make a small backup-QB scenario allocation optional and user-controlled,
  with no default "optimal" percentage. Require a supplied, eligible active
  backup, explicit replacement identity and fresh role evidence. Normal
  starter/backup eligibility gates remain intact; permission to use a backup
  is scoped to the explicitly selected conditional construction.
- Before ranking backup constructions against starter fades, implement and
  validate conditional playing-time/opportunity assumptions. A backup does
  not automatically inherit the starter's projection or all lost fantasy
  points. Keep conditional values separate from baseline forecasts, preserve
  receiver/team consistency, and disclose unsupported or missing evidence.
  Default-build forecasts and historical observations must remain unchanged.

**Acceptance:** synthetic cases cover normal games, either starter leaving at
different times, missing/stale backup evidence, unavailable backups, separate
Captain/FLEX scoring, receiver dependence, repeated entries, conflicting user
rules, unmet coverage targets, retained rows, and cancellation. Verify the
disabled path against the current baseline, and verify that conditional backup
eligibility/forecasts cannot leak into ordinary builds or later slates. Run
focused and full isolated tests, review the UI and documentation, then submit
the stage for review. Historical validation across independent slates is
required before claiming improved returns or choosing automatic allocations;
one injury game and 150 correlated entries do not establish that benefit.

## Historical evidence foundation

Historical Evidence Identity is implemented against latest main as an additive
backend layer, described in [Historical Evidence Identity](HISTORICAL_IDENTITY.md).
It adds atomic reconciliation, a normalized read model, exclusive qualification
states and aggregate review-report coverage. Original observations and source
files are preserved. RL-05B adds the Historical Coverage tab, background atomic
reconciliation, explicit ambiguous snapshot resolution, and shareable coverage
counts. RL-06 adds the read-only Portfolio Risk tab with exact archive selection,
occurrence-based concentration and deterministic retained-production comparisons.
Recorded saved exports support descriptive coverage only. RL-07A adds the read-only
Hindsight tab: independently gated supplied-pool actual-point optimization and an
optional exact snapshot-local restriction comparison. Original contest membership
and submitted-build identity remain unestablished. Validation and Windows
packaging results are recorded in their review PRs. See
[Historical Coverage](HISTORICAL_COVERAGE.md).

Each new slice starts from current `main`; this supersedes the historical launcher's
`feature/configurable-deep-compute` starting point. RL-06 starts at
`a98172186871730c327b857ded8d412ab5f76c79`, with a verified 764-test baseline.
Each slice gets focused tests, isolated full regression, documentation and its own
review before integration. Existing history and strategy formulas remain unchanged
unless a later named change explicitly authorizes them. Remaining sequence after
RL-07A was RL-07B, CO-02, DC-02B; the current contest/QB stage above now takes
priority. RL-07A starts at
`c457522ae7ed3e97f79bf7714ca102e5e3fa3c53`, tree
`0bce554ccb663f1d832428d96fd3c9780d3156c8`, with a newly executed clean baseline of
838 passing tests. See [Hindsight Solver](HINDSIGHT_SOLVER.md).

1. **Opponent portfolios — integrated for v1.23.0.** Read one original NFL Classic/Showdown standings CSV. Compare usernames, observed entries, unique identities, athlete/role overlap, player/Captain variety, duplication, exposures and pair concentration. Include all entrants and exact-entry-count peers, not only winners. Share summaries by default and detailed lineups only on request. Record source hash, missing-data denominators and deduplication conflicts. No live data or database writes. See the user guide and `test_opponent_analysis.py`.
2. **Combined folder import and explicit contest data pairing — integrated for v1.23.0.** Remember results and salary folders and scan both with one button, recursively. Skip identical contents even after a move or rename; snapshot new sources without changing original files. Support standalone and embedded NFL salary tables. Automatically associate a unique candidate only with matching dates and full observed player/role coverage; otherwise let the user select the exact compatible revision and confirm a missing result date. Do not merely attach salaries to the latest imported field. Use a stable contest/result identity and immutable source hashes/revisions; validate sport, format, game/slate date, teams, roster slots, salary coverage and unambiguous player IDs/names. Show missing/conflicting matches and require explicit resolution of ambiguous candidates. Preserve the original salary, position, team and actual-score values plus derivation provenance, including separate Captain/FLEX IDs. A single salary snapshot may support several contests on the same slate, but each results contest has its own association. Renamed files can be recognized by hash; changed contents must not silently retarget an old analysis. Tests cover same-slate multiple contests, wrong games/formats, partial matches, duplicate names, moved/changed files, cancellation and idempotent explicit association. Previously stored data must not be automatically backfilled.
3. **Downside concentration and backup coverage — implemented for RL-06 review.** Portfolio Risk reads one freshly qualified generated archive or one descriptive-only saved export. Any-role/Captain/non-Captain exposure, both/A-only/B-only/neither, inclusive either, exactly one, team/QB-receiver shared dependencies and explicitly selected alternatives preserve repeated occurrences and per-metric denominators. One/two risk targets have five/25 deterministic retained-production cases, fixed complete-forecast M and separate direct-change M_delta. These are not outcome correlations, injury probabilities or simulations. Full qualified snapshot roles use report-only freshness policy v1 (aware role check at/before capture, <=24 hours before historical kickoff). Legacy exports cannot certify pregame forecasts or backup roles. No observed injury is described as predictable; no backup promotion, transferred production or build-time risk limit is added. See [Portfolio Risk](PORTFOLIO_RISK.md).
4. **Hindsight solver — RL-07A implemented; generation explanation — RL-07B future.** RL-07A distinguishes highest reported supplied entry Points, the exact optimum within a complete supplied salary revision, and an optional snapshot-local restricted optimum. Original contest-wide pool completeness and original/submitted build identity remain unverified. It requires every eligible athlete's exact actual score, preserves recorded roster eligibility and Captain identity, and validates proof and distinct ties against exhaustive fixtures. Portfolio percentages and pipeline policies are not evaluated by the single-lineup benchmark. RL-07B will address absence stage by stage: eligibility/filtering, generation, screening, validation, portfolio constraints and selection, only where original evidence supports a reason. That tracing is not implemented here. Hindsight scores never become forecasts.
5. **Overnight work effectiveness.** Instrument the existing candidate-library and scenario-preparation path before changing budgets. Measure CPU/wall time, memory, unique candidates per batch, repeated work, source/seed coverage, cache hits, independent validation disagreement and how much prepared work the next Deep build actually reuses. Benchmark identical frozen inputs and hard rules with fixed seeds and equal compute budgets. Compare candidate quality and selection stability, not CPU utilization alone. Then make one measured improvement (reuse, batching, parallelism or broader search) with cancellation, checkpoint/resume and stale-input rejection tests. Do not simply extend the run time or claim in-sample returns as predictive improvement.

**Separate optional money slice: build and tag contest payout tables.** Reuse the existing contest-profile tier editor/validation where compatible, with named templates containing currency, entry fee, field size and rank-range payouts. Tag an explicit immutable table version to each results contest, rather than to a date, salary file or generic contest name; the same slate may have different prizes. A copied template needs confirmation of its contest-specific values. Preserve a source note and original values, distinguish draft/verified assignments, and create a new revision on edits so prior reports remain reproducible. The existing simulation-profile validator assumes a positive entry fee; historical analysis must explicitly support known zero fees and unknown fees without silently substituting defaults. Validate overlapping/out-of-range tiers and distinguish unknown gaps from confirmed nonpaying ranks. Score, salary and lineup analysis works without payouts. Money reporting requires separately qualified fee, prize and tie information; rank alone never supplies a payout. Preserve reported winnings separately from any schedule-derived estimate and withhold ambiguous settlement/ROI. Include tie, partial-field, free-contest, missing-cash, currency-unit and template-revision tests. This slice does not authorize changes to Showdown EV selection or strategy formulas.

## Evidence gates

- Store original units and source identity. Keep zero distinct from unknown, Captain distinct from FLEX, and comparisons on common cohorts.
- Actual fees and payouts are required for money metrics. Do not infer them from ranks or reinterpret legacy currency as ROI percent.
- Compare future strategy proposals across independent slates and a held-out period; one injury game and many correlated entries cannot establish a successful strategy.
- Keep private usernames, original contests and local reports out of repository fixtures. Test with synthetic entrants. Run GUI tests in disposable Qt settings and storage before importing DFS; block external sources.
- Full-field coverage, complete eligible-player actuals, archived backups, original generation reasons and useful overnight speedups are evidence requirements, not completed findings.

## First-slice verification

Baseline: 387 tests passed in disposable storage/settings with external network blocked. Final local regression: 414 passed, including 27 new tests; no failures, skips or unexpected external connections. Coverage includes username identity, both NFL formats, Captain-aware overlap, exact-count peers, duplicate/conflicting entries, missing versus zero values, Points/FPTS separation, source preservation, global table sorting, export failures, cancellation and real worker-to-dialog lifecycle. The guide screenshot and changed PDF pages were visually reviewed. CI outcomes are recorded in the review PR; visible-desktop and packaged-executable runtime checks remain separate.

Large-file GUI review found repeated column resizing stalled sorting on a populated table. Table updates now batch column sizing; the visible 1,000-row sorting regression enforces a five-second limit. An offscreen production-worker check then loaded 237,812 entries in about 18 seconds, verified whole-field sorting and personal selection, and exercised cancellation with disposable settings and no network access. Later pushes on this PR address that measured review finding.

## Combined-import verification

The combined importer is a separate review slice stacked on the opponent-analysis change. Its baseline is 414 passing isolated tests. Focused cases cover both NFL formats, embedded entry templates, overlapping folders, renamed/changed sources, per-contest associations, ambiguity, date/game/role conflicts, zero versus unknown salaries, interrupted-file rollback, successful retry and real GUI worker retirement. Existing pairs are fixed; replacing a chosen revision is not provided in this slice. Full eligible-pool qualification, payout templates, injury scenarios and optimization remain later work. Final local regression: 447 passed in 214.077s, including 33 new tests; no failures, skips or unexpected external connections. A disposable-copy GUI run imported 237,812 standings entries plus 12 real salary files, found 150 personal entries and saved one salary association in 100.968s. A repeat scan skipped all 13 files in 0.172s. Original hashes stayed unchanged. Final identity qualification covered 95/95 observed player/role identities; 911 unreadable rosters remained excluded. Current CI results are recorded in the review PR; visible-desktop and packaged runtime smoke checks remain not_run.
