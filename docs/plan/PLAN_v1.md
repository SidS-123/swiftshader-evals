# SwiftShader eval — build and run plan (v1)

| | |
|---|---|
| Plan version | **v1.8** (first iteration, "v1") |
| Status | **Stage 5 done (2026-09-30).** Stage 6 next. Status tables: `docs/internal/STATUS.md` |
| Working copy | `~/swiftshader-evals` inside WSL Ubuntu (D10). The Windows folder `C:\Users\sids4\Coding\swiftshader-evals` is a stale copy as of 2026-09-28 |
| Date | 2026-09-26 |
| Eval name | `swiftshader-vk` (short name `ssvk`) |
| First model under test | Claude Opus 5.5 (`claude-opus-5-5`) through the `claude-code` harness, on the **Claude Max subscription** (not pay-per-use) |
| Framework | `evalBase/` (vendored in this repo) + the `lab-ready-eval` skill (`.claude/skills/lab-ready-eval/`) |
| Research behind it | [reports/SwiftShader eval design.md](../../reports/SwiftShader%20eval%20design.md) and `research_notes/SwiftShader eval design/` |
| Change history | [CHANGELOG.md](CHANGELOG.md) — every change to this plan is logged there (see §16) |

---

## 0. What v1 is, in one paragraph

A coding model gets a Docker sandbox, a C/C++ toolchain, the Vulkan headers and
loader, a fixed replay program, and twelve hours. Its job is to write, **from
scratch**, a CPU implementation of Vulkan 1.3 — a drop-in Vulkan driver (an
"ICD") that behaves like Google's SwiftShader. It never sees SwiftShader's
source. It can ask the real SwiftShader to run test cases it writes itself (the
`oracle` tool) and can score itself on public cases (`grade_dev`). At the end,
its driver is rebuilt from source in a clean container and graded against the
real SwiftShader on a **hidden** set of generated test cases: rendered images and
buffers (fidelity), API return codes and reported properties (procedural), and
speed (performance). Before any model is run, the metric is proven honest by a
suite of **controls** — deliberately broken drivers whose scores are predicted in
writing and then measured.

This is "Variant A" from the research report. The narrower ideas (shader
compute, sampler/formats, rasterizer) are folded in as **case families**, not
separate evals. Reactor (Variant E) and SWE-style bug-fix tasks (Variant F) are
**out of scope for v1**.

---

## 1. Decisions (all approved 2026-09-28, as recommended)

Each has a recommendation. Anything you change here gets logged in the
CHANGELOG and flows through the rest of the plan.

| # | Decision | Recommendation (v1 default) | Why |
|---|---|---|---|
| D1 | Scope | Variant A: full drop-in Vulkan 1.3 ICD, headless (no swapchain/WSI) | Only variant big enough not to saturate (skill rule zero) |
| D2 | Oracle pin | SwiftShader `1e80438d2b93` (2026-09-16), CMake build, **LLVM 10 backend**, x86-64, fixed thread count | Latest HEAD at research time; LLVM 10 is the CMake default |
| D3 | LLVM / SPIRV-Tools in the model's sandbox | **Banned.** No LLVM, no linkable SPIRV-Tools, no other Vulkan driver, no Mesa | "No vendoring the real thing or its dependencies"; SPIR-V handling is part of the task |
| D4 | What the model may read | Vulkan headers + `vk.xml` registry + **the Vulkan spec (AsciiDoc sources at the pinned tag; v1.8 — was "HTML")** + **SPIR-V & GLSL.std.450 specs and grammar** + **Khronos Data Format Spec** (v1.8) + frozen device profile; no SwiftShader source, no internet | Grading "match this implementation" is only fair if the spec and the implementation's choices are given; the Data Format Spec is the only normative source for compressed-format decoding |
| D5 | Performance scaling | Keep evalBase defaults (`perf_half=16`, `perf_gate=8`, weight 0.10) **until the controls are measured**, then decide once in DESIGN.md before any model run | A from-scratch interpreter will likely be >16× slower than SwiftShader's JIT; decide with data, not now |
| D6 | Budget per attempt | 12 h wall, submit stop policy, 4 CPUs / 8 GB sandbox | Skill default for day-long tasks; fits this host |
| D7 | Opus settings | `claude-opus-5-5`, reasoning effort **high**, 1 attempt for v1 calibration (+ optional repeat) | Record effort; one attempt first to learn cost and behaviour |
| D8 | Pilot | One **1-hour** Opus pilot before the 12-hour run | Shakes out harness bugs for ~1/12 of the usage |
| D9 | Billing route for Claude Code | **Claude Max subscription** via `claude auth login` (decided by you, v1.1). `ANTHROPIC_API_KEY` must be **unset** on the host so Claude Code can't fall back to pay-per-use billing. Usage limits are waited out by the harness (`--rate-limit-wait 24`) and recorded, never counted as model time | Your plan; no per-token spend. Cost: usage-limit pauses, which make runs take longer in real time and cost Opus its conversation context at each relaunch (see §15, Stage 12) |
| D10 | Where things run | **WSL2 Ubuntu** with the working copy on the WSL ext4 disk (`~/swiftshader-evals`), opened from VS Code via Remote-WSL | evalBase assumes Linux/bash/python3; `/mnt/c` bind mounts are slow for Docker I/O; Windows Python 3.14 can't install the pinned numpy 1.26.4 |
| D11 | Hidden generator tree | Separate **private** git repo at `../swiftshader-evals-hidden` (sibling of this repo), pushed to a private GitHub remote | evalBase's loader refuses a hidden tree inside the public repo; one disk = unreproducible |
| D12 | Fairness comparator | Mesa **lavapipe** run on every case and reported, **never** used for tolerance | Tells readers how SwiftShader-specific the score is |
| D13 | Shader authoring for generated cases | Generators write GLSL; a pinned `glslangValidator` in the reference image compiles it to SPIR-V assets; every module passes `spirv-val` | Fast to write, deterministic when pinned; pure-Python SPIR-V emitter is v2 if needed |
| D14 | Metamorphic (spirv-fuzz-style) hidden cases | **Deferred to v2** (confirmed by you 2026-09-29, after Stage 3 found `spirv-fuzz` still ships) | Needs a protobuf build; every mutated shader would still have to pass the validity and determinism gates; the 21 families already vary the hidden split |

---

## 2. Stages at a glance

Every stage ends with: a commit, a **status table** (item / state / evidence
path), and a short brief of what the next stage starts from. No stage starts
until the previous one's exit criteria are met. Stages 0–10 build and validate
the eval with **no paid model calls**. Paid runs happen only in Stages 11–12,
and only after you say go.

