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

## 4. Historical construction research — initial checkpoint completed; comparison gaps open

- [x] Audit indexed coverage and predeclare the holdout: 42 Showdown records / 23 game groups; 17 discovery and six held-out groups. Field completeness is unverified.
- [x] Complete the existing winner/peer discovery review: 36 contests, 1,136 winner/contest portfolios; no excluded contests.
- [x] Complete six bounded walk-forward date reviews: 5,195 entrant/contest profiles; five/ten-peer support checks and game-level sensitivities. No defaults changed. See [the research checkpoint](research/HISTORICAL_CONSTRUCTION_2026_11_07.md).
- [x] Complete fresh per-entry observed-field and exact-entry-count peer-mean checks for receiver pairing and aggregate specialist counts: 42 contests / 4,996,498 entries, no excluded contests; unresolved entries and incomplete portfolios withheld. Five/ten-peer and game sensitivities recorded in the research checkpoint.
- [x] Complete exact-Captain/salary-band finish-rate checks for those receiver/specialist features, with five/ten entries per side and game sensitivities. No defaults changed.
- [ ] Extend fresh field/peer-mean checks to separate kicker/defense flags, team splits and the earlier-qualified cohort; do not substitute outcome-selected winners for walk-forward evidence.
- [ ] Finish K/DST, team-split and direct Captain/opposing-DST comparisons across game groups. Aggregate K/DST counts now have fresh field/peer-mean and matched-finish checks; separate flags/splits and QB/opposing-DST retain the initial median-based descriptions. The direct all-position Captain pairing is still unavailable.
- [x] Use a predeclared held-out period and disclose coverage, entry counts, support attrition and game-level sensitivity. No grouping defaults proposed; matched construction/field checks remain open.

## 5. Zero-score/team/DST SIM research — queued, diagnostic only

- [ ] Validate the existing reconciled-event prototype on independent qualified games.
- [ ] Preserve forecast means, record infeasible cases and evaluate shared team/opposing-DST behavior.
- [ ] Review zero rates, positive tails and correlations before choosing any production change.

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
