# Metric design and calibration

## Shape

```
overall = w_fid * fidelity + w_proc * procedural + w_perf * performance
```

Weights are renormalised over the categories present. Full success is a
separate bar: every fidelity case at or above a per-case threshold,
procedural conformance at or above a high bar, every performance case within
a ratio gate of the reference. Full success is the headline; the overall
score is the progress measure. Values that worked for one image-based eval:
weights 0.60 / 0.30 / 0.10, per-case threshold 0.9, conformance bar 0.95,
performance gate 8x. Calibrate your own against the controls.

## Fidelity

- Compare in the space the consumer perceives, not in raw values. For images
  that meant a perceptual colour space after tonemapping, on blocks, at
  reduced resolution. A per-block distance with a floor makes the distance
  proportional to noise variance rather than to its standard deviation, so
  stochastic noise averages away and bias does not.
- **Add an absolute-level term.** A structure-only distance is gain-invariant:
  a candidate whose every output value was twice too large looked perfect. A
  per-block absolute-level term caught it (the uniformly scaled control fell
  from near the top to near the bottom of the range) and left every harmless
  perturbation above the fairness bar.
- Per-case score is the mean over outputs of a Hill function of distance over
  tolerance: `1 / (1 + (D/T)^n)`. With n = 4 (the value that worked), at
  D = T an output scores 0.5; at D = T/2 it scores 0.94.
- Missing outputs score 0; a crash scores 0 for the rest of the case.
- Constant or empty candidate outputs are detected and scored, not skipped.

## Tolerance from the reference

Per case:

```
T = clamp(max(k_noise * noise, k_sens * sensitivity), T_lo, T_hi)
```

- `noise` is the mean output distance between the reference and itself at a
  second seed.
- `sensitivity` is the max over the *tolerated* perturbations (a reduced
  setting of the parameter that controls output noise, a sub-resolution shift
  of a continuous input) of the mean output distance to the reference. The
  perturbation that defines T therefore scores 0.94 by construction and the
  reference scores 1.0.
- `k_noise = k_sens = 2`, `T_lo = 0.03`, `T_hi = 0.30`, n = 4 were the
  constants that worked for one image-based eval; calibrate your own. The
  floor catches static cases where the reference's own noise is zero; the cap
  admits that content at the limit of resolution cannot hold a per-case bar.
- Record the tolerance distribution (min, median, max, count at floor, count
  at cap, which cases sit at the cap) in every report; it is the single most
  informative number about how strict the metric is.

## What to tolerate and what to penalise

Every member of the tolerance set is a decision that moves scores. Measure
each one's effect on the leaders before adding it. Concretely: adding a run
at a quarter of the noise-controlling parameter to the set, so that a
quarter-parameter control would pass, raised the two leading models by a few
hundredths without changing their code. Ignoring the requested value of that
parameter is a defect; it was moved back to "penalised" and the control's
prediction restated as a band (0.75 to 0.95) rather than a pass.

Keep generating the penalised perturbations in the cache anyway: they are the
cheapest fairness measurement you have (see `controls.md`, "perturbed
reference").

## The same idea for other output types

Tolerance-from-the-reference needs only a distance and a set of harmless
re-runs of the reference. Per output type:

- **Text**: distance is a normalised edit distance (or a token-level one);
  `noise` is the distance between the reference and its own paraphrase
  re-run; the tolerated perturbations are the paraphrases you name. A
  candidate that differs by wording within that band passes; one that drops
  or invents content fails.
- **Numeric arrays**: distance is a relative error (elementwise or in a norm
  the consumer cares about); `noise` is the relative error between reference
  re-runs at a second seed or a different converged iteration count; the
  floor handles cases whose reference is exactly reproducible.
- **Audio and time series**: distance in a transformed domain (spectral,
  windowed statistics) with a small alignment search; `noise` from a second
  seed or a small offset; a phase-only or delay-only difference is tolerated
  by construction if you put it in the set, penalised otherwise.
- **Structured logs**: distance on a canonical form (sorted independent
  events, normalised timestamps, stable identifiers); `noise` from a re-run
  with harmless re-ordering; anything outside the canonicalisation, such as a
  missing event or a changed error code, is a penalised difference.

In every case the biased controls (a uniform offset, a stale output, a
constant output) must still fall well below the harmless perturbations, and
the tolerance distribution is still the number to report.

## Calibrating and changing the metric

- Unit-test the metric on synthetic outputs: identical outputs score 1.0; a
  uniform gain scores low; pure noise at the reference's level scores above
  0.9; a shifted output scores low.
- Offline projection is exact if the candidate outputs are cached: any change
  to tolerance construction or the Hill exponent can be scored on every run
  and control without re-running anything. Do the projection on a grid of
  variants against the fairness bars before choosing, and keep the projection
  script and its table.
- A grader command that re-scores a run directory from its cached outputs
  under the current metric (`regrade`) makes a metric change a minutes-long
  operation. Its report records the source report's hash, the source metric
  version, and any case that had to reuse a stored value (that list must be
  empty).
- Performance ratios are reused from the original measurement on regrade,
  because the reference times were re-measured at the last cache rebuild;
  say so.
- **Fairness bars a metric change must keep:** reference at a second seed
  scores at least 0.97 on the category and every case above 0.9 (record the
  exceptions and why); each tolerated perturbation at least 0.93 on the
  category; the biased controls at or below their current scores.
- Version the metric (`METRIC_VERSION`) and stamp it in every cache entry,
  report and manifest. The docstring of the metric module carries the history
  of every version and why it changed.

## Performance

- Score by the ratio of candidate wall time to reference wall time on the
  same host, with a half-point and a full-success gate (16x and 8x worked).
  Put the half-point far outside the measured repeat-to-repeat spread (1.33x
  worst case on an idle host, once), so timing noise costs nothing in score.
- Measure that spread: five repeats of the reference on the idle host, per
  case, min/median/max ratio. Keep the report next to the controls report.
- Timed runs must be long enough that startup cost is negligible (at least
  2 s); rescale cases rather than widening the half-point.
- A fidelity gate on performance cases (the candidate must also match the
  output above a low bar) stops a candidate scoring on speed by producing
  nothing.
