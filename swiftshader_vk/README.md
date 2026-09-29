# A new evaluation: the checklist

Copy this directory to your instance root (a git repository of its own),
fill in every `...` and `<placeholder>`, and work through the list in order.
Each item ends with a commit and a measured number where it says so. The
procedure behind the list is the `lab-ready-eval` skill; the interfaces are
described in `NEW_EVAL.md` at the evalBase root, with the toy instance
(`examples/toy`) as the worked example.

## 1. Task and oracle

- [ ] Pick a program with a public API, a deterministic output and a job that
      takes a strong model a working day. Freeze the API version.
- [ ] Build the oracle from source in Docker: base image pinned by digest,
      packages pinned by version. Record the image id in every report.
- [ ] Write `task/TASK.md` (what the model sees; keep the
      `<!-- harness: stopping policy -->` line) and `task/SPEC.md` (the
      graded subset). Everything the grader checks is documented here.
- [ ] Write `task/starter/` (the initial workspace) and `task/CASE_FORMAT.md`.

## 2. Driver and corpus

- [ ] Write the driver: replays a case against the oracle or a candidate and
      writes `ledger.json` under the ledger contract. Build it into the
      trusted image; ship only the binary in the solver image.
- [ ] Implement `MyDriver.run` in `instance.py`; override `inspect_case` with
      your limits.
- [ ] Write one generator per family in `corpus/gen/`, exposing
      `generate(split, corpus)`; compute in float64, narrow once. Public cases
      only: the hidden functions go to the private tree named by
      `CorpusSpec.hidden_env`. Most cases are sequences with mutations.
- [ ] `python3 -m evalbase.corpus.common --instance . both` writes both
      splits; `python3 -m evalbase.corpus.determinism --instance . --split both`
      reports every file identical.
- [ ] `python3 -m evalbase.grader.cli --instance . refcache` for both splits
      (hidden: `--corpus corpus/hidden --cache runs/refcache-hidden`).
      Record the tolerance distribution (min/median/max, at floor, at cap).

## 3. Metric

- [ ] Implement `MyScorer.distance` with a per-block floor and an absolute
      term; `procedural_checks` derived from the reference ledger only;
      `timings` if your ledger differs from the contract.
- [ ] Set `MetricSpec`: which perturbations are run, which are
      tolerated, the constants, the weights, the bars. Version it.
- [ ] Unit-test the distance on synthetic snapshots (identical 1.0, gain low,
      noise at the reference level above 0.9, shifted low).

## 4. Controls

- [ ] Write every prediction in `docs/DESIGN.md` before the first
      measurement: reference 1.0, stub null band, each tolerated perturbation
      >= 0.9, a bias low, stale output low, hardcoded-public ~1 public / null
      hidden, malformed and crash without a grader exception.
- [ ] Implement `MyControls.build` and one directory per control.
- [ ] Measure on both splits: `grade-ref`, then `control <name>` for each;
      `python3 -m evalbase.grader.perturbed --instance .` for the perturbed
      reference. Regenerate `docs/CONTROLS.md` with
      `python3 -m evalbase.reports.controls_summary --instance . --write`.
- [ ] Any miss is a metric defect (fix, re-measure everything) or a wrong
      prediction (restate it, dated, with the reason).

## 5. Harness

- [ ] Build the solver image (toolchain + driver binary, unprivileged user,
      no oracle, no grader source). Set `TaskSpec` (artifact, build command,
      driver command, tool descriptions, instructions, `smoke`).
- [ ] `python3 -m evalbase.harness.run --instance . smoke --harness <h>` for
      each of openrouter, claude-code, codex, opencode: `NO-KEY SMOKE PASSED`.
- [ ] Verify the tool boundary on the wire for each CLI you will use (point
      it at a recording endpoint and read the `tools` array), and again after
      every CLI upgrade.

## 6. Calibration attempts

- [ ] One attempt per model at a recorded reasoning effort, submit policy,
      12 h cap, `--cpus`/`--memory` identical across attempts.
- [ ] Grade finals on the idle host after every attempt has ended; build
      the checkpoint curve with `grade-checkpoints --every 30 --dedupe`.
- [ ] Any infrastructure incident: keep the run, labelled, and repeat it.

## 7. Reports

- [ ] `docs/VALIDATION.md` (host, corpus, thresholds, controls predicted vs
      observed, metric history, timing spread, harness, attempts, defects,
      open items), `docs/CONTROLS.md` and `docs/PERF_VARIANCE.md` generated
      between markers, `docs/DESIGN.md` with the decision log,
      `docs/REPRODUCIBILITY.md`, `docs/RUNBOOK.md`. Maintainers' voice.

## 8. Publish

- [ ] `docs/PUBLISHING.md`: private tree pushed to a private remote; clean
      history export; no credential-shaped path in any commit; numbers of
      record everywhere. The maintainer runs the export and the push.
