# Active completion checklist

Agreed November 7, 2026: work through this list, mark completed evidence, then reassess priorities. Do not expand the list with new features merely because they are available. A research prototype is not a production default. Completion of tests or packaging does not establish a visible packaged-app check.

## 1. App validation and performance delivery — in progress

- [x] Combined performance implementation: PR #197, head 6c7a04e, includes earlier runtime slices.
- [x] Complete Windows regression: 2,483 tests passed, no skips/failures or unexpected network attempts.
- [x] Executable, guide, checksum verification and artifact upload passed; no release published.
- [x] Saved-real-input 150-entry check: exact scores, scenario evidence, lineup order and audit results; 141.675s versus 132.985s in one cold pair against #196.
- [x] Isolated CSV, player-table batching/tag refresh and task-dashboard/attention checks passed with compatibility tests: final 65-test run in 13.296s, zero unexpected network attempts. Source/offscreen automation, not packaged visual review.
- [ ] Visible packaged-app interaction and layout check.
- [ ] User-authorized integration and publication, if requested. PRs remain unmerged.

The real-input comparison used five Captain locks, 63 groups, minimum unique 2, 10,000 validation scenarios, a 4,000-entry field, a frozen disposable library and the full 1,200-row validation/audit shortlist. Actual preparation/screening counts and single-pair timing limits are recorded in PR #197. This does not establish general production latency or old-library reuse.

## 2. Preparation churn — bounded fix locally verified, delivery pending

