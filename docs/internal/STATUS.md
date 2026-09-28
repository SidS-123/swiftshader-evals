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
