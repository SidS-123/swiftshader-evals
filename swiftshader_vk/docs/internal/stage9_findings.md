# Stage 9 — harness findings (2026-10-01)

PLAN_v1.md §13: the model's side. What was built, what was checked, and one
integrity hole found and closed on the way.

## 9.1 The integrity hole: candidates were replayed next to the reference

Until this stage a candidate's graded runs (and its `grade_dev` runs) executed
in `ssvk-ref:1`, the oracle image, which also holds SwiftShader, Subzero,
lavapipe and libLLVM. The Stage 8 controls had shown that an unprivileged
candidate there can `dlopen("/opt/swiftshader/llvm/libvk_swiftshader.so")` and
forward every call. A model could find that path by probing the filesystem from
its own library during `grade_dev` (writing what it finds into a snapshot or
its stderr) and then submit a 200-line forwarder that scores 1.0.

The plan had this right (§12 step 2: "`control.json` mounts the reference `.so`
read-only for control runs only"); Stage 8 dropped the mounts because "the
reference image already holds the drivers", which opened the hole.

Closed:

- **`ssvk-cand:1`** (`images/ref.Dockerfile` target `cand`): the pinned base,
  the loader and vkreplay, nothing else (127 MB). `VkReplayDriver` replays every
  candidate there; the oracle stays in `ssvk-ref:1`.
- Driver-wrapping controls get the real driver as **read-only mounts**
  (`controls/<name>/control.json`; sources extracted by
  `tools/extract_oracle_mounts.sh`, sha256-identical to the image's copies).
  The poisoned-memory gate uses the `reference` control's mounts.
- Proof (`runs/shim_check2.py`, a scratch scene in neither corpus): in
  `ssvk-cand:1` with mounts, `reference`, `one_thread`, `reference_subzero` and
  `lavapipe` reproduce the oracle byte for byte with the same call stream; the
  same `reference` forwarder **without** mounts gets
  `VK_ERROR_INCOMPATIBLE_DRIVER` from `vkCreateInstance`.

## 9.2 Images

| Image | Size | Contents |
|---|---|---|
| `ssvk-solver:1` | 1.05 GB | gcc/g++ 13, cmake, ninja, make, gdb, valgrind, strace, python3, git, jq, pkg-config, file, less, xxd; `/opt/vk` headers, loader, validation layer, registry; `spirv-dis`, `spirv-as` (SPIRV-Tools linked in); vkreplay; user `solver` (1000) |
| `ssvk-cand:1` | 127 MB | libstdc++, the loader, vkreplay, the entry script; users 1000 / 2000 |
| `ssvk-ref:1` | 642 MB | unchanged (the oracle) |

Excluded from the solver image (D3): every ICD, LLVM libraries (so **no clang**:
Ubuntu's clang links `libLLVM-18.so`, which a candidate could embed as a JIT
backend), the SPIRV-Tools and glslang libraries and headers, `glslangValidator`,
`spirv-opt`, network at run time. The task text no longer lists clang.

## 9.3 Isolation check

`tools/isolation_check.sh` (also `TaskSpec.isolation_check`) fails on: any ICD
manifest; any SwiftShader, lavapipe, LLVM, clang, SPIRV-Tools, glslang or
libgccjit library; `clang`, `glslangValidator` or `spirv-opt` on PATH; network;
the reference drivers' identifying strings anywhere on disk (so a renamed copy
is caught). Results (`tools/isolation_selftest.sh`):

| Image | Expected | Result |
|---|---|---|
| `ssvk-solver:1` | pass | pass |
| `ssvk-cand:1` | pass | pass |
| `ssvk-solver:1` + SwiftShader renamed `libhelper.so` + an ICD manifest | fail | fail |
| `ssvk-cand:1` + the same | fail | fail |
| `ssvk-ref:1` | fail | fail (manifests, libLLVM, lavapipe, glslang and SPIRV-Tools libraries, both SwiftShader builds) |

## 9.4 Harness checks

| Check | Result | Evidence |
|---|---|---|
| Workspace (`populate`) | 91 public cases, 91 reference dirs (snapshots + ledger), 195 assets, frozen `spec/` (33 MB), `dev/` and `spec/` read-only, no hidden name or path | `runs/smoke-claude-code/workspace` inspected |
| No-key smoke, claude-code | **NO-KEY SMOKE PASSED**: real tools, oracle, grader; the incomplete candidate scored 0.0069 | `runs/smoke-claude-code` |
| No-key smoke, openrouter | **NO-KEY SMOKE PASSED**, 0.0069 | `runs/smoke-openrouter` |
| Tool boundary (claude-code) | CLI 2.1.274 init event: exactly `mcp__ssvk__{checkpoint,driver,grade_dev,oracle,shell}`, no built-ins, `apiKeySource: none`, MCP `ssvk` connected | `tools/wire_check.sh`, `runs/wire/attempt/transcript.jsonl` |
| Credential hygiene | no API key or token variable; every recorded sandbox mount is the run's own workspace at `/task`; no credential-shaped string in the run dirs | `tools/credential_check.sh` |
| Checkpoint cadence | `--checkpoint-minutes 15` for attempts; curve graded `--every 30 --dedupe` (Stage 13) | PLAN §13 step 7 |

**Request-body recording deferred to G-PAID.** `tools/wire_check.sh` points
the CLI at a local recorder (`tools/wire_recorder.py`) that stores only the
first `/messages` body and answers every request with HTTP 400, so nothing
reaches the API. The WSL login's access token had expired, and its refresh
goes through the overridden base URL, which the recorder refuses to serve, so
no model request was sent. The CLI's own init event above is the tool list it
offers the model; the request body is recorded at G-PAID, after a normal login
refresh. Nothing from the refresh was stored.

**Model id.** In the same run this CLI logged `claude-opus-5-5` as an
unrecognised model for its session-title side request. Whether the main
request accepts the id is confirmed at G-PAID (that check uses a little usage).

## 9.5 Fixes on the way

- `TaskSpec.smoke`: the oracle case was not a valid case (a lone snapshot op);
  now instance, device, buffer, upload, snapshot. The smoke candidate (the
  starter plus a stub) builds and loads, so `driver_fails=False`; the graded
  public case is a replay case (`compute_arith_pub_int_a`): on a procedural
  case the loader's own instance version matches whatever the driver does.
- evalBase (`docs/internal/EVALBASE_CHANGES.md`): `populate` failed when
  `dev_extra` had already put a file into `dev/reference/<case>/`; the smoke
  never ran `TaskSpec.isolation_check`, although its docstring says it does.
- `isolation_check.sh` excluded its own copy from the string search (it holds
  the strings it looks for).

## 9.6 Controls in the new image

The five controls that depend on where a candidate runs were re-graded on both
splits in `ssvk-cand:1` with their mounts (AC, 22:22-22:33), and compared with
their Stage 8 runs in `ssvk-ref:1` (archived in `runs/stage8-controls/`):

| Control | Stage 8 overall (public / hidden) | Stage 9 overall | Every non-performance case identical |
|---|---|---|---|
| `reference` | 1.000 / 1.000 | 1.000 / 1.000 | yes |
| `one_thread` | 0.9998 / 0.9998 | 0.9998 / 0.9998 | yes |
| `reference_subzero` | 0.9911 / 0.9924 | 0.9911 / 0.9924 | yes |
| `lavapipe` | 0.7413 / 0.7382 | 0.7413 / 0.7382 | yes |
| `stub` | 0.0125 / 0.0069 | 0.0125 / 0.0069 | yes |

Performance ratios moved within the host's run-to-run spread (e.g. `one_thread`
3.60 / 3.22 / 2.05 / 3.48 -> 3.64 / 3.30 / 2.14 / 3.57). The Stage 8 numbers in
CONTROLS.md therefore stand for the new image. The poisoned-memory gate, now
replayed in `ssvk-cand:1` with the `reference` control's mounts, still passes
all 7 `formats_copy_blit` public cases (all replay identically under poison).

Tests: instance 26 passed; evalBase 383 passed, 4 skipped (the Stage 0
baseline), with the two evalBase changes.
