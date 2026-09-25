# Task, oracle and split

## Choosing the task

The task must be long, compound and verifiable. A good shape: reimplement a
public API of a real program from scratch so that the candidate library can be
dropped in place of the real one. Tests for the shape:

- **A day of work for a strong model, scoped generously.** Frontier models
  are far more capable than intuition suggests, and the gap between design
  and publication is enough for the next release to land. Scope the task so
  that a full solution sounds unreasonable in a day: the whole public API, the
  rare types, the concurrency paths, the error contracts, a performance
  budget. Calibrate: the top models should land mid-range (0.5 to 0.9
  overall), not at the ceiling and not at the floor. If the first calibration
  run comes back above 0.9, the task is too small; widen it before publishing.
  Under-scoping is the common failure; over-scoping costs nothing but corpus
  work, because the score is a continuum and the controls define the floor.
- **Headroom lives in the tail.** On a generously scoped task, strong models
  build nearly everything and lose a slice of almost every family, and the
  exotic families are what keep them off the ceiling. Include those families
  from the start; they are the difference between an eval that measures for a
  year and one that saturates in a month.
- **Hundreds of API calls that must stay consistent.** Lifetime semantics,
  commit semantics, error codes, asynchronous behaviour, and a performance
  budget, so the score reflects a system, not a function.
- **A deterministic oracle.** The real program, built from pinned source, gives
  the same output for the same input on the same host. Stochastic output is
  fine if the seed is fixed and the noise can be measured.
- **No vendoring escape hatch.** Forbid linking the real thing or its
  dependencies; the sandbox has no network so the rule is enforced, not
  requested.

## The oracle

- Build from source in Docker with the base image pinned by digest and apt
  packages pinned by version. Record the resulting image id in every report.
- The oracle is also a tool the model may call: running the reference on any
  public case is legitimate scientific behaviour and the best models use it
  heavily. Give it to them; it is the same oracle the grader uses.
- Reference outputs are cached per case with the perturbation runs that
  define tolerance (a second seed, a reduced setting of the noise-controlling
  parameter, a small shift of a continuous input, and any other you decide to
  run). Stamp each cache entry with a hash of the case file; the grader refuses
  a cache whose stamps do not match the corpus.
- Reference wall times for performance cases are measured on the idle host at
  cache build time and reused; never re-measure them under load.

## The corpus

- One generator module per feature family, exposing `generate(split)`.
  Public and hidden must be different draws: different seeds AND different
  parameter values and compositions. Names are prefixed by family and are
  unique across splits.
- Most cases are sequences of several snapshots with mutations between them
  (parameter changes, data replacement, state resets, re-entry after an
  error), so a candidate cannot be right by accident on a single output.
- Three categories: fidelity (compare output to the oracle), procedural
  (ledger checks: return values, error codes, lifetimes, asynchronous events),
  performance (timed cases graded by wall-time ratio).
- Keep reference run time per case small (seconds) except performance
  cases; the whole cache should rebuild in about an hour.
- Large inputs go through content-addressed assets (sha256 file names); small
  ones inline.

## Determinism

- Generators compute at a higher precision than the consumer reads and narrow
  exactly once, at the boundary where the consumer reads the narrower type
  (float64 narrowed to float32 in one eval). Write every serialised number as
  the shortest decimal that names the narrowed value; data that is genuinely
  of the wider type is written at full precision.
- Prove it: a script generates both splits twice into throwaway trees, once
  with the CPU's SIMD kernels disabled (numpy: `NPY_DISABLE_CPU_FEATURES`),
  and diffs every file. Ship the script and cite its output in the
  reproducibility doc.
- Pin every library whose numerics reach the corpus.

## The same shape for other output types

The oracle, the perturbation runs and the split do not depend on the output
being an image. What changes is what a "harmless perturbation" is:

- **Text** (generated documents, compiler diagnostics, formatted output): the
  reference re-run under a paraphrase-preserving change (a different but
  equivalent template, a different line width, a re-ordered but equivalent
  option list). Cache the reference and its paraphrases per case.
- **Numeric arrays** (simulation state, solver results, embeddings): the
  reference re-run at a second seed, at a different but converged iteration
  count, or with a different but valid summation order. Relative error
  between those re-runs is the noise the tolerance is built from.
- **Audio and time series** (synthesised signals, sensor traces): a second
  seed, a small time offset, a resampling that the consumer would not notice.
  Compare in the domain the consumer hears or plots (a spectrogram, a
  windowed statistic), not point by point.
- **Structured logs and event streams** (trace output, protocol transcripts):
  a re-run with harmless re-ordering of independent events, or with
  timestamps shifted. Compare on the canonicalised structure and on the
  invariants the consumer relies on, not on byte identity.

Whatever the type, the rules hold: the reference passes by construction, the
perturbations that define tolerance are named, and every other change to the
output is a penalised difference.

## The split, operationally

- Public corpus in git. Hidden corpus git-ignored; its generators in a
  private tree loaded through an environment variable (default: a sibling
  directory), refused if the path lies inside the public repo.
- The hidden-outputs hardcoding control is the proof the split works: a
  candidate that returns stored public outputs by case name must score ~1.0 on
  public and ~0 on hidden.
- Outsiders reproduce every public number; hidden numbers need the private
  tree or a submission graded by the maintainers. Say so in the docs.
