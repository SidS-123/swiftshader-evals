# Building an evaluation on evalBase

This document describes the extension points in `evalbase/interfaces.py`,
what the generic machinery does with each, and what the toy instance
(`examples/toy`) does to fill them. Read it beside the `lab-ready-eval`
skill, which says why each piece exists; `template/README.md` is the
checklist.

## The shape

```
                 instance.py (Instance)
   ┌───────────┬────────────┬───────────┬───────────┬───────────┐
   Driver      CaseScorer   MetricSpec  CorpusSpec  ControlSpec  TaskSpec
      │            │            │           │            │          │
   grader.runner: refcache (n1, n2, perturbations) -> thresholds -> grade_case -> aggregate
   grader.regrade: the same from cached outputs, no driver
   grader.cli:     refcache | grade | grade-ref | control | regrade | sweep
   harness:        workspace <- TaskSpec; tools (shell, oracle, driver, grade_dev, checkpoint)
                   -> mcp_server -> claude-code | codex | opencode ; openrouter loop
   reports:        controls_summary, perturbed control, export + site
```

An `Instance` bundles the six objects with the instance root, the runs
directory and the trees hashed into an attempt's version record. Every
command line takes `--instance <dir or file>[:ATTR]`, or reads
`EVALBASE_INSTANCE`; a handful of environment variables override where the
caches and corpora live (`EVALBASE_REFCACHE`, `EVALBASE_REFCACHE_HIDDEN`,
`EVALBASE_CORPUS_PUBLIC`, `EVALBASE_CORPUS_HIDDEN`, `EVALBASE_ASSETS`,
`EVALBASE_IMAGE`, `EVALBASE_SOLVER_IMAGE`, `EVALBASE_PLATFORM`).

## The ledger contract

The one thing every driver must honour, because it is what makes the
reference cache, the regrade and the site generic:

```
<outdir>/ledger.json
  {"exit": "ok" | ...,
   "events": [{"op": "run", "wall_seconds": 0.5, ...},
              {"op": "snapshot", "name": "snap_000", "files": {"<channel>": "snap_000.<ext>"}},
              ...],
   ... anything the instance's checks read ...}
```

`snapshot` events in order are the outputs the fidelity category scores;
`run` events carry the wall times the performance category uses
(`CaseScorer.timings` can read them from elsewhere). A candidate that
crashed leaves `exit` at something other than `ok`, or no ledger at all; the
grader records the return code and the signal beside it.

Case files are JSON with `name`, `family`, `category`, `meta`, `assets` and
an `ops` list; `Driver.inspect_case` counts snapshots as `snapshot` ops by
default. `meta.timed_run_index` names the run a performance case is timed
on.

## Driver

```python
class Driver(ABC):
    def run(self, case_path, outdir, assets_dir, *, candidate=None, sample_mult=1,
            perturbation="", timeout_s=600.0, cpus=None, memory="8g",
            extra_mounts=(), extra_env=None, owner=None) -> DriveResult
    def inspect_case(self, path) -> {"name", "snapshots", "assets", "bytes"}   # optional override
```

`candidate=None` selects the oracle. `sample_mult` multiplies the oracle's
sampling effort (the cache runs the oracle at 1x and 2x to measure its own
noise; a deterministic oracle may ignore it); `perturbation` names one of
`MetricSpec.perturbations` and is only ever set for the oracle. `owner`
labels any container this run starts; `evalbase.grader.containers` has
`docker_base` (labels, limits, no network), `container_name` and `run_named`
(kills the container on timeout or interrupt) so an instance's driver gets
ownership right without re-implementing it. `template/instance.py` shows the
Docker shape; the toy's `ToyDriver` runs `driver.py` locally or in
`python:3.12-slim` and reads `TOY_SAMPLE_MULT` / `TOY_PERTURB`.

## CaseScorer

```python
class CaseScorer(ABC):
    primary_channel = "color"
    def load_output(self, outdir, event, channel=None) -> array | None
    def distance(self, ref, cand) -> float             # D in [0, 1]; None/wrong size -> 1
    def preview_rgb8(self, output) -> HxWx3 uint8 | None   # optional: pictures
    def block_defects(self, ref, cand) -> 2-D grid | None  # optional: heatmaps
    def block_pixels(self) -> int; def floor(self) -> float
    def timings(self, ledger) -> list[float]
    def procedural_checks(self, case, ref_ledger, cand_ledger, ref_dir, cand_dir, threshold) -> list[dict]
```

