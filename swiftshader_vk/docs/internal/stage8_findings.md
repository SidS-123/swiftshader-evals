# Stage 8 — controls findings (2026-10-01)

Predictions for every control were committed and pushed before any control ran
(`33b80a9`, `docs/DESIGN.md` "Controls"). This file records what the sweep
measured, what it found wrong with the eval itself, and every change made after
the predictions, with its reason.

## 8.1 The control mechanism

(Stage 9 correction: the controls below ran in `ssvk-ref:1`, which also holds
the real drivers. From Stage 9 every candidate and control is replayed in
`ssvk-cand:1`, which holds none, and driver-wrapping controls get the real
driver as a read-only mount; re-graded, the controls score identically
(`stage9_findings.md` 9.1, 9.6).)

Controls are builds of one wrapper ICD (`controls/common/shim.cpp`), each with
one `CONTROL_*` macro (`controls/<name>/build.json`), or the task's starter for
`stub`. The shim dlopens the real driver from the reference image, makes the
oracle's ini directory its working directory, and forwards every call; the
macro alters one thing. Checked before any graded run, on a scratch scene that
is in neither corpus (`runs/shim_check.py`): `reference`, `one_thread` and
`reference_subzero` byte-identical to the oracle with the same call stream;
each altering control changes what it should and nothing else; `crash`,
`malformed`, `init_only`, `stub` end or skip cleanly.

## 8.2 Defects in the eval found by the controls

The controls did their job twice before they were measured: each found a
corpus defect that would have graded candidates on outputs that mean nothing.

1. **Four degenerate advanced-blend cases** (found by `crash`, which scored
   above its prediction). `blend_pub_advanced` and three hidden cases asked for
   `advancedBlendCoherentOperations`, which the reference does not support; the
   device was never created, every snapshot was empty on both sides, and every
   candidate "matched". Stage 7's gate printed skipped ops but did not fail on
   them. Fixed (`660e4c8`): the advanced passes no longer request the feature
   and draw non-overlapping triangles (advanced blending of overlapping
   primitives in one draw is undefined without it); the gate fails any skipped
   op unless the case's meta says `expect_skips` (only `bad_device`).
2. **A case that snapshots memory it never wrote** (found by
   `hardcode_public`, which forwards the oracle unaltered on public cases and
   still lost `formats_copy_blit_pub_copies`). Image `b` was copied into only
   partly and never cleared; its full readback and a buffer copied from it held
   whatever the allocator left. That content is stable inside one process, so
   the Stage 7 repeat and thread-count gates could not see it; any other memory
   layout (the shim's, a candidate's) changes it. Fixed: `b` is cleared first.
   **New gate** so the class cannot recur: `tools/determinism.py` also runs
   every case through the shim built with `CONTROL_POISON` (every new
   allocation filled with 0xA5) and `tools/gate.sh` fails any case whose
   outputs change. This also explains part of the Subzero and lavapipe distance
   recorded for `formats_copy_blit` in Stage 7.
3. **Sequences that cannot show stale output** (found by `stale_frame`, which
   scored above its prediction). The `compute_image` cases' second dispatch
   wrote exactly the first dispatch's image (the per-dispatch push constant
   never reached the destination's channels), so returning the previous result
   cost nothing. Fixed: the constant reaches every channel; both snapshots now
   differ. Some `vertex_input` and `depth_stencil` last passes also repeat the
   previous image (a pass whose correct result is "nothing changes"); those
   stay, as legitimate checks.

## 8.3 Measurement conditions

The first sweep was stopped (`runs/controls.log` records each abort and why):

- after 3 runs, for defect 1;
- again after the host had slept: Windows' Kernel-Power log shows Modern
  Standby 14:54-14:59 and 14:59-15:48 and battery power 13:31-15:48. The
  "54-minute stall" of one `crash` case was the whole WSL VM suspended, not the
  eval; the 600 s reference time recorded once in Stage 7 has the same cause.
  Fidelity and procedural results do not depend on power state (the oracle is
  deterministic), but timings do: the eight perf reference timings, last
  re-measured on battery, were re-timed on AC (timed runs 2.57-3.50 s);
