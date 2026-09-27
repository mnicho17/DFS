# RL-05B: Historical Coverage and Reconciliation

This unreleased slice builds on merged RL-05A and DC-02A. It adds a tab inside
Results & Learning, not a primary main-window action. Qualification still belongs
to `historical_identity.py`; no optimizer, SIM, projection, ownership, payout,
Tournament strategy or automatic-learning formula changes are included.

## Saved view and fresh qualification

`historical_coverage.load_saved` reads committed derived JSON in a read-only SQLite
transaction. It checks the payload hash/schema, counts unprocessed imports and
discloses corrupt rows. It does not open/hash/reparse results, salary CSVs,
snapshots or archives. The UI explicitly labels cached evidence as potentially
stale. Cached capability labels cannot authorize downstream analysis.

`Reconcile All Evidence` runs the existing RL-05A qualifier in a background worker.
It includes previously qualified rows so removed/changed evidence can downgrade.
One `BEGIN IMMEDIATE` transaction covers all derived updates and explicit snapshot
choices. Cancellation or failure before commit rolls everything back. A completed
commit remains completed if cancellation arrives afterward. Controls unlock only
after the worker retires; stale and duplicate callbacks do not apply.
Salary-review continuations retain their worker's plain cancellation Event through
retirement. Cancel suppresses an unopened chooser even if the successful read-only
payload was already queued. It does not discard a committed reconciliation result.

The UI displays source verification, inventory, contest qualification and commit
phases, with an elapsed timer. This is progress, not an invented percent complete
or a claim of parallel processing. DC-02A records actual reconciliation timing.
Workers pin their database and telemetry destination when created. Source imports
retain their existing per-file transaction semantics; cancelling subsequent
reconciliation never rolls back already committed imports.

## Evidence and counts

The six exclusive states sum to the historical contest count. Independent stage
counts can differ: complete scores may exist without build identity. Ready means
`OUTCOME_QUALIFIED`; needs review includes `CANDIDATE` and explicit conflicts;
missing evidence means not ready and not needing review. Conflict is a subset of
needs review. Sport/format filters use the derived identity or Unknown.

Ordinary result observations stay available. Portfolio Risk and future compute
input/output comparisons require qualified salary, snapshot and generated build
evidence. Hindsight requires the complete RL-05A chain. These are prerequisites,
not authorization from cached state. The RL-06 tab freshly verifies one selected
archive; RL-07 remains unimplemented. Build archives do not establish submitted
portfolios or phase timings. Zero and negative scores remain known observations.

## Explicit choices

Salary review uses the existing authoritative `pairing_state` and `save_pair` APIs.
Verification happens off the GUI thread. Candidate details include dates, format,
games, player-role count, import time and revision, with the qualification reason.
New salary confirmations retain their explicit basis, timestamp and additive
`evidence_version=1`; legacy versions remain NULL. Saved pairs are never replaced.

Ambiguous snapshot choices reuse the same full-pool/role/game/pregame matcher and
contest-ID precedence. The additive `historical_evidence_resolutions` table records
identity ID, exact snapshot digest, input ID, salary hash, `user_confirmed` method,
evidence version and confirmation time. Snapshot content and timestamp are bound
together because `input_id` alone does not bind creation time. New choices are
accepted only while evidence is genuinely ambiguous; the transaction revalidates
before and after saving them. Bulk choices succeed together or roll back together.
Automatic reconciliation never replaces a saved choice. A later incompatibility
is a conflict. Restoring the original revision can restore qualification; editing
or clearing confirmed associations is not part of this slice.

Details list archives linked to the current qualified snapshot separately from
other generated archive candidates. Both sections use saved RL-05A evidence and
show archive ID, input ID, timestamp and lineup count without source-file scans.
An archive appears only once by archive ID; distinct builds sharing an input ID
remain separate. Candidates remain visible when the snapshot is ambiguous or the
selected newer snapshot has no archive. They never qualify a stage or change
capability/aggregate counts. Selecting an arbitrary original/submitted build is
unavailable because the archives do not prove submission.

## Shareable report and acceptance

DFS Review schema 2 remains backward compatible. Its historical section adds
`coverage_version=1`, independent evidence-level counts, category counts and
capability-prerequisite counts. It uses freshly derived evidence under the existing
report read transaction. No identity IDs, source hashes, paths, filenames, usernames
or raw payloads are added to the shareable section. Existing preview, cancellation,
overwrite and external report-location protections remain.

The synthetic acceptance fixture imports 19 contests and 1,140 legacy result rows,
with missing recorded dates/formats. Expected exclusive states are 3 unresolved,
4 candidate, 3 salary, 3 snapshot, 3 build and 3 outcome qualified. Independent
coverage is 19 results, 13 salary, 9 snapshot, 6 build and 10 complete-score groups.
There are 3 ready, 4 needing review (including 2 conflicts), and 12 missing evidence.
Six contests have two generated archives each; incomplete actual scores prevent
three of them reaching outcome qualification. Tests use synthetic zero/negative
scores, real SQLite and real Qt worker delivery, with external network blocked.

Opening cost grows with stored derived JSON, not source CSV size. Reconciliation
still performs the existing bounded fresh inventory and score scan; no speculative
caching or compute optimization was added. RL-05A scan limits remain visible as
blockers. A long verification phase may show elapsed time without a percent total.
