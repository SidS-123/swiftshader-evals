# Reproducibility

What a fresh clone needs, how to rebuild everything, and which numbers are
bit-reproducible versus a property of the host that produced them.

## Host requirements

| need | why |
|---|---|
| Docker | every driver run, build and grade runs in a container |
| Python 3.11+ with numpy (pinned in `requirements.txt`) | generators, grader, harness |
| pytest | the test suite only |
| the pinned oracle source / release | the reference |
| `claude` / `codex` / `opencode` CLI, logged in | a `--harness` attempt of that kind only |
| `OPENROUTER_API_KEY` | `--harness openrouter` or `opencode` only |

## The rebuild sequence

```sh
python3 -m pip install -r requirements.txt
swiftshader_vk/images/build_ref.sh           # ssvk-ref:1 from images/pins.lock; prints the image id (~1 h cold, 16 cores)
<build the solver image>
export SSVK_HIDDEN_GEN=../swiftshader-evals-hidden           # the private generators; public needs none
python3 -m evalbase.corpus.common --instance . both
python3 -m evalbase.grader.cli --instance . refcache
python3 -m evalbase.grader.cli --instance . --corpus corpus/hidden --cache runs/refcache-hidden refcache
python3 -m evalbase.grader.cli --instance . grade-ref --label control-reference-public
python3 -m evalbase.grader.cli --instance . --corpus corpus/hidden --cache runs/refcache-hidden grade-ref --label control-reference-hidden
python3 -m evalbase.grader.cli --instance . control stub
```

## Rescoring a graded run under a new metric, without re-running it

A graded run keeps every output the grader scored, so a metric change is a
regrade, not a rerun:

```sh
python3 -m evalbase.grader.cli --instance . regrade --run runs/<run>          # -> <run>/regrade-v<version>/report.json
python3 -m evalbase.grader.cli --instance . regrade --attempt runs/attempts/<name> --out /some/where
```

Fidelity and procedural cases are recomputed; a performance case reuses its
stored wall-time ratio (a timing is a live measurement) but re-evaluates the
fidelity gate; a case with no cached output keeps its stored score and is
listed in `regrade_reused_cases`.

## Bit-reproducible vs host-generated

**Reproducible.** The corpus: generators compute in float64 and narrow to
float32 once, every JSON float is the shortest decimal of that float32;
`python3 -m evalbase.corpus.determinism --instance . --split both` proves it
by generating twice with numpy's SIMD kernels disabled. Reference outputs on
the same host and image. Checkpoint grades, cached by source hash.

**Not reproducible.** The performance category is wall-clock timing and
depends on what else is on the host. Attempts are one-shot measurements: the
artifacts are frozen, hashed and re-gradeable, the attempt itself is not
repeatable.

## The refcache identity guardrail

Every `refcache.json` records `replay_sha256` and the asset names; `refcache`
reuses an entry only when the hash matches the case file, and grading refuses
an entry built from a different file rather than scoring against other
content. An unstamped entry counts as stale unless `--trust-unstamped`.

## What anyone can reproduce, and what needs the private generators

The public split, its reference cache, every public control and every public
number of record need nothing but this repository, the pinned numpy and the
pinned images. The hidden split needs the private generator tree named by
`SSVK_HIDDEN_GEN`; without it, hidden generation stops with one message
and writes nothing.
