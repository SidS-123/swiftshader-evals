# Stage status tables

Appended at the end of every stage (PLAN_v1.md §16.5). Internal; stripped on export.

## Stage 0 — Host and environment setup (2026-09-28)

| Item | State | Evidence |
|---|---|---|
| Host | Intel Core Ultra 7 356H, 16 logical CPUs, 31.4 GB RAM, Windows 11 Home | `Get-CimInstance Win32_ComputerSystem` / `Win32_Processor` |
| WSL | Ubuntu 26.04 LTS, kernel 6.18.33.2-microsoft-standard-WSL2 | `uname -a`, `/etc/os-release` |
| WSL resources | 16 vCPUs, 23 GiB RAM, 8 GiB swap, `vmIdleTimeout=-1` | `%UserProfile%\.wslconfig`; `nproc`, `free -g` after `wsl --shutdown` |
| Docker | Engine 29.7.2 native in WSL Ubuntu; sees 16 CPUs / 25.2 GB | `docker version`, `docker info` |
| P-core pinning | Not possible from WSL: pinned single-thread benchmark 0.45–0.51 s on all 16 vCPUs, two rounds | scratchpad `corebench.sh` output (in conversation log) |
| Working copy | `~/swiftshader-evals` (WSL ext4), LF line endings, `git status` clean except new docs | `git status --short` |
| Python | CPython 3.12.14 via uv; `.venv` with numpy 1.26.4, pytest 7.4.4, evalbase 0.1.0 (editable) | `python -c "import numpy, pytest, evalbase"` |
| evalBase tests | 383 passed, 4 skipped (Codex CLI, OpenCode CLI, slow opt-in, Docker smoke run separately) | `EVALBASE_NO_DOCKER=1 python -m pytest -q` |
| Docker smoke test | 1 passed | `python -m pytest -q tests/test_docker_smoke.py` |
| Slow determinism test | passed | `EVALBASE_SLOW=1 python -m pytest -q tests/test_corpus_determinism.py` |
| evalBase changes | 1 test-only fix (order-dependent case pick) | `docs/internal/EVALBASE_CHANGES.md` |
| Toy end to end | corpus 18/18 deterministic; reference 1.000 all categories; stub replay 0.000 / procedural 0.071; half_samples replay 0.985; openrouter + claude-code no-key smokes PASSED (incomplete candidate 0.0143); site exported (25 pages) | `evalBase/examples/toy/runs/` (git-ignored) |
| Claude Code | 2.1.274, logged in, `authMethod: claude.ai`, `subscriptionType: max`; no `ANTHROPIC_*` variables set | `claude --version`, `claude auth status`, `env` |
| Host tools | git present; jq/cmake/ninja/build-essential absent (need sudo; optional) | `which` |
| Laptop hygiene (power plan, sleep, updates) | Deferred to G-PAID — only matters for long runs | PLAN_v1.md §4 step 4 |

**Open items:** optional `sudo apt install jq cmake ninja-build build-essential` (you); reopen the project in VS Code at the WSL path; decide D1–D14 remaining (D9 decided; D10 carried out).

**Next stage (1) starts from:** the WSL working copy, the `.venv`, and the template at `evalBase/template/`. It needs the private GitHub repo for hidden generators (D11).

## Stage 1 — Repo layout and scaffolding (2026-09-28)

