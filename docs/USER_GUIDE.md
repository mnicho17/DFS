# DFS Optimizer User Guide

Version 1.11.0 | Windows desktop app

DFS Optimizer builds DraftKings lineups for NFL, MLB, NBA, NHL, and WNBA. It combines projections, contest construction, exposure rules, live NFL context, simulation, and local result tracking in one desktop workflow.

> Use the app to organize decisions, not to replace a final review. Player news, projections, ownership, odds, and simulations can be incomplete or wrong.

## 1. Install the app

1. Open the repository's [latest GitHub release](https://github.com/mnicho17/DFS/releases/latest).
2. Download `DFS-Optimizer.exe`.
3. Optional: download `DFS-Optimizer.exe.sha256` and compare it with the executable's SHA-256 hash.
4. Double-click the executable. Python is not required.

The app is currently unsigned. Windows may identify it as coming from an unknown publisher. Only use the executable from this repository's Releases page.

## 2. Five-minute workflow

1. Choose the sport and load the DraftKings salary CSV.
2. Choose **Slate Readiness**. For NFL, it refreshes a stale game-day check before auditing the slate.
3. Choose **Showdown** or **Classic** and set the lineup count.
4. Review **Build Strategy** and **Portfolio Rules**.
5. For NFL Classic, turn on **NFL SIM Edge** when you want field-based tournament scoring.
6. Choose **Generate** and wait for the progress message to finish.
7. Open **Portfolio Insights**, then save the lineups you want.
8. Choose **Export CSV**, upload to DraftKings, and do a final pre-lock review.
9. After the contest, import DraftKings results through **Results & Learning**.

![Main NFL workspace with Build Strategy, the player pool, and Classic lineup results](images/main-workspace.png)

The pictured examples use representative NFL data. Player names, projections, ownership, live context, odds, and SIM results will differ by slate.

## 3. Classic and Showdown

**Classic** uses each sport's normal multi-game DraftKings roster. The selected sport controls slots, eligibility, grading, and sport-specific columns.

**Showdown** uses one Captain and five FLEX players. Captain salary and projection are multiplied according to DraftKings rules. Showdown keeps its own Captain exposure controls and uses the same NFL availability, roles, news, weather, and Vegas context when available.

Always confirm that the loaded salary file matches the contest type you intend to enter.

## 4. Load and review the player pool

Choose **Load Player CSV** and select a DraftKings salary file. The player table is your slate workspace.

- **BaseProj** is the original projection.
- **AdjProj** includes supported context adjustments.
- **Status** and **Role** show NFL availability and depth information.
- **Vegas ITT** is the team's implied point total when odds are available.
- **Own%** is the projected field ownership used by build and SIM logic.
- **Tags** show locks, fades, and related choices.
- Exposure columns hold maximum and minimum portfolio limits.

Sort and inspect the table before generating. A strong optimizer cannot repair a poor or stale player pool.

## 5. NFL Game-Day Check and Vegas

For NFL slates, **Game-Day Check** refreshes structured availability, injuries, practice participation, roster status, news notes, and depth-chart roles. The app automatically excludes confirmed inactive, out, injured-reserve, suspended, and practice-squad players unless they were manually locked.

A locked unavailable player is never removed silently. Generation stops and names the conflict so you can decide what to do.

To add Vegas spreads and totals:

1. Create a personal key at The Odds API.
2. Open **Live Data Settings**.
3. Save the key and choose **Game-Day Check**.

The key remains in the current Windows user's local settings. If lines are unavailable, the status message says whether the key is missing, rejected, or the provider returned no games.

Run the check again near lock. No automated data source is a substitute for late-news review.

### Slate Readiness

Choose **Slate Readiness** for a report-only preflight before generation and again before export. It gives the loaded slate a 0-100 score and separates findings into **Pass**, **Review**, and **Block**.

Select a finding and choose **Show Players**—or double-click it—to filter the player table to the affected players. Choose **Clear player filter** in the status strip to restore the full table. This is a visual filter only; it never changes lineup eligibility by itself.

The audit checks:

- salary-file identity and enough eligible players at each roster position;
- positive projection and ownership coverage, including unrealistic ownership-pool totals;
- locked players whose latest status is unavailable;
- freshness and coverage of NFL player news and depth roles;
- questionable players and active depth-order 3+ backups that deserve review;
- availability of optional Vegas context; and
- after generation, complete rosters, salary use, and the NFL portfolio's fit with the selected contest preset.

