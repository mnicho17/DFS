# Overnight Showdown library foundation

The new `showdown_library` backend enumerates legal six-player NFL Showdown
rosters directly. It is separate from the existing searched candidate libraries.
This first stage provides a resumable preparation command and streaming reader;
the main app does not yet consume these libraries or schedule preparation.

## What preparation saves

A roster contains one Captain and five distinct FLEX players, includes both
teams, and fits the preparation salary cap. Preparation covers every Captain
and player in the supplied slate, including temporarily unavailable or personally
excluded players, so later selections are not limited by yesterday's locks.
The snapshot must contain explicit FLEX/Captain IDs, positive salaries and one
two-team game. Duplicate identities and mixed games are rejected.

SQLite stores the Captain index and five compact player indices. Its primary key
supports reads restricted to the current Captain pool. Each committed batch saves
both its rosters and the next combination cursor atomically. Interrupted work can
resume; deterministic order makes reversing input rows harmless. Optimistic
checkpoint updates reject conflicting writers rather than overwriting progress.

Completion means every structural combination was checked, not that every roster
was legal. Candidate, time and storage limits pause preparation with incomplete
coverage. The backend defaults to one hour, at most five million saved rosters,
and a two-GiB storage threshold checked after each batch. A batch can exceed that
storage threshold slightly. The normal reader refuses incomplete libraries unless
the caller explicitly permits partial coverage.

## Run explicitly during downtime

Extract `input-snapshot.json` from a saved automatic build archive into a working
directory. Use the app's Python environment:

```powershell
python scripts/prepare_showdown_library.py --snapshot input-snapshot.json --output slate-rosters.sqlite --hours 8
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

## Remaining implementation stages

1. Add preparation UI with progress, pause/resume and explicit completeness;
   estimate combinations/storage before starting and partition large slates.
2. Connect the streaming reader to bounded candidate screening. Preserve Captain
   coverage and a feasible portfolio, avoiding a full-library Python list.
3. Add optional overnight screening using a small, recorded scenario sample;
   persist scoring-input and model identities independently of roster identity.
4. Reuse compatible screening or rescore changed inputs, then run the existing
   detailed SIM, exposure allocation and independent validation. A larger time
   allowance must not silently change the user's chosen SIM settings.
5. Measure real archive performance and coverage before enabling default use.

All-Captain enumeration is larger than an individual locked-Captain build. A
56-player slate has 194,810,616 structural combinations before salary/team
filtering; input exclusions or later eligibility can reduce relevant coverage.
Preparing rosters does not remove SIM/portfolio costs or guarantee a build fits
every requested constraint. Classic exhaustive enumeration is out of scope.
