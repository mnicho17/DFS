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