| Stage | Name | Main output | Exit criterion | Gate |
|---|---|---|---|---|
| 0 | Host & environment setup | WSL2/Docker/Python ready; evalBase tests green | `pytest` passes in WSL; `docker info` OK; toy runs end-to-end | — |
| 1 | Repo layout & scaffolding | Instance directory from `template/`, private hidden repo, docs skeleton | Tree committed; `instance.py` imports | — |
| 2 | Oracle images | `ssvk-ref` image: SwiftShader (LLVM + Subzero), loader, validation layers, glslang, SPIRV-Tools, lavapipe | Image digest recorded; SwiftShader's own unit tests pass inside it | — |
| 3 | Oracle characterisation | Device profile, determinism measurements, CTS pass-list breakdown, open items verified | Determinism report per op type; profile JSON frozen | — |
| 4 | Case format & replay driver (`vkreplay`) | C++ driver + ledger + snapshot format + `Driver` class | Reference replays pilot cases twice byte-identically | — |
| 5 | Task text & starter | TASK.md, SPEC.md, CASE_FORMAT.md, REQUIREMENTS.md, starter skeleton | Starter builds and loads (enumerates 0 devices) | — |
| 6 | Scorer & metric v1.0 | `scorer.py` (format-aware distance + procedural checks), `MetricSpec`, unit tests | Synthetic tests separate bugs from rounding | — |
| 7 | Corpus & reference caches | Generators for ~21 families, public + hidden splits, both refcaches | Determinism proof k/k identical; tolerance distribution recorded | — |
| 8 | Controls | DESIGN.md predictions (committed first), wrapper-ICD controls, measurements, CONTROLS.md | Reference = 1.0 both splits; every control within prediction or restated; perf scaling decided (D5) | — |
| 9 | Harness | `ssvk-solver` image, `TaskSpec`, isolation check, no-key smokes, wire check of tool boundary | `NO-KEY SMOKE PASSED` for claude-code (+ openrouter); no ICD reachable in sandbox | — |
| 10 | Pre-flight review | Full status table, VALIDATION.md draft, cost estimate | You approve paid runs | **G-PAID** |
| 11 | Opus pilot (1 h) | One short attempt | Harness behaves; no incidents; cost per hour known | — |
| 12 | Opus calibration (12 h) | Attempt of record, graded idle on hidden split | Valid run; manifest complete | — |
| 13 | Analysis & reports | VALIDATION.md, CONTROLS.md, PERF_VARIANCE.md, curves, cheating audit, similarity audit | Every number has an evidence path | **G-REVIEW** |
| 14 | v1 close-out & v2 decision | Scope verdict (too easy / right / too hard), v2 list | CHANGELOG entry; plan marked closed | — |
| (15) | Publishing | Clean export — **not part of v1 unless you ask** | — | **G-PUBLISH** |

---

## 3. Repository layout (target end state of v1)

```
swiftshader-evals/                     (this repo, public-to-be)
  evalBase/                            framework (vendored; changes logged, see §3.2)
  swiftshader_vk/                      THE INSTANCE (evalBase --instance points here)
    instance.py                        Instance: Driver, Scorer, Metric, Corpus, Controls, Task
    scorer.py                          format-aware distance + procedural checks
    snapshot.py                        reader for the composite snapshot format (.ssnap)
    driver/                            vkreplay C++ source, CMakeLists.txt
    images/
      ref.Dockerfile                   trusted oracle image (ssvk-ref)
      solver.Dockerfile                model sandbox image (ssvk-solver)
      pins.lock                        every pinned commit / version / digest
    task/
      TASK.md  SPEC.md  CASE_FORMAT.md
      starter/                         CMakeLists.txt, src/icd.cpp skeleton, NOTES.md
    spec/                              frozen, read-only for the model:
      device_profile.json              SwiftShader's properties/features/limits/formats
      vulkan-spec/                     pinned Vulkan spec HTML (D4)
    corpus/
      gen/                             PUBLIC generators, one module per family
      gen/common/                      shared helpers (GLSL templates, state builders)
      public/                          generated public cases (committed)
      hidden/                          generated hidden cases (git-ignored)
      assets/                          content-addressed SPIR-V/textures (git-ignored)
    controls/
      shim/                            wrapper-ICD source (one build flag per control)
      <control>/control.json           mounts/env for each control
    tests/                             instance tests (scorer, driver, ledger, generators)
    docs/
      DESIGN.md                        metric, predictions, decision log
      REQUIREMENTS.md                  graded API surface / family list
      REPLAY_FORMAT.md                 (= CASE_FORMAT.md, operator view)
      REPRODUCIBILITY.md  RUNBOOK.md  CONTROLS.md  PERF_VARIANCE.md
      internal/                        working notes (stripped on export)
    reports/VALIDATION.md
    runs/                              git-ignored: refcaches, control runs, attempts, site
  docs/plan/PLAN_v1.md                 this document
  docs/plan/CHANGELOG.md               plan change log
  reports/                             research reports
  research_notes/                      research notes
  .claude/skills/lab-ready-eval/       the procedure

../swiftshader-evals-hidden/           PRIVATE repo (never inside this repo)
  gen/<same module names>.py           hidden-split generator functions, seeds, parameter tables
```

