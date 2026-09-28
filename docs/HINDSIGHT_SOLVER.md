# Hindsight Solver (RL-07A)

Results & Learning > Hindsight provides two actual-point benchmarks and a separate
observed-entry summary. It is read-only and retrospective. RL-07B generation
explanation remains unimplemented: this feature cannot explain which pipeline
stage excluded a lineup or what was predictable before kickoff.

## Scope and independent evidence gates

The ordinary benchmark is the optimum within the complete supplied salary
revision, under the displayed supported rules. Original contest-wide player-pool
completeness is **unverified**. Matching a snapshot, salary file and observed
entries does not establish that the original platform offered no other players.
No stronger original-contest optimum or submitted-portfolio claim is available.

RL-05A freshly selects the exact salary revision and optional pregame snapshot.
Its states, serialized evidence, tolerances, associations and saved snapshot
resolutions are unchanged. `OUTCOME_QUALIFIED` is not a universal switch:

| Scope | Required evidence |
| --- | --- |
| Highest reported supplied entry score | Original immutable standings; identifiable, nonconflicting EntryIds; finite entry Points |
| Supplied-pool optimum | Qualified NFL salary revision, complete original salary table and exact actual scores for every eligible athlete/role, supported identity/period/rules |
| Snapshot-local restricted optimum | All supplied-pool requirements plus the exact qualified pregame snapshot and supported local restrictions |
| Selected generated-build/snapshot comparison | Snapshot requirements plus an explicitly chosen qualified, completed, pregame archive with the exact input relationship |

Snapshots and archives are not required for the ordinary supplied-pool solve.
Missing actuals for even an unobserved or locally excluded athlete block both
exact solves. Unknown is never zero and no shortlist replaces the universe.
Related contests with the same exact salary revision contribute conflict evidence
only; they cannot fill a missing selected-source score. Partial observed fields
may coexist with complete supplied-pool actuals.
Cross-contest Captain observations are compared with the selected source's exact
base score even when that source has no redundant Captain observation. Reported
entry deduplication compares original Decimal values without context rounding.

The report separates salary rows/athletes, actual-score coverage and observed-field
coverage. Original salary roster eligibility, role-specific IDs, prices, teams and
games are retained. Whole-pool exact/coarser name collisions and explicit ID
contradictions fail closed. Names-only score evidence is explicitly labeled as an
exact salary-qualified name match, with whole-pool ambiguity checks.

## Exact numerical policy v1

- Preserve original actual-score lexemes, source row numbers, role and identity
  provenance. `10`, `10.0` and `10.00` agree; `10.00` and `10.01` conflict.
- Accept finite base scores with at most four effective decimal places and
  absolute magnitude at most 10,000. Reject booleans, float coercion, nonfinite
  values, unsupported precision and larger values without rounding.
- Use 20,000 integer units per point. Captain is exactly base times 3/2 once;
  observed Captain scores are already weighted and must agree exactly. Missing
  redundant Captain actuals can be derived from known base actuals; missing base
  actuals cannot be filled from another source.
- Largest role coefficient is 300,000,000; largest nine-slot absolute sum is
  1,800,000,000, below 2^31. Exact salaries are nonnegative integers, at most
  1,000,000 per role; zero is known. Showdown requires separate original Captain
  and FLEX IDs/prices and an exact 3/2 price relationship.
- Existing RL-05A 0.02 score/Captain and 0.15 roster-total diagnostic tolerances
  remain unchanged. They do not establish exact ties or exact agreement here.

The original entry Points stay separate from a reconstructed player-score sum.
Identical EntryId copies deduplicate; conflicting copies are excluded. Different
EntryIds sharing a roster remain different entries. A hidden highest-scoring
roster remains the highest reported score with an unavailable witness; a lower
readable roster is never substituted. Player/FPTS side-table values do not become
entry Points. Tied entries and distinct validated tied rosters have separate counts.
Gaps require compatible exact validated scores. A reported entry above the scoped
optimum is a visible inconsistency, not a negative gap clamped to zero.

## Supported roster rules and local restrictions

The disclosed `NFL-supplied-pool-v1` contract uses a 50,000 cap and at least two
teams. It is a supported ruleset, not verified evidence of a particular historical
platform rule revision. Classic has QB, RB, RB, WR, WR, WR, TE, FLEX, DST; FLEX
requires recorded RB/WR/TE eligibility. Showdown has one CPT and five FLEX slots,
six distinct athletes from the one verified game, and both teams. K/DST can occupy
Showdown roles when the original salary table permits them.

The pure role-count binary model is equivalent to repeated-slot assignment. It
does not greedily assign FLEX. Athlete uniqueness couples all of an athlete's role
variables. The objective contains actual points only. No live optimizer, QB-depth
recalculation, missing-forecast exclusion, ownership, jitter, stacks, salary floor,
SIM, payout, projections or learned terms are invoked.

Raw frozen rules are classified before sanitizing/defaulting helpers:

| Disposition | Examples |
| --- | --- |
| Applied local | Lower/equal recorded cap; role locks/fades; exact `at_least_one` and `never_together` groups; recorded `NFLQBEligible=False` exclusion |
| Applied local percentage endpoints | MaxPct=0 excludes total presence; MaxCptPct=0 excludes Captain; legacy MaxFlexPct=0 excludes Showdown FLEX; MinPct=100 requires presence; MinCptPct=100 requires Captain |
| Portfolio only, not evaluated | Other exposure percentages, team/game appearance percentages, inter-lineup uniqueness, requested count, retained rows and recovery |
| Strategy policy, outside benchmark | Recorded build style, ownership/SIM/Deep settings, automatic selection preferences |
| Unsupported local, blocks restricted scope | Unknown rule keys, malformed groups, unknown scoped identities, unsupported nested policies, conflicting flag records or cap above 50,000 |

