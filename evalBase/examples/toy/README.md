# The toy instance

A complete evalBase instance in pure Python: `oracle.py` (a seeded, noisy
greyscale rectangle painter), `driver.py` (replays a case under the ledger
contract), `scorer.py` (a block distance with a luminance term, two
procedural checks), `corpus/gen/` (public generators) and `hidden-private/`
(the hidden generators, shipped here because the toy is a toy), `controls/`
(reference, stub, half samples, brightness x2, stale output), `task/` (what a
model would see) and `instance.py` (the `Instance`).

`docs/DESIGN.md` has the metric, the control predictions and the measured
results; `docs/CONTROLS.md` the generated table. The commands are in the
repository README under "Running the toy".

The driver runs locally by default (`TOY_SANDBOX=none`, test-only) or in
`python:3.12-slim` (`TOY_SANDBOX=docker`); the harness needs `--sandbox
none` for the same reason. Timed runs are sub-second, so the toy's
performance ratios illustrate the machinery rather than measure anything.
