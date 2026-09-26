# RL-05A: Historical Evidence Identity

`historical_identity.py` qualifies stored NFL Classic and Showdown evidence. It
reuses the salary matcher and automatic results-to-snapshot matcher. It does not
reconstruct forecasts, infer payouts, or change live optimization or learning.

## Exclusive evidence states

One identity represents an import/contest group. Separate result imports on the
same slate remain separate observations, not independent games. One salary
revision may support multiple contests.

| State | Evidence |
| --- | --- |
| UNRESOLVED | Original observations exist; qualified salary/slate identity is unavailable. Legacy export/name matches are insufficient. |
| CANDIDATE | Associations need review, a saved revision is invalid, explicit evidence conflicts, or latest snapshots are ambiguous. |
| SALARY_QUALIFIED | The existing matcher validates the immutable salary revision and sport, format, slate and observed player/role coverage. Missing result dates require existing explicit confirmation. |
| SNAPSHOT_QUALIFIED | The existing latest-pregame/contest-ID matcher selects a valid frozen snapshot. Its full salary pool, player/role IDs, salaries, teams and games also match. |
| BUILD_QUALIFIED | Completed pregame generated archives share that exact input ID and pass archive validation. This does not identify the original or submitted build. |
| OUTCOME_QUALIFIED | The full chain also has readable result rosters and complete eligible-player/role actual scores without score conflicts or scanned roster-total discrepancies. |

Outcome completeness is separate from build identity. Complete scores can exist
without an archive, or a qualified archive can lack scores. Unknown scores remain
`null`; zero and negative scores are valid. Captain and FLEX retain separate
salary IDs. Captain scores are 1.5 times observed base scores, checked against any
observed Captain value. Recorded objectives use recipe precedence; absent legacy
objectives remain unknown.

## Storage and APIs

`learning_db.init_historical_import_tables()` adds
`historical_contest_identities` without backfilling original results. Its primary
key is a deterministic identity ID; `(import_id, contest_key)` is unique. Columns
include schema version, state, evidence hash, normalized JSON evidence and
creation/update timestamps. Evidence includes a method version, salary revision
and method, snapshot input ID and method, archive IDs, player/generated-roster
data, score coverage, blockers, limitations and conflicts.

`qualified_contests(db_path)` uses a read-only transaction and returns fresh
`QualifiedHistoricalContest` objects. Serialization is immutable; `.data` returns
a detached copy. It revalidates sources instead of trusting cached qualification.
`derive_contests(conn, root)` serves callers that already own a consistent DB
transaction. `coverage(contests)` exposes aggregate states and fixed reason codes.

`reconcile(db_path)` computes and replaces derived rows in one transaction after
Analyze Saved Results. Cancellation/errors roll back every derived change.
Unchanged evidence preserves rows and timestamps. Deleted derived rows rebuild;
removed or changed evidence can downgrade a state. Explicit resolutions stay in
`analysis_salary_pairs`, which reconciliation never writes. Unique automatically
compatible candidates may qualify without claiming an unsaved pair was persisted.

## Privacy and limits

Review schema 2 adds `database.historical_identity`: aggregate counts, fixed
reason codes and filter basis only. This section shares no usernames, contest
IDs/names, source hashes, local paths or full identity payloads. Existing optional
report details retain their existing privacy contract. Identity filters use
qualified dates/formats when available; original imported-row metrics keep their
recorded-date basis.

Original results, salaries, snapshots and archives are read only. Hashes establish
consistency, not authenticity. No network, current roster/depth evidence or
historical forecast reconstruction is used. Unsupported sports, multi-day slates
and missing role IDs cannot qualify. The existing archive reader permits 100 files
per directory, 1,000 directory entries and 64 MB expanded data. Truncated inventory
blocks snapshot selection from a partial candidate set. Invalid files appear in
source issues. Result and score scans permit 200,000 rows and 5,000 score identities;
limits become explicit blockers, not zero scores.

This slice adds no reconciliation UI, submitted-build selection, portfolio risk,
hindsight optimization, compute instrumentation or strategy tuning.
