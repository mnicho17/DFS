# Overnight Showdown library foundation

The new `showdown_library` backend enumerates legal six-player NFL Showdown
rosters directly. It is separate from the existing searched candidate libraries.
The app offers explicit preparation, loading and resumable coarse screening.
Prepared builds use a bounded Captain-balanced bank through the existing detailed
SIM and portfolio pipeline. Optional full-slate partitions and streaming legal-roster
screening are described below. Automated scheduling remains future work.

## What preparation saves

A roster contains one Captain and five distinct FLEX players, includes both
teams, and fits the preparation salary cap. Preparation covers the chosen Captain
scope and every FLEX player in the supplied slate, including temporarily
unavailable or personally excluded players. The desktop action prepares locked
Captains if present; otherwise all Captains. Resuming preserves the saved scope.
Requesting an unprepared Captain later requires a broader library.
The snapshot must contain explicit FLEX/Captain IDs, positive salaries and one
two-team game. Duplicate identities and mixed games are rejected.

SQLite stores the Captain index and five compact player indices. Its primary key
supports reads restricted to the current Captain pool. Each committed batch saves
both its rosters and the next combination cursor atomically. Interrupted work can
resume; deterministic order makes reversing input rows harmless. Optimistic
checkpoint updates reject conflicting writers rather than overwriting progress.

Completion means every structural combination was checked, not that every roster
was legal. Candidate, time and storage limits pause preparation with incomplete
coverage. The backend defaults to one hour and five million saved rosters,
with a configurable maximum of fifty million. The desktop default is ten million.
There is also
a two-GiB storage threshold checked after each batch. A batch can exceed that
storage threshold slightly. The normal reader refuses incomplete libraries unless
the caller explicitly permits partial coverage.

## Run explicitly during downtime

In the app, choose NFL Showdown and set the desired Captain locks. Open
**Settings > Prepare Showdown Roster Library**, choose a new `.sdlib` file,
set the limits, then start. Pause/resume checkpoints are retained; closing waits
for safe worker retirement. Later use **Settings > Load Candidate Library**,
select that file, choose Deep with SIM enabled, and build. Loading incomplete
preparation requires an explicit choice with No as the default.

Extract `input-snapshot.json` from a saved automatic build archive into a working
directory. Use the app's Python environment:

```powershell
python scripts/prepare_showdown_library.py --snapshot input-snapshot.json --output slate-rosters.sqlite --hours 8
python scripts/prepare_showdown_library.py --snapshot input-snapshot.json --output locked-rosters.sdlib --hours 8 --max-candidates 10000000 --locked-captains
python scripts/prepare_showdown_library.py --output slate-rosters.sqlite --status
```

Run the preparation command again with the same snapshot and output to resume.
Choose an existing output directory outside historical evidence storage. This
command does not edit results, salary sources, snapshots, or exports.

## On-demand revalidation

`iter_candidates` streams fresh player dictionaries. It verifies the structural
identity, applies current availability and verified QB eligibility, excludes
missing forecasts, and checks current Captain/FLEX locks, slot fades, salary
range, player groups, distinct players and the two-team requirement. Changing
salary, identity, player membership, teams or game/date requires a new library.
Projection, ownership, injury, lock and personal-rule changes can reuse roster
storage, but must be revalidated and rescored. Exposure allocation, team exposure,
minimum diversity and portfolio feasibility remain downstream selection checks.

No projection, score, ownership forecast, historical result, or claimed optimal
lineup is saved in this library. Existing scenario-cache rules remain unchanged:
scenario replay requires exact compatible scoring inputs and model identity.
Database rosters are validated as they stream; this disposable cache is not an
immutable historical-evidence artifact.

## Bounded on-demand screening

The build scans each current Captain's roster partition with an equal scan-time
share and a repeatable seeded reservoir sample. Total admitted candidates fit the
existing Deep candidate budget (up to 20,000), and the scan allowance is at most
30 seconds or ten percent of the selected Deep time limit. Loading time counts
toward the Deep budget and appears in generation timing. Records rejected by
current rules are not sampled. Candidate admission is a sample, not a ranking by
cached scores or full-library SIM. Time-limited scans can omit promising rosters
and can depend on machine throughput; only complete scans give repeatable samples
independent of elapsed time. A library that changes during scanning is rejected.

