# Controls

One subdirectory per control, each with a `build.json`: what the control is and
the macros it is built with. `instance.py: SsvkControls.build` compiles the
wrapper ICD `common/shim.cpp` with those macros (or the task's starter, for
`stub`) in the toolchain image into a candidate directory holding
`libvk_candidate.so`, which the grader runs like any model's build:

```sh
python -m evalbase.grader.cli control <name> --out swiftshader_vk/runs/control-<name>-public
python -m evalbase.grader.cli --corpus swiftshader_vk/corpus/hidden --cache swiftshader_vk/runs/refcache-hidden     control <name> --out swiftshader_vk/runs/control-<name>-hidden
```

The shim dlopens the real driver (`SHIM_TARGET`), makes the oracle's ini
directory its working directory (`SHIM_INI`), forwards every call, and alters
one thing per `CONTROL_*` macro. Controls are replayed in `ssvk-cand:1`, like
every candidate, which holds no driver: each driver-wrapping control's
`control.json` mounts the real one read-only (`runs/oracle-mount/`, extracted
by `tools/extract_oracle_mounts.sh`). Without those mounts the same library
finds nothing to forward to (`VK_ERROR_INCOMPATIBLE_DRIVER`), which is the
point: a model's build never gets them. Predictions for every
control, written before any was run, are in `docs/DESIGN.md` ("Controls");
`tools/predict_controls.py` derives them. Observations go to `docs/CONTROLS.md`.
