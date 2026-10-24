# Showdown opponent field setup

The final FLEX search reuses the Captain and first four FLEX teams within each
proposal. It evaluates the original salary arithmetic and two-team requirement
in the same available-player order, without recreating the same partial roster
for every possible final player. Random draws, weights, salary-band targets,
shortage fallback, cancellation checkpoints and ownership fitting are unchanged.
No field-model version or scenario-cache identity change is needed: the exact
ordered field and diagnostics remain the same. Classic generation is unchanged.

Six alternating synthetic trials with 5,000 opponents and seed 90211 measured
median field setup of 2.372 seconds before and 1.984 seconds after (16.4% less).
All ordered roster/FLEX identities, diagnostics and ownership-fit data matched.
This measures field setup only, not complete builds or real-slate performance.
The fixture uses fallback ownership; calibrated ownership and historical models
also retain their existing regression tests. Timing depends on local load.
Raw receipts: [benchmark](benchmarks/showdown-field-setup-2026-10-24.json).

Six baseline-digest regressions captured from main 1096cfe cover normal draws,
fractional salaries, one/three-team pools, lower-cap fallback and cancellation.
Digests include ordered FLEX identities and all salary-band diagnostics.
