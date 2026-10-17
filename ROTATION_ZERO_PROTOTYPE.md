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
