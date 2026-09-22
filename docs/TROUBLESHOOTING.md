# DFS Optimizer troubleshooting

## Windows warns about an unknown publisher

The app is not code-signed. Download it only from this repository's Releases page. You can compare the executable's SHA-256 value with the supplied `.sha256` file before opening it.

## The player file will not load

- Download a fresh salary CSV directly from DraftKings.
- Confirm the selected sport matches the slate.
- Close the file in Excel before loading it again.
- Do not rename or remove DraftKings columns.

## An NFL player looks active when news says otherwise

Choose **Game-Day Check** immediately before generating and again near lock. The app uses structured availability, practice, roster, and depth-chart data, but late-breaking news can lead the data source. A manually locked unavailable player stops generation instead of being silently removed.

## Slate Readiness says Review or Blocked

- Open the report and follow the **Next step** beside each item.
- **Blocked** identifies a hard preparation problem such as missing roster eligibility, poor projection coverage, or a locked unavailable player.
- **Review** identifies uncertainty such as stale news, questionable players, deep backups, incomplete ownership, or no generated portfolio yet.
- Generate lineups and reopen the report to add salary and contest-preset fit checks.
- The score is a preflight summary, not a prediction of lineup results, and the audit never changes the slate.

## Generation is slow

- After the build completes, choose **Settings > Copy Last Build Report**. It identifies whether Generate, SIM, or Select consumed the most time and includes the candidate counts and active rules needed to reproduce the issue.
- Start with fewer requested lineups while testing settings.
- Remove impossible minimum exposures or conflicting player groups.
- Relax very high uniqueness or very low team/game caps.
- For NFL Classic, Strategic, Balanced, Contrarian, and Chalk use the compact starter/rotation pool. Randomized with SIM Edge off intentionally uses the broader pool and can take longer.
- NFL SIM Edge does additional field and outcome simulation, so it normally takes longer than a projection-only build.
- Deep Build intentionally evaluates a much wider bank and can use up to five minutes. Its progress should advance through Explore, Screen, Validate, and Select/Refine. Select/Refine may spend the remaining budget lowering duplication without sacrificing the combined Edge/return signal, but stops early at a local optimum. Use Fast when you need an immediate iteration.
- Deep reserves its last compute window for portfolio selection. Reaching the validation budget is normal; the app keeps the best completed shortlist and reports how many screening and validation scenarios finished.
- Use **Settings > Build History…** to compare the same slate and settings across app releases.
- Use **Cancel** to keep completed lineups from the current build.

## Fewer lineups were returned than requested

Open **Portfolio Insights** and read the review flags. The available player pool, locks, fades, exposures, uniqueness, team/game caps, and groups may not allow the requested count. The app returns the feasible portfolio it found rather than hiding the shortfall.

## Selected replacement lineups were not filled

- Replacement keeps every unselected lineup fixed, so the remaining player pool and portfolio rules may leave no legal alternative.
- Review locks, minimum exposure, player groups, uniqueness, and team/game caps.
- Try replacing fewer rows together or relax the specific rule named in the portfolio warning.
- The retained rows are not silently changed when a full repair is impossible.

## A 20-Max build closes after SIM completes

Update to version 1.10.1 or later and run the build again. Earlier versions could close while displaying a completed build when the saved 20-Max field reference did not contain a duplication measurement. The lineup generation and SIM had already finished; the failure was in the final comparison message. Missing historical measurements now display as **n/a** and do not interrupt the results.

## The app says it recovered from an error

- The unexpected interface error was contained so the app could remain open. The action that triggered it may still be incomplete.
- Choose **Copy Details** in the message and keep the text for troubleshooting.
- Save or export any valid lineups still visible.
- If the error followed generation, also choose **Settings > Copy Last Build Report** so the timing and aggregate settings can be reproduced.
- Restart the app before repeating the failed action. Do not repeatedly submit or export if you are unsure which action completed.

### Saved repair cancellation (AR-01)

Cancellation, shortages and obsolete work now preserve the current saved set and its compatible reports.
If the status says the saved set or inputs changed, review current entries and request a new repair. Do not
restore an older portfolio over newer edits or live news. A display or diagnostic failure after application
does not undo the saved repair; Build History separates computation from saved application.

