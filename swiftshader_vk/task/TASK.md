# <Task title>

What the model must build, from scratch, so that its artifact can be dropped
in place of the reference. Say what is graded (snapshots, queries, error codes,
timings), what is provided, and what is forbidden (vendoring the reference or
its dependencies; there is no network in the sandbox).

## Deliverable

- Source in `/task`; `<build command>` must produce `/task/<artifact>`.
- Do not modify `spec/`, `dev/`, or the output paths.

## Provided

| Item | Purpose |
|---|---|
| `SPEC.md` | the graded API subset |
| `spec/` | the frozen public API reference |
| `dev/cases/*.json` | public cases, with the reference's outputs in `dev/reference/<name>/` |
| `dev/CASE_FORMAT.md` | the case file format |
| `oracle` tool | runs any case you write against the reference (black box) |
| `driver` tool | runs a case against your artifact |
| `grade_dev` tool | scores your artifact on the public cases with the real metric |

## Scoring

`overall = 0.60 * replay + 0.30 * procedural + 0.10 * performance`; hidden
cases use the same families with different content, parameters and sequences.

## Working practice

Keep `NOTES.md` current: architecture, measured failures, next steps.

## Stopping

<!-- harness: stopping policy -->
