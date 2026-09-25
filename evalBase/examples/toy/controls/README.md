# Controls

Each subdirectory holds a `candidate.py` the grader treats as a candidate.
`reference` has none: the build step copies the oracle itself. Every other
control except `stub` is a *shim* -- it subclasses the oracle's `Painter`
(the build step puts `oracle.py` beside `candidate.py`) and changes exactly
one thing, so it exercises the same driver path as a candidate.

Predicted ranges live in `docs/DESIGN.md`; observed results in
`docs/CONTROLS.md`.

```
python3 -m evalbase.grader.cli --instance examples/toy control <name> \
    [--corpus DIR --cache DIR] [--out runs/control-<name>-<split>] [--label L]
```

| Control | What it is | Predicted |
|---|---|---|
| `reference` | the oracle as the candidate | 1.0 on every category, both splits |
| `stub` | every call succeeds, black snapshots, empty queries | the null band: fidelity ~0, performance 0, procedural the free checks only |
| `half_samples` | the oracle at half the samples | fidelity >= 0.9 (a tolerance perturbation) |
| `brightness_x2` | every pixel twice as bright | fidelity <= 0.2 |
| `stale_output` | returns the previous draw's image | fidelity ~1/snapshots on sequences, 0 on stills |