Developer verification, September 19, 2026: accepted base `0c7dddba08e847da5cca70c911f227afb2c03685`.
The real worker/GUI baseline returned 17 retained Showdown entries on cancellation and installed all 17 over
the original 20. Classic's cancelled fallback returned 20 but still replaced original entries and reports.
Both uncancelled paths returned complete 20-entry proposals. The changed paths preserve the exact originals
on cancellation and apply complete matching repairs only once, with the accepted Classic grade ordering.
The same four cancellation/apply-once acceptance checks failed on the baseline and passed on the change;
both baseline success paths replaced the committed collection again on a repeated completion callback.

Windows Python 3.12.14, PyQt5 5.15.11 / Qt 5.15.2, PuLP 3.3.2: the unchanged baseline passed 145 tests;
the stable change passed 164 tests in 80.226 seconds, with no failures or skips. The focused AR-01 module
passed 19 methods with format/event subtests. One earlier changed-suite run, overlapping another test process,
missed the existing 5-second selector limit at 5.058 seconds; the unchanged limit passed in the stable run.
Initial harness development also exposed an incorrect signature import, a comparison of elapsed time as if it
were a model output, and an invalid-roster probe aimed at a retained entry. Those setup errors were corrected;
production acceptance assertions and the existing performance limits were retained.

| Original cases | Executed coverage |
|---|---|
| C01–C07 | Preflight veto/source edit; real pre-generation and mid-generation cancellation; cancelled full proposals, queued success after GUI cancel, late cancel, shortages |
| C08–C12 | Partial optional analytics, real successful repair, missing retained occurrence, Captain/roster mismatch, wrong scope |
| C13–C16 | Save/Unsave remove-readd, Clear Saved, Insights removal, presentation-only sorting, current rules/news/slate changes |
| C17–C21 | Queued obsolete progress/finished/error, error-first and thread-retirement ownership, duplicate terminal suppression |
| C22 | Shared nested fixture graph, worker-only mutations, unchanged fixed-work rosters/counts/report values (elapsed time measured separately) |
| C23–C28 | Recording/preparation/render failures, current OUT status, unbound receipt rejection, ordinary partial builds |

Real worker coverage includes NFL Classic Fast/Deep, supported main Showdown, and NBA/WNBA/MLB repair
cancellation/success. Close-during-work tests wait for cooperative worker retirement. Main has no PR #37
Deep Showdown or paging subsystem, so those branch-only variants remain inapplicable to this main-only patch.
Controlled boundary payloads are separate from the real-worker tests and are labelled in test names.

Run `python -m unittest discover -v` for the required full suite, or
`python scripts/run_isolated_tests.py test_saved_repair` for the focused module. The test-only environment
is installed before DFS imports/GUI construction: disposable data/working roots, real INI QSettings with
fallbacks disabled and read-back verification, and denied/recorded external requests. Fixture live responses
feed the real preflight; workers, queued callbacks and settings retire before temporary storage cleanup.
No user settings/history are seeded, cleared or restored. Synthetic fixture extracts retain their original
player/roster values; the private handoff and its original status files are not included or modified.

These are Windows offscreen Qt source-runtime checks, not a visible desktop or packaged-executable smoke
test, deployment approval, or full PR #37 acceptance. The review PR's automatic Windows workflow supplies
separate CI/guide/packaging evidence; no release or merge is part of AR-01.

## DraftKings rejects the export

- Confirm the export came from the same slate as the contest.
- Confirm all required roster slots are filled.
- Do not add analysis columns to the DraftKings upload file.
- If the salary file changed, reload it and generate a fresh export.

## Historical results do not match

Results & Learning matches exact rosters previously exported by the app. A row may remain unmatched if the submitted lineup was edited after export, the result file omits its lineup, or its player names/IDs cannot be parsed.

## A standings file did not count as a complete field

- The file must contain at least 25 entry rows and about 95% of the advertised contest field.
- A personal contest-history file can update your own outcomes without representing the opponent field.
- Put **Single Entry**, **3-Max**, **20-Max**, or **150-Max** in the contest name or CSV filename if you want the field associated with that SIM preset.
- Learned preset tuning also needs three complete fields, 1,000 entries, and at least 70% player metadata coverage. Until then, the selected baseline preset remains active.

## A large standings import looks slow

