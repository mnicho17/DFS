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

## Follow-up: fresh field and peer-mean audit

The unchanged receiver/specialist reviewer completed a fresh per-entry audit of all 42 contests and 4,996,498 entries in 681.438 seconds, with zero excluded contests. Original sources, salary associations and index inventory were rechecked before publishing the report; the original database size and modification time were unchanged. The [fresh aggregate receipt](fresh-field-peer-means-2026-11-07.json) contains coverage, five/ten-peer checks, per-game results and matched finish-rate support. Raw usernames and portfolios remain private.

This scan leaves 19,638 unresolved entries unavailable (14,692 discovery; 4,946 later), rather than borrowing metadata from an identical-looking lineup. All 1,628 observed winner/user-contest portfolios were inspected; five incomplete portfolios have unavailable rates. Among 283,694 eligible-size peer/user-contest portfolios, 1,914 were withheld in full because of unresolved roster or username identity. Counts are contest observations, not independent people. There are no missing ranks in this capture, and complete-field certification remains 0/42.

The new comparisons use equal-user **means**, not peer medians. Peers are in the same contest with the exact same indexed entry count; every current winner/tie is excluded from the peer pool. A winner's entire indexed portfolio must resolve. WR/TE Captain pairing uses only that user's receiver-Captain entries as its denominator; specialist-count percentages use all entries. Five and ten eligible peers are checked separately. Winner-minus-field contrasts use entry-weighted resolved field usage, including winners. Both comparisons average within contest, then within game, then equally across games.

The later-period table below selects **current observed winners**, not the earlier-qualified walk-forward cohort used above. It is a descriptive later-window check, not a new prospective validation. Fresh identity exclusions also differ from the earlier cached portfolio review, so differences from the first table cannot be attributed solely to replacing medians with means.

Ten-peer support, percentage-point usage differences:

| Pattern | Discovery 150-Max: winner minus peer mean | Discovery 20-Max: winner minus peer mean | Later 150-Max: winner minus peer mean |
| --- | ---: | ---: | ---: |
| WR/TE Captain with own QB | +1.83 | -5.74 | +1.75 |
| Zero K/DST | +1.70 | +18.21 | -0.43 |
| One K/DST | +0.34 | -9.66 | -0.63 |
| Two K/DST | -1.85 | -8.29 | +1.07 |
| Three or more K/DST | -0.19 | -0.26 | -0.02 |

The columns cover 12, eight and six games respectively. Specialist comparisons support 685, 348 and 458 winner portfolios; receiver comparisons support 668, 290 and 446. Single Entry has only two discovery games, with one receiver-supported winner/game; no general Single Entry conclusion follows.

The observed-field specialist contrast is similar: winner-minus-field two-K/DST usage is -1.82 pp in discovery 150-Max, -7.97 pp in discovery 20-Max and +0.94 pp later. The positive later peer-mean average is sensitive to one game: only one of six games is positive, five are negative, and the omit-one-game range spans -1.18 to +1.57 pp. A positive pooled direction is not a consistently higher preference across games. Discovery 150-Max zero-K/DST usage also has mixed signs (five positive, seven negative).

### Captain/salary-matched finish-rate checks

These are feature-present minus baseline top-1%-cutoff rates, using resolved ranked entries within the same contest, exact Captain identity and salary band. Each stratum requires ten entries per side in this table; five-entry sensitivity checks, top-5% and rank-one endpoints are retained in the aggregate. Overlap weights are n0*n1/(n0+n1), then contests average within games and games equally. The nonzero specialist comparisons each use zero K/DST as their baseline and exclude other counts. K/DST Captain strata cannot have a zero-specialist baseline and therefore do not contribute to those matched contrasts. Other construction choices are not held fixed.

| Comparison | Discovery 150-Max | Discovery 20-Max | Later 150-Max |
| --- | ---: | ---: | ---: |
| WR/TE Captain with own QB versus without | +1.31 pp | +0.52 pp | +0.15 pp |
| One K/DST versus zero | -0.32 pp | -0.45 pp | -0.15 pp |
| Two K/DST versus zero | -0.89 pp | -0.89 pp | +0.68 pp |
| Three or more K/DST versus zero | -0.04 pp | -0.47 pp | -0.66 pp |

Again, the columns cover 12, eight and six supported games. Later two-versus-zero is higher in three games and lower in three, with an omit-one range of -0.52 to +1.28 pp. Later receiver/own-QB is also three higher and three lower, with an omit-one range of -0.05 to +0.40 pp. Discovery three-plus-versus-zero in 150-Max is positive in only one game and negative in eleven, yet its mean is nearly zero; its omit-one range spans -1.14 to +0.19 pp. Game-level sensitivity matters more than a rounded average.

These are observational associations with unknown field completeness, repeated users/rosters and outcome-selected winners. They establish neither causality nor ROI and do not justify automatic grouping defaults. The audit completes the fresh field/peer-mean and matched-finish checks **for receiver pairing and aggregate specialist counts only**. Separate two-kicker/two-defense flags, team splits, direct all-position Captain/opposing-DST and earlier-qualified-cohort mean comparisons remain open.

## Coverage gaps and next accepted checks

- Nineteen cataloged result sources are unpaired: fifteen Classic and four Showdown. A saved-manifest candidate scan found no compatible salary match; eighteen have same-date candidates with identity/role conflicts and one lacks a same-date candidate. This preliminary scan is not fresh source qualification and does not authorize a new association. Salary reuse across contests is allowed; these were not excluded because another contest used the file.
- Fresh whole-observed-field and mean-to-mean same-entry-count peer checks are complete for receiver pairing and aggregate K/DST counts, as detailed above. Extend the same audit to the remaining patterns and earlier-qualified cohort before treating median gaps as preferences or advantages.
- The current portfolio review measures QB/opposing-DST usage. It does not measure every Captain/opposing-DST pairing; extend the audited diagnostic using the existing single-entry feature definition instead of substituting the QB metric.
- Captain/salary-overlap finish-rate checks are complete for receiver pairing and aggregate K/DST counts. Extend to the remaining patterns and evaluate more unseen games before proposing grouping defaults. Preserve minimum-support checks and report unavailable cells.
- These roster/rank reviews do not qualify player-event, zero-score, SIM or payout evidence. Those checklist items retain their own gates.

## Validation and reproducibility

Analysis used runtime commit 9212ba76b532eea8912447c6ab639173acec99d1 and read-only SQLite connections. Original source verification completed within each report. Private receipts include the predeclared plan, source inventory, discovery report, six bounded daily reports, five/ten-peer summaries and game-level sensitivities. The companion aggregate includes hashes of the complete private held-out reports. It is not a substitute historical source or model/cache artifact.

No runtime source changes were made for this checkpoint or its fresh-field follow-up. The follow-up reran 45 focused reviewer regressions: all passed in 7.704s with zero unexpected network attempts. Aggregate privacy, coverage, complete-portfolio and independent equal-game arithmetic checks passed. PR #200’s Windows run passed all 2,512 tests with no skips/failures or unexpected network attempts; executable, guide, checksum verification and artifact upload passed. Publication was skipped. Visible packaged-app interaction, integration and publication remain separate open gates.
