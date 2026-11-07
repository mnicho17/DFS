# Historical construction research checkpoint — November 7, 2026

This is a read-only research checkpoint, not a strategy or model-default change. Winner means an observed rank-one entrant, including ties; it does not establish profitability. Aggregates are saved in [the companion receipt](historical-construction-2026-11-07.json). Usernames and raw portfolios remain outside Git.

## Coverage and method

The catalog contains 68 result sources and 45 salary sources. There are 49 indexed contest records, including 42 Showdown records across 23 date/game groups and 4,996,498 indexed entries. Referenced indexed result/salary snapshots are present and saved associations agree. These are inventory counts, not a fresh audit of every field roster; none of the 42 records certifies field completeness.

Discovery used September 9–October 18: 36 verified contests, 17 game groups, 1,136 winner/contest portfolios and 959 distinct usernames. There were no excluded contests. The latest six game groups, October 19–November 2, were reserved before examining outcomes. All six are 150-Max; the holdout provides no 20-Max or Single Entry validation.

The held-out cohort uses the existing walk-forward rule: at least three earlier ranked slates and two earlier slates with at least one top-1%-cutoff entry, within the same format, entry target and indexed entry count. Same-date outcomes are excluded from qualification. Earlier held-out dates can enter later history, as declared. Peers match current entry count and earlier ranked-slate count; they are not matched on the exact prior contests, fees or field strength. Only observed current participants are represented.

Held-out dates were reviewed independently with all prior history retained. Each completed report respects the existing 300,000 selected-portfolio-entry bound; the 690,269 total below is a sum of separate complete reports, not an enlarged app report. Source hashes, salary associations, exact slot identities and complete portfolio coverage use the existing reviewer. No mappings, database rows, qualification rules or app settings were changed.

The four-rule bundle means no two kickers, no two defenses, no two quarterbacks and no QB with an opposing DST, across all slots. It does not include a general Captain/opposing-DST restriction.

## Discovery: exploratory construction contrasts

Values below are percentage-point differences between winner-profile means and exact-entry-count peer medians, averaged within contest and then game. Minimum ten eligible peers; support is metric-specific. Median-based contrasts can exaggerate rare patterns and must not be read as population mean effects. Peer medians across categories need not sum to 100%.

| Construction | 150-Max gap; games | 20-Max gap; games |
| --- | ---: | ---: |
| Two kickers | +0.28; 12 | +2.03; 8 |
| Two defenses | +1.28; 12 | +1.89; 8 |
| Two quarterbacks | +1.11; 12 | +5.03; 8 |
| QB with opposing DST, all slots | +0.65; 12 | +1.19; 8 |
| Zero K/DST | +3.11; 12 | +20.40; 8 |
| Two K/DST | +0.02; 12 | -1.92; 8 |
| Three or more K/DST | +0.60; 12 | +1.36; 8 |
| 5–1 | +2.08; 12 | +7.06; 8 |
| 4–2 | -0.26; 12 | +0.47; 8 |
| 3–3 | +1.44; 12 | +1.40; 8 |
| Meets all four grouping restrictions | +1.70; 12 | -0.51; 8 |
| WR/TE Captain with own QB | -2.67; 12 | -18.47; 8 |

The combined four-rule pattern is not consistently more common among discovery winners across entry targets. Zero-specialist usage is also mixed by game: the 150-Max winner-minus-median contrast is positive in six games and negative in six. These observations do not justify blanket bans. Single Entry has only two supported discovery games and no held-out coverage; no Single Entry conclusion is supported.

## Held-out earlier-history cohort

Six independently completed date reports reviewed 5,195 entrant/contest profiles (1,494 distinct users) and 690,269 entries, with zero excluded contests. Support filtering is substantial: 1,127 profiles meet five-peer support and 662 meet ten-peer support. The remaining profiles do not establish a matched result.

The outcome is whether a user has at least one entry within the contest top-1% cutoff, not the fraction of entries finishing there. Cutoffs use the reviewer’s supplied field size when available, otherwise observed accepted entries; completeness is unverified. Rates are equal-game averages, not pooled percentages of all reviewed profiles.

