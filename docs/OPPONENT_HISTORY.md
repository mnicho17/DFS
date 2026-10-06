# Username history and field profiles

## Automatic refresh

Choose your Results and Salary folders in **Results & Learning**. With
**Refresh history automatically at startup** enabled (the default), the next
app launch scans those folders in the background, imports new CSV evidence,
qualifies unambiguous salary matches, and indexes every saved mapped contest.
Use **Refresh history now** to pick up downloads while the app is open.
This is a startup/manual scan, not a continuous folder watcher.

The visible refresh summary reports new results/salaries, identical files skipped,
newly indexed entries, unchanged contests verified, and results needing salary
matching or retry. A changed file is saved as separate evidence and listed for
revision review; existing salary mappings stay fixed. Ambiguous matches require
**Review Salary Matches**. Missing configured folders fail before import writes.
Cancellation keeps completed files/contests and rolls back an active contest.
Closing the app waits for safe refresh-worker retirement. Only one refresh runs
at a time within an app instance. Large first imports may take several minutes
and expand the database substantially; later identical imports reuse the index.

Refresh updates evidence and descriptive username history. It does not apply
historical profiles to SIM, projections, ownership, ranking or lineup limits.

Open **Results & Learning → Opponent Portfolios → Username history & field profiles**.
Index all saved mapped contests, then enter an exact username or leave it blank
for the entire imported field. Choose Showdown or Classic, an observed entry-count
band, and a date cutoff. The cutoff excludes its entire calendar date.

The local SQLite database adds six tables:

| Table | Contents |
|---|---|
| `opponent_contests` | Source hashes, contest/export identity, date range, format, field coverage and validation audit |
| `opponent_users` | Platform-specific normalized username and display name |
| `opponent_entries` | Contest-scoped EntryId, username, points, rank and roster signature |
| `opponent_entry_slots` | Original roster labels and exact historical salary identities, positions, teams and salaries |
| `opponent_user_contest_stats` | Per-user portfolio construction, diversity and finish metrics; detailed exposures are derived from entry slots |
| `opponent_profile_versions` | Verified profile versions with cutoff, settings and source evidence |

Each indexed contest commits atomically. Repeating the same import does not add
entries. Conflicting duplicate EntryIds are excluded. A second export of an
already indexed contest fails for review rather than adding another copy. CSVs
without a contest ID use the sorted entry set as their export identity; overlapping
exports with unresolved contest identities are rejected. Original source files,
salary mappings, build history and reconciliation records are not rewritten.

Normalized roster rows are prepared in bounded batches in a temporary SQLite
database before publication. Bulk publication honors cancellation and rolls back
the active contest. Profile previews stream stored summaries, retain a bounded
username overview, and load a full contest timeline only for the selected username.
These bounds do not discard entries from the history tables.

Missing or ambiguous slot metadata remains unknown. Historical player IDs belong
to their slate/role; the database does not infer a permanent cross-slate athlete ID.
Usernames ignore case and trailing entry counters, but punctuation remains distinct.
Renames and alternate accounts are not inferred.

## Comparing historical users

### Testing construction profiles on later games

In the username-history window, select **Showdown**, choose a historical cutoff,
and click **Test later Showdown games**. At least two games must precede the date,
and at least one must be on or after it. This read-only experiment uses the whole
field; username, entry-count and successful-user filters do not apply.

All contests sharing a game and date stay in the same training/test group,
including different salary revisions. One contest with the most known
constructions represents each game (ties use a stable contest key). Training
averages each game's distribution equally; larger contests do not dominate.
Later games and their outcome ranks never train the targets.

The experimental sampler fits five marginal construction targets over bounded
legal proposals: team split, Captain position, QB count, kicker/defense count,
and salary left. It uses exact identities and salaries from the supplied slate,
never copies an old athlete selection, and permits all legal under-cap spending.
Absent targets are reported as missing from proposals, not proven infeasible.
Opponent entries can repeat, as they do in real contest fields. No hard exposure
or specialist limit is inferred from an absent historical construction.

The report compares 300 opponents across three fixed seeds for each model/game.
Total variation distance measures each construction distribution: lower is closer
to the observed field, zero is identical, and one is disjoint. Scores average
seeds within games, then give each comparable game equal weight. Incomplete
generated fields are reported and excluded from scores. Per-game results and a
training-evidence digest accompany the aggregate; the five-category average is
descriptive, not an estimate of financial performance.

