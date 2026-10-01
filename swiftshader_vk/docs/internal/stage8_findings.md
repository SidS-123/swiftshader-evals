# Stage 8 — controls findings (2026-10-01)

Predictions for every control were committed and pushed before any control ran
(`33b80a9`, `docs/DESIGN.md` "Controls"). This file records what the sweep
measured, what it found wrong with the eval itself, and every change made after
the predictions, with its reason.

## 8.1 The control mechanism

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
- again for defect 2.

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
| (this stage) | `formats_copy_blit` copies clears `b`; `CONTROL_POISON` in the shim (with a forwarding `vkAllocateMemory` for all builds); `instance.py` `build_from`; poison run in `determinism.py` and `gate.sh`; power logging in `controls_sweep.sh` | none: without `CONTROL_POISON` the new wrapper forwards; the control libraries build from the same macros |

`tools/predict_controls.py` and the prediction table are unchanged since
`33b80a9`. Where a prediction missed because the predictor's rule was wrong, it
is restated below *after* measurement, next to the original, and marked so.

## 8.5 Results

(Filled in from the sweep.)