| Item | State | Evidence |
|---|---|---|
| Decisions D1–D14 | Approved as recommended | `swiftshader_vk/docs/DESIGN.md` decision log; CHANGELOG v1.3 |
| Instance directory | `swiftshader_vk/` copied from `evalBase/template`. Placeholders renamed (`ssvk`, `SSVK_HIDDEN_GEN`, images `ssvk-ref:1` / `ssvk-solver:1`); the example family is renamed to `inst_dev` (a placeholder with no cases) | `grep -rn "<eval>\|<EVAL>\|family_a" swiftshader_vk` finds nothing |
| Instance import | OK: `name=swiftshader-vk`, `hidden_default=~/swiftshader-evals-hidden`, `forbidden_hidden_roots=(~/swiftshader-evals,)` | `source .envrc && python -c "import swiftshader_vk.instance"` |
| Generation wiring | `evalbase.corpus.common both` runs the public and hidden generators (0 cases so far) | command output |
| Leak guard | A hidden tree inside the repo is refused with the `SSVK_HIDDEN_GEN` message | `SSVK_HIDDEN_GEN=swiftshader_vk/gen_leak ... hidden` |
| Hidden repo | `~/swiftshader-evals-hidden` → `SidS-123/private-repo-swiftshader`; commit `b358e1f` pushed; the anonymous API returns 404 (private) | `git log`, `curl api.github.com/repos/...` |
| Credentials | WSL git uses the Windows Git Credential Manager, set in the hidden repo only; WSL `gh` is not logged in | `git -C ~/swiftshader-evals-hidden config credential.helper` |
| Git identity | Both repos set locally to `Sid <sids4623@gmail.com>`. WSL had no identity, so the Stage 0 commit used `sids4@Sid-Laptop.localdomain` | `git config user.email` |
| Docs | DESIGN.md decision log seeded; `REQUIREMENTS.md` and `REPLAY_FORMAT.md` skeletons; `.gitignore` (runs, hidden, assets, `*.ssnap` outside public, `.env`); `.envrc` | files |

**Open items:** optional `sudo apt install jq cmake ninja-build build-essential`; pushing to the public `origin` waits for your go-ahead.

**Next stage (2) starts from:** the scaffold and the D2 pin. It builds `ssvk-ref` in Docker (SwiftShader LLVM + Subzero, the loader, validation layers, glslang, SPIRV-Tools, lavapipe) and runs SwiftShader's own unit tests inside it.

## Stage 2 — Oracle image `ssvk-ref` (2026-09-29)

| Item | State | Evidence |
|---|---|---|
| Image | `ssvk-ref:1` = `sha256:483b5f09207f949e058c447f32d97ed65cf0d955577810ae562b737d91c10f18`, 639 MB | `docker image inspect`; `swiftshader_vk/docs/internal/build-logs/` (git-ignored) |
| Pins | All in `swiftshader_vk/images/pins.lock`: base digest, apt snapshot `20260928T000000Z`, SwiftShader `1e80438d` (+ glslang/googletest submodule commits), `vulkan-sdk-1.4.357.0`, Mesa `26.2.3`, meson 1.9.1, mako/pyyaml/packaging | `/opt/ssvk/VERSIONS`, `/opt/ssvk/PACKAGES` in the image |
| SwiftShader unit tests, LLVM 10 | Reactor 156, system 25, math 11, vk 139: all passed | `docker run --rm ssvk-ref:1 info` |
| SwiftShader unit tests, Subzero | Same counts, all passed | same |
| Loader + ICDs | `vulkaninfo --summary`: "SwiftShader Device (LLVM 10.0.0)", "SwiftShader Device (Subzero)", both apiVersion 1.3.0, driverVersion 5.0.0; lavapipe "llvmpipe (LLVM 18.1.3, 256 bits)", apiVersion 1.4.354 | `docker run --rm ssvk-ref:1 vulkaninfo <llvm\|subzero\|lavapipe> --summary` |
| Validation layer | `VK_LAYER_KHRONOS_validation` 1.4.357 loads on the SwiftShader instance | `VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation` |
| Shader tools | glslang 16.4.0; SPIRV-Tools v2026.3 (`spirv-val/dis/as/opt/reduce/link`); no `spirv-fuzz` (needs protobuf, not built) | `glslangValidator --version`, `ls /opt/vk/bin` |
| Runtime config | `/opt/ssvk/ini/default` ThreadCount=4, `/opt/ssvk/ini/threads1` ThreadCount=1; implicit layers disabled; user `runner` uid 1000 | `images/ini/`, `ref.Dockerfile` |
| Finding | SwiftShader returns `VK_ERROR_INCOMPATIBLE_DRIVER` for `apiVersion` > 1.3; `vulkaninfo` is patched to request ≤ 1.3 | `src/Vulkan/libVulkan.cpp:576-596`; DESIGN.md log |
| Build issues fixed | Vulkan-Tools `UPDATE_DEPS` built a WSI loader (off now); `vk-unittests` needs the CI build layout (`<src>/build/Linux`); Mesa needs glslang, libdrm and its Python deps in the meson venv; `ubuntu` user held uid 1000 | CHANGELOG v1.4 |
| Infra flakiness | WSL DNS drops lookups intermittently; snapshot.ubuntu.com returned 503s for about an hour. Fetches retry, and apt uses `Acquire::Retries` without pipelining in late stages | `images/fetch.sh`, `images/retry.sh` |
| Entry points | `ssvk info`, `ssvk with <icd> [--ini V] CMD`, `ssvk vulkaninfo <icd>`; `drive*` exits 3 until Stage 4 | `images/ssvk-entry.sh` |

