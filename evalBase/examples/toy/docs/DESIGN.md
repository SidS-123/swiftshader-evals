# Toy: grading design

The toy is the smallest instance that exercises every part of evalBase: a
seeded noisy painter as the oracle, a driver under the ledger contract, a
fidelity distance with a floor and an absolute term, two procedural checks, a
performance case, a public and a hidden split from the same generators, and
five controls. Its numbers are illustrative -- the task is minutes of work,
not a day -- but every mechanism is the real one.

## Trusted boundary

- The candidate owns `/task` and produces `candidate.py`.
- `driver.py` is the only program that calls the candidate. It writes
  `snap_*.pgm` and `ledger.json`; the grader scores those.
- Per-case limits: wall time 10x the reference's recorded time (minimum
  60 s). An exception anywhere in the candidate ends the case with
  `exit: crash`; snapshots not written score 0.

## Fidelity distance (`scorer.py`)

Snapshots are 8-bit greyscale in [0, 1], box-downsampled 2x and cut into 8x8
blocks (16x16 source pixels). Per block

```
d_struct = min(1, rms(ref - cand) / 0.25)
d_lum    = min(1, |log2((m_cand + 0.02) / (m_ref + 0.02))| / 1.0)
d_b      = max(d_struct, d_lum);  d_b < 0.15 -> 0
D        = mean over blocks of d_b
```

A missing, wrong-size or near-uniform candidate snapshot (>= 99.9 % one value
while the reference is not) scores D = 1 outright. The luminance term is what
makes a uniform gain visible; the floor makes D grow with noise variance, so
unbiased noise below the floor costs nothing.

## Per-case threshold

```
noise       = mean over snapshots of D(ref_N, ref_2N)
sensitivity = max over {halfsamples, jitter} of mean over snapshots of D(ref, ref_perturbed)
T           = clamp(max(2.0 * noise, 2.0 * sensitivity), 0.03, 0.30)
```

`halfsamples` draws every snapshot at half the samples; `jitter` shifts the
camera by 1/256 of the image. Both are stored in every refcache entry and
both are tolerated (metric v0.1). Measured on the committed corpus the
thresholds are: public 0.030 (x4), 0.048, 0.089, 0.235; hidden 0.030 (x5),
0.061, 0.062, 0.080, 0.103, 0.194, 0.300 (one case at the cap, a
performance case whose many small edges make it maximally sensitive to the
one-pixel jitter).

## Snapshot score, case score, category

```
s_snapshot = 1 / (1 + (D / T)^4);  s_case = mean over snapshots;  replay = mean over families of mean over cases
```

## Procedural cases

| Check | What it compares |
|---|---|
| `exit_ok` | gate: a crashed candidate fails the whole case |
| `query:<name>` | every query the reference answered (`bounds`, `coverage`, `mean`), numeric within 1e-3, errors and `None` exactly |
| `error_codes` | the whole error stream, consecutive duplicates collapsed |
| `output_matches_reference` | every snapshot within the case's own T (from evalbase) |

Case score = passed / emitted; the category is the mean over cases.

## Performance

`ratio = t_candidate / t_reference` for the draw named by
`meta.timed_run_index`; `score = 1 / (1 + (ratio / 16)^4)`, 0 when the
case's picture scores below 0.5. The toy's timed runs take 0.4-0.6 s on
the development host, under the 2 s rule a real instance follows; the
category is present so the machinery is exercised, not because its ratios
are a measurement worth quoting.

## Overall and full success

`overall = 0.60 * replay + 0.30 * procedural + 0.10 * performance`; full
success: every fidelity case >= 0.9, procedural >= 0.95, every ratio <= 8.

## Controls (predicted, then observed)

Observed with the local driver under metric v0.1 on a shared development
host (the performance numbers are bounds, not measurements);
`docs/CONTROLS.md` holds the generated table.

| Control | Predicted | Why | Observed (public / hidden) |
|---|---|---|---|
| `reference` | exactly 1.0 on every category | the grader must accept what defined it | overall 1.0000 / 1.0000, replay 1.0 / 1.0, procedural 1.0 / 1.0, `full_success=True` |
| `stub` | replay ~0, performance 0, procedural the free checks only; overall < 0.05 | a uniform black snapshot is D = 1 outright; empty queries fail | overall 0.0214 / 0.0143, replay 0.0000 / 0.0000, procedural 0.0714 / 0.0476 (the free check is `error_codes` on a case with no errors) |
| `half_samples` | replay >= 0.9 | it is a tolerance perturbation; the defining run scores 0.94 per snapshot by construction | replay 0.9853 / 0.9798, procedural 1.0, `full_success=True` |
| `brightness_x2` | replay <= 0.2 | the luminance term sees a uniform gain | replay 0.0000 / 0.0000; procedural 0.8661 / 0.8690 (only `output_matches_reference` fails) |
| `stale_output` | replay ~1/snapshots on sequences, 0 on stills | a candidate one step late | replay 0.0002 / 0.1260; the hidden figure is the single-snapshot cases at 0 and the sequences at roughly 1/snapshots; performance 0.5 on hidden because the stale 1-sample warm-up snapshot of one performance case passes the 0.5 picture gate at that case's capped T |

All five land where predicted on both splits.

## Known limitations

- The toy's noise is spatially uniform, so the block floor makes D almost
  bimodal: a perturbation either sits under the floor everywhere (T stays at
  0.03) or clears it in most blocks. A real oracle's output has spatially
  varying noise and a graded T.
- Three hidden cases and one public case have a jitter sensitivity that puts
  T above 0.19; a candidate with a small systematic error scores well there.
- Timed runs are sub-second; see "Performance".

## Decision log

**Metric v0.1 -- the toy tolerates both of its perturbations.** With only two
perturbations run and no calibration attempt, there is no evidence for
penalising either; the machinery for a penalised perturbation (stored in
the cache, its defect recorded, excluded from T) is exercised by the test
suite (`tests/test_metric_versioning.py`) rather than by the toy's metric.
