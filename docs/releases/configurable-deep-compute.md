# Configurable Deep compute (test branch)

- Add 1–60 minute NFL Classic Deep budgets and configurable candidate, shortlist, opponent-field, screening, and search-seed counts.
- Raise the scenario control to 10,000 while preserving the Deep validation minimum.
- Preserve Auto pool sizes and five-minute defaults; persist compute settings and include them in recipes and diagnostic reports.
- Preserve player eligibility, portfolio constraints, cancellation, and early stopping.
- No remote worker service or GPU acceleration is added. Run builds directly on the dedicated machine through remote desktop.

- Add Baseline, Balanced, Thorough, Extended, Maximum, and Custom tiers, including validation scenarios.
- Show live Acer-reference runtime ranges based on four completed 150-lineup runs, with wider uncertainty for unmeasured workloads.
- Persist the main validation scenario setting; selecting Custom retains the tier values.

- Add NFL Showdown Deep: seeded exploration, Captain-aware screening and independent validation, shared athlete outcomes with 1.5x Captain scoring, and portfolio refinement.
- Add legal Showdown opponent sampling, SIM Edge display, cancellation/retained-entry tests, and explicit uncalibrated timing/payout-proxy messaging.

### All-style search and ranked results

For NFL Classic or NFL Showdown, enable SIM, choose **Deep**, and open **Compute settings**. Select **Search all five build styles** to share the tier's candidate and time budgets across Strategic, Balanced, Contrarian, Chalk, and Randomized. Each style uses the configured search seeds. Each search receives a share of the remaining generation time so one style cannot consume the whole generation phase. Identical entries are removed before screening; a different Showdown Captain remains a different entry. The report lists actual style candidate counts before deduplication. Locks, fades and player eligibility remain active; ownership preference remains a separate setting.

The combined pool is screened against common scenarios. Its shortlist is evaluated together against a fresh scenario stream and opponent sample. **Individual ranking** shortlists and selects by top-1% finish rate, then top-2%, top-5%, first-place rate, and mean points. It disables automatic Showdown exposure guardrails, diversification bonuses and portfolio refinement. Explicit player, group, team/game and uniqueness rules still affect selection; minimum exposure shortfalls are reported rather than prioritized. Existing uniqueness relaxation is reported if needed to fill the output. Retained repair entries remain included. **Portfolio selection** uses the existing complementary-outcome selection and refinement, then displays that selected group in the same finish-rate order.

Set **Lineups** to 300, 450, or any count up to **1,000**. Results display 150 per page with global row numbers, a page selector, and Previous/Next controls. The finish-rate columns show top-1%, top-2%, top-5%, first-place (including ties), and mean simulated points. The first page is the strongest individual ranking within the selected output. Portfolio rules and exposure percentages apply to the **entire output**, not each page. Choosing a subset can change exposures. All-style or larger-output runtime is uncalibrated; the dialog does not apply the previous 150-lineup Classic estimates to these workloads.

Use **Save page**, **Unsave page**, or individual checkboxes. Saved selections survive page changes and are ordered by finish rates when added. Export still contains only the proper roster IDs; generating more alternatives does not change a contest's entry limit. Finish-rate ordering is for comparisons within the same simulation run, not calibrated comparisons between separate runs. Non-SIM Classic builds keep their grade order. Equal metrics may tie. Budget exhaustion can produce fewer candidates or outputs, and incomplete validation is reported. Ranked results cover tested candidates, not every possible lineup.

Automatic Showdown guardrail relaxation is now explicitly reported when portfolio selection raises its starting caps to fill the requested count.

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

