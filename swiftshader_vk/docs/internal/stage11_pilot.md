# Stage 11 — Opus 5.5 pilot, 1 hour (2026-10-01/02)

PLAN_v1.md §15. Not a number of record. Run directory:
`runs/attempts/opus55-pilot` (launched 23:04:03 CDT, ended 00:09:24 CDT).

## 11.1 Run

| Item | Value |
|---|---|
| Launch | `claude-opus-5-5`, effort high, submit policy, `--rate-limit-wait 24`, 1 h, checkpoints 15 min, 4 CPUs / 8 GB; Claude Code 2.1.287; commit `6c8125c` |
| Stop | `budget_wall` after 3,601 s of solver time; grading 262 s |
| Validity | `measurement_valid: true`; no infrastructure incident; audit: valid tool boundary, 0 violations, 0 errors |
| CLI stream | event types `system`, `assistant`, `user`, `result`, `rate_limit_event`, `tool_progress`: all in the audit's list; no `unsupported_cli_event` from 2.1.287 |
| Tool calls | 198: shell 154, grade_dev 23, checkpoint 18, driver 3, **oracle 0** |
| Segments | 8. Segment 1 (24.6 min) ended with a final answer at public 0.57; the harness's confirmation prompt then ran 7 short segments in which Opus declined to submit, kept working (stencil, blits), and from segment 5 on made no code changes "with too little time left" |
| Usage (list-price estimate, not billed) | 186,521 output tokens, 18.2 M cache-read, 0.61 M cache-write; the CLI's list-price figure is $12.26, which nobody was charged on the Max login |
| Power | AC throughout; sleep and lid close disabled; power mode not confirmed as Best performance (no overlay value stored). Performance scored 0 anyway |

## 11.2 Score (hidden split, `ssvk-1.0`)

**Overall 0.642** (replay 0.616, procedural 0.909, performance 0.000), no
full success. Opus's last public `grade_dev`: 0.6535, so hidden and public
agree within 0.012.

| Family | Mean | Family | Mean |
|---|---|---|---|
| compute_arith, compute_atomics, compute_cf | 1.000 | descriptors_push | 0.750 |
| mem_buf, queries_sync, vertex_input | 1.000 | errors_robust | 0.713 |
| inst_dev | 0.964 | compute_subgroup | 0.500 |
| depth_stencil | 0.933 | mrt_renderpass | 0.245 |
| blend | 0.854 | formats_copy_blit | 0.188 |
| raster_tri | 0.826 | compute_image, msaa, tex_sample, raster_lines_points | 0.000 |
| compute_types | 0.800 | perf_* (4) | 0.000 |

## 11.3 Usage on Claude Max

From the stream's `rate_limit_event`s (14 readings): the 5-hour window went
0.04 → 0.09 and the 7-day window 0.31 → 0.32 over the hour. One attempt hour
is therefore about **5–6 % of a 5-hour window and about 1 % of the week**.

For the 12-hour run this predicts **no 5-hour limit pause** (a window would
take ~0.3) and about **12–15 % of the weekly allowance**. Starting from ~32 %
used, the run fits with a wide margin. The readings are coarse (two decimal
places) and the hour was the light, early part of a build, so the margin
assumes up to twice this rate.

## 11.4 Findings

1. **The harness works end to end on a real model.** Valid boundary, all
   five tools offered, four used, checkpoints taken, final rebuilt and graded
   on the hidden split.
2. **0.64 in one hour is high.** Lavapipe, a complete conformant driver,
   scores 0.74. Eight families are already at ≥ 0.93 and six compute/buffer
   families at 1.0. The unsolved surface (textures, MSAA, lines/points,
   compute images, copies/blits, render passes, performance) is real work, so
   the 12-hour run is the measurement that decides whether the scope
   saturates (Stage 14 verdict). This is recorded now, before the run, so the
   verdict is not read off the result alone.
3. **Opus offered to stop at 24 minutes** (public 0.57) and, once asked to
   confirm, chose to keep working. Under the submit policy the 12-hour run
   ends whenever Opus types `SUBMISSION FINAL`; the pilot suggests it does
   not do so while families are visibly failing.
4. **The oracle tool went unused.** Opus worked from the public cases'
   reference outputs. Legitimate; reported with the attempt of record.
5. **Detached launch survives the launching shell.** `nohup setsid` from a
   one-off `wsl.exe` kept running after that shell exited.
