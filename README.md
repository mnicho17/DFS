# DFS Optimizer



A Windows desktop lineup optimizer for DraftKings NFL, MLB, NBA, NHL, and WNBA slates.

NFL Classic and Showdown support **Settings > Ownership Sensitivity**: compare a saved Deep bank against baseline, higher ownership for favorites, and concentrated ownership, with paired player outcomes. Defaults run three batches of three profiles at 5,000 scenarios each. Reports save automatically and include Copy Report. See the [ownership sensitivity guide](docs/USER_GUIDE.md#ownership-sensitivity).



## User documentation

Completed sensitivity reports can be reopened as sortable tables. Use **Settings > Ownership Sensitivity > View saved comparison**, or **Comparisons… > Load ownership comparison** beside the Classic/Showdown output table to attach exact matching player inputs. **Settings > Projection Sensitivity** adds explicit lower-production and limited-history outcome stresses on the same saved banks, with separate projection comparison columns and automatic reports.



- [Five-minute quick start](docs/QUICK_START.md)

- [Complete user guide](docs/USER_GUIDE.md)

- [Troubleshooting](docs/TROUBLESHOOTING.md)

- [Release notes](docs/releases/)



Tagged releases include a downloadable `DFS-Optimizer-User-Guide.pdf`.



## Download the Windows app



1. Open the repository's [latest release](../../releases/latest).

2. Download `DFS-Optimizer.exe` and `DFS-Optimizer.exe.sha256`.

3. Optionally verify the download from PowerShell:



   ```powershell

   Get-FileHash .\DFS-Optimizer.exe -Algorithm SHA256

   ```



   The displayed hash should match the value in `DFS-Optimizer.exe.sha256`.



4. Double-click `DFS-Optimizer.exe`.



The release executable is self-contained; Python does not need to be installed. The app is currently unsigned, so Windows may identify it as being from an unknown publisher. Only use binaries downloaded from this repository's Releases page.



## Run from source



Python 3.12 is recommended.



```powershell

py -m venv .venv

.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt

python app.py

```



On Windows, `Launch_DFS.bat` also starts the app after its dependencies have been installed.



## Results & Learning



1. Save the lineups you plan to enter, then choose **Export Saved CSV** on the Showdown or Classic tab.

2. After the contest, download the DraftKings contest standings or contest-history CSV.

3. Open **Results & Learning** and choose **Import DraftKings Results**.

4. For complete NFL Classic or Showdown standings, choose **Attach Matching Salaries** and select the matching historical DKSalaries CSV or DKEntries CSV containing its embedded player salary table.



The app matches result rosters to exact lineups it previously exported. The report includes net return, ROI, cash rate, finish percentile, projection error, and guarded breakdowns by salary use, ownership, construction, and context adjustment. Complete standings can also measure actual and winning ownership profiles, exact duplication, salary use, QB stacks, bring-backs, and FLEX patterns. Large fields import in the background with progress and cancellation. Personal entry history is never mistaken for a complete opponent field.



Export history and imported results stay on the computer. They are stored in the app's local `history` folder and are not uploaded to this repository.



## Portfolio & Exposure



The optimizer can shape the complete set of generated lineups, not just each lineup in isolation:



- set minimum and maximum player exposure, including separate Showdown Captain limits;

- require a minimum number of unique players between lineups;

- cap the percentage of lineups containing a team or game;

- create selected-player groups that require at least one player or prevent players from appearing together;

- balance ownership concentration and duplication risk across the portfolio; and

- review a portfolio compliance summary before exporting to DraftKings.



The app generates a larger candidate pool and selects a compliant portfolio from it. If aggressive settings cannot all be satisfied, it returns the feasible lineups it found and clearly reports any relaxed uniqueness or minimum-exposure shortfall.



## NFL SIM Edge



NFL Classic builds can use the **NFL SIM Edge** option. The app first removes inactive and low-depth players from the automatic field pool while preserving manual locks. It then:



- applies a **Single Entry**, **3-Max**, **20-Max**, or **150-Max** opponent-field preset;

- creates candidates from projection-led optimization, realistic field constructions, and correlated ceiling, balanced, leverage, and low-duplication scenarios;

- creates only complete, near-cap field lineups with realistic QB-stack, bring-back, and FLEX construction;

- evaluates candidates with separate balanced, shootout, defensive, and blowout game scripts plus role-aware player ranges and guarded rare ceiling outcomes;

- rotates across three sampled opponent fields rather than treating one generated field as exact;

- ranks every candidate against the same active opponent field in each scenario; and

- selects a 150-lineup portfolio that covers different top-one-percent outcomes while respecting exposure rules.



The Classic results table shows slate-relative SIM Edge and top-one-percent rate. Its tooltip also includes top-five-percent rate, representative win rate, cash and bust rates, average percentile, simulated ceiling, tournament return index, leverage, duplication risk, scenario count, and representative field size. These metrics are decision aids based on the loaded projections and assumptions, not guarantees of contest results.



For a specific NFL Classic contest, open **Settings > Contest-Aware SIM** and save its field size, entry fee, your entry count, and payout tiers. The candidate SIM replaces the generic payout-shape proxy with that contest's actual prizes. After selection, a joint pass places every chosen entry into the same contest, so your lineups occupy ranks together and share covered prizes across ties. Portfolio Insights and build reports add total entry cost, expected payout and profit, profit and double-up chances, payout percentiles, estimate stability, and a 95% ROI range. A pre-build prompt catches profile and lineup-count mismatches. Saved profiles are reusable and local to the current Windows user. Choose **Use Preset Only** at any time to return to the normal preset workflow.



**Compute profile** offers Fast, named Deep tiers and Custom. Fast preserves the normal candidate budget and optional Classic SIM setting. Deep expands the search and validation; for NFL Classic it:



- defaults to as many as 6,000 candidate lineups across four independent optimizer seeds plus field-shaped and correlated scenario constructions;

- runs a quick screening SIM over the expanded bank and keeps a source-diverse shortlist;

- validates that shortlist with a different random stream, at least 2,500 scenarios, and a larger representative field; and

- performs constraint-safe lineup swaps when they improve the complete portfolio.



Choose **Custom** in **Build > Compute profile** to edit a **1–60 minute** maximum, up to **20,000 candidate lineups**, a **2,000-lineup validation shortlist**, up to **10,000 sampled opponents**, **4–32 independent search seeds**, and **250–1,000 screening scenarios**. Zero/Auto pool values preserve the original budgets. The Custom resource editor supports up to **10,000** validation scenarios (Deep still uses at least 2,500). The sampled opponent count applies to independent validation; coarse screening retains its smaller field. Pool sizes are ceilings subject to the deadline, available unique lineups, and requested portfolio size. These controls expand candidate lineups, not the eligible-player pool.



Settings persist on this computer and in named build recipes. Older recipes retain the original five-minute defaults. Build reports include the requested time/pool limits and actual completed counts. More time alone may still finish early at a local optimum; increase candidate count and search seeds to broaden exploration. More computation reduces sampling noise, not projection/model error.



For an initial dedicated-machine test, try **15 minutes / 8,000 candidates / 1,200 shortlist / 4,000 opponents / 8 seeds / 750 screening scenarios**, with **5,000 validation scenarios**. Compare actual work, timing, and independent agreement using the same slate and strategy. Larger settings use more RAM; cancellation remains available between work units.



Deep Build reserves time for final selection and, when a payout profile is active, the joint-contest pass. After the normal coverage refinements, it uses remaining compute to search for lower-duplication replacements that retain the lineup's combined Edge and return strength and continue to satisfy every hard portfolio rule. It stops at the time limit or when that constrained search reaches a local optimum; it never burns time solely to fill the selected time window. Cancellation and a slower computer therefore return the best valid portfolio available instead of discarding the run. The copied build report records the expanded bank, shortlist, screening and validation scenarios, independent top-candidate agreement, total and duplication-polish swaps, search time, stop reason, remaining budget, and joint-contest uncertainty.



When an NFL SIM lineup is exported, Results & Learning retains those original estimates, including contest-specific expected ROI when a payout profile was active. Imported DraftKings results can then compare predicted and actual top-one-percent, top-five-percent, cash, and contest ROI rates, plus the relationship between SIM Edge and finish percentile. Complete fields can also be compared directly with the latest representative NFL SIM field for the same preset. After three complete fields, 1,000 entries, and 70% metadata coverage for a named preset, a guarded blend can refine its salary, construction, and winning-ownership assumptions. Small samples remain report-only.



## NFL Game-Day Check



NFL salary files automatically receive current player availability, injury status, practice participation, roster status, news notes, and depth-chart roles from Sleeper. The status strip reports how many salary-file players matched, when the check ran, and whether anything changed. **Game-Day Check** refreshes the data on demand, and a stale check is refreshed before lineup generation.



- Confirmed out, inactive, injured-reserve, suspended, and practice-squad players are automatically removed unless the user locked them.

- A locked unavailable player is never silently removed; lineup generation stops and names the conflict.

- Questionable and doubtful players remain available, with their uncertainty reflected in the NFL role adjustment.

- The Status and Role columns expose starter/backup depth, practice participation, the source, and freshness details.



NFL Showdown generation uses the same key-free player, role, usage, matchup, and weather inputs without applying the Classic low-depth pool filter. When a confirmed starter is unavailable, the next active depth-chart player receives a small, reversible opportunity adjustment; manual locks and exposure limits remain hard rules.



High-volume Showdown builds create a wider candidate bank before selecting the final portfolio. Captain-specific ownership, key-free QB/receiver and RB/DST correlations, team splits, salary use, leverage, estimated duplication, and distinct Passing Stack, Receiver Captain, Rushing Control, Defensive, Onslaught, and Balanced constructions guide selection. These are portfolio decision aids rather than predictions of exact contest outcomes.



## Slate Readiness



**Slate Readiness** is a one-click, report-only preflight. For NFL it refreshes stale player status first, then audits roster viability, projections, ownership, locks, role certainty, and news freshness. After generation it also checks complete lineups, salary use, and how the portfolio's QB stacks, bring-backs, FLEX mix, and ownership coverage compare with the selected contest preset.



Actionable findings can filter the player table directly. The adjacent **Space** dashboard shows the current eligible pool, structural lineup possibilities, requested entries, and live generation phase. It updates after fades and locks. Normal NFL Classic styles use a compact starter/rotation pool with or without SIM Edge; locks, minimum exposures, and required player groups remain eligible. **Randomized** with SIM Edge off is the deliberate broad-pool option. The tooltip clearly distinguishes exact NFL roster-shape counts from upper bounds and reports generation, simulation, and selection timing after a build.



Findings are separated into Pass, Review, and Block. Hard problems such as missing positions, poor projection coverage, or a locked unavailable player are blockers. The audit never changes player settings or lineups.



## Final Lock Check, Entry Safety, and build recipes



Immediately before an NFL export, **Final Lock Check** refreshes the live source and maps late changes or unavailable players to exact saved lineup numbers. The user can preserve the unaffected portfolio and replace only the affected rows.



Every saved-lineup export then passes through **Entry Safety**. It audits the exact portfolio for roster and position validity, unique athletes, slot-specific DraftKings IDs, salary data and cap compliance, current-slate membership, Classic and Showdown team diversity, one-game Showdown construction, duplicate entries, unavailable players, and current portfolio-rule violations. Hard failures block export and can be repaired by replacing only the blocked rows; questionable players, stale slate data, and intentional salary leverage remain review items that can be acknowledged. The app never changes a lineup unless the user explicitly chooses and confirms replacement.



Named build recipes are available under **Settings**. They preserve reusable contest and strategy choices—including lineup count, NFL preset, SIM depth, ownership behavior, salary strategy, uniqueness, and concentration caps—without carrying slate-specific player locks, fades, exposure limits, or groups into a future slate.



NFL Classic SIM tooltips include a **Why this SIM Edge** breakdown showing each component's slate percentile, direction, and model weight. Build progress identifies candidate generation, field simulation, and portfolio selection as separate phases.



## Portfolio Insights



After generation, **Portfolio Insights** explains the finished lineup set and turns its review signals into a repair workflow. With a contest profile, the overview leads with the joint portfolio's total economics, profit chance, payout range, and estimate stability. It also reports grade distribution, salary and ownership shape, QB stacks, bring-backs, FLEX mix, candidate-source selection, scenario archetypes, leverage, duplication risk, preset fit, scenario coverage, concentration, and automatic review flags. The lineup table can filter and select C/D grades, high-duplication builds, excessive unused salary, unstacked NFL lineups, and concentrated cores. Selected rows can be removed or replaced while every unselected lineup remains fixed.



The **Player exposure** tab lists each player's portfolio percentage and exact lineup numbers, then jumps directly to those rows for review. Repair uses the current slate and settings, applies the same portfolio constraints, and generates only the open slots.



Candidate provenance survives NFL contest simulation and portfolio selection, so the report can distinguish projection-led optimizer lineups, realistic field-shaped candidates, and correlated Ceiling, Balanced, Leverage, or Low-Dup scenario builds.



## Build diagnostics



After lineup generation, **Settings > Copy Last Build Report** copies a shareable snapshot of the build-space count, pool size, candidate flow, phase timing, active strategy, portfolio rules, preset fit, selected source mix, SIM quality, scenario coverage, and generalized warnings. The NFL SIM Edge candidate budget distinguishes projection-led optimizer candidates, realistic field-shaped candidates, and correlated scenario-built candidates. **Settings > Build History…** keeps the 25 most recent runs available, compares two selected runs side by side, and lets the user copy or clear the local reports.



Unexpected interface exceptions are caught by an application-level safety handler. The user can copy the technical details and retain any valid on-screen lineups instead of losing the entire session without an explanation.



Build diagnostics stay in the app's local history folder. They contain aggregate settings and counts only—not player names, lineup contents, source-file paths, or API keys.



## Create a release



The `Windows Release` GitHub Actions workflow supports two modes:



- **Manual build:** Open **Actions → Windows Release → Run workflow**. When it finishes, download the Windows artifact from the workflow run.

- **Published release:** Push a version tag such as `v1.0.0`. The workflow runs the tests, builds the executable and user-guide PDF, calculates the executable's SHA-256 checksum, and publishes all three files to GitHub Releases.



Example release commands:



```powershell

git tag v1.0.0

git push origin v1.0.0

```



Create release tags from a reviewed commit on `main` so the published executable matches the supported source version.



Every release must also update the user-facing guide and release notes, recapture screenshots for changed workflows with `scripts/capture_documentation_images.py`, and visually inspect the refreshed images before tagging. The versioned PDF must be rebuilt from that reviewed documentation.



## Tests



```powershell

python -m unittest discover -v

```





### Compute tiers and Acer runtime estimates



In **Build → Compute profile**, choose a Deep tier to set every resource count. **Search & output** groups search scope and selection, with preset resource details collapsed. Choose **Custom** to edit the current tier's values. OK saves the changes; Cancel leaves the active settings unchanged. Tier selection is recovered from its saved values, and recipes include those values. Custom opens the resource editor with the current values.



| Tier | Time cap | Candidates | Shortlist | Opponents | Search seeds | Screening | Validation | Acer planning range |

| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |

| Baseline | 5 min | 4,500 | 900 | 2,700 | 4 | 600 | 5,000 | 3–6 min |

| Balanced | 15 min | 8,000 | 1,200 | 4,000 | 8 | 750 | 10,000 | 7–12 min |

| Thorough | 20 min | 12,000 | 1,200 | 4,000 | 8 | 1,000 | 10,000 | 9–17 min |

| Extended | 30 min | 16,000 | 1,600 | 6,000 | 12 | 1,000 | 10,000 | 11–27 min (extrapolated) |

| Maximum | 60 min | 20,000 | 2,000 | 10,000 | 16 | 1,000 | 10,000 | 16–39 min (extrapolated) |



These are **reference estimates for the Acer i7-9750H/16 GB and 150 NFL Classic lineups**, using a similar 192-player eligible pool and no contest-specific payout profile. The four completed reference runs took 261.06, 574.71, 747.16, and 790.29 seconds. The earlier 300.10-second time-limited run is excluded from calibration. Only workload timings are stored in code, not lineups or player data.



The heuristic scales generation, SIM, and selection phase costs from the closest measured workload. Its assumed SIM split is 40% screening / 60% validation, adjusted for opponent sample size; this split is not separately measured. The planning band is ±25%, widened to ±40% outside the measured workload range, rounded outward to minutes. It is not a statistical confidence interval. Changes in player pools, hard rules, search seeds, hardware, or joint-contest validation can substantially change actual runtime. The estimate is capped by the selected time budget, with a small allowance for finishing work units; a low time cap can stop work before the pools are exhausted.



The time cap does not force the app to keep running. A 60-minute tier can finish much sooner when its search reaches a local optimum. These presets are not claims of improved prediction quality.



### NFL Showdown Deep



Load a one-game NFL Showdown slate and choose a Deep tier in **Build > Compute profile**. Start with **Baseline**. The tier sets candidate count, shortlist, sampled opponents, search seeds, screening/validation scenarios, and the time cap. Showdown timings are not yet calibrated; the dialog does not show Classic's Acer estimates.



![Showdown Deep controls](docs/images/showdown-deep-build.png)



![Showdown compute tiers](docs/images/showdown-deep-compute-settings.png)



Deep explores candidates across independent optimizer seeds, screens them against a Showdown opponent sample, validates a Captain-aware shortlist with a different random stream, and refines the selected portfolio. Captain identity survives deduplication: changing Captain produces a distinct entry even with the same six athletes. Every scenario draws each player's outcome once, then applies 1.5x to the Captain. Kickers have a separate offensive-opportunity correlation rather than using the defense model.



The opponent sampler uses Captain/FLEX ownership with a projection-based fallback, requires six unique athletes, both teams, and legal salary, and retains repeated entries as sampled duplicates. Personal locks and fades do not restrict the opponent field. Confirmed unavailable players are excluded; an unavailable user lock raises an error. No Classic starter/rotation pruning is applied. Existing Captain/FLEX locks, fades, and final portfolio constraints remain active. Repairs preserve the retained lineup identities.



The results table adds **SIM Edge** with scenario/top-one-percent details. Export still uses one Captain and five FLEX slots. The copied build report includes actual candidate, screening, validation, shortlist, refinement, and timing counts. Cancellation or a deadline returns the best available stage; missing independent validation is explicitly reported as a warning.



This version uses a generic tournament payout proxy and relative candidate metrics, **not contest-specific Showdown ROI or a calibrated opponent model**. Classic contest payout profiles and historical field calibration are not applied to Showdown. Higher compute does not guarantee stronger predictions. Keep the original Fast Showdown path available for comparison.



### All-style search and ranked results



For NFL Classic or NFL Showdown, choose a Deep profile and open **Search & output**. Select **Search all five build styles** to share the tier's candidate and time budgets across Strategic, Balanced, Contrarian, Chalk, and Randomized. Each style uses the configured search seeds. Each search receives a share of the remaining generation time so one style cannot consume the whole generation phase. Identical entries are removed before screening; a different Showdown Captain remains a different entry. The report lists actual style candidate counts before deduplication. Locks, fades and player eligibility remain active; ownership preference remains a separate setting.



The combined pool is screened against common scenarios. Its shortlist is evaluated together against a fresh scenario stream and opponent sample. **Individual ranking** shortlists and selects by top-1% finish rate, then top-2%, top-5%, first-place rate, and mean points. It disables automatic Showdown exposure guardrails, diversification bonuses and portfolio refinement. Explicit player, group, team/game and uniqueness rules still affect selection; minimum exposure shortfalls are reported rather than prioritized. Existing uniqueness relaxation is reported if needed to fill the output. Retained repair entries remain included. **Portfolio selection** uses the existing complementary-outcome selection and refinement, then displays that selected group in the same finish-rate order.



Set **Lineups** to 300, 450, or any count up to **1,000**. Results display 150 per page with global row numbers, a page selector, and Previous/Next controls. The finish-rate columns show top-1%, top-2%, top-5%, first-place (including ties), and mean simulated points. The first page is the strongest individual ranking within the selected output. Portfolio rules and exposure percentages apply to the **entire output**, not each page. Choosing a subset can change exposures. All-style or larger-output runtime is uncalibrated; the dialog does not apply the previous 150-lineup Classic estimates to these workloads.



Use **Save page**, **Unsave page**, or individual checkboxes. Saved selections survive page changes and are ordered by finish rates when added. Export still contains only the proper roster IDs; generating more alternatives does not change a contest's entry limit. Finish-rate ordering is for comparisons within the same simulation run, not calibrated comparisons between separate runs. Non-SIM Classic builds keep their grade order. Equal metrics may tie. Budget exhaustion can produce fewer candidates or outputs, and incomplete validation is reported. Ranked results cover tested candidates, not every possible lineup.



Automatic Showdown guardrail relaxation is now explicitly reported when portfolio selection raises its starting caps to fill the requested count.



Illustrative results on synthetic fixtures (finish rates below are demonstration values):



![Classic ranked results, page 2](docs/images/ranked-results.png)



![Showdown ranked results, page 2](docs/images/showdown-ranked-results.png)



### Sorting results and reading finish rates



Click a column heading in either generated-results table to sort **all generated results**, across every page. Numeric columns start highest first; player names start A–Z. Click again to reverse the order. **Best first** restores top-1%, top-2%, top-5%, first-place rate and mean-points ordering. Row numbers describe positions in the current view. Sorting changes the view, not the selected lineup set or simulation results. Save checkboxes remain attached to the correct lineup after sorting and paging.



Top 1%, Top 2% and Top 5% are the shares of simulated scenarios where the lineup reaches those opponent-field thresholds, including ties. They are cumulative: top-1% outcomes are also within top 2% and top 5%; do not add the columns. First % includes ties for the best sampled opponent score. Mean pts is average simulated fantasy points. SIM Edge is a relative composite score out of 100, not a probability. For example, a 19.58% top-1% rate over 10,000 scenarios means 1,958 qualifying scenarios. These are model results, not calibrated actual-contest winning probabilities.



Result refreshes rebuild exactly one set of finish-rate columns. Reentrant table rendering is removed to prevent stale renderers appending repeated metric groups.



### Broader candidate search and ranked-group diagnostics



NFL Classic and Showdown Deep now pass previously found lineup identities into subsequent seed/style searches. These are **exact exclusions**, not a requirement that every candidate differ by multiple players from the entire candidate bank. Classic retains the separate minimum-uniqueness checks within a batch and against retained repair lineups. Showdown treats Captain plus five FLEX identities as the entry identity; swapping Captain remains a distinct lineup. Search can still stop short of its candidate ceiling when time or feasibility is exhausted.



**Individual ranking** allocates up to 60% of the total time cap to generation, then screens until 78% of the cap, with the remaining time reserved for independent validation and selection. Maximum therefore allows up to **36 minutes of generation**, instead of 22 minutes 48 seconds. **Portfolio selection** keeps the previous 38% generation / 58% screening deadlines so it retains refinement time. These are phase deadlines, not forced waits or measured runtime predictions. Spare time after validation is not recycled into new unvalidated candidates. All-style budgets remain shared rather than multiplied by the number of styles.



Build reports include **Ranked groups**, covering 1–150, 151–300 and every remaining block, including a partial final block. Groups always follow canonical SIM finish-rate order, regardless of the table's current sort. Each block reports average top-1%, top-2%, top-5% and first-place rates, the range of individual top-1% rates, mean points, salary-strategy exceptions, and the five highest player exposures. Showdown also includes Captain exposures and correlation flag counts/types. Group exposures use the actual block size as their denominator; these groups are not independently optimized portfolios. Reports with these summaries include player names and say so in the privacy footer.



Individual ranking now reports **refinement disabled in individual ranking**, with zero refinement time; its normal mode explanation is no longer classified as a failed rule. Reports explicitly identify automatic Showdown cap increases, the effective relaxed uniqueness when available, incomplete validation, and candidate shortages while retaining identifier-free constraint warnings.



### Simulation input integrity



NFL simulation projections and opponent sampling ignore internal minimum-exposure/group search boosts. Explicit team projection adjustments still apply; optimizer search continues to use exposure preferences. Showdown fills missing opponents from its two validated slate teams and shares game context across the pool. Conflicting opponent or game metadata stops the build with an explanation. These are input-integrity fixes, not historical calibration or new penalties against unusual lineups.



### Replaying identical NFL build inputs



Under **Settings**, use **Save Build Snapshot** to save the currently loaded, enriched player data and build settings, then **Load Build Snapshot** to restore them without a live refresh or ownership recalculation. This supports NFL Classic and Showdown. Snapshots include locks, fades, exposure limits, groups, contest settings and field calibration; they do not include account settings or API keys. They contain player data, so share them deliberately.



Each ordinary NFL build also saves its effective pre-build inputs automatically under `history/snapshots`. The existing history-folder USB backup includes these files. Repair builds do not claim a replayable snapshot because they also depend on retained lineups. Snapshot files accumulate and can be copied or removed manually when no longer needed.



The workspace displays **Snapshot replay** while automatic pre-build refresh is paused. **Settings > Data Freshness** shows the original recorded check time and available player/odds source states. Unknown means no source status was recorded. Manually refreshing live data or loading another CSV leaves replay mode. Before using old snapshots for current contests, refresh current availability.



Build reports include an **Input ID**. In **Settings > Build History**, select two reports and choose **Compare Two Builds**: matching IDs confirm identical captured input data/settings; different IDs identify an uncontrolled comparison. Older reports cannot establish an input match. IDs include player order and metadata, so a changed ID does not prove projections changed. Matching inputs do not guarantee identical results across software versions or time-limited searches. No historical accuracy claim is implied.



To compare updates: keep the automatic snapshot from the first build, load it after updating, run the same build, and compare input IDs in Build History. A manually saved snapshot captures the current controls, which may differ from an earlier build. Generated lineups still use the separate export controls.



### Independent ranking audit



After independent validation, NFL Classic and Showdown Deep can score the same shortlist against **2,000 additional scenarios and a fresh sampled opponent field**. The separate fixed seed is 481516. Audit results never change selection or displayed scores. The audit starts only with at least 120 seconds before the existing selection reserve, and stops within 120 seconds or at that reserve, whichever comes first. SIM timing includes this work, so builds may take longer than earlier versions with the same snapshot.



The report compares the **top 50**: overlap, median absolute rank movement of the original leaders, and their worst audit rank. Banks below 200 use one quarter of the candidates; banks below 20 or validation below 1,000 scenarios are not audited. Exact boundary ties are disclosed. This measures sensitivity to both scenario and opponent sampling within the model, not historical accuracy or a confidence interval. A 2,000-scenario audit is noisier than a 10,000-scenario validation.



Skipped or incomplete audits are identified; partial audits never publish overlap percentages. The audit is not used to select or tune lineups. Existing screening/validation agreement remains a separate diagnostic.



The audit consumes available build time. Its scores never guide selection, but less time may remain for optional portfolio refinement; the existing selection reserve is preserved. Compare software versions as well as snapshot inputs when assessing changes.





### Sampled opponent-field diagnostics



NFL Classic and Showdown build reports describe the actual base opponent sample used for the reported simulation. They show sample size, unique entries, repeated copies beyond the first, largest duplicate group, mean/range salary and cap usage, team-count splits, and same-team WR/TE counts per QB. QB stack observations count quarterbacks, not entries; a Showdown entry with two quarterbacks contributes twice. Showdown also shows both-defense and three-plus kicker/defense frequencies and separate Captain/FLEX ownership.



The report lists the most sampled players and the largest absolute differences between recorded ownership percentages and sampled exposure. Missing inputs remain unknown, distinct from zero; zero-use players in the sampling pool are included. Ownership guides conditional sampling and is not an enforced marginal target. Slot-specific inputs may fall back to total ownership or projections. These diagnostics do not adjust field generation, scoring, or rankings and are not measured contest outcomes.



Duplicate entries count every sampled opponent; bootstrap field copies are not added to these counts. Showdown Captain swaps are distinct entries. If Classic cannot generate a field and uses candidate lineups as fallback opponents, the report discloses this limitation. Reports containing these diagnostics include player names in their privacy notice. The ranking audit uses a separate field; this section describes the primary simulation, not that audit or a later joint payout validation.



### NFL projection sources



Historical PPG is now separate from supplied forecasts. NFL Classic and Showdown honor manual overrides and imported projections before using automatic role-and-usage workload estimates, then historical estimates. Double-click BaseProj or AdjProj to edit a forecast; inspect the source beside the selected player. Read [projection sources and limitations](docs/USER_GUIDE.md#projection-sources-and-players-without-nfl-history). Reload the salary CSV to use the new handling; old snapshots preserve their original inputs.



NFL usage now uses the current weekly-statistics endpoint. Workload-v2 preserves historical player differences when usage is missing. The [shared server preparation command](docs/USER_GUIDE.md#shared-server-preparation) generates projections and ownership through the desktop code, with source hashes for reproducibility.





### Shared specialist event scoring



Classic and Showdown now share experimental possession events for K/DST scoring, with common field goals, touchdowns, extra points and opposing points allowed. Reports disclose uncalibrated event rates and the distinction between input projections and resulting SIM means. Offensive-player scoring remains projection-based; this is not a complete game ledger. See the user guide for assumptions and omitted events.





### Kicker opportunity forecasts



Matched kickers now receive explicit attempts/accuracy/distance/XP forecasts from weekly records, with documented shrinkage and context. Components drive shared specialist events. Source details and report counts are visible; manual/imported forecasts and missing-data fallbacks are preserved. Reload the salary CSV to adopt this model; old snapshots retain their recorded inputs. See the guide for uncalibrated assumptions and tonight’s validation sequence.



### One-file results and username

Open **Results & Learning**, enter your **DraftKings username**, and choose **Save username**. The setting stays on this computer and is not included in shared source code. Import the complete contest standings CSV to identify your submitted entries by exact username (case-insensitive, ignoring the trailing entry counter), save their scores and ranks, and compare your regular-slot and Captain exposure with the observed field. No salary or entry-upload file is required for these comparisons.

If you previously imported the same file without a username, save your username and import it again; the field and personal entries are not duplicated. Different username spellings are not guessed. **Optional Salaries** adds salary/construction detail when desired. Forecast validation still requires original saved forecasts; winnings/cash rate require payout data. This update stores empirical results and ownership comparisons; it does not automatically retrain projections or tune Showdown from a single contest.


### Results audit and Copy Report

Results & Learning now appends a diagnostic audit automatically. **Copy Report** copies the complete displayed text, including per-contest match counts, up to five unmatched rosters, lineup score reconciliation, Captain/FLEX scoring consistency, player forecast misses, saved ownership ranges, and available export version/timestamps. Keep the original standings file accessible for its player FPTS side table. Missing files or fields are reported rather than guessed.

Identical player-score tables across contests indicate shared outcomes; the count is a proxy, not a verified game count. Repeated player appearances are not independent observations. Older exports may lack forecast provenance and ownership units; low saved ownership alone does not prove a normalization bug. New exports preserve forecast source and workload/kicker context for future audits. No forecasts are regenerated and no model weights change automatically. Copied reports contain player names, username and aggregate results, but omit file paths, entry IDs and API keys.


### Results folder

In **Results & Learning**, choose or create a folder with **Choose folder**. The location is saved on this computer. Store downloaded standings there, then click **Import New Results**. The app scans that folder and subfolders, ignores non-result CSVs, and imports contents it has not already saved. Identical contents are skipped even after a rename; moved files refresh their audit location. Your saved username identifies your entries. An unavailable USB drive is reported without changing saved results.

The scan runs only when clicked. Keep **Import DraftKings Results** for selecting individual files or reprocessing an existing field after changing your username. File-content identity prevents duplicate imports of identical files; changed contents are treated as a new import.


Long Search now supports checkpointed NFL Classic/Showdown candidate libraries, 1–12 hour sessions, and fresh Deep scoring after loading. NFL builds exclude unverified/backup QBs unless the next quarterback is confirmed eligible. New quick ownership uses roster exposure units. See the user guide for controls and limits.


Results & Learning adds cached Construction & Scenario Review for NFL Classic/Showdown and player performance histories. Analyze Saved Results uses matching history/snapshots; Refresh Free NFL Stats caches the selected and previous regular seasons from nflverse, with PPR and DraftKings scores kept separate. See the guide for coverage and date limitations.


Results reports reconcile overlapping same-date player scores across contests, distinguishing missing Captain rows from score conflicts. New NFL SIM build reports track QB counts through generation, screening, independent simulation and selection. These diagnostics do not change projections or lineups.


Deep Showdown now samples opponents with a documented experimental salary-spending distribution. Build reports show target/actual salary bands, fallback entries and explicit Captain/FLEX locks. Ownership percentage-point comparisons require recorded percent-of-entries units; old snapshots retain their original weights. See the guide for a frozen-candidate comparison command and limitations.


Build controls now use one Compute profile picker. Search & output groups Deep search/selection choices, resource numbers stay collapsed for presets, and ownership-data controls live in Data and Learning. Settings > Show Build Controls reveals the panel. Existing recipe settings remain supported.
# Ranking repeatability

**Settings > Ownership & Leverage** compares projected field ownership with the actual simulated field, top-candidate exposure and selected exposure for Classic and Showdown. Build reports disclose bounded ownership matching; Results & Learning separately audits recorded ownership forecasts against imported results. These are diagnostic comparisons, not automatic exposure recommendations or trained predictions.

NFL Deep builds now save the validated shortlist automatically. Open **Settings > Ranking Repeatability** to compare that same set across fresh scenario/opponent batches. Copy the summary or use the automatic CSV/JSON/text exports in `history/ranking-checks`. This diagnoses sampling sensitivity without changing selected lineups. A new Deep build is required to capture a bank; older snapshots do not contain the shortlist. See the [user guide](docs/USER_GUIDE.md#ranking-repeatability).


Projection checks distinguish recent four-week form from full available current/prior-season history for both NFL formats. Reload salaries and create a new Deep bank after updating to capture the new evidence; old snapshots remain frozen. See the [full-season history guide](docs/USER_GUIDE.md#full-season-history-in-projection-checks).


Projection Sensitivity now tests each of the five saved favorites individually at 15% lower simulated production, alongside baseline, the combined reduction and wider-history outcomes. Classic and Showdown both support this. Defaults run up to eight profiles per batch: 3 batches at 5,000 scenarios means 24 simulations, roughly 2.7 times the former three-profile workload. The dialog shows the exact profile count for the selected bank. Existing banks can be reused; no new Deep build is required for these additional tests.

Copy Report identifies each displayed baseline leader's largest individual decline and summarizes each tested player's effect across the baseline top150. JSON/CSV retain all candidate/profile/batch results. Effects apply equally to opponents and candidates, with shared Captain scoring; they do not redistribute touches or add up to the combined effect. Every requested profile must finish before a batch counts. Saved comparison columns include all tested profiles in lowest-rate/rank summaries; older three-profile reports remain viewable. This diagnoses dependence on forecasts, not proven projection errors or automatic exposure changes.


### Automatic forecast checks

Normal NFL Classic and Showdown build reports now include **Automatic forecast checks**, using recorded player inputs with no additional simulations, network requests or user setup. Open **Settings > Copy Last Build Report** or Build History to read them. Long sensitivity comparisons remain optional model-development tools; they are not a per-contest requirement.

Checks review automatic skill-player estimates when base points differ from a positive historical average by at least 5 points and 50%, or projected opportunities differ from at least four recent observed games by at least 50% and 8 attempts / 4 carries / 3 targets. Observed zero opportunities remain valid; missing observations do not become zero. The report identifies the usage season because prior-season roles may differ. Recorded position-budget overruns and missing/invalid opportunity breakdowns also appear. Limited-history automatic estimates of at least 6 base points receive informational notices, not assertions that they are wrong. Thresholds are uncalibrated review heuristics and can miss real errors; role changes can legitimately trigger them.

The copied report shows at most six review findings and two limited-evidence notices, with all findings retained in build diagnostics. Warnings summarize the number of players needing review. Player names are included under the existing report privacy notice. Supplied/manual projections are preserved and excluded from automatic-workload comparisons; K/DST are outside these skill-player checks. No projections, lineups, exposure limits or simulation weights are changed. Continue importing results after contests to evaluate original forecasts against outcomes; these checks do not train the model.


### Automatic entry targets

**Auto from lineup count** is on by default in the NFL Build controls. Request 1 for Single Entry; 2–3 for 3-Max; 4–20 for 20-Max; 21–150 for 150-Max. More than 150 uses 150-Max assumptions while keeping individually ranked alternatives, so a 450-lineup request remains a browsing bank rather than claiming one legal 450-entry portfolio.

For Deep Classic and Showdown, one entry uses Individual ranking; 2–150 entries use Portfolio selection to select the requested set together. More than 150 uses Individual ranking. Explicit player/group/uniqueness rules still apply, and shortages are reported. Best means selected under the app's uncalibrated model and constraints, not proven optimal entries or guaranteed winnings. The compute profile stays under your control; choosing a count does not automatically start a longer simulation.

Classic selects its existing contest preset (including the 150-Max large-field defaults); supplied Contest-Aware SIM field size and payouts remain authoritative where supported. Entry count does not verify the real contest. Showdown uses the automatic entry-selection approach but retains its generic opponent model; changing the target is not a new calibrated Showdown field model. The summary discloses that distinction.

Turn Auto off to choose the contest preset and Search & output selection manually. For example, 20 entries can still be intended for a 150-Max contest. Auto mode persists locally and in new recipes/snapshots; older recipes without this setting retain manual behavior. Existing snapshots are not silently rewritten. Build reports show the target and whether it came from count or manual settings. No additional setup or test run is required.


## Review my entries

Open **Settings > Review my entries** (also under the saved-entry **More** menu) for the active NFL Classic or Showdown tab. Choose **Generated outputs** for the whole build, including all pages, or **Saved entries** for your saved collection. No extra simulations run and no entries, rankings or settings change.

Sortable tables show player exposure, repeated pairs/trios, repeated full rosters, constructions and unused salary. Showdown adds Captain + FLEX pairs and Captain positions; Classic includes QB receivers, opposing players and game concentrations. Percentages use all analyzed rosters, including repeated entries. Missing salaries/context are disclosed. These are shared-player counts, not measured outcome correlations or opponent duplication estimates. Saved collections may contain multiple builds: review one slate together. **Copy Report** copies the summary and top 20 rows per section; all rows remain available in the tables.


## Optional portfolio comparison

**Settings > Portfolio Comparison** compares the current portfolio selector with an experimental repeated-pair/trio penalty for NFL Classic and Showdown. Choose a saved Deep bank with more candidates than requested entries (1-150). Defaults: 150 entries and 2,000 scenarios per pass. Two passes run: shared selection scenarios, then fresh independent evaluation of both frozen selections. Allow several minutes; Cancel discards incomplete comparisons. This is optional development work, not a required step for every contest.

The trial adds a bounded 6-point penalty on the existing selection scale, normalized by roster core count and requested portfolio size. It supplements existing scenario-coverage rewards; it is not a calibrated risk estimate or a hard player cap. Athlete pairs/trios ignore Captain assignment, while existing Captain constraints remain. Production builds and Individual ranking stay unchanged.

Saved banks omit original group/team/game settings, so this does not recreate the original build. Both trials use minimum unique 2, ownership balancing on, saved player limits and standard Showdown guardrails, without refinement. The report discloses selection shortfalls/relaxations. Fresh evaluation reports average individual top-1% rates, scenarios covered by at least one entry, roster overlap and repeated-core concentrations. One held-out simulation stream does not establish historical accuracy, monetary returns or an improvement. Repeating uses the same seeds. Original forecasts and selected entries remain unchanged. **Copy Report** and automatic JSON/text files under `history/portfolio-checks` preserve the results, including player names and selected roster identities.


## Core Plays from loaded salaries

After loading NFL salaries and refreshing player data, open **Settings > Core Plays** for the active Classic or Showdown output tab. No Deep run is required. This read-only view uses the currently loaded inputs; it does not fetch data, change projections, set exposure limits or lock players. Reopen after changing inputs. Filters show core candidates, all eligible/loaded players, individual signal categories and Captain/FLEX slots. Search by player/team/position; click headers to sort numerically, with unknowns last in either direction. Select a row for workload, sources, history, status-check time, reasons and review notes. **Copy Report** copies every row in the current filtered/sorted view.

Signals are transparent heuristics, not recommendations to lock a player: **Underpriced role candidate** requires points per $1,000 percentile >=75 and projection percentile >=50 among at least four supported positive-forecast peers at the same position/slot. **High-projection anchor** requires projection percentile >=80; equal peers do not count as strictly lower. **Popular play to assess** uses estimated ownership >=10% Classic, >=25% FLEX or >=8% Captain. **Lower-owned alternative** compares same-position/slot players with >=90% of a popular peer's projection, <=110% salary, and both >=5 percentage points and >=30% lower ownership. Similar mean projections do not prove similar ceilings. **Role-change opportunity** requires explicit unavailability in every earlier recorded depth slot. No salary file alone establishes current starters or actual ownership.

Supported roles are eligible starting/promoted QBs, RB1-2, WR1-3, TE1-2, and K/DST in separate positional comparisons. Unknown/deep roles can be inspected under All loaded but receive no positive tags. Missing forecasts differ from explicit zero; neither receives positive tags. Recorded unavailable players, backup/unverified QBs and slot-specific fades are excluded from candidates. Rookie/limited evidence, automatic forecast-check findings, uncertain status, unknown check times and status checks older than 24 hours remain review notes even on otherwise attractive candidates. These signals do not validate data accuracy or injury news.

Showdown uses separate Captain/FLEX prices, forecasts and explicit percentage ownership; missing slot values are not borrowed. Captain and FLEX points-per-dollar are often equal because both salary and scoring scale together, so value alone cannot rank the best Captain. Classic QB details suggest up to three same-team receiving partners from eligible supported roles. Ownership remains an estimate; unverified units show Unknown. Reports contain player names, forecast inputs and data timestamps, not account settings/API keys. The existing Ownership & Leverage view provides post-SIM contender comparisons separately.


## Update one contest in a combined entries file

Save the replacement lineups, click **Update Entries**, and select your DraftKings entries CSV. When it contains multiple contests, choose the contest by name and ID. The dialog shows its entry count and how many other entries will remain unchanged; continue is enabled only when the saved lineup count matches. Same-name contests remain distinct by ID. **All contests** remains available for an explicitly ordered full-file replacement. Single-contest files retain the existing workflow.

Only the selected contest's roster cells change, in existing entry order. Entry IDs, contest metadata, other contests, instructions and embedded salary tables are preserved. Save a new upload file. To edit another contest, select that newly saved file as your next input, then upload the final combined file. Reusing the original download for the second update would omit your first update. This does not submit entries or change contest entry limits. Use replacement lineups from the matching slate.


## Results plus username: automatic snapshot comparisons

For NFL Classic and Showdown, open **Results & Learning**, save your DraftKings username once, then **Import DraftKings Results** or **Import New Results** from your chosen folder. The app identifies your submitted entries directly from standings, including DraftKings entry-counter suffixes. No salary upload or lineup export is required to analyze your scores, ownership versus the field, repeated rosters and available constructions. If files were imported before you saved a username, importing again or **Analyze Saved Results** finds your entries without duplicating the contest. **Copy Report** includes the analysis and comparison diagnostics.

Forecast comparisons automatically search local pre-game snapshots. Ordinary NFL builds save these automatically; repeating identical inputs preserves the original snapshot timestamp. **Update Entries** also records the selected contest ID with the current inputs so future standard `contest-standings-ID.csv` downloads can be linked. Older imports can match a date in their filename (for example `09_10_2026_NFL_Showdown_Results.csv`). No second file is required. A date or previously saved contest-ID association is needed; unknown dates are not guessed from scores or player names.

Matching requires NFL, the same roster format, complete coverage of observed player names without ambiguous identities, one scheduled game date, and a snapshot recorded before the earliest game starts. The latest qualifying snapshot supplies the reference forecasts. It may differ from inputs used for manually edited entries; it does not create an export record or claim exact prediction provenance. Missing, conflicting, post-game, unsupported-schedule or multi-date snapshots leave forecast comparisons unavailable while observed results analysis remains available.

The automatic comparison reports snapshot ID/time, per-player forecast errors and largest misses, Captain/FLEX ownership error separately for Showdown, and unique submitted-lineup forecast error when all player scores reconcile with the reported entry score. Repeated identical entries count once for this forecast comparison. Missing forecasts stay unknown; recorded zero forecasts remain zero. Teams, positions and salaries use saved metadata when available; a standings file alone cannot supply missing prices, depth roles, touches or payouts.

These are calibration diagnostics saved locally, not automatic model changes. Multiple contests from the same game and lineups sharing players are not independent evidence. Existing export-linked validation remains separate. Import results after each slate and use **Copy Report** to review mismatches before changing the model.

![Results analysis with automatic snapshot comparison](docs/images/automatic-results-learning.png)


## Ownership and forecast report corrections

After updating, use **Results & Learning > Analyze Saved Results** once. Older lineup-ownership summaries are rebuilt from observed roster appearances; no salary file or new simulation is needed. Each player's field ownership and each lineup's ownership total now share the same denominator and observed source, including distinct Captain/FLEX slots. The CSV's listed percentages are compared separately as an input consistency check, never used in place of actual roster ownership. Older unversioned summaries are hidden and excluded from ownership-profile calibration until rebuilt; other field metadata remains intact. Cancel leaves the current contest's previous summary unchanged. Original files must still be available for rebuilding.

The summary distinguishes your username-matched results, reconciled snapshot-compared entries and export-linked matches. Snapshot comparisons count repeated submitted entries, while forecast error continues to count identical lineups once per contest. These counts overlap and must not be added together. A low export-match percentage is not a failure to identify your entries or match snapshots.

Snapshot player-error reports show separate groups for recorded starters/eligible QBs, other depth roles/rotation, backup QBs, recorded unavailable players, unverified/excluded QBs and unknown roles. Classification uses only frozen pre-game evidence. An eligible replacement QB stays in the eligible group. Other depth roles are not automatically inactive, and zero outcomes do not determine eligibility. Legacy snapshots with missing role evidence remain unknown. The all-player error is explicitly labeled as including these groups; no forecast, role, lineup or simulation weights change.


## Observed ownership accuracy and username finish summaries

**Analyze Saved Results** now refreshes snapshot ownership accuracy and construction-review ownership using the same observed roster counts as field summaries. Export-linked ownership audits also use those counts. Classic regular slots and Showdown Captain/FLEX remain separate. A player/slot present in the results side table but absent from all parsed field rosters has observed zero ownership. Without one complete field, observed ownership is unavailable; the CSV's listed percentages are not substituted. Existing snapshot ownership comparisons are hidden until refreshed. Forecasts, actual scores and simulation weights remain unchanged.

Finish summaries use all entries belonging to the saved username, independently of exports and snapshots. Reports show covered/total entries, overall and per-contest average finish percentile and top-1% rate. Missing field size, missing rank and out-of-range ranks are excluded with coverage disclosed. Higher percentile is better; percentile is `100 * (1 - (rank - 1) / field size)`. Rank ties share their reported rank, and the top-1% cutoff is rounded up. Overall figures are entry-weighted; repeated submitted entries count separately and do not establish independent predictive evidence. Monetary results and export-linked projection validation remain separate; finish rates do not establish cash rate or ROI.


## Automatically recorded scoring ranges

Complete a normal NFL SIM build before kickoff: Classic Fast or Deep, or Showdown Deep. The app records each player's actual simulated mean and percentiles from the scenarios already used by that build, without an additional simulation pass. **Copy Last Build Report** confirms whether scoring distributions were saved. Compact summaries, input ID, model version, scenario count and capture times stay under `history/scoring-distributions`; raw scenario draws are not retained. Save failure is reported without discarding lineups.

After the games, import your standings in **Results & Learning**, then use **Analyze Saved Results** to refresh comparisons and **Copy Report** to share them. Existing snapshot matching identifies the reference inputs; a scoring record must have that exact input ID and format, a complete scenario run, and a capture timestamp before the player's scheduled kickoff. Missing or ambiguous game identities, invalid records and later replays do not qualify. Old snapshots alone cannot recreate historical scoring ranges, so older results may correctly say unavailable. Continue with your next pre-game build; another long run on an old slate is unnecessary.

The recorded-distribution section separates Classic/Showdown and model versions, then groups observations by position and frozen role. It shows how often actual scores fall inside the simulated p10–p90 range, below it or above it, along with expected simulated tail rates, mean-score error and the largest misses. Quantile boundaries count inside; empirical tail rates account for ties and zero scores, so the expected inside rate is not always exactly 80%. These ranges are not hard minimums or maximums.

One player in one scheduled game counts once across repeated contests within a format, using the latest qualifying capture. Conflicting actual scores are excluded. Captain uses the same base-player outcome, not a second observation. Classic and Showdown can share games, so their sample counts are not additive; players in the same game remain correlated. No projection, ownership, scenario weights or lineup rankings are changed by this diagnostic. Many separate games are needed before drawing conclusions about calibration.

Older ownership aggregates are labeled **Legacy export-metadata ownership difference** because their forecast timing or units may be unverified. Use the snapshot-backed ownership comparison for current accuracy checks.

![Recorded scoring-distribution comparison, illustrative data](docs/images/scoring-distribution-validation.png)


## Showdown Captain search coverage

Normal **Showdown Deep** builds now reserve a bounded part of generation and screening for plausible Captain alternatives. No extra control or results upload is required. Review eligibility requires a positive forecast and recorded eligible/starting QB status, RB/TE depth 1–2, WR depth 1–3, K/DST, a supplied forecast or an explicit Captain lock. Active-player/QB eligibility checks, Captain fades, FLEX locks, another Captain lock and explicit zero exposure limits still apply. Ownership alone neither qualifies nor disqualifies a Captain; unknown unsupported roles are not automatically included.

The search attempts up to 12 candidates per eligible Captain within 10% of the overall candidate budget. It uses at most 30 seconds or 10% of the generation time allowance, whichever is smaller, sharing that time across Captains. These are existing-budget candidates, not additional output entries. Temporary Captain search locks are removed before scoring and selection; user locks are preserved. Candidate shortage can mean insufficient time, budget or feasible constructions, not proof of an impossible lineup.

After coarse scoring, up to three of each review Captain's strongest generated candidates receive shortlist reservations. Reservations share at most 20% of shortlist capacity; existing retained entries take priority. Scarce slots are distributed round-robin, with higher projected players considered first. The remaining shortlist uses the existing selection policy. This applies to both **Individual ranking** and **Portfolio selection**. It improves coverage but cannot guarantee every Captain is tested under every budget or constraint. Saved candidate libraries are not expanded; their existing Captain candidates can receive shortlist reservations.

**Copy Last Build Report** and **Build History** include **Showdown Captain coverage**: generated, shortlisted, fully evaluated and selected counts for each review Captain, plus the best tested rank, top-1% rate and first-place rate including ties. Full evaluation is claimed only when all requested independent validation scenarios complete. Reports distinguish missing candidates, screening gaps, incomplete validation and evaluated-but-unselected alternatives. Ranks are among that build's evaluated candidates and do not measure historical prediction quality.

Coverage does not impose final exposure minimums or change forecasts, ownership, outcome models, or portfolio rules. Final lineup selection can still choose zero of a reviewed Captain. Classic and non-Deep Showdown behavior are unchanged. Run a fresh Showdown Deep build to use the expanded search; old banks are not rewritten. A new build may have different lineups because different candidates were compared.

![Showdown Captain coverage in Build History, illustrative data](docs/images/captain-coverage.png)


## Usage resilience and build-rule integrity

Full NFL data refreshes retry a transient weekly-statistics download failure once (timeouts, connection failures, HTTP 429 or server errors). Successful, schema-checked season downloads are saved under `history/usage-cache`. If a later download fails, a matching season/source cache with a valid content digest can supply the last successful rows. The original download timestamp stays intact. Build input summaries explicitly label cached usage as not freshly downloaded; each player retains the fetch state/date. Current and prior seasons remain separate, and missing usage is never interpreted as an observed zero. Cache loss or corruption leaves the source unavailable. There is no cache to recover until a successful download has occurred. A successful empty season still allows the existing prior-season fallback.

Reload the salary file for a full data refresh after updating; the lightweight pre-build status check does not download weekly usage again. Snapshot replay continues to preserve its original inputs. Results & Learning's **Refresh Free NFL Stats** uses the same download cache and labels cached refreshes. Back up the history folder to retain this cache with snapshots and results.

Normal desktop Classic and Showdown builds no longer automatically weaken minimum uniqueness or raise automatic Showdown exposure caps to fill an output request. If greedy selection gets stuck, a bounded feasibility repair can rearrange candidates under the same maximums, group rules, team/game limits, retained entries and uniqueness. It prioritizes retaining greedy selections, then their quality order, and does not fit or change scoring models. The repair has a 15-second maximum, limited by remaining Deep time. A solver result is accepted only when it is integral and satisfies every modeled constraint; it need not be proven optimal. Existing minimum-exposure shortfalls remain reported rather than guaranteed.

If no complete compliant portfolio is found, the build explains the shortage instead of releasing the incomplete constrained portfolio or silently changing its limits. This is a search limitation, not proof of mathematical infeasibility. Increase candidate/shortlist coverage or explicitly change the requested count or rules before rebuilding. Evaluated Deep banks and pre-game scoring summaries saved before selection remain available. Cancellation still preserves retained entries. Unconstrained generator shortages may still return fewer entries with a warning.

Showdown's experimental opponent sampler now targets underfilled salary bands by drawing a Captain and four FLEX players, then choosing a weighted legal fifth FLEX that completes the requested band. It retains distinct athletes, both teams and legal salaries. Unavailable bands, finite search and cancellation can still cause disclosed shortages/fallbacks; it never copies a roster just to fill a quota. Ownership feedback accepts a lower-error field only if it does not worsen salary-band shortage. Matching the salary mix may therefore leave a larger ownership mismatch. Both diagnostics remain visible; this is not a calibrated joint ownership model or proven improvement in predictive accuracy.

Captain coverage now reports every Captain appearing in generated, shortlisted, validated or selected candidates, alongside reservation-eligible candidates with no generated lineups. Each row distinguishes **reserved-search eligible** from **ordinary search; no reservation**. This closes reporting gaps for successful ordinary-search Captains without expanding the reservation eligibility rules or forcing final exposure.

![Build History distinguishes reserved and ordinary-search Captains](docs/images/captain-coverage.png)


### Clear messages when lineup limits stop a build

A constrained portfolio shortage now opens a **Lineup limits** warning instead of a Python traceback. It lists the strongest blockers at the greedy selection stop: uniqueness, player total/Captain caps (automatic or explicit), team/game caps, specialist Captain caps, and player groups. Candidate counts can overlap; they are diagnostic observations, not proof that the constraints are impossible. No incomplete constrained portfolio is released and no rules are silently relaxed. Broaden the candidate pool or Deep shortlist to supply more choices; increasing scenario count alone does not do that. Reducing output count recalculates percentage caps, so it is not a guaranteed remedy. Unexpected programming errors still retain their traceback for troubleshooting. This handling covers Classic and Showdown.


### Preserve a feasible portfolio before Deep shortlisting

Classic and Showdown Deep builds now search the full screened candidate pool for a complete set satisfying the current maximum exposures, uniqueness, groups, team/game limits and retained entries **before** narrowing the SIM shortlist. The check uses the same limits as final selection, including automatic Showdown caps, with up to 20 seconds from remaining build time. A verified complete set is reserved ahead of optional shortlist reservations; it receives the same later SIM evaluation as other shortlisted candidates. The rest of the shortlist still follows the selected ranking/search mode. No scoring forecasts or exposure limits are changed.

If final greedy selection gets stuck, the app rechecks the preserved set against the current candidates and rules and can use it as a complete fallback, followed by normal refinement where enabled. Output ordering still uses the final SIM ranking. This is a feasible portfolio, not proof of the best possible portfolio. Build reports disclose preservation and fallback use. If no complete set is found within the available pool/time, existing strict shortage handling remains. More scenarios alone cannot replace missing candidate alternatives. Run a fresh Deep build after updating; old saved shortlists are not expanded or rewritten.


### Responsive portfolio feasibility checks

The pre-shortlist check now has an app-enforced solver deadline and cancellation, rather than relying only on CBC's internal time limit. The app polls its own solver process, terminates that process if the budget expires or Cancel is pressed, and cleans up its temporary files. Preparation checks the same deadline/cancellation; the full-pool check avoids expanding shared-roster groups into a large pairwise conflict graph. Classic and Showdown display the feasibility subphase explicitly and record its start/end in the debug log.

A timed-out check cannot supply a partial or unchecked portfolio. The build continues with the ordinary shortlist if the feasibility check finds no complete set within its budget; final exposure/uniqueness safeguards still apply. Cancellation ends the current check. These bounds apply to the portfolio feasibility check, not to all other build phases.


### Successful recovery messages and Showdown salary filtering

A successful preserved-portfolio fallback or feasibility repair is no longer translated into a generic failed-rule warning. Recovery remains visible in the build details; real exposure, uniqueness, group and other warnings remain reported.

Deep Showdown now filters generated candidates, including Captain coverage and loaded-library candidates, before screening and feasibility preservation. Near Cap requires at least cap minus $2,500; Maximize Salary requires at least cap minus $500 ($47,500 and $49,500 respectively on a $50,000 cap). These match the existing Showdown report tolerances. Balanced Spend and Salary Leverage keep their broader salary choices. A conflicting retained lineup produces a clear stop before generation, rather than silently modifying it. Build reports show how many candidates were excluded. If the allowed pool cannot support the requested portfolio, limits remain intact and a shortage is reported.

This does not change Classic salary behavior or turn Showdown correlation review flags into hard bans. Old saved shortlists and completed lineups remain unchanged; run a fresh Deep build to apply the filter before shortlisting.


### Automatic generated-output archives and incremental USB backups

Every build completion now writes a compressed audit ZIP under `history/build-archives`, including all returned outputs beyond the first 150 displayed. Settings > **Open Automatic Build Archives** opens the folder. Each ZIP contains `lineups.json` (ranked slot identities, projections and SIM metrics), `audit-lineups.csv` (readable long-form audit rows, not a DraftKings upload template), a build report, and a SHA-256 manifest. The matching validated input snapshot is included when available; missing snapshots are disclosed. The implementation fingerprint and build settings are recorded. Repeated builds have separate files; cancelled builds with returned outputs are labelled cancelled. Archives exclude bulky per-scenario arrays and do not create export records or imply that any lineup was submitted. Archive failures are visible but do not discard built lineups. Existing builds are not reconstructed retrospectively.

`history_backup.py` is an optional replacement for copying every history file to a new USB folder. Run it only after DFS closes. It hashes local history, copies and verifies new content once, reuses unchanged content, and writes a dated manifest for each successful backup under `D:\DFS-Backups\incremental-v1`. Earlier manifests and content remain unchanged, including files later deleted from the source. Keep the **entire incremental-v1 folder** together; manifests alone are not recoverable backups. Existing dated full backups remain untouched. The first incremental backup still copies all unique content. Subsequent backups avoid rewriting and rereading unchanged USB objects; local files are still hashed. Use `--verify-existing` for a full check of existing USB content; restores always verify content. No automatic retention/deletion policy is enabled.

After installing the updated app code and closing DFS, the prepared `scripts/Launch_DFS_Acer_Incremental.example.bat` can replace the personal launcher. It retains update/launch behavior and calls the incremental backup only after the app exits. Do not replace a launcher that is currently running.

```powershell
.\.venv\Scripts\python.exe history_backup.py --source history --destination D:\DFS-Backups
.\.venv\Scripts\python.exe history_backup.py --source history --destination D:\DFS-Backups --verify-existing
.\.venv\Scripts\python.exe history_backup.py --restore "D:\DFS-Backups\incremental-v1\manifests\CHOSEN-BACKUP.json" --target "D:\DFS-Restore-New"
```

Restore requires a new destination folder, never the live installation. The backup summary reports bytes written, files reused, and time spent hashing the source, copying, and verifying so bottlenecks can be measured. These changes do not enable overnight scheduling or alter lineup generation, ownership, exposures, projections or submission behavior.

### Overnight candidate preparation targets

Settings > **Long Search / Resume** opens Overnight Preparation for NFL Classic or Showdown. Choose **New library**, select a maximum of 1–12 hours, and choose a total saved-candidate target of 12,000, 20,000, 50,000 or 100,000. The default is 20,000. Start / Resume runs all five styles in checkpointed batches; it stops when the target or time limit is reached. Existing candidates count toward the target. Pause and save retains completed batches. Leave the app open and the computer awake; the dialog prevents starting a competing interactive build in this app window. It is not an unattended scheduler and does not change Windows sleep settings.

Saved roster identities are excluded from later batches and resumed searches. Captain swaps remain distinct in Showdown. Five consecutive styles adding no new candidates stop the search with an explanation; this does not prove all possible lineups were searched. Time limits and heuristic searches can finish below the target. App-code or input changes still require a new library to continue searching; existing libraries can still be loaded for a matching slate and checked against current player inputs.

After preparation, close the dialog, use **Load Candidate Library**, and run **Deep** with SIM enabled. Both formats reuse the saved combinations and validate current eligibility, salary and lineup rules. This avoids ordinary candidate generation for that build, but still runs current scenario scoring, feasibility checks and selection. It does not cache scenario scores or automatically submit/export lineups. Start with 12,000–20,000: very large libraries can increase screening time and memory use. No overnight job starts automatically on app launch.

Command-line preparation also accepts `--candidates 20000` with `--hours 8`; both limits are validated before creating a library. The candidate target is not a contest entry limit.