- Complete contests can contain hundreds of thousands of entry rows and may take about a minute to inspect and summarize.
- Results & Learning remains usable enough to show progress while the import runs. Choose **Cancel Import** to stop after the current batch; a partially read file is not saved.
- The app saves compact field summaries instead of a local record for every opponent entry.

## Matching salaries were not attached

- Select the DraftKings salary CSV from the exact historical slate represented by the latest imported complete NFL field.
- The app requires at least 70% of field players to match before replacing the field summary with salary and construction details.
- A rejected or cancelled attachment leaves the existing field summary unchanged.

## Real Field vs latest NFL SIM is missing

- Import a complete NFL Classic field associated with a named preset.
- Generate and export an NFL Classic SIM build using that same preset.
- Reopen or refresh Results & Learning. The comparison is report-only until the complete-field learning guardrails are satisfied.

## Where local data is stored

Choose **Results & Learning**, then **Open Local History Folder**. Exports, imported results, slate snapshots, and build diagnostics remain on the computer.

## Export Review Report: unavailable values or save failures

The new action captures stored evidence without refreshing matches, migrating schema, generating lineups,
fitting models or uploading data. The existing dialog constructor/Refresh still calls the legacy report and
can update matches; this boundary is unchanged. Finish or cancel an active import/attachment before exporting.

Check the source states in the preview. Missing tables, unsupported schemas, unreadable/locked databases,
and invalid or inaccessible diagnostic JSON are different from empty valid history. SQLite reads use a
one-second busy timeout and one explicit read transaction, including committed WAL content. JSON diagnostics
have a separate capture time. No application checkpoint/journal-mode change occurs; this is not a promise
that SQLite performs no OS-level WAL/shared-memory housekeeping.

Older results often lack explicit format/currency. A specific format filter excludes unknown types. Cash
needs original nominal fee and gross cash winnings with compatible explicit currency (USD, CAD, GBP, EUR);
a dollar symbol alone does not establish currency. Unsupported prizes/adjustments and missing/invalid
amounts stay excluded. The old `roi` column is not read as a percentage. Paid-subset totals use identical
complete positive-fee rows; whole-group ROI is unavailable if any target row cannot qualify. Zero-fee
prizes are separate. Explicit paid-rank/cash contradictions are counted without inferring a payout.

Legacy exported metric values are observations, with finite/invalid/missing/zero counts for each field.
Neither Edge nor scenario count certifies completion, timing, or forecast/result identity. No historical
forecast pair is qualified on this base. Role/total ownership aggregates cannot be qualified from legacy
averages; this action does not add the missing E0 storage or lineage. Optional detail retains recognized
numeric export observations without rescaling them. Recorded export slots are not certified submitted slots.

Save errors preserve an existing destination; retry uses the same frozen report. Select a writable location
with free space. The source history folder, diagnostic file and database WAL/SHM sidecars cannot be chosen as output.
Cancel or Close waits for owned worker cleanup. A save that already committed remains a successful save
even if cancellation arrived immediately afterward. No automatic open-folder or upload action follows it.

### Review ZIP schema 2

`evidence.json` is strict UTF-8 JSON with `schema_version`, `report_id`, `generated_at`, `generator`,
`filters`, `options`, `database`, `diagnostics`, `build_evidence`, `observation`, `findings`, `limitations`, and
`qualification_limits`. App version/revision remain null when not embedded; recorded export versions are
separate. Source paths retained privately for overwrite protection never enter the archive.

`database` includes per-table availability/scanned/total/truncation counts, result coverage and filter
exclusions, independent import inventory, recorded match methods, contest-label count, performance groups,
export-setting groups, per-field metric availability, and separate opponent-field observations. Each
performance measure carries state/value/unit/basis/cohort/qualified-count/target-count/exclusions. Values
are finite numbers or null; null never means measured zero. Summary values are displayed to four
significant figures; JSON and detail retain numeric values. Contest labels are report-local references,
not certified unique contests. Imported occurrences are not deduplicated or claimed to be personal submissions.

The recognized raw-result adapter reads canonical `entry_fee`, `winnings`, `actual_points`, `rank`,
`field_size`, `places_paid`, explicit `currency` or compatible `entry fee currency`/`winnings currency`,
`prize type`, and `contest type`. It does not export raw JSON or arbitrary unknown keys. Result dates come
from recorded `slate_date`; other sections use their own recorded ISO timestamps' calendar date, preserving
the recorded offset rather than guessing a locale/timezone. Unknown filtered dates/formats are counted.