Only hard preparation problems are blockers, such as a missing roster position, poor projection coverage, or a locked-out player. Missing early-week Vegas lines remain a review item because odds are useful but optional. The audit never changes a player, projection, ownership value, lock, fade, or lineup. Choose **Copy Report** when you want to keep or share the findings.

Reopen Slate Readiness after generation. The portfolio check then compares salary use, QB-stack mix, bring-backs, FLEX mix, and ownership coverage with the selected NFL field preset.

![Slate Readiness findings with player drill-down](images/slate-readiness.png)

## 6. Build Strategy

The **Build Strategy** tab controls how candidates are created and ranked. Available choices vary by sport and contest type.

- Choose a style that fits the contest, such as balanced, projection-oriented, or leverage-oriented.
- For NFL Classic, Strategic, Balanced, Contrarian, and Chalk use the starter/rotation pool even when SIM Edge is off. Locks, minimum exposures, and required player groups are preserved. Choose Randomized with SIM Edge off when you intentionally want the broad player pool, including deep backups.
- Use ownership mode and weight to decide how much popularity affects ranking.
- For MLB, choose stack preferences and use optional lineup, form, matchup, park, weather, and Vegas inputs.
- Use salary strategy to discourage obviously under-cap builds without forcing every lineup to spend the full cap.
- For NFL Classic, enable **NFL SIM Edge** for correlated scenario and field evaluation.
- For NFL Classic, choose the contest entry-limit preset: **Single Entry**, **3-Max**, **20-Max**, or **150-Max**. This changes the opponent field size, salary floor, ownership emphasis, stack mix, bring-back rate, and FLEX mix used by the SIM.

Start with moderate settings. Combine only rules you can explain and review.

## 7. Portfolio Rules

Portfolio rules shape the whole set rather than one lineup at a time.

- **Max Exposure%** limits how often a player appears.
- **Min Exposure%** requests a floor across the portfolio.
- Showdown has separate Captain minimum and maximum controls.
- Minimum uniqueness requires a set number of different players between lineups.
- Team and game caps prevent too much concentration in one source.
- **Group: At Least 1** requires one or more selected players.
- **Group: Never Together** blocks selected players from sharing a lineup.

Locks, fades, salary, roster eligibility, and hard maximums remain hard rules. Some minimums and uniqueness targets may be relaxed when the requested portfolio is impossible. The completion message and **Portfolio Insights** disclose those shortfalls.

![Portfolio Rules tab with uniqueness, concentration, and group controls](images/portfolio-rules.png)

## 8. Generate and review lineups

Choose **Generate** from the active Classic or Showdown area. During a build, the progress area reports the current stage and offers **Cancel**. Cancellation keeps valid lineups already completed.

The compact **Space** display shows the current eligible build pool, structural lineup possibilities, and requested lineup count. Fading, locking, or changing the NFL build style recalculates it immediately. Normal NFL Classic styles use the same starter/rotation role pool as generation whether SIM Edge is on or off, so omitted inactive players and deep backups visibly shrink the count. NFL Classic is an exact roster-shape count; Showdown and multi-position sports are labeled as upper bounds. Salary cap, stacking, correlation, exposure, and uniqueness rules narrow the real build space further.

During generation, the Space display follows the Generate, SIM, and Select stages. After completion, hover it for the last build's phase timing and the number of candidates evaluated versus lineups selected.

To share a complete performance snapshot, open **Settings** and choose **Copy Last Build Report**. The report includes the build-space count, eligible and omitted pool sizes, candidate budget, generated and selected counts, Generate/SIM/Select timing, strategy settings, portfolio rules, preset fit, and aggregate warnings. For NFL SIM Edge, the budget separates optimizer candidates from additional field-shaped candidates. It also identifies the slowest phase so a performance problem can be isolated without guessing.

Choose **Settings > Build History…** to review the 25 most recent runs and copy any earlier report. Select exactly two rows and choose **Compare Two Builds** for a side-by-side view of candidate counts, timing, contest preset, SIM quality, duplication risk, scenario coverage, and selected candidate sources. Compare similar slates and inputs; a score change is not meaningful when the underlying assumptions changed. History is saved automatically after a completed or cancelled build and stays on this computer. Choose **Clear History** in that window when you no longer need it.

![Build History comparing two aggregate NFL Classic runs](images/build-history.png)

### Portfolio Insights

