# Running attempts and producing numbers of record

## Before the first attempt

- Reference as candidate at 1.0 and the null band measured on both corpora
  under the current grader, today, on this host.
- All controls green or restated.
- Every harness kind's no-key smoke passing.
- The host idle: no control sweep, no cache rebuild, no other measurement.

## Launch recipe

Record every parameter; the manifest carries them all so a rerun is exact:
harness kind, model id, reasoning effort, stop policy, rate-limit wait,
budget hours, checkpoint minutes, sandbox CPUs and memory, the corpus and
cache the final will be graded against, the CLI version.

- One attempt per model at a chosen effort; the comparison of interest is at
  equal effort, so record the effort and prefer the same one for every strong
  model.
- No more attempts in parallel than the host can isolate (two 4-core
  sandboxes on a 12-core host worked); never a sweep alongside them. Parallel
  attempts share memory bandwidth and host grading work, so in-run
  performance readings are noisier; finals graded idle are not affected.
- Launch detached (`nohup setsid`), keep the pid, and hold a tracked wait in
  the coordinating session so the end of the run is a notification, not a
  hope. A subagent told to "watch" a job may end its turn and never return.
- If a CLI needs an update to accept a new model, update it, record the
  version change, and say in the report that the earlier runs used the older
  CLI.

## After the run

1. Read the stop reason and the incident list. An infrastructure incident
   that cost the model its tools means the run is repeated; the compromised
   run stays on disk and in the report, labelled.
2. Grade the final on the idle host, after every attempt has ended, against
   the hidden corpus: this is the number of record.
3. Build the checkpoint curve at a fixed cadence with deduplication; the
   curve's last point must equal the final of record.
4. Per-family fidelity means, worst cases, and the model's own dev-grade
   history are the material for the analysis; keep the transcript, the
   checkpoints and the grade with the run.
5. Regrade, never re-run, when the metric changes; candidate outputs are
   cached per case for exactly this reason.

## What calibration taught (so predictions for new evals are grounded)

Patterns from one eval's calibration runs; expect them, then measure them.

- The leading models land mid-range, well short of the ceiling, and lose
  slices of nearly every family rather than failing only the exotic ones.
- The null band is real: a small model landed in it three times in three
  attempts. A score inside the null band is not signal.
- Models given an oracle probe it: they run controlled experiments against
  the reference to recover undocumented defaults and numerical details. This
  is legitimate and worth reporting.
- Under submit policy, models differ in when they stop: one submitted after
  most of the budget, one after a small fraction of it, others ran to the
  wall. Record it.
- Two runs of the same strong model at different efforts are not comparable;
  the second attempt at equal effort is the first open item after
  calibration.
