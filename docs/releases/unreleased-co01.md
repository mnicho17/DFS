# Unreleased — CO-01 Contest Objective Framework

Contest intent can be recorded as **Tournament**, **Double-Up**, or **Multiplier**
in the existing **Settings > Contest > Contest-Aware SIM...** surface. The choice
is independent of exact payout profiles: **Use Preset Only** keeps the selected
objective while disabling the profile. Saved profiles and build recipes retain it.

Tournament remains the existing lineup strategy. Double-Up and Multiplier are
currently metadata/framework choices. Objective-specific strategy arrives later
in CO-02; this change does not implement cash lineup construction, paid-rate
optimization, multiplier EV optimization, or cash ownership strategy.

New snapshots, diagnostics, generated build archives, and Results & Learning
exports record the objective. Legacy recipes/profiles/snapshots execute as
Tournament. Historical exports and artifacts that never recorded an objective
remain **Not recorded**. Contest names, payouts, fees, results, and SIM metrics
are never used to guess historical intent. Existing export rows remain NULL.

Snapshots retain schema version 1 and their exact integrity/provenance identity.
Candidate libraries also carry a generation compatibility identity that excludes
only objective metadata. Objective-only changes can reuse a library under the
same app code; other inputs, slate identity, current eligibility/rules, and code
checks remain enforced. Loading old artifacts does not rewrite their evidence.

Projection, ownership, generation, SIM, payout, duplication, correlation, ranking,
and portfolio formulas are unchanged. AR-01 cancellation/saved-repair and AR-02
automatic recovery behavior are unchanged. This is an unmerged framework review,
not a tagged release.
