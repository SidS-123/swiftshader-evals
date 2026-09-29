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