**Open items:** `drive`/`drive-candidate`/`drive-lavapipe` wait for vkreplay (Stage 4), after which the image is rebuilt. D14 (`spirv-fuzz`) is checked in Stage 3.

**Next stage (3) starts from:** `ssvk-ref:1`. It dumps `vulkaninfo --json` for both backends (device profile), breaks down the CTS pass list and checks the open items. The determinism measurement needs a minimal replay driver first (Stage 4, step 6).

## Stage 3 — Oracle characterisation (2026-09-29)

| Item | State | Evidence |
|---|---|---|
| Device profile | Frozen: 85 device + 15 instance extensions, 144 formats, 1 queue family, 1 heap/1 memory type; body sha256 `3099331062bec522775e88dca47e1456ce0ae493f14dad6af13ebb6d90158eba`, reproducible | `swiftshader_vk/spec/device_profile.json`; `tools/make_device_profile.py --check` → IDENTICAL |
| LLVM vs Subzero profile | Identical except deviceName (1,925 values) | `make_device_profile.py` fails if not |
| CTS breakdown | 416,472 PASS mapped to families, 0 unmapped | `swiftshader_vk/docs/internal/cts_breakdown.md` |
| REQUIREMENTS.md | Measured facts + exclusions | `swiftshader_vk/docs/REQUIREMENTS.md` |
| Determinism | **Deferred** to after Stage 4 step 6 (needs vkreplay), as §7.4 allows | `stage3_findings.md` §3.4 |
| Loader env vars | Both work; `VK_DRIVER_FILES` takes precedence | `stage3_findings.md` §3.5 |
| Rebuild timeout | 1800 s grading rebuild (plan said 900); 600 s per shell call | `evalBase/evalbase/harness/tools.py:40,784` |
| Spec precision | Quoted from Vulkan-Docs v1.4.357 | `stage3_findings.md` §3.5 |
| D14 `spirv-fuzz` | Ships in pinned SPIRV-Tools (not built) | SPIRV-Tools `CMakeLists.txt:81` |
| lavapipe diff | 80/85 extensions shared; 64/164 features and 87/143 properties differ; same precision bits; subgroup size 8 vs 4 | `stage3_findings.md` §3.6 |

**Open items:** D14 needs a decision now that its condition is met; the determinism report closes in Stage 4.

**Next stage (4) starts from:** the frozen profile and REQUIREMENTS.md. It writes the case format and `vkreplay` (trusted parent / untrusted child), rebuilds `ssvk-ref` with it, hand-writes the ~50-case pilot corpus, and runs the §7.4 determinism measurement.

## Stage 4 — Case format and replay driver `vkreplay` (2026-09-29)