Choose **Insights** beside the saved-lineup tables or **Settings > Portfolio Insights…** after generation. If lineups are saved, the report analyzes that saved set; otherwise it analyzes the currently generated lineups.

The Overview explains:

- A/B/C/D grade distribution and salary bands;
- the selected mix of optimizer, field-shaped, and scenario-built lineups;
- selected Ceiling, Balanced, Leverage, and Low-Dup scenario archetypes;
- QB stacks, bring-backs, FLEX usage, and combined ownership shape;
- average SIM Edge, leverage, duplication risk, and preset fit;
- top-one-percent scenario coverage and portfolio concentration; and
- automatic review flags for weak grades, high duplication, excessive unused salary, unstacked NFL lineups, or concentrated player cores.

The sortable **Lineup details** tab shows the same signals for every lineup. Insights do not modify lineups, portfolio rules, or DraftKings exports.

![Portfolio Insights explaining candidate sources, quality, construction, and review flags](images/portfolio-insights.png)

![Compact Lineup Space dashboard during NFL Classic generation](images/lineup-space.png)

### After generation

- review salary and every roster slot;
- inspect the grade or NFL SIM Edge details;
- look for repeated cores and unexpected exposure;
- save only lineups you might enter;
- open **Portfolio Insights** before export; and
- correct warnings rather than assuming they are harmless.

Large candidate pools, NFL simulation, and restrictive portfolio rules take more time than a simple projection build.

## 9. Understand NFL SIM Edge

NFL SIM Edge is for NFL Classic tournament decisions. It combines projection-led optimizer lineups, realistic field-shaped lineups, and lineups built from correlated ceiling, balanced, leverage, and low-duplication scenarios. The app evaluates them against a representative opponent field with a separate set of simulated outcomes, then selects a portfolio that covers different strong scenarios. Separating candidate creation from evaluation reduces overfitting.

Choose the preset that matches the contest's maximum entries per person. Presets are conservative starting assumptions, not promises. After at least three complete fields, 1,000 entries, and 70% player metadata coverage have been imported for that preset, the app can blend measured salary, construction, and winning-ownership patterns into its baseline. Until then, measurements remain report-only.

- **SIM Edge** is a slate-relative 0-100 summary. It is useful for comparing candidates from the same build, not as a universal score across slates.
- **Top 1%** and **Top 5%** estimate how often the lineup reached those field thresholds.
- **Win rate** is the representative-field first-place frequency, not an exact contest promise.
- **Cash rate** estimates how often the lineup cleared the simulated cash threshold.
- **Bust rate** measures poor simulated finishes.
- **Average percentile** summarizes normal field position.
- **Ceiling** is a high-end simulated score.
- **Tournament return index** combines upside, top finishes, and other tournament signals on a 0-100 scale.
- **Leverage** rewards useful paths that differ from expected field behavior.
- **Duplication risk** estimates how likely the construction is to be shared. Lower is generally better when other qualities are similar.

The Classic results show Edge and top-one-percent rate. The **Why this SIM Edge** detail explains slate percentile, direction, and model weight for top finishes, ceiling, tournament return, and duplication safety. Generation and Slate Readiness show preset fit; the copied build report separates optimizer, field-shaped, and scenario-built candidates.

Portfolio selection rewards coverage of different strong scenarios, but applies a soft quality guardrail below the B-grade boundary so novelty alone does not rescue a weak candidate. Do not choose by one number alone. Compare ceiling, top-five rate, leverage, duplication, construction, news risk, preset fit, and portfolio coverage.

![NFL Classic results ranked with slate-relative SIM Edge and top-one-percent rates](images/nfl-sim-results.png)

## 10. Save and export

Use **Save All**, individual save choices, or **Unsave** to control the portfolio. Saved-lineup tools include player exposure, stack and team exposure, and Portfolio Insights.

Choose **Export CSV** from the correct contest tab. The export uses DraftKings roster IDs and does not add analysis columns that could break upload format. Every completed export is also recorded locally for Results & Learning.

Before submitting:

1. Confirm the slate and contest type.
2. Confirm late scratches and start times.
3. Review salary, slots, duplicates, and exposure.
4. Upload to DraftKings and review the entries there.

## 11. Results & Learning

The app can compare exact exported rosters with DraftKings standings or contest-history CSV files. It can also measure the whole opponent field when the selected file contains complete standings.