Both models use the same explicitly selected hypothetical athlete draw weights:
uniform, or a salary-based proxy `(salary / 1000, floored at 0.1)^1.3`. The proxy
is the experiment's default; it weights plausible spending without using outcomes.
These temporary weights are never saved as projections or ownership forecasts.
Uniform weights may underfill the current sampler on broad historical salary lists;
such fields remain visible as failures and are never scored as complete.
Historical salary CSVs do not establish original pregame ownership, projections,
availability or the complete eligible player pool. It does not measure SIM ranking
or lineup returns, and marginal fits do not establish joint correlations or
multi-entry opponent portfolio behavior. A better result here supports further
testing with preserved pregame inputs; it does not authorize changing defaults.
No simulation, optimizer, projection, ownership, ranking or lineup-limit setting
is changed. Current source files and salary associations are verified again before
the report is published; cancellation withholds the unfinished report.

Preview reports show the field, selected user, and a successful historical cohort.
By default that cohort requires at least three complete observed contests and a
top-1% entry in at least two. Both minimums are editable. Entry-count bands help
compare similar portfolio sizes. All eligible portfolios of qualifying users are
retained, including their losing entries.

Standard standings often omit total field size. Those imports still support
construction trends. An unchecked option permits high-finish comparisons using
the observed entry count when all accepted entries have usable ranks. The report
then explicitly says completeness is unverified; this is not proof of a complete
field. Conflicted or inconsistent supplied field sizes are not used for this cohort.

Success here describes finishes, not profitability or established skill. Entry
fees/payouts are not inferred. Different contest types and slate conditions can
have different opponents, and repeated entries share game outcomes. Raw points
across slates are not a comparable performance measure. Absent contests are unknown.

## Using profiles in the app

Preview construction priors cover team splits, Captain position, correlation,
specialist counts and salary bands. Sparse username history blends with the field
baseline using `known_entries / (known_entries + 100)`; reports disclose that weight.
Priors are entry-weighted and contain construction categories, not old athlete IDs.

Saving a version re-verifies sources and records the evidence. It does **not**
change ownership, projections, ranking or simulation behavior. A later integration
should generate legal current-slate opponent portfolios, preserve user entry-count
and within-portfolio dependence, and evaluate held-out contests before applying a
profile. The CSV date cutoff does not establish when results became available;
historical backtests need an additional availability record to prevent look-ahead.

## Example SQL on a backup copy

## Recorded pregame field comparison

The optional **Compare qualified pregame fields + SIM** action reads an original
history folder. It requires snapshot integrity, exact full historical salary
identities and roles, matching original kickoff times, and a timestamp strictly
before kickoff. Saved exact snapshot resolutions and contest associations retain
precedence. Conflicting timestamp revisions require an exact saved choice.
Missing eligibility, forecasts, percentage ownership units or valid slot totals
exclude the game; no present-day inputs are substituted.

The independent multi-game catalog allows at most 256 MB, retains the existing
32 MB per-file limit, and allows at most 100 files and 64 MB per format/date.
Directory enumeration remains bounded at 1,000 entries. Invalid or incomplete
inventories stop selection. Existing reconciliation reader limits are unchanged.
Files, associations and saved resolutions are rechecked before publication.

Both field models use the same recorded eligible pool and a frozen diagnostic
candidate bank (seed 7109; default 100 candidates). Each comparison uses 200 shared
scenarios and seeds 17, 101 and 509. Candidate point moments must agree exactly.
The report shows top-20 ranking overlap, model-dependent simulated top-1% rates
and Captain/FLEX ownership drift. Personal locks are cleared for opponents.
This bank is not the original submitted portfolio; checksums and timestamps show
consistency rather than authenticity. The experiment establishes neither returns
nor profitability and does not change production simulation or generation.

The original salary-proxy experiment remains available separately. Its synthetic
athlete weights are not recorded forecasts. Whole-game holdouts and equal game
weights apply to both experiments; missing qualified games remain unscored.

```sql
SELECT u.username, c.end_date, c.name, s.entries, s.best_rank,
       s.top_one_pct_entries
FROM opponent_user_contest_stats AS s
JOIN opponent_users AS u USING (user_key)
JOIN opponent_contests AS c USING (contest_key)
WHERE s.user_key = 'dk:example_username'
ORDER BY c.end_date, c.contest_key;
```

```sql
SELECT u.username, c.end_date, COUNT(*) AS captain_entries, slots.position
FROM opponent_entry_slots AS slots
JOIN opponent_entries AS e USING (contest_key, entry_id)
JOIN opponent_users AS u USING (user_key)
JOIN opponent_contests AS c USING (contest_key)
WHERE slots.role = 'CPT' AND slots.player_id IS NOT NULL
GROUP BY u.user_key, c.contest_key, slots.position;
```

`top_one_pct_entries` in stored summaries must be interpreted with
`history_success_basis` inside `stats_json` and `opponent_contests.complete`.
An unknown value is not a zero. The UI applies the explicit coverage option.
