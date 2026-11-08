# Synthetic DST marginal specification

Declared November 7, 2026. Research only; no historical outcome files are read and no production imports, parameters, forecasts, draws or defaults change. This follows the alignment diagnostic in PR #204. The seven-game outcomes already seen are not a new validation set.

## Fixed family and assumptions

Use X = max(-4, location + spread * Z), Z standard normal. The -4 lower bound matches the existing DST score floor. This continuous approximation has an atom at -4 and an unbounded Gaussian upper tail; it does not reproduce discrete scoring events or claim a finite maximum legal score. No additional upper clipping is introduced. It is a marginal-only specification, with no QB/opposing-DST linkage or empirical correlation guarantee.

The reference latent spread is max(3, 0.8 * max(0.1, forecast)), taken from the existing baseline DST scale with its default position CV. It deliberately does not claim to reproduce player-specific volatility adjustments, the full production mixture of game environments or its exact marginal distribution. Fixed sensitivity factors 0.75, 1 and 1.25 bracket this reference. They are arbitrary symmetric sensitivity choices, not fitted estimates or recommendations. All three must be reported; do not choose a winner from the already-seen seven games. Actual realized SD differs from latent spread because of the floor.

Solve the location numerically so analytic E[X] equals the supplied forecast (absolute residual <= 1e-9). With d = location + 4, z = d/spread, Phi normal CDF and phi density:

- E[X] = -4 + d*Phi(z) + spread*phi(z).
- E[(X+4)^2] = (d^2 + spread^2)*Phi(z) + d*spread*phi(z).

Bisection uses a fixed 100 iterations and a bounded bracket [-4 - 40*spread, forecast]. A forecast exactly at -4 requires the degenerate constant -4 distribution; forecasts below -4 are infeasible and rejected. The synthetic implementation supports forecasts [-4,100], latent spreads <=200 and only the three declared factors. These are explicit research budgets, not production forecast restrictions. No sample recentering, empirical normalization, random search or random draws are used. Cancellation before, during or after the solve withholds the result.

## Fixed synthetic exercise and verification

Forecast grid: 0, 2, 5, 10, 20. Report all fifteen cells: analytic mean, realized SD, floor probability, p10/p90 and probability of scoring at least 8. The [retained synthetic receipt](dst-marginal-synthetic-2026-11-07.json) contains no historical actuals or forecast substitutions. Synthetic means are mathematical fixtures, not revised forecasts.

Ten regressions verify grid reconciliation and reproducibility, legal lower bounds, a closed-form half-normal oracle, an independent 40,000-cell density-integration oracle for mean and SD, exact-floor degeneracy, near-floor/high-bound means, unchanged global RNG, invalid values/budgets, cancellation during/final solve and final grid check, and the necessity of correcting location after clipping. Together with existing alignment and reconciled-event regressions, 32 focused tests pass; no unexpected network attempts. Complete-suite/Windows verification is tracked separately on the PR head; prior-head CI is not new-head evidence.

## Remaining acceptance work

This checks mathematics, not predictive calibration. No historical replay, scoring-source access, source qualification or evidence mutation is part of this change. Before any predictive comparison, freeze the sampling/seed method, protected-offense and parent-RNG parity checks, source-qualified unseen window, exclusion rules and game-weighted marginal scores. Retain all factors and report means, dispersion, tails, CRPS, support and game sensitivities without selecting favorable cells. A dependence proposal is a separate step after marginal calibration; the Gaussian-tail approximation and sensitivity factors remain unvalidated. Preserve existing bounded reads, identity checks, saved evidence, reconciliation, AR-01/AR-02 and cancellation safeguards. No production activation is authorized by these synthetic results.
