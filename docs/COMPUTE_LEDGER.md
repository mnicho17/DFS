# DC-02A: observational compute ledger

This slice measures existing work. It does not choose a seed, stop a search,
change a budget, reject a candidate, rerun a simulation, change ranking or use
historical outcomes in a live build. Fast/Deep settings and AR-01/AR-02 remain
the execution authority. Runtime overhead still consumes the existing deadline;
the ledger never extends it. Deadline-limited runs are not promised identical
outputs across machines or different wall-clock loads.

## Run and phase receipts

`compute_ledger.py` owns schema 1. Each run has a random run ID, input/code
fingerprints, requested resources, effective candidate budget, purpose, status,
wall time, worker-thread CPU time and process memory observations. Normal builds,
saved repairs, candidate/scenario preparation and historical reconciliation are
distinguished. An explicit reconciliation has its own purpose; read-only
historical evidence queries do not start a persistent run.

Phases aggregate existing generation, deduplication, screening, primary SIM,
validation, ranking audit, shortlisting, selection/refinement and archive calls.
Reconciliation records derivation, inventory and contest qualification. Timestamps
span the first/last observed calls. Phase durations are inclusive where nested
(for example feasibility selection inside shortlisting); do not sum them to
estimate total run time. Unknown item counts, isolated dedup timing and CPU
capabilities are null, never estimated from returned lineups.

Batch details identify source, seed and style. `attempted` counts actual roster
construction trials where an engine exposes them; `legal_trials` means a trial
returned a feasible construction, before later portfolio acceptance. Returned,
new and duplicate counts describe generator outputs, not all internal rejected
alternatives. PULP and field/scenario generators currently leave unobservable
internal trial counts unknown. Sources include `exposure_recovery` and
`captain_coverage`; existing candidate source/archetype fields are unchanged.
Showdown separately records new athlete sets and new Captain assignments.
Screen/validation/selection source survival counts use existing candidate
provenance, so recovery candidates can still appear as optimizer survivors.

`reuse` separates loaded, accepted, rejected, generated, unique observed
(`deduplicated`), resumed-library batches and actually reused scenarios. A stale
library rejected before row loading has unknown rejected-row count. The presence
of a library/cache file is not counted as useful reuse. Scenario-cache identities
come from the existing cache implementation, not a parallel cache policy.

## Checkpoints, storage and privacy

Receipts live in the external user-data `compute-ledger` directory; the existing
`DFS_OPTIMIZER_DATA_DIR` override applies. Writes use a same-directory temporary
file, flush/fsync and atomic replacement. The envelope records schema/run/input/
code/candidate/simulation IDs and atomic completion. It is explicitly
`resumable: false`: telemetry is not a reusable candidate checkpoint.

Natural batch/phase/SIM progress boundaries observe approximately 30, 60, 120 and
300 seconds, then each 300 seconds. Existing selection results can produce an
additional checkpoint. No timer or optimization is created for a checkpoint.
Without an existing portfolio, its hash and overlap are absent. Ordered roster
hashes are deterministic; overlap is roster-set Jaccard overlap with the previous
observed portfolio. Completed batch counts/digests/source state exclude batches
whose existing cancellation/deadline checks interrupted work. Stop reasons do
not guess whether a phase's stop callback represented a deadline or cancellation.
Run cancellation uses the original worker receipt/event.

Limits per run: 256 detailed batches, 64 checkpoints, 200,000 ephemeral roster
identities. Aggregates continue when detailed batches roll off; identity-dependent
counts become unknown if their cap is reached. There are no persistent candidate
rows, player names, contest labels, usernames or local paths. Approximately the
latest 250 recognized ledger receipts are retained in deterministic timestamp/ID
order, including old interrupted receipts. Cleanup never visits historical
results, salary associations, snapshots, archives, exports or learning evidence.

Memory is sampled at start/end and at least five seconds apart at ordinary
boundaries. **Peak observed process memory** is a lower-bound observation, not a
true peak or a per-worker allocation measurement. CPU and wall time are separate;
no utilization is inferred. Capability/storage failures are nonfatal. A failed
disk write may leave the last complete checkpoint; it cannot make partial data
reusable. Abrupt process termination may leave a `running` receipt.

Build History/Copy Last Build Report show only the compact run summary. The
existing 25-report Build History retention and the 250-receipt ledger retention
are separate. Detailed ledger UI and automatic resume are later work.

## Compatibility

Existing snapshot fingerprints are reused as input identities. Candidate
compatibility hashes the slate, roster/salary/availability constraints, retained
rosters, requested portfolio size, style/rules and code. Simulation compatibility
also includes forecast/ownership inputs, recipe, calibration and contest context.
A projection-only change can therefore be candidate-compatible and simulation-
stale. These diagnostic identities authorize **no reuse**: original exact
candidate-library generation/code/slate checks and scenario-cache keys remain
unchanged. Source edits intentionally invalidate the existing code fingerprints.

## Historical distribution calibration

The existing Results & Learning scoring-distribution validation now includes a
report-only calibration track. It reads previously qualified, completed pregame
captures; it never reconstructs historical ranges or calls a simulator.

Keep the latest capture per format/model/player/scheduled game. Repeated contest
entries and Captain/FLEX appearances do not add independent samples. Conflicting
actual scores exclude the player-game across formats/models. Zero and negative
scores remain known. Models and formats are separate; they may share the same
outcomes and their sample sizes must not be added.

The report groups by recorded position, pregame role, their combination and model.
It shows p10/p25/p50/p75/p90 at-or-below coverage, strict below/inside/above p10-p90
rates, mean-score MAE, actual-minus-mean bias and median-score MAE. It also gives
equal-game weighted tail/error metrics alongside player-weighted metrics.
Boundaries count inside; at-or-below coverage includes ties. Games are the
independent evidence accounting unit; players within a game remain correlated.
Small samples do not establish predictive accuracy.

New p25/p75 metadata comes from the exact outcomes already captured by SIM;
legacy captures without these quantiles stay unavailable. No model weights,
projection/ownership formulas, payouts, search budgets, historical observations
or automatic learning behavior are changed. RL-06/RL-07 and adaptive DC-02B are
outside this slice.

## Verification

Use `python scripts/run_isolated_tests.py` for all tests. The baseline at main
`c46bb6130902541461f6619ad3663064e659c09b` is 666 passing tests with zero unexpected
network attempts. New tests exercise receipts, storage failure, cancellation,
bounded retention, actual reuse, deterministic real-worker parity in four
format/mode combinations, historical reconciliation and calibration evidence.

`python scripts/benchmark_compute_ledger.py --output benchmark.json --repeats 3`
runs fresh isolated children with fixed hash/RNG seeds and alternates enabled/
disabled order. The actual worker performs generation, SIM and selection. Every
candidate, scored stage and ordered final portfolio must match exactly. The
benchmark reports wall/process-CPU medians and native process peak memory; this
external benchmark memory metric differs from the ledger's sampled memory.
The disabled path retains inexpensive integer counters and decorator dispatch.
Results and final validation counts are recorded in the review PR and benchmark
report; timings are observations, not an improvement claim.