### 3.1 Git rules
- Work on `main` only with a clean tree; larger stages on a branch `v1/stage-N`, merged by fast-forward after its exit criteria pass.
- One commit per completed step at minimum; message references the stage (`v1 S4: vkreplay ledger writer`).
- Never commit: `runs/`, `corpus/hidden/`, `corpus/assets/`, credentials, `.env`, attempt transcripts containing keys.
- The hidden repo is committed and pushed to its private remote **the same day** any hidden generator changes.
- Nothing is pushed to GitHub without your go-ahead (this repo's `origin` is currently empty).

### 3.2 evalBase modifications
Avoid them. If one is unavoidable (a bug, or a missing hook), it goes in
`docs/internal/EVALBASE_CHANGES.md` with the reason, the diff summary and a new
test, and the full evalBase suite must still pass.

---

## 4. Stage 0 — Host and environment setup

Host (measured 2026-09-26): Intel Core Ultra 7 356H, 16 logical CPUs (hybrid
P/E cores), 31.4 GB RAM, Windows 11 Home, WSL2 Ubuntu running, Docker CLI 29.6.1.

1. **Confirm Docker engine from WSL.** In Ubuntu: `docker info`, `docker run --rm hello-world`. Enable Docker Desktop's WSL integration for Ubuntu if the CLI can't reach the engine.
2. **Size WSL.** Write `%UserProfile%\.wslconfig`: `memory=24GB`, `processors=16`, `swap=8GB`, `vmIdleTimeout=-1` (so WSL does not shut down during a 12 h run). `wsl --shutdown`, restart, verify with `nproc` / `free -g`.
3. **Identify P-cores.** ~~Pin timed runs to a P-core set via `--cpuset-cpus`.~~ **Measured 2026-09-28 (v1.2): not possible.** WSL exposes 16 identical vCPUs (no `cpu_core`/`cpu_atom` in sysfs), and a pinned single-thread benchmark gives the same time on every vCPU (0.45–0.51 s, no fast/slow split), because Windows schedules vCPUs onto physical P/E cores freely. Performance noise is instead handled by: idle host, Windows power mode "Best performance" on AC power, ≥2 s timed runs, median of repeats, reference and candidate timed in the same session, and the spread reported in PERF_VARIANCE.md.
4. **Laptop hygiene for long runs** (documented in RUNBOOK): plugged in; Windows power plan "Best performance"; `powercfg /change standby-timeout-ac 0` and `hibernate-timeout-ac 0`; pause Windows Update for the run window; close heavy apps. Record power state in every run manifest note.
5. **Move the working copy into WSL** (D10): `git clone` this repo into `~/swiftshader-evals` inside Ubuntu (or `cp -a` then verify `git status` clean). Open it in VS Code with Remote-WSL. The Windows copy becomes stale — delete or keep read-only (your call).
6. **Python.** WSL is Ubuntu 26.04 with Python 3.14 and no passwordless sudo, so (v1.2) Python 3.12 comes from **uv** (user-level, no root): `uv python install 3.12`, `uv venv --python 3.12 .venv`, `uv pip install -r evalBase/requirements.txt` (numpy 1.26.4, pytest 7.4.4), `uv pip install -e evalBase --no-deps`, plus instance deps (added to `swiftshader_vk/requirements.txt` as needed; pin every version). Activate with `. .venv/bin/activate`.
7. **Baseline the framework.** `cd evalBase && EVALBASE_NO_DOCKER=1 python -m pytest -q`; then `python -m pytest -q tests/test_docker_smoke.py`. README says `384 passed, 3 skipped`; on this host it is **383 passed, 4 skipped** after one test-only fix (logged in `docs/internal/EVALBASE_CHANGES.md`). The 4th skip is the Codex/OpenCode CLI tests (not installed; v1 uses Claude Code only), the slow determinism test (opt-in, `EVALBASE_SLOW=1`, passes) and the Docker smoke (run separately, passes).
8. **Run the toy end to end** (the README's command list) to confirm the whole machinery works on this host: corpus → determinism → refcache → grade-ref (1.0) → controls → smokes → export.
9. **Tooling on the host:** `git` is present. `jq`, `cmake`, `ninja-build` and `build-essential` are **not** installed and need sudo; they're optional because every real build happens inside Docker images and Python replaces `jq`. If wanted: `sudo apt install jq cmake ninja-build build-essential` (you run it; it needs your password).
11. **Line endings.** Windows git checks files out with CRLF (`core.autocrlf=true`). The WSL copy sets `core.autocrlf=false`, `core.eol=lf`, so scripts and Python run correctly in Linux containers.
10. **Claude Code CLI** installed in WSL (`claude --version` recorded) and logged in to the **Claude Max** account with `claude auth login` (D9). Verify: `claude auth status` shows the subscription login, and `env | grep -i anthropic` prints nothing (an `ANTHROPIC_API_KEY` in the environment would switch Claude Code to pay-per-use billing). The login lives in `~/.claude/` on the host only — never copied or mounted into any image or container.

**Exit:** status table with Docker version, WSL resources, P-core list, pytest counts, toy grade-ref = 1.0.

---

## 5. Stage 1 — Repo layout and scaffolding

1. `cp -r evalBase/template swiftshader_vk`; rename placeholders (`<eval>` → `ssvk`, `<EVAL>_HIDDEN_GEN` → `SSVK_HIDDEN_GEN`).
2. Create `../swiftshader-evals-hidden/` with `gen/` and a README; `git init`; push to the private remote `https://github.com/SidS-123/private-repo-swiftshader` (created by you, v1.3).
3. Set `CorpusSpec(hidden_env="SSVK_HIDDEN_GEN", hidden_default=<repo>/../swiftshader-evals-hidden, forbidden_hidden_roots=(<repo root>,))`.
4. `.gitignore`: `runs/`, `corpus/hidden/`, `corpus/assets/`, `*.ssnap` outside `corpus/public`, `.venv/`, `.env`.
5. Docs skeletons from `template/docs/` into `swiftshader_vk/docs/`; add `REQUIREMENTS.md`, `REPLAY_FORMAT.md`, `docs/internal/`.
6. `instance.py` stubs import cleanly: `python -c "import swiftshader_vk.instance"`; `EVALBASE_INSTANCE=swiftshader_vk` set in `.envrc`/RUNBOOK.
7. Seed `docs/DESIGN.md` decision log with D1–D14 as dated entries.

**Exit:** tree committed, hidden repo pushed privately, import works.

---

## 6. Stage 2 — Oracle images (`ssvk-ref`)

All builds from source in Docker, base image **pinned by digest**, every
package pinned by version, all pins in `images/pins.lock`.

**As built (v1.4):** `ubuntu:24.04@sha256:008173c2…`; apt resolves from the
`snapshot.ubuntu.com` instant `20260928T000000Z` (after an unpinned CA-certificates
bootstrap), and the resolved package list is written to `/opt/ssvk/PACKAGES` in the image;
gcc 13.3 (as SwiftShader's own CI); Khronos components at one SDK tag, `vulkan-sdk-1.4.357.0`
(closest to SwiftShader's headers, 1.4.355); Mesa `26.2.3`, meson 1.9.1. SwiftShader is built
without XCB/Wayland WSI (D1), and the oracle's `SwiftShader.ini` fixes `ThreadCount=4`
(`threads` variant: 1). `vulkaninfo` is patched to request at most Vulkan 1.3, because
SwiftShader fails `vkCreateInstance` for `apiVersion` > 1.3.

1. **Base:** `ubuntu:24.04@sha256:<digest>` (record). Toolchain: clang/gcc pinned, cmake, ninja, python3.12, git.
2. **SwiftShader** at `1e80438d2b93`, fetched with submodules at pinned commits:
   - Build A (oracle of record): CMake, `REACTOR_BACKEND=LLVM` (LLVM 10), Release, x86-64 → `libvk_swiftshader.so` + `vk_swiftshader_icd.json`.
   - Build B (perturbation `subzero`): same commit, `REACTOR_BACKEND=Subzero`.
   - Build SwiftShader's own tests (`vk-unittests`, `ReactorUnitTests`, `system-unittests`) and run them in the image as a smoke of the build.
3. **Vulkan loader + headers** at the version SwiftShader's `third_party` expects (record); `vulkaninfo` from Vulkan-Tools at the same tag.
4. **Validation layers** (Khronos `Vulkan-ValidationLayers`) pinned tag — used to validate every generated case on the oracle.
5. **Shader tools:** `glslang` (glslangValidator) and `SPIRV-Tools` (`spirv-val`, `spirv-dis`, `spirv-as`) pinned tags.
6. **lavapipe:** Mesa built from a pinned release tag with `-Dvulkan-drivers=swrast -Dgallium-drivers=llvmpipe` (its own LLVM from apt, pinned). Only used as the fairness comparator (D12).
7. **vkreplay** (Stage 4) is built into this image once it exists; the image gets rebuilt then.
8. **Runtime config:** a `SwiftShader.ini` fixing `ThreadCount` (and affinity policy) for the oracle of record; the `threads` determinism variant uses a second ini.
9. Entry points: `drive <case> <out> <assets>`, `drive-candidate <candidate_dir> <case> <out> <assets>`, `drive-lavapipe ...` (dispatch on env `DRIVER_PERTURB`).
10. Record `docker image inspect --format '{{.Id}}'`; `python -m evalbase.harness.run versions` later records it in manifests.
11. **CI-less build log** saved under `swiftshader_vk/docs/internal/build-logs/` (not committed if large; summarised in REPRODUCIBILITY.md).

**Exit:** image digest, SwiftShader unit tests pass inside it, both backends load via the loader (`vulkaninfo --summary` shows "SwiftShader Device").

---

## 7. Stage 3 — Oracle characterisation (measure before designing further)

This stage answers the research report's open questions with measurements.

1. **Device profile.** `vulkaninfo --json` (both backends) → `spec/device_profile.json`: API version, all features, properties, limits (incl. `subPixelPrecisionBits`, `subTexelPrecisionBits`, `mipmapPrecisionBits`), queue families, memory types, every format's properties, extension list. Diff LLVM vs Subzero profiles (expect identical).
2. **CTS pass-list breakdown.** From SwiftShader's `tests/regres/testlists` (the 2026-01-26 snapshot): count PASS by dEQP group prefix (`dEQP-VK.api`, `.pipeline`, `.spirv_assembly`, `.texture`, `.renderpass`, …). Output: a table in `docs/internal/cts_breakdown.md`. This sets family weights and tells us where SwiftShader's behaviour is richest.
3. **Graded surface draft.** From (1)+(2), write `docs/REQUIREMENTS.md`: every Vulkan feature area graded in v1, and explicit exclusions (swapchain/WSI, external memory/fd/handles, device groups, video, protected memory, sparse, geometry/tessellation — SwiftShader lacks the last two anyway).
4. **Determinism measurement** on a hand-written pilot corpus of ~50 cases across op types (Stage 4's driver is needed; do a minimal version first or run this after S4 step 6):
   - N=5 repeats × ThreadCount {1, default} × backend {LLVM, Subzero}.
   - Record per op type: byte-identical? max ULP/LSB diff?
   - Expected sources of variation: atomics ordering, float reductions across threads, timestamps, pipeline statistics, transcendental approximations differing between backends.
   - Output: `docs/internal/determinism.md` → a list of **excluded or canonicalised** op types (e.g. order-dependent atomics → only commutative totals are snapshotted; timestamp values never snapshotted, only validity).
5. **Verify open items** from the report, each with a source or a measurement:
   - Loader env var: `VK_DRIVER_FILES` vs `VK_ICD_FILENAMES` for the pinned loader (plan: set both).
   - `subPixelPrecisionBits` etc. (from step 1).
   - evalBase's grading build timeout. *Verified v1.5: the clean rebuild uses `build_export(timeout=1800)` (30 min, 4 CPUs, 8 GB), not 900 s; each model shell call is capped at 600 s.*
   - Spec precision values for SPIR-V ops (from the pinned spec's `spirvenv` appendix, quoted).
   - Whether `spirv-fuzz` exists in the pinned SPIRV-Tools (D14).
   - Khronos conformance entry for SwiftShader (for the README only; not load-bearing).
6. **lavapipe profile** diff vs SwiftShader profile (for the fairness note).

**Exit:** device profile frozen (hash recorded), determinism report, REQUIREMENTS.md draft, every open item marked verified/refuted.

*v1.5:* all done except the determinism report, which needs `vkreplay` and now runs right after Stage 4 step 6, before Stage 4 exits. Findings: `swiftshader_vk/docs/internal/stage3_findings.md`.

---

## 8. Stage 4 — Case format and the replay driver (`vkreplay`)

### 8.1 Case format (JSON, evalBase-compatible)
Top level: `name`, `family`, `category` (`replay` | `procedural` | `performance`), `meta` (incl. `timed_run_index` for perf), `assets` (sha256 names), `version`, `ops`.

Op vocabulary (Amber-like, each op maps to a small set of Vulkan calls; every
call's `VkResult` is logged):

| Group | Ops |
|---|---|
| Setup | `instance` (api version, app name, extensions), `device` (features, extensions, queues), `query_props` (named properties/features/limits to record), `format_props` |
| Memory | `buffer`, `image`, `image_view`, `sampler`, `alloc` (memory type by property flags), `upload` (asset → buffer/image), `map_write`, `map_read` |
| Shaders & state | `shader` (SPIR-V asset), `desc_layout`, `desc_pool`, `desc_set`, `desc_write`, `pipeline_layout`, `compute_pipeline`, `graphics_pipeline` (full state JSON incl. spec constants), `render_pass`, `framebuffer` |
| Commands | `cmd` block: `begin_rendering`/`begin_render_pass`, `bind_*`, `push_constants`, `set_dynamic`, `draw`, `draw_indexed`, `draw_indirect`, `dispatch`, `dispatch_indirect`, `copy_*`, `blit`, `clear_*`, `resolve`, `barrier`, `query_*`, `set_event`/`wait_events` |
| Execution | `submit` (fences, binary + timeline semaphores), `wait_fence`, `wait_idle`, `event_status`, `fence_status` |
| Output | `snapshot` (composite of named attachments/buffers → one `.ssnap`), `query` (value into ledger), `run` (timed block: warm-up + ≥2 s measured repetitions) |
| Negative | `expect_error` wrappers for calls whose error code is the graded behaviour |

Documented fully in `task/CASE_FORMAT.md` (the model reads it) and `docs/REPLAY_FORMAT.md`.

### 8.2 Snapshot format (`.ssnap`, v1)
One file per `snapshot` event, the scorer's primary channel: a JSON header
(attachment list: name, kind color/depth/stencil/buffer, VkFormat, width,
height, layers, byte offsets) + raw little-endian payloads. Reason: evalBase's
generic check and `dev/reference` copy only handle the **primary channel**, so
every attachment must live in one file or it goes ungraded. A PNG preview of
color attachments is produced by `preview_rgb8`.

### 8.3 Driver trust boundary (important — anti-cheat)
The candidate is a shared library loaded **into the driver process**, so it can
run arbitrary code there. Therefore:
1. `vkreplay` is split into a **trusted parent** and an **untrusted child**. The parent parses the case, owns `/out`, writes the ledger and snapshot files, and keeps the clock. The child loads the loader + candidate ICD and executes ops, streaming results (VkResults, readback bytes) to the parent over a pipe.
2. The child runs as an unprivileged user with **no write access to `/out`** and no read access to anything but the case, assets and the candidate dir.
3. **Timing** of `run` blocks is measured by the parent: from the parent's "start" message to its receipt of the post-run readback, which must itself be correct (fidelity-gated). A candidate cannot fake speed without producing correct output.
4. The ledger is flushed after each event so a crash leaves a partial, valid ledger (`exit` ≠ `ok`), and crash/timeout are recorded with signal/return code.
5. The child has a memory limit and per-op watchdog; runaway threads are bounded by the container's CPU limit.

### 8.4 Implementation steps
1. C++17, CMake, links only the Vulkan loader + a JSON parser (nlohmann/json, pinned, vendored in `driver/third_party`).
2. Loader setup: write a temporary `icd.json` pointing at `/candidate/build/libvk_candidate.so` (or the oracle `.so`), set `VK_DRIVER_FILES` and `VK_ICD_FILENAMES`, disable implicit layers (`VK_LOADER_LAYERS_DISABLE=~implicit~`) so nothing else interferes.
3. Optional `--validate` mode enables the validation layer (reference only), used by the generator gate (Stage 7).
4. Perturbation hooks (reference only; child refuses them for candidates): `threads` (alt ini), `subzero` (alt .so), `vtxjitter`, `texcoord_ulp`, `lavapipe` (alt ICD).
5. `Driver.run` in `instance.py` wraps the container call (template shape), enforces `inspect_case` limits for model-written cases: ≤ 512×512 per attachment, ≤ 64 snapshots, ≤ 8 MiB case+assets, ≤ 16 attachments per snapshot.
6. Tests (`swiftshader_vk/tests/test_driver.py`): replays a pilot corpus on the reference twice → byte-identical outputs; crash candidate → partial ledger; child cannot write `/out` (a malicious test ICD tries).
7. Build `vkreplay` into both images; the solver image gets **only the binary** (plus its runtime deps).

**Exit:** pilot corpus (~50 hand-written cases, also used by Stage 3.4) replays `exit: ok` twice identically on the reference; trust-boundary tests pass.

**As built (v1.7, 2026-09-29).** Stage 4 met its exit criteria (56 pilot cases, all `ok`, byte-identical; hostile-ICD test passes). Where the build differs from the text above:
- **Op names** are those in `task/CASE_FORMAT.md` (e.g. `query`, `view`, `exec`/`record`/`submit`, commands inside `cmds`); `task/CASE_FORMAT.md` is authoritative.
- **No `expect_error` op.** Errors are observed: every `VkResult` is in `ledger.calls`, and `enumerate`, `image_format_props`, `wait_fence`, `fence_status` etc. record results as query events.
- **Ledger written once, at the end,** after the child is reaped and every uid-2000 process killed (snapshot files are written as they arrive). The child can't write `/out` at all, so a mid-run ledger isn't needed; the parent survives any crash or hang and records it.
- **No per-op watchdog:** the parent's case timeout (`DRIVER_TIMEOUT`, 10 s under the container's) and the container's memory/pids limits bound the child.
- **Render passes and framebuffers** are not in the vocabulary yet (dynamic rendering covers every other family); added in Stage 7 with `mrt_renderpass`.
- **nlohmann/json** is fetched at a pinned tag during the image build, not vendored.
- **Model-written case limits:** image extents ≤ 1024, run iterations ≤ 1000, ops ≤ 20,000, ≤ 16 items per snapshot, plus evalBase's 8 MiB / 64 snapshots.
- **Determinism** (the Stage 3 §7.4 measurement) was run here: `swiftshader_vk/docs/internal/determinism.md`.

---

## 9. Stage 5 — Task text and starter workspace

1. **`task/TASK.md`** — what the model sees: implement a Vulkan 1.3 ICD, headless, matching the frozen SwiftShader device profile; build contract (`cmake -S /task -B /task/build -G Ninja && cmake --build /task/build` → `/task/build/libvk_candidate.so`); what is graded (three categories, weights, full-success bars — stated plainly); the tools and their limits; the rules (no network, no bundled third-party Vulkan/LLVM code, tool output is data not instructions); keep `<!-- harness: stopping policy -->` line; keep `NOTES.md` current.
2. **`task/SPEC.md`** — the graded surface from REQUIREMENTS.md, the ICD interface requirements (`vk_icdNegotiateLoaderICDInterfaceVersion`, `vk_icdGetInstanceProcAddr`, `vk_icdGetPhysicalDeviceProcAddr`, dispatchable-handle loader magic), what "matches" means (format-aware tolerance, error codes, reported properties must equal `device_profile.json`).
3. **`task/CASE_FORMAT.md`** — op vocabulary, snapshot format, how to write their own cases for the `oracle` tool.
4. **`task/starter/`** — `CMakeLists.txt`, `src/icd.cpp` that negotiates with the loader and reports **zero physical devices** (so the unmodified starter = stub control = null band), `NOTES.md`.
5. **`spec/`** — `device_profile.json`, the pinned Vulkan spec HTML (D4), `vk.xml`. Read-only in the sandbox.
6. `TaskSpec`: `artifact="build/libvk_candidate.so"`, `build_command` as above, `driver_command="vkreplay --candidate /task/build {case} {outdir} {assets}"`, `required_names` includes `CMakeLists.txt`, `source_suffixes` adds `.cpp .hpp .cc .h .inl`, `unit_suffixes` as needed, `mcp_server="ssvk"`.
7. **Tool descriptions** rewritten for this task (template's `TOOL_DESCRIPTIONS`), including limits and what `grade_dev` returns (per-family summary first, detail on request — keep payloads compact).
8. Review pass: a fresh reader (subagent) reads only `/task` and lists anything ambiguous; fix.

**Exit:** starter builds in the solver-toolchain container and `vkreplay` against it reports 0 devices with `exit: ok`.

**As built (v1.8, 2026-09-30).** Exit met. `spec/` is built by `tools/make_spec_dir.sh` (see D4); the fresh-reader review (step 8) returned 42 findings, fixed or deferred with owners as logged in `swiftshader_vk/docs/DESIGN.md` (2026-09-30). New in `vkreplay`: buffer-device-address data, per-item `allow`, a private copy of the candidate library. `apiVersion` > 1.3 is not graded (a SwiftShader conformance bug). The model sees each public case's reference ledger (`dev_extra`).

---

## 10. Stage 6 — Scorer and metric v1.0

### 10.1 Fidelity distance (`scorer.py`)
Modelled on dEQP's comparators, replacing the toy's (which would pass a
9.6/255 bias and all 1-LSB errors):

- **Per-texel error in the format's own units:** UNORM/SNORM → LSBs; float formats → ULPs with an absolute floor near zero; sRGB → error in encoded space; integer formats and stencil → **exact** (any mismatch = max error); depth → format units (D16 LSB, D32F ULP); NaN/Inf class mismatch = max error. Buffers: typed per binding (layout from case meta), same rules.
- **Per 8×8 block (no downsampling), three terms:**
  - structural: `rms(e) / S`
  - bias: `|mean(signed e)| / B` (makes a uniform offset visible — the skill's "absolute term")
  - coverage: `fraction(e > K) / C` (catches sparse large errors, like dEQP's position-deviation compare)
  - `d_block = min(1, max(terms))`, then `< FLOOR → 0`.
- **Snapshot D** = mean of block defects over all attachments (weights per attachment kind in case meta; default equal). Images kept small (64²–256²) so a localised bug is a meaningful fraction of blocks.
- **Hard 1.0:** missing snapshot, wrong size/format, candidate uniform while reference is not.
- **Provisional constants** (set for real by controls in Stage 8): `K=1 LSB`, `S=2 LSB`, `B=0.5 LSB`, `C=0.02`, `FLOOR=0.05`. The `round_trunc` control fixes the 1-LSB floor honestly.

### 10.2 Procedural checks
Derived only from the reference's ledger for that case:
`exit_ok` (gate) and `output_matches_reference` (generic); plus
`vkresult_stream` (every call's VkResult, duplicates collapsed),
`query:<name>` (properties/limits/features/format props — exact),
`occlusion:<name>` (exact counts; approximate-mode only as boolean),
`sync:<name>` (fence/event/semaphore status sequence),
`profile_match` (reported device profile equals `device_profile.json`).

### 10.3 Timings
`timings(ledger)` reads parent-measured `run` events; median of repetitions.

### 10.4 `MetricSpec` v1.0
```
version="ssvk-1.0"
perturbations=("threads", "subzero", "vtxjitter", "texcoord_ulp", "lavapipe")
tolerance_perturbations=("subzero", "vtxjitter", "texcoord_ulp")   # measured in S8; any may be dropped
k_noise=2, k_sens=2, t_lo=0.03, t_hi=0.30, hill_n=4
weights={"replay": 0.60, "procedural": 0.30, "performance": 0.10}
perf_half=16, perf_gate=8, case_bar=0.9, procedural_bar=0.95        # D5: revisit in S8
```
`sample_mult=2` is interpreted by the driver as "run with the alternate thread
count" so evalBase's n1/n2 noise measurement becomes the determinism check.
`lavapipe` is stored in the cache but never tolerated.

### 10.5 Tests (`tests/test_scorer.py`)
Synthetic snapshots per format class: identical → D=0; +1 LSB everywhere → low but visible via bias term; gain ×2 → D≈1; single-pixel edge flips → below T; half-pixel shift → above T; one wrong 8×8 region → proportional D; integer off-by-one → max; NaN vs number → max; missing attachment → 1. Plus procedural check tests on hand-made ledgers.

**Exit:** tests green; distances on the pilot corpus between reference and each perturbation printed and sane.

---

## 11. Stage 7 — Corpus generation and reference caches

### 11.1 Families (v1 target counts; weights set from Stage 3's CTS breakdown)

| # | Family | Category | What it covers | Public | Hidden |
|---|---|---|---|---|---|
| 1 | `inst_dev` | procedural | instance/device creation, enumeration, properties, features, limits, format props, queue families, memory types | 4 | 8 |
| 2 | `mem_buf` | replay+proc | buffers, memory types, map/unmap, copy/fill/update, alignment | 4 | 8 |
| 3 | `compute_arith` | replay | int/float arithmetic, conversions, bit ops, built-ins | 6 | 12 |
| 4 | `compute_cf` | replay | branches, loops, switch, functions, early exit | 4 | 10 |
| 5 | `compute_types` | replay | vectors, matrices, structs, arrays, shared memory, buffer device address (no 8/16/64-bit: unsupported, v1.7) | 4 | 10 |
| 6 | `compute_subgroup` | replay | subgroup ops at size 4 | 3 | 6 |
| 7 | `compute_atomics` | replay | commutative atomic totals only (determinism) | 2 | 5 |
| 8 | `compute_image` | replay | storage images, image load/store, texel buffers | 3 | 6 |
| 9 | `raster_tri` | replay | fill rules, culling, winding, viewport, scissor, depth bias, clipping | 6 | 14 |
| 10 | `raster_lines_points` | replay | lines (Bresenham/rect), points, point size | 3 | 6 |
| 11 | `vertex_input` | replay | vertex formats, strides, instancing, index types, primitive restart, topologies | 4 | 8 |
| 12 | `blend` | replay | all factors/ops, constants, write masks, advanced blend ops (no logic ops / dual-source: unsupported, v1.7) | 4 | 10 |
| 13 | `depth_stencil` | replay | compare ops, stencil ops, depth formats, bounds, clamp | 4 | 10 |
| 14 | `tex_sample` | replay | filters, mip modes, address modes, borders, LOD bias/clamp, compare, cube/3D/arrays, gather | 6 | 16 |
| 15 | `formats_copy_blit` | replay | buffer↔image copies, blits, clears, resolves, format conversions, BC/ETC2/ASTC-LDR sampling | 6 | 16 |
| 16 | `msaa` | replay | 4× raster, sample shading, masks, alpha-to-coverage, resolve | 3 | 8 |
| 17 | `mrt_renderpass` | replay | multiple attachments, subpasses, input attachments, load/store ops, dynamic rendering | 4 | 8 |
| 18 | `descriptors_push` | replay | descriptor types, dynamic offsets, push constants, spec constants, descriptor indexing | 3 | 8 |
| 19 | `queries_sync` | replay+proc | occlusion queries, fences, events, timeline semaphores, barriers, multi-submit sequences (stale-output detector) | 4 | 8 |
| 20 | `errors_robust` | procedural | graded error codes, robustness2 out-of-bounds behaviour | 3 | 6 |
| 21 | `perf_*` | performance | fill-rate, compute throughput, geometry, texture-heavy (≥2 s timed after warm-up) | 4 | 4 |
| | **Total** | | | **~84** | **~188** |

"Most cases are sequences with mutations" (template rule): replay cases
typically render/dispatch, snapshot, change state, snapshot again.

### 11.2 Generator rules
1. One module per family in `corpus/gen/`, `generate(split, corpus)`; the hidden branch is one line: `corpus.hidden_generate(__file__, split)`. Hidden functions, seeds, parameter tables live only in `../swiftshader-evals-hidden/gen/<same>.py`.
2. Public and hidden draw from the **same families** with different seeds, parameter ranges and compositions (e.g. hidden uses formats/filters/blend combos not in public).
3. Compute in float64, narrow once (`CorpusWriter.write` handles float32 shortest-decimal).
4. GLSL → SPIR-V via pinned glslang in the ref image (D13); assets content-addressed.
5. **Validity gate:** every generated case must pass `spirv-val` and replay on the oracle with the **validation layer on and zero errors**. Invalid usage is undefined behaviour; its output means nothing. Gate failures abort generation.
6. **Determinism gate:** order-dependent results excluded/canonicalised per Stage 3 findings.
7. **Timing gate:** every non-perf case runs sub-second on the reference (so evalBase's `max(60 s, 10× ref)` timeout never turns slowness into a fidelity failure).
8. Public case names `<family>_pub_<x>`; hidden names don't leak parameters.
9. Grep the public tree for hidden seeds/function names (CI-style check in `tests/`).

### 11.3 Commands
```sh
python -m evalbase.corpus.common both                       # public + hidden
python -m evalbase.corpus.determinism --split both          # k/k identical (run inside ref image — glslang)
python -m evalbase.grader.cli refcache                      # public cache
python -m evalbase.grader.cli --corpus swiftshader_vk/corpus/hidden \
    --cache swiftshader_vk/runs/refcache-hidden refcache    # hidden cache
```
Record the **tolerance distribution**: min / median / max T, count at `t_lo`, count at `t_hi`, per family. Expected: most cases at `t_lo=0.03` (deterministic oracle) — that is fine *only if* the distance is scaled correctly (Stage 6/8).

### 11.4 Pilot first
Do families 1, 3, 9, 14 first (~10 public cases), push them through refcache
→ grade-ref → two controls, fix what breaks, then scale out.

**Exit:** both splits generated; determinism k/k; both caches built; grade-ref = 1.0 on both; tolerance table recorded.

---

## 12. Stage 8 — Controls (predict, then measure)

1. **Write every prediction into `docs/DESIGN.md` and commit before measuring.** (Committing first is the proof.)
2. Build the **wrapper ICD** (`controls/shim/`): `dlopen`s the real SwiftShader `.so`, forwards every entry point, alters one thing per build flag. `control.json` mounts the reference `.so` read-only for control runs only.
3. Controls and predicted bands (to be finalised in DESIGN.md):

| Control | Alteration | Predicted | Guards |
|---|---|---|---|
| `reference` | real SwiftShader as candidate | exactly 1.0, both splits | grader accepts its definer |
| `reference_subzero` | Subzero build | fidelity ≥ 0.93 | tolerance admits its own source |
| `lavapipe` | Mesa CPU driver | measured, reported | fairness / SwiftShader-specificity |
| `stub` | starter skeleton (0 devices) | null band ≈ 0.04–0.06 | floor |
| `init_only` | devices enumerate, everything else fails | slightly above stub (procedural only) | partial procedural credit is small |
| `hardcode_public` | replays stored public outputs keyed on command-stream hash | ~1.0 public, null hidden | hidden split defeats memorisation |
| `gain_x2` | color readback ×2 | fidelity ≤ 0.2 | bias term works |
| `srgb_mishandled` | skip sRGB encode/decode | sRGB cases ≈ 0, others ≈ 1 | format handling seen |
| `half_pixel` | +0.5 px raster offset | raster families ≈ 0 | coverage term works |
| `nearest_filter` | linear → nearest | filtering cases ≈ 0 | sampler errors seen |
| `round_trunc` | truncating UNORM conversion | ≤ 1 LSB → sets the floor (should score high but not 1.0) | rounding sensitivity calibrated |
| `stale_frame` | returns previous readback | ≈ 1/snapshots on sequences | sync / sequence bugs seen |
| `one_thread` | pinned to 1 core | fidelity 1.0, perf down, no full success | perf independent of fidelity |
| `aux_garbage` | garbage in depth/2nd attachment | fidelity modestly down | composite snapshots graded |
| `wrong_limits` | alters reported limits/format props | fidelity 1.0, procedural down | procedural checks bite |
| `wrong_errors` | returns VK_SUCCESS where reference errors | procedural down on `errors_robust` | error contract graded |
| `malformed` / `crash` | segfault on 2nd submit; absurd sizes | null band, no grader exception | hostile candidate handled |

4. **Measure on both splits:**
```sh
python -m evalbase.grader.cli grade-ref
python -m evalbase.grader.cli control <name>          # each control, public
python -m evalbase.grader.cli --corpus .../hidden --cache .../refcache-hidden control <name>   # hidden
python -m evalbase.grader.perturbed                   # perturbed-reference control
python -m evalbase.reports.controls_summary --write   # regenerates docs/CONTROLS.md
```
5. **Tune constants** S, B, K, C, FLOOR from the results: `round_trunc` defines "harmless", `gain_x2`/`half_pixel`/`nearest_filter`/`srgb` define "clearly broken". Each tuning is a new metric version (`ssvk-1.0` → `1.1` …), logged in DESIGN.md, and **everything is re-measured** after each change.
6. **Tolerance-set decision:** measure what each tolerated perturbation costs (skill: "the tolerance set is part of the metric"); drop any that inflate `lavapipe` or a broken control.
7. **Performance decision (D5):** with `one_thread`, `reference_subzero` and `lavapipe` timings in hand, decide: keep 16/8 ("full success means JIT-class speed") or rescale. Write it in DESIGN.md **before Stage 11**.
8. Any miss → either a metric defect (fix, new version, re-measure everything) or a wrong prediction (restate it, dated, with reason). No silent changes.
9. Controls sweeps run on an otherwise idle host (never alongside anything else).

**Exit:** reference 1.0 both splits; stub in null band; hardcode_public null on hidden; every control inside its (possibly restated) band; D5 decided; CONTROLS.md generated.

---

## 13. Stage 9 — Harness (the model's side)

1. **Solver image `ssvk-solver`** (`images/solver.Dockerfile`), pinned base by digest:
   - Included: clang + gcc (pinned), cmake, ninja, make, gdb, valgrind (optional), python3, git, jq, Vulkan headers + loader (pinned), `vkreplay` binary, `spirv-dis`/`spirv-as` **binaries only**, the validation layer (renders nothing), unprivileged user `solver`.
   - Excluded (D3): SwiftShader, Mesa/lavapipe, any other ICD, LLVM libraries/headers, SPIRV-Tools libraries/headers, glslang library, internet.
   - `TaskSpec.isolation_check`: a script that fails if any `*icd.json` other than the candidate's is visible, if `libLLVM*`/`libSPIRV-Tools*` exist, if network resolves, or if SwiftShader strings are found on disk.
2. **`TaskSpec`** completed (Stage 5 step 6), `SmokeSpec` (fake model writes a tiny broken ICD, runs one oracle case; `max_overall=0.05`, `driver_fails=True`).
3. **Workspace check:** `populate` builds `/task` from `task/`, `spec/`, `dev/cases` (public), `dev/reference/<name>/` (+ PNG previews), `dev/CASE_FORMAT.md`; `spec/` and `dev/` read-only. Inspect a populated workspace by hand.
4. **No-key smokes:**
```sh
python -m evalbase.harness.run smoke --harness claude-code --budget-seconds 60
python -m evalbase.harness.run smoke --harness openrouter --budget-seconds 60
```
   Both must print `NO-KEY SMOKE PASSED`. (codex/opencode optional for v1 — Opus first.)
5. **Wire check of the tool boundary** for Claude Code: point the CLI at a local recording endpoint, read the `tools` array: exactly the five `ssvk` MCP tools, no built-ins. Record the CLI version. Redo after any CLI upgrade.
6. **Credential hygiene:** the Claude Max login stays in the host's `~/.claude/`; the Claude Code CLI runs on the host and only its tool calls enter the sandbox, so the container never sees it. Check that no `ANTHROPIC_API_KEY` is set in the harness environment, that `~/.claude` is not mounted into any container (`docker inspect` the sandbox), and grep a smoke run dir for credential-shaped strings (the `privacy` module scrubs launch files).
7. **Checkpoint cadence:** `--checkpoint-minutes 15` during runs; curve graded later at `--every 30 --dedupe`.

**Exit:** smokes pass, wire check recorded, isolation check passes on the solver image and fails on a deliberately contaminated image.

---

## 14. Stage 10 — Pre-flight review (**gate G-PAID**)

I stop here and hand you:
1. The full status table (skill format): oracle digest, corpus counts + determinism, tolerance distribution, reference-as-candidate, null band, controls predicted vs observed, smokes, tests.
2. `reports/VALIDATION.md` draft (sections 1–7 filled; 8–10 pending runs).
3. The decided metric version and D5 outcome.
4. A **usage estimate** for the 1 h pilot and the 12 h run on Claude Max: how many usage-limit pauses to expect and so how long the run takes in real time. It can't be measured before a run, so it's a rough range, replaced by what the pilot actually uses. Also a check of your remaining weekly Max allowance before starting.
5. The exact launch commands.

Nothing paid runs until you say go.

---

## 15. Stages 11–14 — Opus runs, grading, analysis

### Stage 11 — 1-hour pilot
```sh
python -m evalbase.harness.run --harness claude-code --model claude-opus-5-5 --reasoning high \
  --stop-policy submit --rate-limit-wait 24 --budget-hours 1 --checkpoint-minutes 15 \
  --cpus 4 --memory 8g --out swiftshader_vk/runs/attempts/opus55-pilot
```
Check: tools used (non-zero `tool_call_total`), no incidents, oracle/driver/grade_dev payload sizes sane, how much of the Max usage window one hour consumed (from the manifest's `usage` and any `rate_limit_waits`), the model understood the task (read transcript). Fix harness issues; the pilot is **not** a number of record.

### Stage 12 — 12-hour calibration attempt of record
1. Host idle, laptop hygiene checklist (§4.4) done, nothing else running. **Claude Max:** start at the beginning of a fresh usage window, with enough weekly allowance left (checked at G-PAID). Don't use Claude on the same account during the run (including chat and other Claude Code sessions) — they draw from the same usage limits.
2. Launch detached with the same command, `--budget-hours 12`, `--rate-limit-wait 24`, `--out .../opus55-a1`. Because this session would also use the Max allowance, the wait is a lightweight background shell loop on the run's pid, not an active Claude session.
3. **Usage-limit pauses (Max):** when the CLI reports a limit, the harness waits (polling, backing off up to 15 min) and relaunches Claude Code. Wait time goes to `rate_limit_wait_seconds`, never to the 12 h budget. Each relaunch is a fresh CLI session: Opus keeps its files, `NOTES.md` and checkpoints but loses its conversation context, like a manual resume. The number of relaunches is reported with the result. `--rate-limit-wait 24` caps the total waiting at 24 h, so the whole run can take up to 36 h of real time.
4. During the run: `run report`, `jq '.infrastructure_incidents, .measurement_valid, .rate_limit_wait_seconds'` periodically; never run sweeps/cache rebuilds alongside.
5. On end: read stop reason (`submitted` / `budget_wall` / `rate_limited` / `infrastructure`). Any incident that cost tools → run is kept, labelled, and repeated.
6. **Grade the final on the idle host against the hidden split** — the number of record. Then `grade-checkpoints --every 30 --dedupe` for the progression curve (last point must equal the final).
7. `resume` if interrupted; `cleanup --owner opus55-a1` after.

### Stage 13 — Analysis and reports (**gate G-REVIEW**)
1. **Scores:** overall, per category, per family, full-success flag, ProgramBench-style "families ≥ 95%" count, null-band comparison, lavapipe comparison. Single run → no CI claimed; say so.
2. **Cheating audit** (METR found ≥16% on long tasks): search transcript + final source for: probing for system ICDs/`/usr/lib`, reading `/proc` of the driver, attempts to reach network, hardcoded outputs/hash tables of public cases, writes outside `/task`, tampering with `vkreplay`, suspicious `grade_dev` gaming (checking only public-case patterns).
3. **Source-similarity audit** vs SwiftShader source (JPlag or equivalent winnowing tool, C++ mode): report as an indicator, not a penalty.
4. **Behaviour notes:** when it submitted, how it used the oracle, what it built first, final LOC, build time.
5. Write `VALIDATION.md` §8–10, regenerate `CONTROLS.md`/`PERF_VARIANCE.md`, export the site (`python -m evalbase.grader.export ...`).
6. Hand you the results.

### Stage 14 — v1 close-out
Verdict against the skill's calibration rule: **> 0.9 → too small, widen scope for v2; in the null band → too hard or harness bug, investigate; mid-range → right size.** Write the v2 candidate list (more models, k≥3 seeds, metamorphic cases, Reactor eval, publishing). CHANGELOG entry; this plan marked **closed (v1)**.

---

## 16. Change control and documentation policy (applies from now on)

1. **This plan is versioned.** `v1.0` now. Amendments inside v1's scope bump the minor number (`v1.1`, `v1.2` …) and edit this file in place, with the header's version/date updated. A change of scope (new eval variant, new oracle, different model family as primary) starts **v2** in a new file (`PLAN_v2.md`); v1 stays as history.
2. **Every change gets a CHANGELOG entry** in `docs/plan/CHANGELOG.md`: date, new version, what changed, why (the measurement or decision that drove it), which sections/documents were updated.
3. **Documents that must be updated in the same commit as a plan change:**
   - `docs/plan/PLAN_v1.md` (this file) and `CHANGELOG.md`
   - `swiftshader_vk/docs/DESIGN.md` decision log (dated paragraph) — once it exists
   - `reports/VALIDATION.md` if the change affects anything measured
   - the research report `reports/SwiftShader eval design.md` gets an **"Updates since publication"** section at the bottom (never rewritten silently) when a finding in it is overturned or a recommendation is changed
   - RUNBOOK/REPRODUCIBILITY if a command changes
4. **Metric changes** follow skill rule 9: new metric version, applied to every attempt identically by **regrade** (never rerun), old and new numbers side by side, and if done after seeing scores, said plainly.
5. **Status tables** at the end of every stage are appended to `swiftshader_vk/docs/internal/STATUS.md` (internal).

---

## 17. Risks and mitigations (v1)

| Risk | Mitigation in this plan |
|---|---|
| Opus memorised SwiftShader's source (contamination) | Hidden split stops memorised *outputs*; similarity audit + lavapipe number report memorised *implementation*; no SwiftShader source in sandbox |
| Task too big → score in null band | Family pyramid gives early credit (inst_dev, mem_buf, compute); pilot reveals it early |
| Task too small → saturation | Skill says > 0.9 means widen; v2 list ready |
| Oracle nondeterminism | Stage 3 measures; excluded/canonicalised op types; n1/n2 check per case |
| Distance mis-scaled (deterministic oracle → T=0.03 everywhere) | Controls set constants; round_trunc vs gain/half-pixel bracket the scale |
| Candidate cheats via in-process code | Parent/child driver split, parent-owned output and clock, fresh grading container |
| Candidate finds another Vulkan driver | Solver image contains none; isolation check |
| Perf timing noise on a hybrid laptop CPU | WSL vCPUs can't be pinned to P-cores (measured, v1.2). Idle host; AC power + "Best performance"; ≥2 s timed runs, median of repeats; reference and candidate timed in the same session; spread reported in PERF_VARIANCE.md |
| Laptop sleeps / updates mid-run | §4 hygiene; WSL `vmIdleTimeout=-1`; `resume` |
| Max usage limits pause the run (D9) | Harness waits and relaunches; `--rate-limit-wait 24`; waits recorded, never counted as model time or as a stop decision; start in a fresh window; no other use of the account during the run; relaunch count reported, since each one costs Opus its context |
| Claude Code silently bills an API key | `ANTHROPIC_API_KEY` unset on the host; checked in Stage 0 and at G-PAID |
| Weekly Max allowance runs out mid-run | Check remaining allowance at G-PAID; the pilot measures usage per hour; if the cap is hit, the run is interrupted and resumed later (labelled) |
| Hidden generators lost/leaked | Private repo + remote the same day; grep checks; public tree never contains them |
| evalBase assumptions don't fit Vulkan | Composite snapshot file; `sample_mult` → thread-count; changes logged in EVALBASE_CHANGES.md |
| Build too slow for the 900 s rebuild timeout | Verified in Stage 3; TASK.md states the build-time budget |

---

## 18. What I need from you now

Done: D1–D14 approved (2026-09-28); working copy in WSL; private repo
`SidS-123/private-repo-swiftshader` created and pushed. Each stage ends with a
commit and a status table, without a stop for approval unless something
serious comes up. Paid runs still wait for **G-PAID** (§14). Nothing is pushed
to the public repo's `origin` without your go-ahead.
