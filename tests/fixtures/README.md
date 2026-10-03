# CO-01 golden evidence

`co01-golden.json` was captured from clean main
`46a501f6b35add46bcb1650d3d795f1216ed1584` before changing application code.
The capture used `test_co01_parity.evidence()` with no objective argument, then
repeated the computation and required exact equality before freezing the fixture.

The fixed synthetic slates come from the existing NFL Classic and Showdown test
fixtures. Real workers run Classic Fast SIM and Showdown Deep with bounded,
explicit candidate/scenario budgets. No generator, simulator, or selector is
stubbed. The SIM wrapper only records its inputs and returned scores. The
Showdown fixture includes screening, validation, and ranking-audit SIM stages.

The isolated child process sets `PYTHONHASHSEED=0`: main's exposure tables use set
iteration to order tied counts. Without this, equivalent runs can display those
ties in a different order. Timestamps and elapsed/budget receipt fields named
`seconds` or ending `_seconds` are excluded; all candidate signatures/counts,
scenario metrics, numeric strategy outputs, selected signatures/order, exposures,
DK rows, Entry Safety, portfolio reports, and remaining build report content are
compared exactly. Only additive objective fields/labels are removed from CO-01
output before comparison. No numeric rounding or tolerance is applied.

The Core Plans integration also excludes the additive Showdown safety-policy
report field and line, the disclosed feasibility method label, and report blank
line placement. Preselection SIM candidate and scored-row lists are compared by
signature because the reviewed diversity witness may reorder their reservation
sequence. Their complete identity sets and every metric per identity remain
exact. Final selected lineup order, exports, exposures and safety evidence are
still compared without reordering. The new policy and Core Plan behavior have
separate integrated portfolio and UI assertions; the original fixture is retained.

Both fixtures compare legacy missing objective, explicit Tournament, Double-Up,
and Multiplier against that same original main evidence. Each child installs the
network-denying disposable test environment and asserts zero network attempts.
Run with `python scripts/run_isolated_tests.py test_co01_parity`.

Do not regenerate this fixture from the implementation under test to make a
failure disappear. A future intentional strategy change requires a separately
reviewed baseline and explanation.
