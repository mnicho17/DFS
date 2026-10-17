# Rotation zero-score prototype

Diagnostic only. Normal builds keep the existing simulator defaults. The private
scenario entry point accepts an explicit RotationZeroPrototype; no GUI setting
or optimizer path enables it. Input forecasts and transformed offensive means
are preserved in expectation, conditional on each original score draw.

The hurdle probability is min(0.95, exp(-(carries + targets * catch_rate))).
A nonzero draw is divided by its survival probability. Only frozen, qualified
automatic workload estimates for unpromoted RB/WR/TE rotation roles qualify.
Sampled opportunities are the prototype default; frozen opportunities are a
comparison mode. Neither mode fits parameters to historical outcomes.

Gates use a separate deterministic stream. Shared continuous workload budgets,
QB, DST and untargeted offense draws remain identical. Gates run before kicker
feedback. This does not allocate discrete real plays; kicker means can change
through nonlinear feedback, and finite samples need not reproduce means exactly.

## Historical experiment

Read-only audit completed October 16, 2026: eight qualified games, 102 observed
rotation player-games, three fixed seeds and 2,000 scenarios per seed/profile.
One representative contest per game was selected before reading outcomes;
exact frozen pregame inputs and original source identities were revalidated.
Missing outcomes were not converted to zeros or filled from other contests.
Oversized outcome files, including SEA-DEN, retained the existing two-million-row
bound and were excluded. Results are exploratory retrospective comparisons,
not a holdout evaluation or proof of calibration.

Equal-game averages:

| Measure | Baseline | Frozen hurdle | Sampled hurdle |
| --- | ---: | ---: | ---: |
| CRPS (lower is better) | 2.6490 | 2.5455 | 2.5449 |
| 80% interval coverage | 40.1% | 73.5% | 73.3% |
| Predicted zero frequency | 0.0% | 23.9% | 24.4% |
| Zero Brier score | 0.3054 | 0.1646 | 0.1636 |
| SIM mean minus actual | -0.2494 | -0.2459 | -0.2477 |
| Team offense mean | 81.3230 | 81.3453 | 81.3339 |
| Team offense SD | 30.1282 | 30.8180 | 30.8489 |
| Team offense p90 | 120.4328 | 121.2106 | 121.1491 |
| QB / receiving correlation | 0.5422 | 0.5222 | 0.5216 |
| Rotation-pair correlation | 0.2346 | 0.1370 | 0.1375 |
| Kicker mean (available teams) | 8.7996 | 8.8000 | 8.8020 |

For sampled opportunities, mean within-game CRPS change was -0.4164 on actual
zero outcomes but +0.0381 on positive outcomes (a small regression). Paired team
mean shifts ranged from -0.0948 to +0.1435 points; team SD increased by
0.1924 to 1.4346 points, and p90 shifted by -0.1426 to +1.4363 points.

Both hurdle modes improved CRPS in seven of eight games. Actual zero frequency
was 30.5% with equal-game weighting. The largest absolute paired team kicker
mean shift was 0.0252 points for sampled opportunities (0.0218 frozen).
Mean preservation does not preserve correlations: rotation-pair correlation
weakened materially. More independent games, team opportunity conservation and
positive-outcome tail checks remain necessary before considering defaults.

## Reproduce

Run from the repository with an appropriate Python environment:

```powershell
python scripts/run_isolated_tests.py test_rotation_zero_prototype test_nfl_specialists test_nfl_workload test_co01_parity
python scripts/audit_rotation_zero_prototype.py --db <exports.sqlite> --output-prefix <output-path> --completed-before 2026-10-16
python scripts/run_isolated_tests.py
```

The audit opens the database read-only. Its JSON includes exclusions, source
hashes, snapshot receipts, per-player scores, team interaction summaries and
model identity. Strict model/cache identity includes the prototype source;
normal-build behavior is additionally checked against a prechange seeded golden.


## Dependence and positive-outcome follow-up (diagnostic only)

Use `--interaction-study` on the same audit command to add three predeclared
comparisons: 25% shared team gates, half-strength independent hurdles, and
half-strength hurdles with 25% shared team gates. Existing prototype defaults
remain unchanged, and normal builds still do not invoke any hurdle.