| Minimum peers | Supported profiles | Games | Cohort rate | Peer rate | Gap, pp | Higher/lower games | Omit-one-game gap range, pp |
| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 5 | 1,127 | 6 | 58.37% | 37.68% | +20.70 | 6/0 | +19.22 to +21.95 |
| 10 | 662 | 6 | 52.21% | 38.55% | +13.66 | 6/0 | +11.60 to +15.08 |

This supports continued investigation of earlier-history user cohorts. It does not identify which construction caused a finish, establish ROI, or validate a live field model. Repeated users, reused peer groups, selection of current participants and only six games limit the inference. Omit-one ranges are descriptive sensitivity checks, not confidence intervals.

At ten-peer support, held-out construction usage remains a cohort-mean versus peer-median comparison:

| Construction | Cohort usage | Peer median usage | Gap, pp | Games |
| --- | ---: | ---: | ---: | ---: |
| Two kickers | 4.30% | 0.66% | +3.63 | 6 |
| Two defenses | 1.23% | 0.07% | +1.15 | 6 |
| Two quarterbacks | 39.76% | 30.87% | +8.89 | 6 |
| QB with opposing DST, all slots | 11.54% | 9.04% | +2.50 | 6 |
| Zero K/DST | 43.03% | 43.82% | -0.79 | 6 |
| Two K/DST | 12.25% | 7.78% | +4.47 | 6 |
| Three or more K/DST | 1.23% | 0.00% | +1.23 | 6 |
| 5–1 | 18.78% | 11.49% | +7.29 | 6 |
| 4–2 | 46.74% | 46.89% | -0.15 | 6 |
| 3–3 | 34.48% | 33.56% | +0.92 | 6 |
| Meets all four grouping restrictions | 51.69% | 58.91% | -7.22 | 6 |
| WR/TE Captain with own QB | 81.47% | 83.11% | -1.64 | 6 |

The earlier-qualified cohort meets all four grouping restrictions less often in all six held-out games. This is another reason to avoid automatically imposing the whole bundle, but the median comparator and observational design still prevent a causal rule recommendation. Discovery winners and this earlier-qualified cohort are different estimands; this is not a literal replication of a winner-selection contrast.

## Coverage gaps and next accepted checks

- Nineteen cataloged result sources are unpaired: fifteen Classic and four Showdown. A saved-manifest candidate scan found no compatible salary match; eighteen have same-date candidates with identity/role conflicts and one lacks a same-date candidate. This preliminary scan is not fresh source qualification and does not authorize a new association. Salary reuse across contests is allowed; these were not excluded because another contest used the file.
- Fresh whole-observed-field construction comparisons and mean-to-mean same-entry-count peer checks remain open. The existing equal-user peer-mean review should be evaluated before interpreting median gaps as preferences or advantages.
- The current portfolio review measures QB/opposing-DST usage. It does not measure every Captain/opposing-DST pairing; extend the audited diagnostic using the existing single-entry feature definition instead of substituting the QB metric.
- Add matched finish-rate checks with Captain/salary overlap and evaluate more unseen games before proposing grouping defaults. Preserve minimum-support checks and report unavailable cells.
- These roster/rank reviews do not qualify player-event, zero-score, SIM or payout evidence. Those checklist items retain their own gates.

## Validation and reproducibility

Analysis used runtime commit 9212ba76b532eea8912447c6ab639173acec99d1 and read-only SQLite connections. Original source verification completed within each report. Private receipts include the predeclared plan, source inventory, discovery report, six bounded daily reports, five/ten-peer summaries and game-level sensitivities. The companion aggregate includes hashes of the complete private held-out reports. It is not a substitute historical source or model/cache artifact.

No runtime source changes were made for this checkpoint. PR #200’s Windows run passed all 2,512 tests with no skips/failures or unexpected network attempts; executable, guide, checksum verification and artifact upload passed. Publication was skipped. Visible packaged-app interaction, integration and publication remain separate open gates.