The distance is the whole fidelity metric of the instance: a defect D in
[0, 1] between a reference output and a candidate output of the same
snapshot. What kind of output is the instance's business -- an image (a
block distance with a structural and a luminance term), a text document (a
normalised edit distance), a numeric array (a relative norm), an audio
buffer (a spectral distance). Two properties the methodology needs, whatever
the kind: a floor, so that unbiased noise below the reference's own level
costs nothing and the defect grows with noise variance; and an absolute
term, so that a uniform bias (a gain, an offset, a constant shift) is
visible -- a purely structural distance is invariant to it. Missing,
wrong-size and degenerate (near-uniform, empty) candidate outputs score 1
outright.

`procedural_checks` returns `[{"check", "ok", "detail"}, ...]`; the case
scores the fraction that pass. `evalbase.interfaces.generic_checks` gives
every instance the crash gate (`exit_ok`) and `output_matches_reference`
(every snapshot within the case's own T, so a candidate cannot pass the
ledger while producing nothing); an instance appends its own. Every check is
derived from the reference's ledger for that case, never from an expectation
written into a generator.

`preview_rgb8` and `block_defects` are optional: an instance whose outputs
have no natural picture returns None and the workspace, the site and the
export carry numbers without pictures.

The toy (`examples/toy/scorer.py`): 8-bit PGM images, 2x downsample, 8x8
blocks, `max(rms/0.25, |log2 ratio|)` with a 0.15 floor; checks
`query:<name>` and `error_codes`.

## MetricSpec

```python
MetricSpec(version, perturbations, tolerance_perturbations,
           k_noise=2.0, k_sens=2.0, t_lo=0.03, t_hi=0.30, hill_n=4,
           weights={"replay": .6, "procedural": .3, "performance": .1},
           fidelity_category="replay", procedural_category="procedural", performance_category="performance",
           perf_half=16.0, perf_gate=8.0, case_bar=0.9, procedural_bar=0.95)
```

`build_refcache` runs the oracle at N, at 2N and once per `perturbations`
entry; every perturbation's mean defect is recorded, only
`tolerance_perturbations` raise T:

```
T = clamp(max(k_noise * D(ref_N, ref_2N), k_sens * max_tolerated D(ref, ref_p)), t_lo, t_hi)
s_snapshot = 1 / (1 + (D / T) ** hill_n)
```

The tolerance set is part of the metric: adding a member widens T for every
candidate. The version is stamped into every cache entry and report; a cache
entry calibrated by an older version is recalibrated from its own outputs on
the fly (`runner.entry_threshold`) and permanently by `refcache
--recalibrate`, without running the oracle. A cache missing an output of
record is stale and rebuilt.

The categories are the keys of `weights`; the three above are the default
set, and `fidelity_category`, `procedural_category` and
`performance_category` say which key plays which role. An instance may
rename them, re-split the weights (a category it does not measure gets
weight 0 and no cases), or add a category of its own, which is scored as a
mean over its cases and gated by `case_bar`.

`aggregate` averages the fidelity category per family, the others per case,
renormalises the weights over the categories present, and judges full
success (every fidelity case >= `case_bar`, procedural >= `procedural_bar`,
every performance ratio <= `perf_gate`) over the categories present.

## CorpusSpec and the generators

```python
CorpusSpec(root, gen_dir, public_dir, hidden_dir, assets_dir,
           hidden_env="EVALBASE_HIDDEN_GEN", hidden_default=<root>-hidden,
           hidden_package="evalbase_hidden_gen", forbidden_hidden_roots=(root,),
           double_keys=(), format_version=1)
```

One module per family in `gen_dir`, exposing `generate(split, corpus)`;
`corpus` is a `CorpusWriter` with `write(name, family, category, ops,
split=...)`, `asset(array)` (sha256-named files) and
`hidden_generate(__file__, split)`. The public module's hidden branch is
that one line; the private tree has `gen/<same module>.py`, loaded by path
under a synthetic package so it cannot shadow the public module it imports
from. Generation of the hidden split stops with one message naming
`hidden_env` when the tree is absent, before anything is written; the loader
refuses a tree inside `forbidden_hidden_roots`.