- again for defect 2;
- the final sweep was paused once more when the host left AC (18:13) and
  resumed on AC (20:49), by `runs/s8_resume.sh`, which waits for AC before each
  control and repeats any control whose run ends on battery.

From then on: AC power, a keep-awake request held only while the eval runs
(`SetThreadExecutionState`, no setting changed; lid close still sleeps), power
source logged with every run, and every run's window checked against the
Kernel-Power log afterwards. The Windows power plan is Balanced (the runbook's
"Best performance" is for the paid run); reference and candidates are timed
under the same plan.

## 8.4 Changes after the predictions commit

| Commit | Change | Effect on a control's behaviour |
|---|---|---|
| `660e4c8` | blend generator, `gate.sh` skip rule, `bad_device` meta | none (corpus and gate only) |
| `a1a8ee9` | `formats_copy_blit` copies clears `b`; `compute_image` push constant in every channel; `CONTROL_POISON` in the shim (with a forwarding `vkAllocateMemory` for all builds); `instance.py` `build_from`; poison run in `determinism.py` and `gate.sh`; power logging in `controls_sweep.sh` | none: without `CONTROL_POISON` the new wrapper forwards; the control libraries build from the same macros |
| (Stage 8 close) | per-run grader logs moved to `runs/controls-logs/` (evalBase's summary globs `control-*`); `tools/controls_vs_predictions.py`; `tools/controls_report.py` (CONTROLS.md without hidden case names); docs | none |

`tools/predict_controls.py` and the prediction table are unchanged since
`33b80a9`. Where a prediction missed because the predictor's rule was wrong, it
is restated *after* measurement in DESIGN.md, next to the original, and marked so.

## 8.5 Results

All 38 runs (19 controls x 2 splits) on the corpus at `a1a8ee9`, on AC, with
no standby inside any graded run (Kernel-Power log). Full table and analysis:
`docs/CONTROLS.md`; predicted vs observed: `docs/DESIGN.md` "Controls".

- **32 of 38 rows hit their committed band.** The 6 misses are `crash`
  procedural, `lavapipe` procedural and `nearest_filter` performance, on both
  splits; each is a predictor error (restated after measurement in DESIGN.md),
  none a metric defect.
- Key properties hold on both splits: `reference` 1.000 with full success;
  `stub` 0.013 / 0.007; `hardcode_public` 1.000 public, 0.124 hidden; every
  output alteration zeroes the families it touches and no other; `round_trunc`
  is free; `crash` and `malformed` produce scores without grader errors.
- Perturbed-reference control (offline): `vtxjitter` 0.995 / 0.989,
  `texcoord_ulp` 1.000, ThreadCount 1 1.000; cases below 0.7 under jitter are
  all at `t_hi`.
- Decisions (DESIGN.md log): D5 keeps 16 / 8 / 0.10; the procedural bar stays a
  mean of 0.95; no metric change (`ssvk-1.0` stands); the poisoned-memory
  gate is permanent.

## 8.6 Performance measurements (AC)

Timed-run ratio candidate / reference, the four perf cases (compute, fill,
geometry, texture), public; hidden within 0.06 of these:

| Control | compute | fill | geometry | texture |
|---|---|---|---|---|
| `reference` | 0.99 | 0.95 | 0.82 | 0.98 |
| `one_thread` | 3.60 | 3.22 | 2.05 | 3.48 |
| `reference_subzero` | 4.64 | 1.50 | 1.02 | 1.31 |
| `lavapipe` | 0.73 | 0.49 | (fidelity fails) | (fidelity fails) |

The reference against itself spans 0.82-1.05 (geometry is the noisiest case,
about 18 %); the perf reference times were re-measured on AC at 15:58 after the
battery-era ones. A five-run performance-variance study (`PERF_VARIANCE.md`,
`runs/perfvar/`) is not part of this stage; the spread above is what the
controls show.
