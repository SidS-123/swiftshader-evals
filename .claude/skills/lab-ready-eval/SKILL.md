---
name: lab-ready-eval
description: Build, validate and ship a lab-ready evaluation of coding models against a real piece of software (an oracle), with a hidden split, calibrated controls, an honest metric, a sandboxed agent harness and reproducible numbers of record. Use when asked to design a benchmark, harden an eval, calibrate a metric, run model attempts, or get an eval ready to publish.
---

# Lab-ready eval

A lab-ready eval is one a frontier lab could run tomorrow and trust: every
number is measured, reproducible, and separable from noise, and the ways it
could be gamed have been tried. This skill is the procedure distilled from
building one such eval (a day-long reimplementation of a real library's public
API, graded against the shipped program on a hidden corpus) and from watching
its metric, controls and harness fail in instructive ways. It is written for
an agent that coordinates subagents, but every rule holds for a human team.

The companion code repository is `evalBase` (private): the generic core, a
template for a new eval and a toy example. This skill describes the method;
`evalBase` is where the method is implemented.

Read this file fully. Pull a reference file when you reach its stage:

| Stage | File |
|---|---|
| Choosing the task, the oracle and the split | `references/task-and-oracle.md` |
| Designing and calibrating the metric | `references/metric.md` |
| The control suite: predict, then measure | `references/controls.md` |
| The agent harness and the tool boundary | `references/harness.md` |
| Running attempts and producing numbers of record | `references/attempts.md` |
| The validation report and the decision log | `references/reports.md` |
| Going public | `references/publishing.md` |
| Coordinating subagents on one host | `references/coordination.md` |

## Rule zero: do not underestimate the models

Frontier models are very good now, and they get better between the day you
design the task and the day you publish. Every under-scoped eval saturates:
the top model scores near the ceiling on its first attempt and the number
stops carrying information. Over-scope rather than under-scope. A task that
sounds unreasonable for one day (reimplement a mature library's entire public
API from scratch, with the exotic corners, the asynchronous paths, the error
handling and a performance budget) is the right size: on one such task three
models landed at 0.85 to 0.86 on their first attempt and got a near-perfect
result on two thirds of the hidden cases, and the hard families were the only
thing keeping them off the ceiling. If the calibration runs come back above
0.9, the task is too small; add breadth and depth before publishing, not
after. Design for the model that will exist in six months.

## The ten rules

1. **A real oracle, not a rubric.** Score against a shipped program's actual
   output (the outputs themselves, return values, error codes, timings), never
   against a checklist or a judge model. Pin the oracle by digest and build it
   from source in a container so anyone can rebuild it byte for byte.
2. **A hidden split with a public twin.** The model sees a public corpus and
   its reference outputs; it is scored on a hidden corpus drawn from the same
   generators with different parameters and compositions. Run a control that
   hardcodes the public outputs: it must score ~1.0 public and ~0 hidden.
3. **Controls before models.** Write every control's prediction in the design
   doc first, then measure it on both corpora. Reference-as-candidate must
   score exactly 1.0. A stub that does nothing defines the null band. A
   uniformly biased output, the previous case's output returned for this one,
   a constant plausible-looking output, garbage in a secondary channel, one
   thread, malformed output, a crash: each must land where predicted or the
   metric changes and everything is re-measured.
4. **Tolerance comes from the reference, not from a constant.** Per case, the
   tolerance is a multiple of the distance the reference produces against
   itself under harmless perturbations (a second seed, a reduced setting of
   the parameter that controls output noise, a sub-resolution shift of a
   continuous input). The reference passes by construction; bias fails. Say
   which perturbations are tolerated and which are penalised.
5. **Every number of record is measured on this host, idle, by the current
   grader.** Never quote a projection as a result. Grade finals after all
   attempts have stopped. Keep candidate outputs on disk so a metric change is
   a regrade, not a rerun.
6. **The model only ever sees the sandbox.** Tools reach the model through one
   bridge you control; the container never sees the hidden corpus, the
   reference cache, the grader source, or a credential. Any loss of the sandbox
   mid-run is an incident that invalidates the measurement.
7. **Let the model decide when it is done, with a cap.** Submit policy: the
   model marks its submission final, is asked once to confirm, and the run ends;
   a wall-clock budget is the backstop (12 h worked for a day-long task).
   Record how each run ended; the decision is a behaviour worth measuring.
8. **Reproducible corpus, pinned everything.** Generators compute at a higher
   precision than the consumer reads and narrow once; serialised numbers name
   exactly the value the consumer will read; numeric libraries and base images
   are pinned by version and digest. Prove determinism by generating twice
   with CPU-specific dispatch disabled and diffing every file.
9. **Never tune the metric to a leaderboard.** If the metric changes after
   scores are seen, decide on the fairness controls only, apply the change to
   every attempt identically, keep the old numbers in the report next to the
   new ones, and say in plain words that it was tightened after the scores were
   seen and why.
10. **Write for the reader who was not there.** Reports state what was
    measured, when, on what, with what defects found and fixed. No process
    diary, no "owner", no agent names, no "not yet" lists. Durable decisions go
    in a dated decision log; working notes go in an internal directory the
    public export strips.

## The procedure

Work in stages; each ends with a commit, a status table and a written brief
for the next stage. Do not start attempts until stage 4 is green.