Existing SIM shortlisting, bounded feasibility preservation, exposure allocation
and independent validation still apply. Sampling can miss a feasible portfolio;
a failed sampled build is not proof that the full library is infeasible. Build
reports distinguish complete/partial preparation, full/time-limited scans and
sampled candidates. Captain-balanced admission is separate from final Captain
exposure requirements. Defaults without a loaded prepared library are unchanged.

## Optional overnight screening

In Settings → Prepare Showdown Roster Library, select **Also prepare screening for Deep reuse**. Prepare a complete library first (raise the
stored-roster limit if needed). Screening uses the current player inputs, rules,
Captain allocation, candidate budget and Deep screening settings captured when
the dialog opens. It admits at most 20,000 Captain-balanced candidates, saves
that specific bank, and scores it in checkpointed batches. This is screening of
a sample, not SIM of every stored roster and not a submitted portfolio.

Pause/close waits for the worker to retire. Only whole batches with every
requested screening scenario completed are committed. Resume uses the saved
bank and skips its completed batches. A time or 512-MiB cache limit can leave
partial screening. Deep uses only a fully screened compatible bank; otherwise
it follows the existing fresh-screening path.

Disposable caches live in the app's `showdown-screening` data folder, separately
from the roster library. Exact player inputs (including projections and
ownership), rules, salary strategy/cap, candidate budget, scenario/field settings,
seed, library identity/count/completeness, Python runtime and app code identify
each cache. Changed inputs get another cache; no stale score is promoted to a
current result. Payload checksums and roster validation guard reuse. Rank-based
metrics are recomputed over the full saved bank, rather than comparing ranks
from different batches. Detailed SIM, independent audits, current exposure
requirements and portfolio validation continue to run fresh.

For an explicit saved snapshot, the equivalent CLI is:

```powershell
python scripts/screen_showdown_library.py input-snapshot.json showdown-rosters.sdlib --hours 1
```

CLI and packaged app code identities are intentionally distinct. Reuse requires
the same runtime/app code used for preparation. Progress reports scored/total
candidates and completion; the final summary records elapsed time and throughput.
Elapsed preparation includes initial bank sampling and integrity checks.

## Full slate preparation and full legal-roster screening

Select **Full slate: prepare every Captain in separate partitions** before choosing
New library. The `.sdfull` manifest and its adjacent `.sdfull.parts` folder form
one library; keep both together. Every salary-file Captain is prepared even when
current Captain locks are set. Each partition resumes independently and completed
partitions are preserved. Coverage is complete only when all partitions finish.
Current exclusions, locks, salary strategy and rules still apply when building.

Choose a 4/8/16/32-GiB storage budget and up to 12 hours per run. Storage is a soft
checkpoint limit and can overshoot by one batch; insufficient space leaves partial
coverage. Resume with more time or storage. Existing single-file libraries remain
supported. Full libraries avoid the single-file total row limit by partitioning.

For full screening, check the screening option and then **Screen every currently
legal roster**. This streams all rosters passing the captured current inputs and
rules, rather than admitting a sample first. Screening scenarios and field sizes
stay unchanged. Completed batches save a cursor and bounded leaders, so Pause or
a time limit can be resumed without rescoring completed batches. Partial screening
is never reused as completed evidence. At most 20,000 leaders are retained, divided
across eligible Captains with reserved construction strata (team split, QB count,
combined kicker/defense count). These reservations do not change ranking, exposure
limits or lineup-generation rules and do not guarantee a feasible portfolio.

The checkpoint payload is capped at 128 MiB; a storage pause requires a smaller
retention budget and a new job. Caches have exact input/code identities. Changed
projections, ownership, locks or rules require a separate screening job. Full
screening can take multiple nights; full roster enumeration does not imply every
roster has received detailed SIM. Deep reuses completed retained leaders, then runs
detailed SIM, independent audit and portfolio validation fresh. Reports explain
missing/incomplete cache reuse and distinguish full screening from sampled admission.

Equivalent explicit snapshot commands:

```powershell
python scripts/prepare_showdown_library.py --snapshot input-snapshot.json --output full-showdown.sdfull --hours 12 --storage-gib 8
python scripts/screen_showdown_library.py input-snapshot.json full-showdown.sdfull --hours 12 --full
```