The shared-gate parameter is the probability that a team's rotation players
use one common uniform draw in a scenario. Otherwise they use independent
uniforms. Each player's marginal zero probability remains unchanged; same-team
joint zero probabilities increase. Teams receive separate gate draws. This is
a dependence sensitivity test, not an empirically fitted football mechanism.
The hurdle strength multiplies the original zero probability; positive draws
still divide by survival probability, preserving conditional offensive means.
Half strength reduces both the number of zeros and the required positive uplift.
It is not possible to retain the original positive conditional mean, introduce
zeros, and preserve the original unconditional mean simultaneously.

Reports include CRPS separately for actual-zero and actual-positive outcomes,
player p95, team simultaneous-rotation-zero frequency, offense SD/p90, QB and
rotation correlations, and kicker feedback. All comparisons use the same seeds,
qualified pregame evidence and source safeguards. Controls are not selected or
fitted from these historical scores. Correlation alone cannot select a winner:
shared gates can create excessive simultaneous failures or team upper tails.


Why the original correlations fall: conditional on a baseline score X and zero
probability q, the transformed score has mean X and additional variance
X^2*q/(1-q). Independent gates add no conditional cross-player covariance.
Pearson correlations therefore weaken even when the original cross-player
covariance is retained. Shared gates add covariance through simultaneous zero
and positive events; this does not demonstrate that their joint pattern matches
real football outcomes. A weaker hurdle reduces both variance inflation and
zero-outcome benefits. These are structural tradeoffs, not forecast-mean bugs.


### Follow-up results, October 17, 2026

Same eight games / 102 outcomes / three seeds x 2,000 scenarios per profile.
All source/evidence revalidation and protected-draw assertions passed. Four
new tests cover control bounds, conditional means/uplift, marginal versus joint
zeros, and RNG/input/order invariance. No forecasts or production data changed.

| Profile | Overall CRPS | Positive-outcome CRPS | Zero-outcome CRPS | Predicted zeros | Rotation correlation |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 2.6490 | 3.2020 | 1.2886 | 0.0% | .2346 |
| Original sampled hurdle | 2.5449 | 3.2401 | .8722 | 24.4% | .1375 |
| 25% shared gate | 2.5494 | 3.2448 | .8774 | 24.4% | .1554 |
| Half-strength hurdle | 2.5805 | 3.1969 | 1.0802 | 12.2% | .1821 |
| Half strength + 25% shared | 2.5837 | 3.2002 | 1.0839 | 12.2% | .1918 |

Subgroups contain 31 zero and 71 positive outcomes, with within-game averages
then equal-game weighting. Observed zeros were 30.5% with that weighting.
Half strength has a tiny positive-outcome improvement over baseline (.0051),
and a larger improvement over the original hurdle (.0433), but substantially
underpredicts zeros. These are exploratory results on the same historical
sample, not independent confirmation of improved forecasting.

| Profile | Team offense SD | Team offense p90 | QB/receiver correlation | Simultaneous rotation zeros |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 30.1282 | 120.4328 | .5422 | 0.000% |
| Original sampled hurdle | 30.8489 | 121.1491 | .5216 | 0.000% |
| 25% shared gate | 30.9822 | 121.4123 | .5202 | 0.064% |
| Half-strength hurdle | 30.3697 | 120.7781 | .5349 | 0.000% |
| Half strength + 25% shared | 30.4242 | 120.7713 | .5337 | 0.031% |

Simultaneous zeros means all eligible rotation players on a team score zero in
one simulated scenario, averaged within game then across games. Simulated rates
are sensitivity diagnostics; the experiment does not validate them against a
large observed joint-event sample. Correlations in the baseline are references,
not established truth. Shared gates raise team variance and do not improve
marginal CRPS here. No profile is selected or activated.

The remaining problem needs more independent evidence and a coherent joint
opportunity/scoring model. A gate alone trades zero calibration, positive-score
shape and dependence against one another. Next validation should reserve new
qualified games for holdout checks and assess observed joint events and
positive tails before considering any default change.


## Finite opportunity allocation follow-up (diagnostic only)

