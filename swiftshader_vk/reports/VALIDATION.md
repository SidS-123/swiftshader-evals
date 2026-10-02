# swiftshader-vk — validation report

The evidence that the eval measures what it claims: the host, the corpus, the
tolerance, the controls, the metric, performance timing, the harness, and the
calibration attempts. Every number names the run directory or script it comes
from. Metric of record: `ssvk-1.0`.

> **Draft.** Sections 1–7 are complete. Sections 8–10 are filled from the
> calibration attempts.

## 1. Host

| Item | Value |
|---|---|
| CPU | Intel Core Ultra 7 356H, 16 logical CPUs (hybrid P/E cores) |
| Memory | 31.4 GB (WSL2 VM: 23 GiB + 8 GiB swap, 16 vCPUs) |
| OS | Windows 11 Home; WSL2 Ubuntu 26.04 LTS, kernel 6.18.33.2-microsoft-standard-WSL2 |
| Docker | Engine 29.7.2, native in WSL Ubuntu |
| CPU pinning | none: inside WSL the 16 vCPUs time identically (pinned single-thread benchmark 0.45–0.51 s on every vCPU), so P-cores cannot be selected; reference and candidate share the same unpinned 4-CPU allocation |
| Oracle image `ssvk-ref:1` | `sha256:82a05e1f0dc9b9169850b90a12689d5b128850f7bf82cd8cfe465265b52a11fd` |
| Candidate image `ssvk-cand:1` | `sha256:a1bf8d9d5b96e19d528e0a2968dd023a0405bc45c862cfc23b3e1ebf27852165` |
| Solver image `ssvk-solver:1` | `sha256:eff445901766fe1307257cb9db8c04e7645062153c3b32a6d7cc2a6d74f256a6` |
| Pins | `images/pins.lock`: base digest, apt snapshot `20260928T000000Z`, SwiftShader `1e80438d` (LLVM 10 backend), Vulkan SDK `1.4.357.0`, Mesa `26.2.3` |

The oracle passes SwiftShader's own unit tests in the image (Reactor 156,
system 25, math 11, vk 139; both LLVM 10 and Subzero builds).

Timed runs need the laptop on AC with sleep disabled: Windows Modern Standby
freezes the WSL VM, which pauses runs and inflates wall times. Fidelity and
procedural scores are unaffected; performance timings are. Every timed run's
power source is logged and checked against the Windows Kernel-Power log.

## 2. Corpus

| Split | Cases | Replay | Procedural | Performance | Families |
|---|---|---|---|---|---|
| public | 91 | 75 | 12 | 4 | 21 |
| hidden | 187 | 161 | 22 | 4 | 21 |

Families and their CTS weighting are in `docs/REQUIREMENTS.md`. Hidden cases
come from private parameter tables over the public builders; every family has
hidden cases, hidden seeds are new and unique, and no hidden name or seed
appears in the public tree (`tests/test_hidden_leak.py`).

Every case passes a gate before it enters a split (`tools/gate.sh`): the
Vulkan validation layer reports no spec violation; the oracle exits ok;
repeats and ThreadCount 1 vs 4 are byte-identical; no op is skipped; and a
replay with poisoned allocations is identical (so no case reads memory it
never wrote). Public 91/91, hidden 187/187.

Generation is deterministic: 776/776 files identical across two generations
(numpy default vs AVX features disabled; `python -m
evalbase.corpus.determinism --split both`).

Surface narrowed by the oracle itself: buffer device addresses only through
push constants (pointer chasing crashes SwiftShader), no image atomics
(nondeterministic), robustness through core features only.

## 3. Reference thresholds

Tolerance `T` per case from the reference cache (`tools/refcache_summary.py`):

| Split | min | median | max | at `t_lo` (0.03) | at `t_hi` (0.30) |
|---|---|---|---|---|---|
| public | 0.030 | 0.030 | 0.300 | 89 | 2 |
| hidden | 0.030 | 0.030 | 0.300 | 180 | 4 |

The `t_hi` cases are raster and occlusion-query scenes where the tolerated
vertex jitter flips edge pixels or occlusion counts. Tolerated perturbations:
`vtxjitter` and `texcoord_ulp` only (the Subzero backend was measured and
left out of the set). The perturbed reference scores vtxjitter 0.995 / 0.989,
texcoord_ulp 1.000, second run 1.000 (public / hidden), and no case reaches
0.7 at `t_hi` (`python -m evalbase.grader.perturbed`).

## 4. Controls

Nineteen controls on both splits, predictions committed before any control
ran. Full generated table and analysis: `docs/CONTROLS.md`.

| Property | Public | Hidden |
|---|---|---|
| reference as candidate | 1.000, full success | 1.000, full success |
| null band (`stub`) | 0.013 | 0.007 |
| `hardcode_public` (memorises the public outputs) | 1.000 | 0.124 |
| `lavapipe` (another conformant driver; fairness comparator) | 0.741 | 0.738 |
| `reference_subzero` (same source, other JIT backend) | 0.991 | 0.992 |
| `one_thread` | 1.000, full success | 1.000, full success |