Bounds: 100,000 scanned rows per table in rowid order; 16,385 characters per fetched text/blob field
(raw JSON over 16,384 is rejected); 500 groups per breakdown; 1,000 optional result details with at most
12 recorded roster slots each; 8,000,000 diagnostic bytes/100 records; 2,000 observation characters.
Source/group/detail exclusions disclose lost coverage. Totals cover the explicitly scanned/qualified cohort,
not a truncated detail sample. Large source tables may need a future separately scoped paging enhancement.

`summary.md` and optional `lineups.csv` derive from the same immutable evidence as the preview. CSV has
the fixed header `result_ref,export_ref,sport,format,date,association,slot,player,player_id,score,fee,winnings,currency,net,roi_pct,cash_state`.
Rows repeat per recorded slot; do not sum entry cash once per slot. Numeric entry values are repeated only
for context. Formula-leading text is quoted and prefixed safely. Default reports omit detail entirely;
opt-in still excludes account/entry/contest names, source paths, unknown keys and raw logs. Review optional
observation/player text before sharing; redaction is not a guarantee of anonymity.

### Saved build explanations (RL-02)

The reader supports existing version-1 generated-output archives and input snapshots from the
Deep-compute branch without importing that branch's code or changing the current app. Choose its
`history` folder explicitly. Selecting a folder invalidates any preview. This is not a migration,
launcher update, or integration of PR #37; absent files remain unavailable.

`build_evidence.records` has report-local build references, original input hashes, recorded game dates,
checksum state, settings and decision counts. Archives additionally disclose their code fingerprint,
completion state and generated-output Captain concentration. Git revision, GUI application and submission
remain unestablished where not recorded. Details expose only allowed player fields when opted in; raw
reports, arbitrary metadata, filenames, paths, usernames and source files are never copied into the ZIP.

`result_linkage` counts selected imported occurrences in mutually exclusive categories: one archived
roster match, multiple archive matches, compatible snapshot only, tied conflicting snapshots, unsupported
identity, or no supported evidence. Archive matching requires a completed output recorded before every
game start, a valid matching input checksum, matching sport/format/date, and a complete Captain-aware
normalized name roster. Name collisions within an input are rejected. Matches are roster equivalence,
not certified player IDs or producing-build attribution. `certified_original_build_count` remains zero.
Do not reuse these counts as cash, forecast-accuracy or independent-game denominators.

Snapshot-only comparison picks the latest qualifying input among scanned files containing the roster;
it never supplies proof of output, application or original forecasts. Existing matching tables are not
read as authoritative or modified. Missing QB eligibility is unknown rather than recomputed. Zero limits
remain zero. Locks/fades describe recorded settings, not verified user intent or real-world availability.

Each directory scan stops at 1,000 entries, sorts discovered filenames descending, then reads at most 100
matching files. Shared expanded bytes are capped at 64,000,000, individual files at 32,000,000, input players
at 2,000, archived lineups at 1,000 and opt-in player details at 1,000 total. Limits and malformed/inaccessible
files produce partial evidence. "One match" means among valid scanned archives, never complete history.
ZIPs are not extracted; fixed members, per-member hashes, embedded metadata and input hashes are checked.
Checksums establish consistency, not authenticity. Database and file captures are independently timed.

RL-02 is a dependent change on PR #39 at `967907768c2ca2c9ac791ffa4878746ba61f4079`,
whose accepted-main base is `ec292c1060d559f345e195d9d18de12894c6188d`.
The scoped PR records the final revision and fresh verification outcomes. The Windows workflow also runs
for PRs targeting `codex/rl01-review-report`, preserving automatic regression and packaging checks while
the dependency is open. Neither prerequisite PR is merged by this change.