| Item | State | Evidence |
|---|---|---|
| Image | `ssvk-ref:1` = `sha256:10f433b809bf2dfa7e90eaec99937ccaa5d93aacb838ab47402f9a993226fa87` (adds vkreplay 1.0.0 and uid 2000 `cand`; SwiftShader/Mesa stages unchanged, from cache) | `docker run --rm ssvk-ref:1 info` |
| vkreplay | C++17, ~3.5k lines (`driver/src/`), links only the loader + nlohmann/json `v3.12.0` (pinned); tables generated from the pinned `vk.xml`: 4,620 enum values, 210 flag types, 297 formats, 401 structs (267 feature, 121 property) | `driver/codegen/gen_vk_tables.py`; build log |
| Op vocabulary | setup/queries (5), resources (12), pipelines (2), exec/record/submit + 60 commands, sync/queries (16), snapshot + timed run | `task/CASE_FORMAT.md` |
| Trust boundary | parent (root, not dumpable) owns `/out` and the clock; child uid 2000; plan-checked protocol; child reaped at exit; uid-2000 processes killed; hostile ICD: `crash` recorded in 0.2 s, 0 hostile files | `tests/test_driver.py::test_hostile_candidate_cannot_touch_the_outputs` |
| Python driver | `VkReplayDriver` (container as root, `DRIVER_TIMEOUT` = container − 10 s, perturbations for the oracle only); `inspect_case` limits for model-written cases (extent ≤ 1024, run iterations ≤ 1000, ops ≤ 20,000) | `instance.py` |
| Snapshot reader | `.ssnap` reader + per-format decoder (packed, float, UFLOAT, shared-exponent, depth, stencil, buffers), LSB/ULP distances | `snapshot.py` |
| Case builder | `corpus/gen/common.py` (`Case`, pinned-glslang GLSL→SPIR-V with content cache) | file |
| Pilot corpus | 56 cases over every op group; all `exit: ok` on the oracle | `tools/pilot_corpus.py`; `runs/pilot/` (git-ignored) |
| Validity gate | 0 spec violations on all 56 (validation layer on) | `tools/determinism.py` |
| Determinism (closes Stage 3 §3.4) | 55/56 byte-identical over 3 repeats and ThreadCount 4 vs 1; the exception is atomic return values (order undefined, excluded from snapshots) | `swiftshader_vk/docs/internal/determinism.md` |
| Perturbations measured | Subzero: float compute only (≤ 4 ULP transcendentals, 5,664 ULP matrix inverse, 1 ULP depth); vtxjitter: 1-3 edge texels; texcoord_ulp: ≤ 1 LSB filtered texels; lavapipe: 1 LSB rounding broadly, larger in lines/blits/float, no ETC2/ASTC | same |
| Tests | driver 5 passed; evalBase 383 passed, 4 skipped | `pytest swiftshader_vk/tests/test_driver.py`; evalBase suite |
| Scope corrections | no logicOp, dual-source blend, pipeline statistics, 8/16/64-bit types, D24 → REQUIREMENTS/DESIGN updated | `docs/REQUIREMENTS.md`, `docs/DESIGN.md` log |

**Open items:** render passes/framebuffers (for `mrt_renderpass` subpasses and input attachments) are added in Stage 7; the perf `run` in the pilot lasts 8 ms, so Stage 7's generators size iterations to ≥ 2 s; `solver` image copy of vkreplay is Stage 9.

**Next stage (5) starts from:** `task/CASE_FORMAT.md` (done here) and REQUIREMENTS.md. It writes TASK.md, SPEC.md, the starter ICD skeleton (reports zero devices) with a CMake build producing `build/libvk_candidate.so`, freezes `spec/` (profile + pinned Vulkan spec HTML), and sets `TaskSpec`.

## Stage 5 — Task text and starter workspace (2026-09-30)