A Showdown FLEX lock cannot be satisfied at Captain. Contradictory locks/fades or
two Captain locks are modeled as contradictions and can be proven infeasible.
Small positive portfolio minima never become one-lineup locks. A missing forecast
or personal fade does not remove a salary-listed backup from the supplied-pool
solve; any recorded local exclusion appears separately. A build-style string is
not used to reconstruct unrecorded hard constraints. Full original-build constraint
satisfaction remains unestablished when portfolio/pipeline policies are unevaluated.

## Bounded proof and ties

One named diagnostic budget defaults to 30 seconds (accepted range 0-30), shared
by model preparation, both requested primary solves, validation and tie searches.
Source reads have separate bounds. Both primary objectives run before tie work.
No Deep budget or production recovery caller changes.

`bounded_solver.solve(..., strict=True)` is an additive option; its default
True/False feasibility API remains unchanged. Strict mode requests zero absolute
and relative gaps, one thread and fixed CBC seeds. It captures the solution header,
both PuLP statuses, exit code, variable receipts and bounded termination log.
Optimal requires optimal model **and** optimal solution status, a matching explicit
optimal header/completion, complete variable rows, exact binary values, independent
roster validation and an exact integer objective receipt. Time-stopped or
integer-feasible incumbents are never certified or put in an optimal lineup field.
Bounds/gaps remain null when not independently returned and validated.

Completed infeasibility needs matching model/solution/termination evidence. PuLP
3.x does not map CBC's `Integer infeasible` header into its solution-status table;
the strict path recognizes that exact header only with a completed infeasibility
log and the model's infeasible status. A finite binary model's unbounded status is
an error. Parent timeout/cancellation kills and reaps the subprocess, hides its
Windows console and cleans the owned temporary MPS/solution/log directory.

Statuses are `not_requested`, `unavailable_evidence`, `unsupported_rules`,
`optimal`, `infeasible`, `time_limit`, `cancelled`, `solver_error` and
`validation_failed`. Tie status is separately `not_checked`, `partial` or
`complete`. After proving score Z, constrain the exact integer objective to Z and
exclude complete prior roster identities. Classic identity ignores repeated
position/FLEX assignment permutations; Showdown identity retains Captain.

At most 20 distinct examples are displayed per scope. An additional feasibility
probe may prove exhaustion or establish a larger lower bound. Exact total or
uniqueness is claimed only after exhaustion. A time/display limit leaves an unknown
total with a lower bound. Partial tie work preserves the primary certificate;
cancelling the UI operation suppresses its pending read-only application. Examples
are sorted canonically; no epsilon objective, global lexicographic-first or
cross-solver-version sample claim is made.

## Read bounds, immutability, lifecycle and privacy

The adapter reuses RL-05A qualification and the RL-06 read-only SQLite transaction,
CaptureReader, source byte receipts and final inventory/revision revalidation. It
does not route actual scores through RiskCapture. Before the existing authority's
CSV scans, at most 100 cataloged sources and 64 MB of available source inventory
are allowed, with 32 MB per file. Those existing authority scans remain separate
from the reader's expanded-archive accounting.

Additional lossless CSV passes, the fresh original salary-table pass and final
receipt rereads consume the same 64 MB capture budget as expanded snapshots/ZIPs.
The result pass allows 200,000 rows and at most 5,000 score observations. Salary
capture allows at most 2,000 athletes. Existing archive/snapshot bounds and allowlists
remain unchanged. Exhausted budgets cannot authorize a partial universe. Linked
roots/sources, missing databases, mixed revisions and changed inventories fail
closed. No database is initialized or migrated by capture.

No source, association, observation, settings, saved lineup, candidate library or
analysis receipt is written. SQLite-managed reader sidecars are accounted for
separately, not deleted to claim immutability. Only owned ephemeral solver files
are created. No network or live sports data is needed.

Opening Hindsight uses cached SQL only. Capture / Solve is deliberate and shares
Results & Learning's one-job ownership with imports, salary review, reconciliation
and Portfolio Risk. A plain cancellation Event survives QObject deletion; final
application is gated until worker/process retirement. Stale/duplicate callbacks,
selector changes, Close and Escape cannot apply pending work. Failure/cancel
retains prior output and disables copying. Late cancellation does not undo a
committed reconciliation. Later disk changes require a new frozen capture.

Copy Summary is aggregate-only by default. Explicit private detail adds names and
roster witnesses. There is no upload, bank-save or report-file export action.

## Validation and references

`test_hindsight_solver`, `test_hindsight_evidence` and `test_hindsight_ui` exercise
independent exhaustive oracles, real CBC, original synthetic CSV/SQLite/ZIP
fixtures, exact conflicts, preserved authority outputs, source/DB immutability,
shared deadlines, process cleanup and real Qt cancellation/retirement. The focused
suite includes historical identity/coverage, imports/pairing, readers/archives,
Portfolio Risk, entry review, AR-01/AR-02, CO-01 and compute-ledger parity. Counts,
durations, solver versions, measured synthetic pools and Windows artifact receipts
are recorded in the review PR. Packaging/static inspection is separate from an
executable launch.

General construction background: [DraftKings Classic overview](https://support.draftkings.com/dk/en-us/game-style-classic-overview?id=kb_article_view&sysparm_article=KB0010665)
and [Showdown overview](https://support.draftkings.com/dk/en-us/game-style-showdowns-overview?id=kb_article_view&sysparm_article=KB0010694).
These pages do not prove private historical pool membership or rules. The
[PuLP migration guide](https://coin-or.github.io/pulp/guides/how_to_migrate_to_v4.html)
explains the 3.x status distinction; this slice retains `PuLP>=3.2,<4`.
