# Historical Showdown field use

Showdown Deep can explicitly use saved field construction history. The default
field remains the salary-band model. Historical use is experimental; structural
fit and simulated top-1% changes do not establish better realized returns.

1. Import and map results/salaries, then index mapped contests in Username History.
2. Load the current Showdown slate and choose **Prepare field history…** in the
   SIM controls. Select the contest's entry limit and prepare the profile.
3. Choose **Use this profile for Deep builds**, enable SIM, and use Deep.
4. Copy the build report. Its sampled opponent field says **applied**, **baseline
   fallback** with a reason, or **not enabled**. A build without fresh field
   evidence discloses that application cannot be established.

## Evidence and sampling

Profiles require three earlier games and at least 1,000 known entries. Each
included contest requires 95% known construction coverage, exact verified saved
result/salary sources, a qualifying saved association, one dated game, current
index version, and no excluded conflicting EntryIds. Coverage describes observed
entries; a complete contest field is not inferred. Presets are inferred only from
explicit recorded names. Unclassified contests and other entry limits are excluded.

One contest with the most known constructions represents each date/game, with a
stable contest-key tie break. Games receive equal weight. Finish ranks, winners,
successful-user selection, and the user's Captain choices do not train this field.
Current-day and later results are excluded, including when replaying old snapshots.
Missing kickoff information blocks historical use rather than substituting today's
date. A frozen earlier cutoff may be reused for a later slate.

The five marginal categories are team split, Captain position, quarterbacks,
combined kicker/defense slots, and salary left. Sampling blends 35% historical
probabilities with 65% of the current sampled field's construction distribution.
Current eligible athletes, salaries, and verified slot ownership remain the inputs.
Historical athlete identities are never copied to the slate. The sampler clears
personal locks for opponents, as the existing field does.

At most two ownership calibration passes are used. Historical proposals must fill
the requested field, have no worse distance to the blended construction targets,
and increase mean slot ownership error by no more than 0.50 percentage points
relative to the current field. Otherwise the current field is retained. Missing
verified ownership also retains the current field. Infeasible construction targets
are disclosed; no hard quotas or changes to the user's portfolio limits result.
Historical spending profiles require the recorded $50,000 cap; a different cap
retains the current field with a disclosed reason.

Preparation runs in a cancellable worker. Profiles are frozen in snapshot
calibration, with source hashes, dates, per-game counts/distributions and a profile
digest. Sources and associations are checked again before and after a build.
Changed history requires preparing the profile again. Screening, scenario replay,
ranking banks and ranking audits carry the same profile; scores from a different
field profile are not reusable. Roster preparation itself remains reusable.

## Held-out measurement

In Username History, **Compare qualified pregame fields + SIM** can include the
guarded production blend. It freezes entry-limit profiles before the selected
training cutoff, qualifies exact recorded pregame ownership/forecasts/eligibility,
and compares current, historical and production fields on the same candidate bank
and scenario outcomes. Later outcomes are evaluation only. Baseline fallbacks are
included and labeled. Insufficient profile or pregame evidence is reported as an
exclusion, not silently reconstructed from current data.

An initial local salary-proxy experiment on seven later games reduced average
construction distance from 0.171 to 0.091 with the pre-existing unblended historical
sampler. That is a hypothetical structural comparison, not validation of this
production blend or a reason to change the default.

A subsequent recorded-input comparison qualified seven later games with three
fixed seeds per game, 300 opponents and 200 shared scenarios on a frozen bank.
The production blend reduced average construction distance from 0.0990 to 0.0909
and mean slot-ownership drift from 2.48 to 2.20 percentage points. It applied
history in 11 trials and retained the baseline in 10. Kicker/defense construction
distance increased slightly (0.0881 to 0.0909). Two games exceeded the existing
outcome CSV bound, so no partial actual-outcome evaluation was released for them.
These are preliminary local measurements, not evidence of better ROI or a
validated solution to kicker/defense-heavy portfolios. No parameters were chosen
from these held-out outcomes.

This change does not alter projections, candidate generation, ranking formulas,
Captain eligibility or portfolio constraints. The optional opponent field can
change simulated competitive metrics and consequently selected lineups. Backup
storage/retention and Classic calibration migration remain separate work.