1. Export the lineups you actually plan to use.
2. After the contest, download the DraftKings result CSV.
3. Open **Results & Learning**.
4. Choose **Import DraftKings Results** and select the file.
5. For complete NFL standings, choose **Attach Matching Salaries** and select the DraftKings salary CSV from that exact historical slate.
6. Review the match rate, ROI, cash rate, finish percentile, projection error, and guarded breakdowns.

The app reports general outcomes and projection calibration. Starting with v1.6.1, NFL SIM exports also retain their original Edge, top-finish, cash, return, leverage, and duplication estimates. When the Results & Learning summary shows matched SIM results, the report compares predicted and actual top-one-percent, top-five-percent, and cash rates. It also checks whether Edge tracks finish percentile and whether the return index tracks net results.

For a complete standings file, the report also measures:

- actual player ownership and its error versus the saved projection;
- field-wide and top-one-percent total ownership, including low-owned and high-owned player counts;
- ownership bands showing field share, top-one-percent rate, and exact-duplication rate;
- exact duplicated rosters, including duplication among top-one-percent entries;
- salary used and the low end of normal field salary;
- QB stack and bring-back patterns; and
- RB, WR, and TE FLEX usage.

A file is treated as a complete field only when it contains at least 25 entries and roughly 95% of the advertised contest field. Personal entry-history files still update your results, but they are not used to model opponents. To associate a complete field with a SIM preset, include **Single Entry**, **3-Max**, **20-Max**, or **150-Max** in the contest name or CSV filename. Entry labels such as `(92/150)` can also identify a 150-Max contest. Unclassified fields remain report-only.

Large complete standings are summarized in the background while they are read. The Results & Learning window stays responsive, shows progress, and lets you cancel safely. The app stores the field measurements rather than creating millions of local opponent-result records. DraftKings files that omit sport and contest-name columns can still be recognized from NFL roster slots and entry-name suffixes.

The matching salary file is optional, but it unlocks accurate salary, QB-stack, bring-back, and FLEX analysis when the standings file only contains names and roster slots. The app checks the player overlap first and refuses to attach a mismatched slate; at least 70% of the historical field players must match.

After generating and exporting an NFL Classic SIM build for the same preset, reopen **Results & Learning** to compare the latest simulated field with the measured real field. The comparison includes duplication, salary, construction, and ownership profile differences. It is a diagnostic comparison, not proof that the real contest will repeat.

SIM validation is labeled directional until at least 50 matched entries. Complete-field learning has separate, stricter guardrails. When those guardrails are met, only a small part of the measured winning-ownership profile affects candidate scoring, while salary and construction patterns are blended conservatively into the preset.

![Results and Learning report comparing predicted SIM rates with actual outcomes](images/results-learning.png)

Exact matching matters. An entry can remain unmatched if it was edited after export or if its result row lacks a parseable lineup.

## 12. Troubleshooting

**No Vegas values:** save a valid Odds API key, refresh Game-Day Check, and read the status message. Lines may not be posted yet.

**Slow generation:** reduce the lineup count while testing, remove conflicting minimum exposures or groups, and relax extreme uniqueness or concentration limits.

**Too few lineups:** read the generation message and Portfolio Insights. The player pool and combined rules may not support the requested count.

**Rejected upload:** use a fresh DraftKings salary file from the exact slate and export again without modifying the upload columns.

**Unmatched results:** confirm the submitted roster exactly matches a lineup exported from this app.

**Incomplete learning comparison:** a preset with partial historical field data may show the real-field duplication value as **n/a**. Generation and results remain available; importing a complete matching field can fill the missing measurement.

**Recovered error message:** the app contains unexpected interface errors instead of closing immediately. Copy the displayed technical details, keep or export any valid lineups still visible, and include the details with the last build report when reporting the problem.

See the separate Troubleshooting guide for more detail.

## 13. Local data and privacy

The optimizer stores exports, imported results, slate snapshots, build diagnostics, and settings on the computer. Choose **Open Local History Folder** in Results & Learning to inspect the history location.

Build diagnostics contain aggregate settings, timing, counts, and generalized warning categories. They do not store player names, lineup contents, salary-file paths, or API keys.

The Odds API key is stored in the current Windows user's local settings. It is not placed in lineup exports, logs, or this repository.

Back up the local history folder if the accumulated learning record matters to you.

## 14. Responsible use

Daily fantasy sports involve financial risk. Simulations describe modeled possibilities, not certainty. Use contest limits you can afford, follow applicable laws and platform rules, and perform your own final review before lock.
