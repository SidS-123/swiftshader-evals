# evalBase

evalBase is the reusable core of an oracle-graded evaluation of coding
models. It holds everything about such an evaluation that does not depend on
*which* program is being reimplemented: a fixed driver replays scripted cases
against a candidate inside a sandbox, the outputs are scored against a real
oracle's own outputs, a reference cache holds the oracle's runs and the
perturbed runs that define per-case tolerance, controls with written
predictions guard the metric, a hidden split is generated from private
generators, an agent harness hands a coding model a fixed set of tools
through one MCP bridge, and every number of record is measured, cached and
re-gradeable.

It is the code counterpart of the **`lab-ready-eval`** skill: the skill is
the procedure, this repository is the machinery the procedure assumes.
Everything that belongs to one particular program under test -- the driver,
the distance between two outputs, the ledger checks, the corpus generators,
the control shims, the task text -- is supplied by an *instance*.

## What an instance provides

An evaluation is one `evalbase.interfaces.Instance` value in an
`instance.py`, found through `--instance` or `EVALBASE_INSTANCE`:

| Extension point | What it answers |
|---|---|
| `Driver` | how to run one case against the oracle or a candidate and produce an output directory with a `ledger.json` |
| `CaseScorer` | how to read one output, the fidelity distance between two outputs, the procedural checks over the ledgers, the timings |
| `MetricSpec` | which perturbations are run and which are tolerated, the tolerance constants, the categories and their weights, the full-success bars, the metric version |
| `CorpusSpec` | where the generators, the public split, the hidden split and the assets live, and which environment variable names the private generator tree |
| `ControlSpec` | how a control candidate is built from the reference |
| `TaskSpec` | the task text, the tool descriptions the model sees, the artifact, the build and driver commands, the images, the no-key smoke |

Everything else is generic and consumes those: the reference cache with
case-hash stamps and staleness refusal, tolerance from perturbations and
Hill scoring, aggregation with family weighting and renormalisation, regrade
from cached outputs, checkpoint selection and progression curves, the
sandbox with owner-labelled containers and owner-scoped sweeps, the four
launch recipes (Claude Code, Codex, OpenCode, OpenRouter) under one tool
boundary with an audit, the no-key smoke, the site exporter, the controls
summary, the determinism proof. `NEW_EVAL.md` describes each interface with
the toy as the worked example.

The distance is whatever the instance says it is: a block metric over images,
a diff over text, a norm over numeric arrays, a spectral distance over audio.
The tolerance, the scoring, the controls and the reports are the same for
every kind of output.

## The harness contract

The harness gives the model exactly five tools, by fixed name, through one
MCP server (`TaskSpec.mcp_server`, default `evalbase`): `shell` (a command in
the sandbox), `oracle` (the reference on a case the model wrote), `driver`
(the driver on the model's own artifact), `grade_dev` (the real metric on the
public cases) and `checkpoint` (an immutable, hashed snapshot of the source).
The names are the contract the CLI launch recipes, the audit and the smoke
are built on; the descriptions the model reads are the instance's.

## Layout

```
evalbase/
  interfaces.py        the extension points and the ledger contract
  grader/              metrics, containers, runner (refcache + grading + aggregate),
                       regrade, cli, perturbed control, export + sitehtml, jsonio, png
  harness/             workspace, tools (the five tools, sandboxes), mcp_server, attempt,
                       cli_runner + codex/opencode boundaries + audit, openrouter,
                       smoke + stub_cli, run (the operator CLI), privacy
  corpus/              common (hidden-generator loader, float narrowing, writer), determinism
  reports/             controls_summary
examples/toy/          a complete instance that runs end to end without Docker
template/              a skeleton instance with every interface stubbed, a checklist
                       README and the docs templates (DESIGN, REPRODUCIBILITY,
                       PUBLISHING, RUNBOOK, CONTROLS, PERF_VARIANCE, internal/)
tests/                 the suite: the generic machinery, the interfaces, the toy end to end
```

## Making a new evaluation in ten steps

