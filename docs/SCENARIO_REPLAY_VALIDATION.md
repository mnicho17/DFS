# Scenario replay score validation

Cached opponent scores must be finite and sorted. A single pass checks both
conditions, preserving equal scores and exact decoded doubles (including signed
zero). This avoids a second Python traversal and the temporary array slice used
by the previous adjacent-score check. No cached value is recomputed or rounded.

Full-cache validation still completes before any replay hit. Every frame retains
bounded decompression, exact player/field coverage, script checks, ordered frame
IDs, complete count and whole-cache digest verification. Replay decodes and
checks each frame again within the same read transaction. Identity, cache format,
leases, cancellation checkpoints, storage limits and failure recovery are unchanged.
This affects shared Classic/Showdown cache decoding, not their simulation models,
forecasts, ownership, field sampling, payout calculations or lineup selection.

Six decode-only alternating trials (1,000 frames, 5,000 scores) measured median
0.841 to 0.420 seconds. Three alternating full warm Showdown trials measured
4.834 to 3.970 seconds (17.9% less), including setup and verification. All 1,000
frames replayed; outcome generation was prohibited. Exact float-hex result digests
matched the cold run for all trials. Both comparison modes explicitly used the
existing compiled scoring option. This is a synthetic simulation benchmark, not
a whole-build or real-slate speedup claim; see the raw [receipts](benchmarks/scenario-validation-replay-2026-10-24.json).

Six new regressions cover finite sorted doubles/ties/signed zero, nonfinite
values at every position, inversions throughout a frame, wrong/oversized coverage,
compression trailers/truncation and invalid final frames with valid recalculated
digests. The last check proves semantic rejection before the first replay hit,
followed by ordinary recomputation, even when the stored digest matches.