`--joint-study` compares baseline, the original sampled hurdle, finite-count
scoring and finite-count occupancy scoring. `joint_opportunity_prototype.py`
allocates randomized integer carry/target budgets within each recorded team and
position group, using the shared sampled workload probabilities. Every event
has exactly one participant or goes to the unused reserve. Targets can produce
at most one catch, with the frozen catch-rate assumption. The expected assigned
counts equal the original sampled workloads; no teammate shares are refitted.

Count scoring multiplies each qualified rotation score by realized weighted
opportunities divided by expected weighted opportunities. Weights use the frozen
workload's expected rushing and receiving points per event. Occupancy scoring
uses the same counts but only distinguishes zero from positive opportunities;
its compensating positive uplift uses the exact zero probability under the
randomized finite budget. Both preserve conditional means in expectation.
Zero expected opportunities with a positive score fail closed. Parent simulator
RNG, inputs, QB/starter/DST draws and workload dictionaries remain unchanged.
Kicker feedback runs after the altered rotation scores.

This remains a partial joint model: all recorded workload participants compete
for latent opportunities, but only qualified rotation scores use those counts.
QB and starter scores do not yet reconcile to the realized events. Yards,
touchdowns, bonuses and fumbles are not drawn as discrete events. Mean-preserving
residual scaling can inflate tails. Conservation alone is not evidence of
predictive improvement, and no production option enables these models.

The development cutoff is exclusive October 12, 2026. Candidate temporal
validation uses `--evaluation-after 2026-10-11 --completed-before 2026-10-17`.
These boundaries were fixed before examining the new prototype results. Newer
source games do not automatically constitute an independent blind holdout:
previously inspected outcomes must be disclosed. If no games meet the existing
source, pregame evidence and outcome scan safeguards, the report records
`no_qualified_games` with no summary. No scan bounds are relaxed to fill it.


### Finite-opportunity results, October 17, 2026

Development audit: eight games / 102 rotation outcomes / 6,000 scenarios per
profile per game, with all original source/evidence revalidation and protected
player/RNG assertions passing. Neither finite-count variant resolves the issue.

| Profile | CRPS | Positive CRPS | Zero CRPS | Predicted zeros | Rotation correlation |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 2.6490 | 3.2020 | 1.2886 | 0.0% | 0.2346 |
| Original hurdle | 2.5449 | 3.2401 | 0.8722 | 24.4% | 0.1375 |
| Finite counts | 2.5319 | 3.2697 | 0.7795 | 23.2% | 0.0878 |
| Finite occupancy | 2.5484 | 3.2333 | 0.8957 | 23.2% | 0.1401 |

| Profile | Team SD | Team p90 | QB/receiver correlation | Mean player p95 |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 30.1282 | 120.4328 | 0.5422 | 10.9451 |
| Original hurdle | 30.8489 | 121.1491 | 0.5216 | 12.3141 |
| Finite counts | 31.7305 | 122.1648 | 0.5032 | 13.6249 |
| Finite occupancy | 30.7626 | 121.0696 | 0.5250 | 12.2041 |

Counts improve overall CRPS slightly versus the original hurdle, driven by zero
outcomes, but positive-outcome CRPS worsens and upper tails/variance grow. Rotation
correlation moves farther from baseline. Occupancy stays close to the original
hurdle without a clear improvement. Mean preservation and opportunity conservation
are insufficient when existing score noise is multiplied by new count noise.
Baseline correlations are reference values, not verified real-world targets.

Candidate temporal validation: zero qualified games. October 12 BUF-LAR and
October 15 SEA-DEN both exceeded the existing two-million-row outcome bound;
no partial outcomes were used and the scan bound was not changed. The JSON
status is `no_qualified_games`, with empty games and summary. SEA-DEN had also
been inspected in earlier app calibration work, so it must not be represented
as a completely blind sample. Development improvements cannot establish fresh
game performance. New qualified, previously unexamined games remain necessary.

No profile is activated. The next model step requires opportunity-linked score
generation that replaces redundant score noise, reconciles receiver/QB events,
and is validated on independent games. This diagnostic implementation preserves
existing forecasts and protected draws rather than attempting that default change.