`CorpusWriter.write` narrows every float to the shortest decimal naming its
float32 (double-precision data under `double_keys` gets 15 significant
digits). `python3 -m evalbase.corpus.determinism --instance X` generates
twice with numpy's SIMD kernels disabled and diffs every file.

The toy ships its private tree inside the instance (`hidden-private/`) and
sets `forbidden_hidden_roots=(root/"corpus",)`; a real instance keeps the
default and a private repository.

## ControlSpec

```python
class ControlSpec(ABC):
    root: Path
    def names(self) -> list[str]
    def build(self, name, out_dir, *, owner=None) -> Path      # the candidate directory
```

`grader.cli control <name> [--variant v]` builds the control, reads an
optional `control.json` beside it (`mounts`, `env`, `variants`,
`default_variant` -- extra read-only mounts and environment for the *driver*
runs only) and grades it like any candidate, recording the variant, mounts
and environment in the report. `python3 -m evalbase.grader.perturbed` scores
the cache's own perturbed outputs against the thresholds they defined.

The toy's controls are `candidate.py` files that subclass the oracle;
`build` copies the oracle beside them.

## TaskSpec

```python
TaskSpec(task_dir, instructions, tool_descriptions, artifact, build_command, driver_command,
         spec_dir=None, format_doc=None, dev_extra={}, solver_image, reference_image,
         mcp_server="evalbase", source_suffixes, source_names, required_names, unit_suffixes,
         smoke: SmokeSpec | None, isolation_check="")
```

`workspace.populate` builds `/task` from `task_dir/starter`, `TASK.md` (whose
`<!-- harness: stopping policy -->` line is replaced by the attempt's actual
policy) and `SPEC.md`, the frozen `spec_dir`, `dev/cases` (the public
split), `dev/assets`, `dev/reference/<name>/` (the cache's primary-channel
outputs plus PNG previews when the scorer has them), `dev/CASE_FORMAT.md`
and `dev_extra`; `spec/` and `dev/` are read-only. The five tools -- `shell`,
`oracle`, `driver`, `grade_dev`, `checkpoint` -- are the harness's contract:
their names are fixed (the launch recipes, the audit and the smoke depend on
them), their descriptions are the instance's. `driver_command` runs inside
the sandbox with `{case}`, `{outdir}`, `{assets}` filled in; `build_command`
runs in a clean sandbox at grading time and must produce `artifact`, which
the driver then receives as a directory. `SmokeSpec` is what the no-key
smoke's fake model writes and runs through the oracle.

## Running things

| Command | What |
|---|---|
| `python3 -m evalbase.corpus.common --instance X [public\|hidden\|both]` | generate |
| `python3 -m evalbase.corpus.determinism --instance X` | the determinism proof |
| `python3 -m evalbase.grader.cli --instance X refcache [--force\|--stamp\|--recalibrate\|--trust-unstamped]` | the reference cache |
| `... grade --candidate DIR`, `grade-ref`, `control NAME [--variant V]` | grade |
| `... regrade --run DIR \| --attempt DIR` | rescore from cached outputs |
| `... sweep [--owner ID \| --all --yes]` | container cleanup |
| `python3 -m evalbase.grader.perturbed --instance X` | the perturbed-reference control |
| `python3 -m evalbase.reports.controls_summary --instance X --write` | the controls tables |
| `python3 -m evalbase.grader.export --instance X --runs-root DIR --site DIR` | the site bundle |
| `python3 -m evalbase.harness.run --instance X --harness H --model M ... --out DIR` | an attempt |
| `... smoke --harness H [--sandbox none]`, `resume`, `grade-checkpoints`, `report`, `cleanup`, `versions` | the rest |

`--sandbox none` (and the toy's `TOY_SANDBOX=none`) runs the model's shell
on the host with no isolation. It exists for tests and for instances whose
driver is plain Python; it is never a measurement configuration. The Docker
sandbox is the default and the only one a number of record may come from.

## What is not here

Anything that belongs to one program under test: the driver source, the
distance, the procedural checks, the corpus generators, the control shims,
the task text, the reference and solver Dockerfiles. The toy shows one small
answer to each.
