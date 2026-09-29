# Controls

One subdirectory per control; `ControlSpec.build(name, out_dir)` turns it into
a candidate directory. Write every prediction in `docs/DESIGN.md` before the
first measurement, then measure on both splits and record the observations in
`docs/CONTROLS.md`.

| Control | What it is | Predicted |
|---|---|---|
| `reference` | the oracle as the candidate | 1.0 on every category, both splits |
| `stub` | every call succeeds, empty outputs | the null band (measure it) |
| ... | one perturbed-reference control per tolerated perturbation | fidelity >= 0.9 |
| ... | a uniform bias (gain, offset) | fidelity low |
| ... | the previous output returned again | fidelity ~1/snapshots on sequences |
| ... | a candidate that returns stored public outputs by case name | ~1.0 public, null band hidden |
| ... | a crash mid-case, absurd output | snapshots after the crash 0; no grader exception |