## Direct linked-event scoring follow-up (diagnostic only)

`--event-study` compares baseline, the original sampled hurdle, and two
predeclared event variants (fixed completion priors, and a shared team completion
environment of plus/minus 20%). The latter amplitude is reduced if necessary so
all completion probabilities remain valid without asymmetric clipping. The two
states are equally likely, preserving each frozen completion mean. No outcome
scores select the amplitude or fit the event priors.

The model uses frozen workloads, not the existing sampled-share or independent
score draws. Carries/targets/catches use the conserved allocation. Each realized
carry or catch draws nonnegative gamma yardage with shape 4 and its frozen mean,
and a touchdown Bernoulli from its frozen workload rate. Receiver yards and TDs
are mirrored exactly on the verified frozen eligible QB. Interceptions can occur
only on allocated incomplete passes. Unused reserve targets remain unresolved;
they are not converted to completed passes, zero-score players or evidence.
There are no yardage bonuses, fumbles or opponent defensive event effects.

Analytic expected raw points come from those frozen event priors. Each modeled
automatic forecast is preserved by multiplying raw points by forecast divided
by expected raw points. The audit records every factor, expected raw mean,
forecast, simulated mean, standard error and a five-standard-error check. This
normalization is a diagnostic compromise: raw production reconciles, but the
normalized fantasy points are not exact DK totals from the raw stat ledger.
Independently anchored forecasts need not be physically consistent; a coherent
production model cannot hide that conflict. Factor sizes are reported, not fit
or silently clipped. QB/start-role draws intentionally change inside these
experimental profiles; manual/historical forecasts, DST, parent RNG and inputs
stay protected. Kicker feedback follows the new offense. No app default enables
any prototype. Strict model identity includes the event-model source.

The audit separately records all modeled offense and QB/receiver/opposing-DST
relationships alongside the existing rotation-only comparison. Frozen forecasts
are the mean target, not the old simulator's sometimes shifted finite-sample
mean. Normalizer compatibility and lost opponent linkage remain activation
risks even if marginal outcome scores improve. The same development and temporal
boundaries, exact-score observations and read-only source safeguards still apply.


### Linked-event results, October 17, 2026

Development: eight qualified games, 102 rotation outcomes and 166 modeled
QB/RB/WR/TE outcomes; three fixed seeds x 2,000 scenarios/profile/game. All source,
pregame-evidence, protected-input and scoring-identity revalidation passed.
Both new variants preserve frozen forecast means analytically; all 332 per-player
Monte Carlo checks fell within five standard errors. The old simulator means
are not the mean target and can differ from frozen forecasts.

| Profile | Rotation CRPS | Positive rotation CRPS | All-offense CRPS | Predicted rotation zeros |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 2.6490 | 3.2020 | 3.3987 | 0.0% |
| Original hurdle | 2.5449 | 3.2401 | 3.3358 | 24.4% |
| Linked events | 2.5434 | 3.2178 | 3.3323 | 23.1% |
| Linked + completion environment | 2.5339 | 3.2117 | 3.3247 | 23.7% |

| Profile | QB/receivers | Rotation pairs | QB/opposing DST | Team offense SD | Rotation p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0.5422 | 0.2346 | -0.2571 | 30.1282 | 10.9451 |
| Original hurdle | 0.5216 | 0.1375 | -0.2571 | 30.8489 | 12.3141 |
| Linked events | 0.7057 | -0.0184 | 0.0066 | 16.7008 | 10.3251 |
| Linked + completion environment | 0.7836 | 0.0131 | 0.0040 | 21.0475 | 10.4755 |

The completion variant modestly improves marginal CRPS and positive-outcome
CRPS versus the original hurdle. Positive-outcome CRPS remains slightly worse
than baseline. Raw QB/receiver linkage is achieved, but rotation pairs lose the
baseline's shared environment, offense variability shrinks, and the negative
opposing-DST relationship disappears. Baseline correlations are reference values,
not established real-world targets. No result supports activation or proves
fresh-game performance. Temporal validation again has zero qualified games:
BUF-LAR and SEA-DEN exceed the existing scan allowance; no bounds were relaxed.