- [x] Reproduce the existing model gate rejecting an older real candidate library; preserve the original files and qualification policy.
- [x] Identify benchmark code as an unnecessary part of the conservative model fingerprint; audit current production imports.
- [x] Local regression verifies audited unused benchmark edits preserve identity and permit compatible roster resume/load; seven new cases passed within the 65-test acceptance run.
- [x] Local regression verifies runtime/dependency/unknown-file/imported-benchmark changes remain strict, including dynamic/relative/transitive imports and syntax errors. Existing legacy/frozen/mismatched-model gates passed.
- [x] Full validation on runtime commit d50ef1f: local 2,490 tests in 792.114s (2,489 passed, one symlink-creation skip); Windows all 2,490 passed in 663.522s with no skips. Executable, guide, checksum and artifact checks passed. [Run](https://github.com/mnicho17/DFS/actions/runs/42375109998).
- [x] Remaining boundaries documented in MODEL_COMPATIBILITY.md: runtime, dependency, Python/platform, unknown-source and mismatched-stamp changes remain strict; old artifacts are not migrated. This bounded fix does not eliminate legitimate refreshes.
- [ ] Review/integrate the bounded fix and verify first post-update preparation; original library stamps may require a one-time refresh.

The bounded change excludes only an explicit audited benchmark list when not reachable from strict source. Static/transitive imports reinstate strict hashing; ambiguous dynamic execution keeps all audited benchmarks strict. New benchmark filenames remain strict until audited. The changed policy itself intentionally requires a new stamp once: it does not migrate or approve existing old stamps, saved SIM evidence or historical records.

## 3. Explain lineup decisions — automated validation complete; review/delivery pending

- [x] Initial source inventory: build_explanations.py explicitly records supported local-rule checks and final output membership; generation, screening, independent validation and selection rejection histories are not recorded per witness. This is a documented limit, not completed pipeline tracing.
- [x] Inventory the separate Showdown screening audit and evidence boundaries; see BUILD_EXPLANATION_EVIDENCE.md. Bounded integration is implemented in PR #200; application delivery remains pending.
- [x] Implement archive-time capture of the exact referenced screening audit bytes with receipt, snapshot/context and selected-occurrence checks. Optional evidence failures remain unavailable; the read-only explanation integration is implemented in PR #200; delivery remains pending.
- [x] Implement source-bound screening membership in the historical explanation view, including exact occurrences, Captain roles, partial/not-run scoring and aggregate/private reporting. Original ZIP bytes are revalidated; decompression consumes the shared read budget.
- [x] Limit stage explanations to originally recorded membership. Older/missing/conflicting evidence is unavailable; no pipeline rejection cause is inferred.
- [x] Keep missing causes explicitly unavailable; absence alone is not proof of rejection. Independent-validation and portfolio-selection rejection causes remain not recorded.
- [x] Automated validation on 9212ba7: local 2,512 tests (2,511 passed, one symlink-permission skip); Windows all 2,512 passed, no skips/failures or unexpected network attempts. Executable, guide, checksum and artifact upload passed; publication skipped. [Run](https://github.com/mnicho17/DFS/actions/runs/42382898646).
- [ ] Review/deliver this read-only slice; integration/publication and visible packaged-app testing remain separate gates.

## 4. Historical construction research — bounded comparisons completed; validation limits remain

- [x] Audit indexed coverage and predeclare the holdout: 42 Showdown records / 23 game groups; 17 discovery and six held-out groups. Field completeness is unverified.
- [x] Complete the existing winner/peer discovery review: 36 contests, 1,136 winner/contest portfolios; no excluded contests.
- [x] Complete six bounded walk-forward date reviews: 5,195 entrant/contest profiles; five/ten-peer support checks and game-level sensitivities. No defaults changed. See [the research checkpoint](research/HISTORICAL_CONSTRUCTION_2026_11_07.md).
- [x] Complete fresh per-entry observed-field and exact-entry-count peer-mean checks for receiver pairing and aggregate specialist counts: 42 contests / 4,996,498 entries, no excluded contests; unresolved entries and incomplete portfolios withheld. Five/ten-peer and game sensitivities recorded in the research checkpoint.
- [x] Complete exact-Captain/salary-band finish-rate checks for those receiver/specialist features, with five/ten entries per side and game sensitivities. No defaults changed.
- [x] Extend fresh field/peer-mean checks to separate kicker/defense flags and team splits for current observed winners, retaining five/ten-peer checks and per-game sensitivity.
- [x] Complete the earlier-qualified cohort's fresh peer-mean comparison for ten audited construction/pairing metrics: six bounded windows, 5,195 profiles / 690,269 entries, exact original legacy-series reproduction, five/ten-peer and per-game sensitivities. Current winners remain a separate cohort. Direct Captain/opposing-DST and fresh separate double-K/DST/QB flags are unavailable in this earlier-qualified slice.
- [x] Complete fresh K/DST flags, team-split and direct all-position Captain/opposing-DST field/peer-mean and exact-Captain/salary-matched comparisons across game groups. Position-conditional support and game sensitivity are recorded; no defaults changed. The app's older portfolio report is unchanged.
- [x] Use a predeclared held-out period and disclose coverage, entry counts, support attrition and game-level sensitivity. No grouping defaults proposed; broader cohort-pattern checks and further unseen-game validation remain open before any production/default change.

## 5. Zero-score/team/DST SIM research — first independent pass complete; acceptance blocked

- [x] Complete a declared later-game pass with the unchanged reconciled-event auditor: two admitted games / 26 rotation outcomes; five larger exports withheld by the original outcome-row limit. See [the independent-game checkpoint](research/ZERO_SCORE_INDEPENDENT_2026_11_07.md). This is exploratory coverage, not production acceptance.
- [x] Verify fixed-bound feasibility and modeled forecast means on admitted games: optimal plans, normalizers 1, analytic residual below tolerance, 88 profile-specific Monte Carlo checks within five standard errors; frozen-input/protected-draw/DST-offense invariants retained. No feasibility inference for excluded games.
- [x] Review zero, strictly positive/negative outcomes, team tails and DST interactions: zeros improve, positive bias worsens and linked-DST CRPS worsens in both admitted games. No defaults changed.
- [x] Implement the reporting-only strict-sign correction: actual_positive now requires actual > 0; actual_negative is separate, zero remains exact zero, and unavailable categories remain unavailable. Six focused regressions, 59 focused tests and exact five-profile replay of the prior strict-sign receipt pass. Full isolated suite: 2,518 run, 2,517 passed, one symlink-permission skip; no unexpected network attempts. [PR #202](https://github.com/mnicho17/DFS/pull/202) review/integration and Windows packaging remain pending; the original historical report is unchanged.
- [ ] Broaden admitted independent-game coverage through a reviewed bounded outcome reader, preserving full-source identity/conflict/cancellation checks; do not promote partial large-export results.
- [ ] Resolve positive bias/tails and opposing-DST calibration before any production/default change; two games and four DST outcomes are insufficient.

## 6. QB early-exit coverage — queued

- [ ] Require fresh replacement identity/role evidence and explicit conditional opportunity assumptions.
- [ ] Extend receiver, rushing and K/DST interactions without changing baseline forecasts.
- [ ] Qualify contest-money comparisons separately from conditional point comparisons.

## 7. Historical payout records — queued

- [ ] Add immutable contest-specific fee/payout revisions and source notes.
- [ ] Handle ties, partial fields, free contests, unknown values and confirmed nonpaying ranks.
- [ ] Keep money metrics unavailable when settlement evidence is insufficient.

## Reassessment gate

Reassess after these accepted scopes are completed or explicitly resolved with the user. Preserve validation limits and blockers in this file; do not mark research hypotheses, unmerged changes or unperformed GUI checks as delivered.

Validation counts above qualify runtime commit d50ef1f. The subsequent checklist/results update changes documentation only; current follow-up status is maintained in [PR #198](https://github.com/mnicho17/DFS/pull/198). Visible packaged-app testing, integration and research acceptance remain open.