| Item | State | Evidence |
|---|---|---|
| Starter | `task/starter/`: CMake build → `build/libvk_candidate.so` (16 KB); negotiates interface 5, `vkEnumerateInstanceVersion` = 1.3, the core instance entry points the loader requires, **0 physical devices**. On pilot cases: `vkCreateInstance` `VK_SUCCESS`, `vkEnumeratePhysicalDevices` `VK_ERROR_INITIALIZATION_FAILED` (the loader's answer to 0 devices), everything else skipped, `exit: ok` | scratchpad `starter_test.sh` output |
| Finding | The loader (1.4.357) skips an ICD missing core instance-level entry points even with 0 devices, and treats one without `vkEnumerateInstanceVersion` as 1.0 | `VK_LOADER_DEBUG` output; `SPEC.md` |
| `spec/` | 741+ files: profile; Vulkan-Docs `v1.4.357` AsciiDoc (15 MB); `vk.xml`; SPIR-V grammar/headers (SDK tag) + SPIR-V & GLSL.std.450 HTML (committed, `spirv/ORIGIN`); Khronos Data Format Spec `1.4.0-gh` (11 MB). Rebuild `--check`: IDENTICAL | `tools/make_spec_dir.sh`, `spec/MANIFEST.sha256`, `spec/SOURCES` |
| Task text | `TASK.md`, `SPEC.md`, `CASE_FORMAT.md` rewritten after a fresh-reader review (42 findings; triage in DESIGN.md log 2026-09-30) | files |
| `TaskSpec` | artifact `build/libvk_candidate.so`; build = the CMake command; `driver_command` = `vkreplay --candidate /task/build …`; `required_names` = CMakeLists.txt; tool descriptions and instructions written; `dev_extra` = each public case's reference `ledger.json` | `instance.py` |
| vkreplay 1.0.0 additions | `"address"` data (buffer device address), snapshot `allow`, private copy of the candidate library | `ssvk-ref:1` = `sha256:99afb1deea7c…` |
| Pilot | 57 cases (+ `p_c_bda`: values correct, valid, deterministic) | `tools/pilot_corpus.py` |
| Tests | driver 5 passed | `tests/test_driver.py` |
| Incident | A mis-expanded shell variable ran a git checkout in the WSL home directory; 364 new files + `~/.git` removed after verifying none pre-existed (birth times) | this table; memory note |

**Open items carried forward:**
- Stage 6: `D` formula stated exactly in `SPEC.md`; scorer `preview_rgb8` (first colour item); `allow` handling; `procedural_checks` as documented (exit, snapshots within T, VkResult stream incl. internal calls, every query value, timestamp validity).
- Stage 7: render-pass ops in vkreplay + CASE_FORMAT (`mrt_renderpass`); `errors_robust` scenario list; generators within oracle limits; perf runs ≥ 2 s; `allow` on transcendental outputs.
- Stage 8: add a "success everywhere" control (reports the profile, returns `VK_SUCCESS`, does no work).
- Stage 9: solver image contains gcc, clang, cmake, ninja, make, gdb, valgrind, strace, python3, `/opt/vk` headers + loader + validation layer, `vkreplay`, `spirv-dis`, `spirv-as`; Ubuntu 24.04 base (same glibc as the grading image); `SmokeSpec` updated for the CMake build.

**Next stage (6) starts from:** `snapshot.py`, the Stage 4 determinism/perturbation measurements, and `SPEC.md`'s scoring section. It writes `scorer.py` (format-aware distance, procedural checks, timings), metric v1.0, and unit tests.

## Stage 6 — Scorer and metric `ssvk-1.0` (2026-09-30)

| Item | State | Evidence |
|---|---|---|
| Scorer | `scorer.py` `SsvkScorer`: `.ssnap` loading, format-aware distance (free 1 LSB / 2 ULP; capped magnitude and bias terms; coverage with C = 1/4 images, 1/64 buffers; RMS over blocks and items), uniform-output override, `allow`, previews, block heatmaps, procedural checks | `scorer.py` |
| Unit tests | 17 passed (identity, free rounding, 2-LSB bias, gain x2, half-pixel, one wrong block = 0.125, edge flips < 0.02, uniform, missing/mismatch, integer exactness, float ULP/NaN, float allow, buffer element, depth/stencil, item weighting, preview, procedural) | `tests/test_scorer.py` |
| Metric | `ssvk-1.0`; tolerated `vtxjitter`, `texcoord_ulp`; recorded `subzero`, `lavapipe`; k 2/2, T in [0.03, 0.30], Hill 4, weights 0.6/0.3/0.1, perf 16/8, bars 0.9/0.95 | `instance.py` |
| Pilot distances | noise 0 on every case; vtxjitter <= 0.0135; texcoord_ulp 0; subzero 0.25-1.0 on three float cases (hence not tolerated); T = 0.03 on 47/51 | `tools/perturb_distance.py` output |
| Fairness preview | lavapipe mean snapshot score 0.773 (51 pilot cases, no allowances) | same |
| End to end (evalBase grader, 10 pilot cases) | refcache built; **reference = 1.000, full success**; **starter = 0.000** | `runs/pilot/grade-ref`, `runs/pilot/grade-starter` (git-ignored) |
| SPEC.md | exact `D` formula, constants, tolerated set, procedural checks | `task/SPEC.md` |
| DESIGN.md | fidelity, threshold, procedural sections written; decision log: metric design, subzero not tolerated, `allow` policy | `docs/DESIGN.md` |

**Open items carried forward:** Stage 7 applies the `allow` policy in generators (spec-bounded float results) and adds render-pass ops; Stage 8 decides depth-interpolation and filtered-sampling allowances from the lavapipe run, writes the controls' predictions first, and may revise the constants (as a new metric version); the performance scaling (D5) is decided in Stage 8.

**Next stage (7) starts from:** `corpus/gen/common.py`, the pilot's case shapes, REQUIREMENTS.md's 21 families. It writes the generators (public here, hidden in the private repo), render-pass ops, the validity gate for every generated case, the determinism proof, and both reference caches.

## Stage 7 — Corpus generation and reference caches (2026-10-01)

| Item | State | Evidence |
|---|---|---|
| Generators | 20 modules for 21 families (`perf.py` makes the four `perf_*`); shared builders in `corpus/gen/common.py` (`compute_case`, `raster_scene`, `texture_scene`, `spec_allow`, ...) | `swiftshader_vk/corpus/gen/` |
| Public corpus | **91 cases** (replay 75, procedural 12, performance 4), 498 assets across both splits | `swiftshader_vk/corpus/public/` |
| Hidden corpus | **187 cases** (replay 161, procedural 22, performance 4); private tables over the public builders | `~/swiftshader-evals-hidden/gen/` (commits `5689456`, `67c2910`, not pushed) |
| vkreplay | render pass objects: `render_pass`, `framebuffer`, `begin_render_pass` / `next_subpass` / `end_render_pass`, pipelines bound to a subpass | `driver/src/child_{pipelines,commands}.cpp`, `child.hpp`; CASE_FORMAT, REPLAY_FORMAT |
| Validity gate | public 91/91, hidden 187/187: validation layer 0 spec violations, exit ok, repeats and ThreadCount 1/4 byte-identical | `tools/gate.sh` → `runs/gate-*/determinism.json` |
| Generation determinism | 776/776 files identical (two generations, numpy default vs AVX features disabled) | `python -m evalbase.corpus.determinism --split both` |
| Leak check | 4 passed: every family has hidden cases; hidden seeds new and unique; parameters differ; no hidden seed or name in the public tree | `tests/test_hidden_leak.py` |
| Reference caches | public 91, hidden 187 entries, `ssvk-1.0`; T = 0.03 for 89/91 and 181/187, `t_hi` for 2 and 4 (raster_tri, queries_sync occlusion) | `runs/refcache`, `runs/refcache-hidden`; `tools/refcache_summary.py` |
| Timing gate | non-perf reference ≤ 0.63 s (public), ≤ 0.54 s (hidden); perf timed runs 2.57-3.11 s | refcache; `stage7_findings.md` 7.4-7.5 |
| grade-ref | **public 1.0000, full success** (replay 75, procedural 12, performance 4 all 1.0); **hidden 1.0000, full success** (replay 161, procedural 22, performance 4 all 1.0) | `runs/grade-ref-{public,hidden}/report.json` |
| Surface narrowed | BDA via push constants only (pointer chasing crashes the oracle); no image atomics (nondeterministic); robustness via core features (no robustness2) | `stage7_findings.md` 7.3; REQUIREMENTS, SPEC, DESIGN |
| Docs | findings file (new); CASE_FORMAT / REPLAY_FORMAT render passes; SPEC `errors_robust` row; REQUIREMENTS narrowed list; DESIGN log (6 entries); PLAN v1.10 §11 "As built"; CHANGELOG | files |

**Open items carried forward:**
- Stage 8: whether the `t_hi` cases (vertex nudges that flip occlusion counts and edge pixels) are too loose; depth-interpolation and filtered-sampling allowances (`depth_allow` is still `None`); D5 performance scaling; controls' predictions first.
- The hidden generator commit is local only: pushing `~/swiftshader-evals-hidden` to its private remote (D11 says same day) waits for your go-ahead, like every push.
- `snapshot.py` prints a NumPy `RuntimeWarning` when a float snapshot holds NaN (harmless; silence it in Stage 8).

**Next stage (8) starts from:** both caches and grade-ref = 1.0. It commits the controls' predictions to DESIGN.md first, then builds the wrapper-ICD controls and measures them on both splits.