A separate read-only consistency check reconstructed the same active pools from
the audit's SHA256-verified frozen snapshot receipts (with the same cleared
personal locks). It compared each non-QB forecast against targets x catch rate,
the minimum expected PPR points before nonnegative yards or TDs. Eleven of 150
modeled non-QB player-games, across five games, fall below that floor. Both the
snapshot bytes and scoring identity were rechecked, and no outcome scores were
used to fit anything. The local sidecar records receipts and the original audit
hash. Forecast normalizers span 0 to 3.4050. Thus the frozen event priors and some
forecast means cannot simultaneously produce exact raw DK scoring. Zero factors
are disclosed, not interpreted as zero raw production.

No prototype is selected. Remaining work must reconcile opportunity/event priors
with forecast mean constraints, model shared offense variation and opposing
production/DST effects, and obtain independent qualifying validation games.
Per-player point normalization is explicitly a research compromise, not a
production solution to those conflicts. Defaults and production forecasts remain
unchanged.


## Forecast/event-prior reconciliation and opposing DST (diagnostic only)

`--reconciled-study` compares baseline, the original hurdle, linked-completion
events, reconciled events, and those same reconciled events with opposing DST
linkage. All profiles are paired on the common feasible cohort. Source-qualified
games rejected by the moment constraints remain explicit exclusions and are
counted separately; they must not disappear into an accuracy claim.

A small diagnostic LP reconciles copies of expected carries, targets, catches,
yards and touchdowns to the frozen automatic player forecasts. QB passing yards
and TDs are sums of receiver production, and interceptions occur only on
incomplete allocated targets. Per-position shared budgets remain upper bounds
with unused reserves. Nonautomatic individual carry/target/receiving moments are
fixed; manual fantasy scores remain protected. No source forecast or saved
workload is edited and no observed outcome enters the solve.

The declared mean bounds are 15 rushing yards/carry, 30 receiving yards/catch,
.15 rushing TD/carry, .35 receiving TD/catch, and .25 interceptions/incomplete
pass with at most three expected interceptions. These are research bounds, not
claims of universal physical impossibility. Relative-L1 distance from recorded
prior moments defines the objective with predeclared scale floors. CBC must
return an optimal solution within ten seconds; no feasible incumbent, forecast
edit or bound relaxation is substituted. Constraint/raw-mean residuals must be
within 0.00002 points. Receipts record solver/library identity, executable hash,
parameters, objective and every before/after moment. Calibrated plans carry a
diagnostic provenance marker. Raw offensive points have multiplier exactly one.

Yardage uses symmetric beta(1.5, 1.5) on [0, 2 x mean], preserving the prior gamma
shape-4 mean and variance while providing finite event support. This tail-shape
change is disclosed. It is not a game-outcome fit. Completion environments keep
the same symmetric bounds as the earlier event prototype.

The optional DST result is a forecast-centered residual from the opposing QB's
same raw event score Q, not a full DK defensive stat model:

    DST = forecast + a * (E[Q] - Q) + Uniform(-r, r)

Here r=min(3, (forecast+4)/2), and a is at most .35 and is reduced using the
analytic maximum opposing score so the lower bound remains -4. No clipping is
used. The new noise stream does not change offensive events or the parent RNG.
This construction preserves the DST forecast mean and gives negative covariance
with modeled opposing QB production. It does not claim integer DK scoring,
sack/return/fumble modeling or calibrated DST variance. Only the explicit Historical average estimate DST source is supported. Manual
override, Imported forecast, unknown provenance, recorded manual/imported values,
unavailable or ambiguous/missing defenses, or an unmodeled opposing QB fail closed
when linkage is requested.

A prior-only check found seven of eight previously qualified games feasible.
BAL-ATL is rejected: its BAL QB forecast 7.9012 is below a conservative 8.9570
passing-point lower bound implied by the WR/TE forecasts, available targets,
rushing allowance and interception bound. That is a conflict under this model's
declared assumptions, not a license to alter forecasts or dismiss the game.
The historical audit will report its common feasible cohort and this exclusion.
No normal app defaults enable any of these models.
