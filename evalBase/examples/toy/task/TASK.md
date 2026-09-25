# Reimplement the rectangle painter

Write, from scratch, a Python module `candidate.py` in `/task` that behaves
exactly like the reference painter for the same canvas descriptions. A fixed
driver replays scripted cases against your module; every snapshot it produces,
every query it answers and every error code it reports is compared against
the reference, and the timed case is compared against the reference's speed.

## Deliverable

- `/task/candidate.py` defining a class `Painter` with the three methods in
  `SPEC.md`. Python 3, standard library only; you may add helper modules
  beside it (the directory is on `sys.path`).
- Do not modify `dev/` or `SPEC.md`. The evaluator grades a frozen copy of
  your source with its own driver.

## Provided

| Item | Purpose |
|---|---|
| `SPEC.md` | the complete semantics: ops, compositing, the noise model, queries, error codes |
| `dev/cases/*.json` | public cases, with the reference's own outputs in `dev/reference/<name>/` |
| `dev/CASE_FORMAT.md` | the case file format |
| `dev/driver.py` | the driver: `python3 dev/driver.py <case.json> <outdir> --candidate /task` |
| `oracle` tool | runs any case you write with the reference (black box; no source) |
| `driver` tool | runs a case against your `candidate.py` |
| `grade_dev` tool | scores your module on the public cases with the real metric |

## Scoring

`overall = 0.60 * replay + 0.30 * procedural + 0.10 * performance`. Hidden
cases use the same ops with different canvases, sample counts and sequences;
memorising the public outputs does not help. A snapshot is scored against a
per-case tolerance derived from the reference's own noise, so exact sample
reproduction (the seeded generator in `SPEC.md`) is what earns full marks.

## Working practice

Keep `NOTES.md` current with what works, measured failures on `grade_dev`
and next steps.

## Stopping

<!-- harness: stopping policy -->