32 of 38 control-split results landed inside their predicted bands. The six
misses (`crash` procedural, `lavapipe` procedural, `nearest_filter`
performance, each on both splits) were predictor errors; each is restated
with its reason in `docs/DESIGN.md` "Controls". Alteration controls lose
score only in the families they touch; `round_trunc` (1-LSB rounding) is
free by design; `crash` and `malformed` produce no grader error.

The controls found three corpus defects before any model ran (four
degenerate advanced-blend cases, one case reading unwritten memory, compute
image sequences blind to a stale output); all are fixed and two permanent
gates added (section 9).

## 5. Metric history

### ssvk-1.0 (current)

Format-aware distance with a free 1 LSB / 2 ULP, capped magnitude and bias
terms, coverage weighting (images 1/4, buffers 1/64), RMS over blocks;
procedural checks per query key plus the `VkResult` stream; performance
scored against the reference's timed runs. Weights, constants and the
full-success bar are in `task/SPEC.md` and `docs/DESIGN.md`.

No revision so far. The controls left the metric unchanged; it was fixed
before any model attempt.

## 6. Performance timing

| Item | Value |
|---|---|
| Reference timed runs, public | 2.57–3.50 s (`perf_compute`, `perf_fill`, `perf_geometry`, `perf_texture`) |
| Reference timed runs, hidden | 2.63–3.39 s |
| Reference vs itself across the control sweeps | perf ratio 0.82–1.05 |
| `one_thread` (ThreadCount 1) | ≤ 3.6× slower |
| `reference_subzero` | ≤ 4.7× slower |
| Scaling (D5) | `perf_half` 16×, `perf_gate` 8×, weight 0.10 |

Both slower reference configurations sit well inside the 8× gate and far
below the 16× half-point, so the scaling was kept as designed. The
five-repeat variance study (`docs/PERF_VARIANCE.md`) is listed in section 10.

## 7. Harness

| Check | Result | Evidence |
|---|---|---|
| Sandbox image | `ssvk-solver:1`: gcc/g++ 13, cmake, ninja, gdb, valgrind, Vulkan headers and loader, validation layer, `spirv-dis`/`spirv-as`, vkreplay. No ICD, no LLVM (and so no clang), no SPIRV-Tools or glslang library, no network | `images/ref.Dockerfile` target `solver` |
| Isolation | passes on `ssvk-solver:1` and `ssvk-cand:1`; fails on `ssvk-ref:1` and on contaminated copies (SwiftShader renamed + an ICD manifest); runs inside every smoke | `tools/isolation_check.sh`, `tools/isolation_selftest.sh` |
| Candidate replay image | every candidate replay (grading, `grade_dev`, controls) runs in `ssvk-cand:1` (loader + vkreplay only); without it a candidate could `dlopen` the reference driver and forward to it. Driver-wrapping controls get the real driver as read-only mounts only | `instance.py`, `controls/*/control.json` |
| No-key smokes | claude-code and openrouter: `NO-KEY SMOKE PASSED` (incomplete candidate 0.0069) | `runs/smoke-*` |
| Tool boundary on the wire | the main request carries exactly `mcp__ssvk__{checkpoint,driver,grade_dev,oracle,shell}`, no built-in tool; `effort: high`, adaptive thinking. A tool-less session-title side request precedes it | `tools/wire_check.sh` → `runs/wire/request.json` |
| Credentials | no key or token variable in the sandbox; the only mount is the run's own workspace at `/task`; no credential-shaped string in run directories | `tools/credential_check.sh` |
| Container ownership | every container carries an owner label; every automatic sweep is scoped to one owner | evalBase `containers` |
| Billing route | Claude Code on a Claude Max login (`authMethod: claude.ai`), no `ANTHROPIC_*` variable; usage-limit waits are recorded and never counted as model time | `tools/preflight_host.sh`, attempt manifest |

## 8. Calibration attempts

*Filled from the attempt manifests and the idle final grades.*

## 9. Defects found and fixed during validation

- Advanced-blend cases that never created their device on the reference (1 public, 3 hidden): fixed; a skipped op now fails a case (`660e4c8`).
- A case that snapshotted memory it never wrote: fixed; poisoned-allocation replay of every case (`a1a8ee9`).
- Compute-image sequences that could not see a stale output: fixed (`a1a8ee9`).
- Candidates replayed in the oracle image, next to the reference drivers: moved to `ssvk-cand:1` (`577396a`).
- clang in the solver image (links libLLVM, an embeddable JIT): removed (`577396a`).
- Wire recorder stopped at the CLI's session-title side request and never saw the main request: it now records side requests separately and continues.

## 10. Open items

*Completed after the calibration attempts.* Known before them: the
five-repeat performance-variance study; a single host and ISA (x86-64).