A 52-player slate has 122,151,120 structural combinations and a 56-player slate
has 194,810,616, before salary/team filtering. Actual screening work depends on
current eligibility and rules. Large-slate throughput, final portfolio cost,
and optional detailed-score reuse remain follow-up work. Classic exhaustive
enumeration is out of scope.

Bounded library loading skips currently excluded players and salary-invalid rosters
inside SQLite before constructing candidates. Query progress remains cancellable
and time limited; malformed stored rosters remain subject to integrity checks.
Screening disabled in the dialog also clears the full-screening selection. Roster
completion alone does not establish screening completion.

Full `.sdfull` builds allow up to 60 seconds for scanning (at most 10% of the
selected Deep budget); single-file libraries retain the 30-second cap. Large
rejected prefixes can still exhaust a bounded scan. Completed compatible screening
remains the preferred path; scan failure does not prove infeasibility.

On cache admission, construction metadata (projection, ownership, duplication
risk, correlation flags, leverage and archetype) is rebuilt from current compatible
players and the requested salary cap. Saved SIM points, finish rates, hits and
scenario values are preserved; detailed SIM and portfolio validation remain fresh.
# Configuring the Captain pool

Use **Settings > Showdown Captain Pool...** before preparing screening. Check the
players eligible at Captain and save. Unchecked players receive a Captain fade;
their FLEX eligibility and exposure settings are preserved. This pool does not
force equal Captain exposure. Existing Captain locks still restrict the pool and
use their existing exposure reconciliation. Unavailable players and FLEX locks
cannot be enabled from this checklist. **All eligible** restores Captain
eligibility for the available players.

The pool is captured in build snapshots and screening compatibility. Changing it
requires screening again. Full-slate roster libraries can be reused, and the pool
filters candidates before simulation. Open the preparation dialog after saving
the pool so it captures the updated inputs. This does not remove the cost of
scanning qualifying FLEX combinations for the remaining Captains.
# Screening compatibility and refresh times

Coarse screening ignores only the observation timestamps `NFLUsageCheckedAt`,
`NFLUsageFetchedAt`, `LiveStatusUpdatedAt`, `NFLVegasUpdatedAt`,
`NFLNewsUpdatedAt`, and `NFLUsageHistory.checked_at`. A refresh that changes only
these fields or the observation flag `LiveStatusChanged` can resume or reuse screening. Original player records, snapshots,
archives and safety evidence retain their timestamps; restored candidates use
the current player records. Final SIM, audits and freshness checks still run.

Changes to forecasts, ownership, status, availability, depth, usage values,
news/weather/odds values, Captain pool, locks, exclusions, rules, player order,
library identity, screening settings, model code or Python runtime remain strict.
Unknown fields are included by default. No prior-cache migration is performed:
the identity-schema/code update requires screening once after updating. The
running app and existing cache files are not modified by this repository change.

Game-day checks preserve existing ownership when these same scoring inputs are unchanged. Changes to relevant inputs or missing ownership trigger quick ownership estimates; the check reports whether ownership was preserved or recalculated. Explicit ownership recalculation remains available.

Kicker events use one available recorded starter per team when depth 1 or an explicit STARTER status identifies exactly one player. That player supplies both opportunity rates and the team scoring budget. Missing or conflicting starter evidence retains the deterministic projection/player-key fallback; unavailable kickers cannot claim the budget. This model update requires new screening and scenario preparation.

Completed scored Showdown portfolios report automatic-cap pressure: ranked leaders versus selected QB/specialist construction, and starting/effective automatic player and Captain caps. The comparison does not rerun selection, change limits, or isolate a causal effect. Manual limits and locks are not labeled automatic caps. Unscored banks do not supply a ranked comparison.

In Portfolio Rules, **Showdown zero-QB maximum** optionally limits the percentage
of submitted lineups without a QB at Captain or FLEX. The default is **Unset**;
0% requires a QB in every lineup. Percentage maxima round down against the
requested entry count (10% of 150 permits 15 zero-QB lineups). The rule is explicit:
repair, automatic exposure-cap recovery, retained entries and refinement cannot
relax it. Conflicting limits stop the build without releasing a partial portfolio.
Active limits require complete roster position metadata. The copied build report
shows the actual count and allowed count. Recipes and snapshots preserve the rule;
older snapshots restore Unset. Changing this portfolio-only setting preserves
prepared roster libraries and compatible coarse screening scores. It does not
change projections, simulation, ownership or individual lineup ranking.