1. **Task and oracle.** Pick a program with a public API, a deterministic
   output, and a job that takes a strong model a working day at the scope you
   would first call too big. Grade the whole API surface, not a curated core:
   the exotic types, the asynchronous paths, the error contracts and a
   performance budget are where the headroom lives. Freeze the API version. Build the oracle in Docker from source, record the image digest.
   Write the task text the model will see and the graded API subset.
2. **Corpus and driver.** A fixed driver replays scripted cases against any
   candidate library. Generators produce the public and hidden splits from the
   same families; the hidden generators live in a private tree loaded through
   an environment variable (see `references/publishing.md`). Build the
   reference cache: the oracle's output for every case plus the perturbation
   runs that define tolerance. Stamp cache entries with a replay hash so a
   stale cache is refused.
3. **Metric.** A few categories with fixed weights (fidelity to the reference
   output, procedural conformance, performance) and a full-success bar.
   Unit-test the metric on synthetic outputs. Calibrate the tolerance on the
   reference's own noise. See `references/metric.md`.
4. **Controls.** Predict, measure, compare, on both corpora. Any miss is either
   a metric defect (fix, re-measure everything) or a wrong prediction (restate
   it, with the reason, in the design doc). See `references/controls.md`.
5. **Harness.** One MCP bridge with a handful of tools (shell in the sandbox,
   oracle run, driver run, dev grade, checkpoint). Native coding-agent CLIs
   with every built-in tool disabled; a no-key smoke that drives the whole
   pipeline with a stub CLI. See `references/harness.md`.
6. **Calibration attempts.** One run per model of interest at a recorded
   reasoning effort, submit policy, a recorded wall budget, and no more
   attempts in parallel than the host has cores to isolate (two 4-core
   sandboxes on a 12-core host worked). Grade finals idle. Build checkpoint
   curves. Re-run anything touched by an infrastructure incident and keep the
   bad run, labelled.
7. **Reports.** A single consolidated validation report, a controls report and
   a performance-variance report generated from the run directories, a design
   doc with predictions and a decision log, a reproducibility doc, a runbook.
   See `references/reports.md`.
8. **Publish.** Private generators backed up to a private remote; a clean
   history export because the generators were once in the tree; placeholder
   links filled; numbers of record everywhere, projections nowhere. See
   `references/publishing.md`.

## Status table

End every stage and every hand-back with this table, filled from
measurements, never from memory:

| Item | State | Evidence |
|---|---|---|
| Oracle image | digest | build log |
| Corpus | N public / M hidden, deterministic (files identical: k/k) | determinism script output |
| Reference cache | entries, tolerance distribution (min/median/max, at floor, at cap) | cache summary |
| Reference as candidate | overall on both corpora (must be 1.0) | report paths |
| Null band (stub) | overall on both corpora | report paths |
| Controls | predicted vs observed, each corpus | controls report |
| Attempts | model, effort, stop reason, elapsed, overall/replay/proc/perf, valid | manifests |
| Tests | passed/skipped | pytest output |
| Open items | who owns each | |

## Things that went wrong once, so the rule exists

- **The models were better than the scope assumed.** A "complete engine in a
  day" task that looked out of reach was 85% solved by three models on their
  first attempt, and the rest was the tail of exotic features. Had the graded
  surface been the comfortable core, the eval would have saturated on day one.
  Over-scope, then let the controls and the calibration runs tell you where
  the ceiling actually is.
Each is a general failure with the one-line form it took once.

- **A structure-only distance is blind to uniform bias.** A control whose
  every output value was scaled by two scored near the top of the range. An
  absolute-level term that a structure-only distance misses fixed it. Run the
  biased controls before trusting any model number.
- **The tolerance set is part of the metric.** Adding a harmless-looking
  perturbation (a further reduction of the noise-controlling parameter) to
  the set raised every leader by a few hundredths without changing a line of
  their code. Measure what each member costs before adding it.
- **Cleanup must be owner-scoped.** A routine that swept every managed
  container, not just its own, destroyed the other attempt's sandbox whenever
  two ran in parallel. Label every container with its owner; sweep only your
  own; record sandbox loss as an invalidating incident.
- **Corpus numerics follow the CPU unless you stop them.** Case files differed
  across CPU ISAs because single-precision transcendentals in the numeric
  library dispatch to different SIMD kernels. Compute at higher precision,
  narrow once, write the shortest decimal that names the narrowed value.
- **One checkout, one owner.** A subagent switched the shared checkout's
  branch under a running job; a coordinator committed with `-a` while an
  agent held the checkout. Doc agents work in worktrees; only the coordinator
  merges; runners stay in the checkout that has the run directories.
- **A promise to watch is not a wait.** A runner agent told to "watch the
  sweep" ended its turn and never resumed; sixteen hours passed. A detached
  job needs a tracked wait in the coordinator's own session.
- **Grade at a cadence, not continuously.** Hundreds of checkpoint grades
  made curves unreadable and slow. Grade every N minutes with deduplication
  of unchanged sources, and grade only the final for numbers of record.
- **Serializers meet non-finite values.** The model's own dev-grade tool
  returned a JSON with an infinite ratio and crashed; every serializer must
  handle them.
- **Rate limits are not stop reasons.** Wait up to a bounded time, record it,
  and never count a 429 as a model decision.
- **Working logs do not become reports.** Docs written as a diary had to be
  rewritten in full before publication. Write in the maintainers' voice from
  the first commit.