The `lab-ready-eval` skill is the procedure; `template/README.md` is the
checklist; this is the map.

1. Copy `template/` to a repository of its own and fill `instance.py`.
2. Build the oracle from pinned source in Docker; write the driver that
   honours the ledger contract (`evalbase/interfaces.py`); implement
   `Driver.run`.
3. Write `task/TASK.md`, `task/SPEC.md`, `task/CASE_FORMAT.md` and the
   starter workspace; fill `TaskSpec`.
4. Write one generator per family (`generate(split, corpus)`), public cases
   in the instance, hidden cases in the private tree; generate both splits
   (`python3 -m evalbase.corpus.common --instance X both`) and prove
   determinism (`python3 -m evalbase.corpus.determinism --instance X`).
5. Implement `CaseScorer` (distance with a floor and an absolute term,
   ledger checks, timings) and set `MetricSpec`.
6. Build both reference caches (`python3 -m evalbase.grader.cli --instance X
   refcache`); record the tolerance distribution.
7. Write every control's prediction in `docs/DESIGN.md`, implement
   `ControlSpec`, measure on both splits (`grade-ref`, `control <name>`,
   `python3 -m evalbase.grader.perturbed`), regenerate `docs/CONTROLS.md`.
8. Build the solver image; run the four no-key smokes
   (`python3 -m evalbase.harness.run --instance X smoke --harness <h>`).
9. Run calibration attempts (`python3 -m evalbase.harness.run --instance X
   --harness claude-code --model ... --stop-policy submit --budget-hours 12`),
   grade finals idle, build curves with `grade-checkpoints --every 30 --dedupe`.
10. Write the validation report, regrade rather than rerun when the metric
    changes, and publish from a clean export (`docs/PUBLISHING.md`).

## Running the toy

The toy grades a pure-Python noisy rectangle painter (it draws rectangles
into a greyscale image); its driver runs as a local subprocess by default
(`TOY_SANDBOX=none`, a test-only mode) and in `python:3.12-slim` with
`TOY_SANDBOX=docker`.

```sh
python3 -m pip install -r requirements.txt            # numpy 1.26.4, pytest 7.4.4
export EVALBASE_INSTANCE=examples/toy

python3 -m evalbase.corpus.common both                                    # 7 public + 11 hidden cases
python3 -m evalbase.corpus.determinism --split both                       # 18/18 identical
python3 -m evalbase.grader.cli refcache                                   # public reference cache
python3 -m evalbase.grader.cli --corpus examples/toy/corpus/hidden \
    --cache examples/toy/runs/refcache-hidden refcache                   # hidden reference cache
python3 -m evalbase.grader.cli grade-ref                                 # the reference: 1.0
python3 -m evalbase.grader.cli control stub                              # the null band
python3 -m evalbase.grader.cli control half_samples                      # a tolerated perturbation
python3 -m evalbase.harness.run smoke --harness openrouter --sandbox none   # no key, no Docker
python3 -m evalbase.harness.run smoke --harness claude-code --sandbox none  # the stub CLI + real bridge
python3 -m evalbase.grader.export --runs-root examples/toy/runs --site examples/toy/runs/site

EVALBASE_NO_DOCKER=1 python3 -m pytest -q                                # 384 passed, 3 skipped
python3 -m pytest -q tests/test_docker_smoke.py                          # one container in python:3.12-slim
```

Measured on the toy (`examples/toy/docs/CONTROLS.md`): reference 1.000 on
both splits, stub 0.021 / 0.014, half samples 0.985 / 0.980 on fidelity,
brightness x2 and stale output 0.000 on fidelity (stale output 0.126 on the
hidden sequences).

## Tests

`python3 -m pytest -q` runs the suite; set `EVALBASE_NO_DOCKER=1` to skip
the one test that starts a container. The suite needs no oracle, no CLI and
no key: containers are replaced by inspected argv, CLIs by a stub that speaks
the real launch protocol and the real MCP bridge, and the toy's driver runs
locally.

## Licence

MIT (`LICENSE`). The toy's oracle is part of this repository; a real
instance states its oracle's licence in its own README.
