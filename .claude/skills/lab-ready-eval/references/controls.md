# Controls: predict, then measure

A control is a candidate whose score you can predict before running it. The
design doc carries the prediction; the controls report carries the
observation; a miss changes either the metric or the prediction, never the
observation. Measure every control on both corpora, and re-measure all of
them after any metric or reference change.

## The suite

Bands are the ones observed in one image-based eval; predict your own.

| Control | What it is | Prediction | Why it exists |
|---|---|---|---|
| reference | the oracle's own library as the candidate | exactly 1.0 on every category, both corpora | the grader must accept what defined it |
| stub | compiles, answers every call, produces nothing | the null band (0.04 to 0.06 overall, once) | a fraction of conformance checks are satisfied by doing nothing; know the floor exactly |
| perturbed reference (second seed, half noise parameter, small shift) | the oracle with a different seed, half the noise-controlling parameter, a sub-resolution shift of a continuous input | category at least 0.93; per case at least 0.7 except cases at the tolerance cap | the tolerance must accept what it is built from |
| quarter noise parameter | the oracle at a quarter of the requested noise-controlling parameter | penalised: category 0.75 to 0.95, noisy cases may score near zero | ignoring a requested parameter is a defect, and the report says how much it costs |
| uniform gain | every output value scaled by two | low on fidelity (about 0.2) | a uniform bias must be visible; this is the control that exposed a gain-invariant metric |
| constant output | every output a constant, plausible-looking value | near the null band | a plausible-looking output with no content |
| stale output | returns the previous step's output | fidelity 0.25 to 0.4 | sequences catch a candidate that is one step late |
| auxiliary garbage | correct primary output, garbage in a secondary channel | fidelity 1.0, conformance slightly down | secondary channels are checked but do not dominate |
| one thread | the oracle throttled to one thread | fidelity unchanged, performance 0.87 to 1.0, full success false on the performance gate | performance is independent of fidelity; the gate is for candidates, not shims |
| hardcode public | returns stored public outputs by case name, empty otherwise | 1.0 public, near null band hidden | the split is what defeats memorisation, not the metric |
| malformed | wrong output format, absurd size, a crash | near the null band, no grader crash | hostile output must not take the grader down |

## Rules

- Write the prediction as a number or a band and the reason, in the design
  doc, before the first measurement. Restating a prediction after a miss is
  allowed if the restatement is dated, reasoned, and the old prediction stays
  visible.
- A control that reads case names from the driver's arguments (hardcode
  public) is legitimate; the point is to show the split defeats it.
- Controls are built by a shim over the reference library (forward every
  call, alter one thing) so they exercise the same driver path as a candidate.
- The controls report is generated from the run directories by a script with
  begin/end markers; humans edit the prose outside the markers only. The
  script prefers a regrade directory when told which metric version is of
  record, and footnotes which reports came from a regrade.
- Never run the control sweep while attempts are live: it uses every core and
  the attempts' performance readings would be contaminated.

## Reading a miss

- Reference below 1.0: a flaky check. Find it (once it was an asynchronous
  cancellation race; a wall-time tolerance fixed it) and re-measure the whole
  suite under the fixed grader, from the beginning.
- A biased control scoring high: the metric is blind to that bias. Add the
  term that sees it, then re-measure everything and show every harmless
  perturbation still passes.
- A harmless control scoring low: either the tolerance is too tight for that
  content (cap it, and say which cases sit at the cap) or the control is not
  actually harmless (the quarter-parameter run), in which case restate the
  prediction as a penalty band.
- A control scoring identically to another (hardcode public on hidden equals
  constant output): check it is by construction (both fall back to the same
  output), then say so in the report.