Fresh local evidence on Windows 11 / Python 3.12.14 / PyQt 5.15.11 / Qt 5.15.2:
the unchanged dependency base passed 195 tests (81.931 seconds); the isolated build-evidence
acceptance assertion failed as expected because the field was absent. The changed focused suite
passed 48 methods, including 17 new storage/UI cases. Final `python -m unittest discover -v`
passed 212 tests in 79.735 seconds, with no failures or skips. An earlier full run passed 210 before
the junction guard and denominator checks; the full suite was rerun after that change. Required
assertions and performance limits were unchanged. Synthetic 10,000-row responsiveness remained
covered (about 2.3 seconds and 10 MiB peak traced Python allocation on the final run, not process RAM).
Archive checksums, wrong dates/Captain swaps, ambiguous builds, postgame/cancelled outputs, original
byte preservation, privacy, bounded reads/details, source-folder protection and real offscreen Qt
preview/save/close were exercised. No production history was used as an application test fixture.
The rebuilt guide and updated synthetic screenshot were visually inspected. Visible desktop and
packaged-executable runtime smoke remain unperformed; CI outcomes belong to the PR, not this local record.

### RL-01 verification record

Base: `ec292c1060d559f345e195d9d18de12894c6188d` (v1.21.3). The scoped PR records the exact change SHA.
Fresh baseline: 164 tests passed; the absent-button acceptance probe failed as expected and separately
confirmed preexisting legacy dialog database creation. The identical probe now passes; legacy construction
still creates its database. Initial new-test errors were fixture connection cleanup on Windows and a missing
required field timestamp; both were fixed without changing acceptance assertions or performance limits.

Windows 11 build 26200, Python 3.12.14, PyQt 5.15.11 / Qt 5.15.2. Focused command:
`python scripts/run_isolated_tests.py test_review_report` (31 methods). Real offscreen Qt preview/save,
SQLite and ZIP boundaries use disposable synthetic data and verified INI settings with network denied.
The new reader also runs under an SQLite authorizer denying writes, and quiet source/diagnostic/settings
bytes are compared before/after. WAL concurrency is checked separately with an actual second writer.
A 10,000-row synthetic run used approximately 7.9 MiB peak traced Python memory and 2.5-3.1 seconds with
tracing enabled; GUI timer callbacks continued. This does not measure total process memory or certify every
possible 100,000-row shape. Final `python -m unittest discover -v`: 195 passed in 79.976 seconds,
no failures/skips. The preceding direct-discovery run had one existing performance test fail at 5.755
seconds against its unchanged 5-second limit; the isolated recheck passed in 3.849 seconds before the full
repeat. No optimizer/test-limit changes were made. An earlier full isolated run passed 194 tests before the
additional source-overwrite guard test. Automatic PR Windows CI supplies separate evidence.

| Supplied case IDs | Executed scope or explicit limit |
|---|---|
| C01-C06 | Missing/empty/partial sources, actual preview/save, source-byte/SQL authorizer checks, WAL snapshot, frozen retries, filters, corrupt/locked/inaccessible paths |
| C07-C12 | Original cash source reader and UI ZIP, valid zero/missing/invalid/overflow, 2-of-3 common cohort (-80% ROI), free prizes/currency rejection, legacy preservation, paid-rank contradictions |
| C13 | Positive role/total ownership fixture skipped: accepted history has no supported role/unit/completion record. Legacy zero/missing observations remain unverified; no substitution or ownership buckets |
| C14-C17 | Per-field legacy presence/zero/invalid state, projection without Edge, zero/positive scenario count not certified; forecast-pair exclusion path. Positive paired/partial-stage producer cases unavailable on this base; no invented storage |
| C18-C21 | Name-based/repeated result links across slates remain unverified, export settings/version separation, bounded missing/invalid diagnostics, computation vs saved-application distinction |
| C22-C27 | Real UI default/detail ZIP privacy, exact previews, strict JSON/CSV, hostile text, option invalidation, cancellation, injected write/fsync/replace failures and UI retry |
| C28-C30 | Close at real reader checkpoint, job-owned stale/duplicate delivery checks, import/attach launch and retirement gating, bounded detail/table scans, timed/traced 10,000-row worker |
| C31-C35 | Partial-source UI reports, separate opponent fields, sport/currency/contest groups, denied external calls, full existing regression suite including import and AR-01 |
| C36 | Visible desktop and packaged-executable runtime smoke checks not run. Offscreen screenshots and CI packaging are separate evidence |

The new synthetic screenshot was captured using `scripts/capture_documentation_images.py --only review-report --isolated`
and visually inspected. The rebuilt guide's changed pages were rendered and inspected. The private handoff,
example/status files, existing README line-ending work and real user history remain outside this patch.

