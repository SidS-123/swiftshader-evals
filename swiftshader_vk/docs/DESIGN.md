# Grading design

This document owns the scoring formulas and the control predictions;
`CASE_FORMAT.md` owns the case file format. The decision log at the end
records the decisions that shaped the metric, dated, with the evidence for
each.

## Trusted boundary

- The candidate owns `/task` and produces `build/libvk_candidate.so`, a
  Vulkan ICD.
- The trusted driver, `vkreplay`, is the only program that calls the
  candidate. The candidate is loaded into an untrusted child process (uid
  2000, no write access to the output directory); the trusted parent keeps the
  clock, checks every message against its own plan of the case, and is the
  only writer of the ledger and snapshot files. The grader (a separate process,
  separate container) scores those. Details: `REPLAY_FORMAT.md`.
- Per-case limits: wall time (10x the reference's recorded time, minimum
  60 s), memory, process count, no network. A crash or timeout ends the case;
  snapshots not written score 0.

## Fidelity distance

<How a candidate output is compared with the reference output of the same
snapshot, as a defect D in [0, 1]: preprocessing, the structural term, the
absolute term (so a uniform bias is visible), the floor (so noise at the
reference's own level costs nothing), the degenerate-output override. State
every constant. An image metric, a text diff, an array norm and a spectral
distance all fit this shape.>

## Per-case threshold

```
noise       = mean over snapshots of D(ref_N, ref_2N)
sensitivity = max over the TOLERATED perturbations of mean over snapshots of D(ref, ref_perturbed)
T           = clamp(max(k_noise * noise, k_sens * sensitivity), T_lo, T_hi)
```

Tolerated perturbations: <list>. Run but not tolerated: <list>.
Defaults: `k_noise = 2.0`, `k_sens = 2.0`, `T_lo = 0.03`, `T_hi = 0.30`.

## Snapshot score, case score, category

```
s_snapshot  = 1 / (1 + (D / T)^4)
s_case   = mean over snapshots of s_snapshot
fidelity = mean over families of (mean over that family's cases of s_case)
```

## Procedural cases

<The checks, one row each: name, subsystem, what it compares. Every check is
derived from the reference's own ledger, never from an expectation written
into a generator. A clean exit is a gate for the whole case.>

## Performance

`ratio = t_candidate / t_reference` on the same host; `score = 1 / (1 +
(ratio / 16)^4)`; a case whose fidelity score is below 0.5 scores 0. Every
timed reference run takes at least 2 s on the grading host.

## Overall and full success

```
overall = 0.60 * fidelity + 0.30 * procedural + 0.10 * performance
```

Full success: every hidden fidelity case >= 0.9, procedural >= 0.95, every
performance ratio <= 8. Weights are renormalised over the categories present
in a partial run.

## Controls (predicted, then observed)

| Control | Predicted | Why | Observed |
|---|---|---|---|
| reference as candidate | exactly 1.0, both splits | the grader must accept what defined it | |
| stub | the null band | a fraction of checks is satisfied by doing nothing; know the floor | |
| <tolerated perturbation> | category >= 0.9; per case >= 0.7 outside the T_hi cap | the tolerance must accept what it is built from | |
| <untolerated perturbation> | penalised: category band | the cost of the defect is documented, not demanded to be small | |
| <bias x2> | fidelity <= 0.2 | a uniform gain must be visible | |
| stale output | fidelity ~1/snapshots on sequences | sequences catch a candidate one step late | |
| hardcoded public | ~1.0 public, null band hidden | the split defeats memorisation | |
| malformed / crash | snapshots after the crash 0; no grader exception | hostile output must not take the grader down | |

## Known limitations

<What the metric does not discriminate, and why it is accepted.>

## Decision log

Entries D1–D14 were approved as a set on 2026-09-28 (D9 on 2026-09-26, D10
carried out in Stage 0). The full rationale is in `docs/plan/PLAN_v1.md` §1.

**2026-09-28 — D1 Scope.** Variant A: a full drop-in Vulkan 1.3 ICD,
headless (no swapchain/WSI). The narrower variants become case families. It
is the only variant big enough not to saturate.

**2026-09-28 — D2 Oracle pin.** SwiftShader `1e80438d2b93` (2026-09-16),
CMake build, LLVM 10 backend, x86-64, fixed thread count. Subzero is built
too, as a perturbation and a control.

**2026-09-28 — D3 Banned dependencies.** No LLVM, no linkable SPIRV-Tools, no
other Vulkan driver and no Mesa in the model's sandbox. SPIR-V handling is
part of the task.

**2026-09-28 — D4 What the model may read.** Vulkan headers, `vk.xml`, the
pinned Vulkan spec (HTML) and the frozen device profile. No SwiftShader
source, no internet.

**2026-09-28 — D5 Performance scaling.** Keep `perf_half=16`, `perf_gate=8`
and weight 0.10 until the controls are measured (Stage 8), then decide once
in this log before any model run.

**2026-09-28 — D6 Budget per attempt.** 12 h wall clock, submit stop policy,
4 CPUs / 8 GB sandbox.

**2026-09-28 — D7 Model settings.** `claude-opus-5-5`, reasoning effort high,
one calibration attempt (optional repeat).

**2026-09-28 — D8 Pilot.** One 1-hour Opus pilot before the 12-hour run.

**2026-09-26 — D9 Billing route.** Claude Max subscription via `claude auth
login`; `ANTHROPIC_API_KEY` unset on the host. Usage-limit pauses are waited
out (`--rate-limit-wait 24`), recorded, and never counted as model time.

**2026-09-28 — D10 Where things run.** WSL2 Ubuntu, working copy on the WSL
ext4 disk (`~/swiftshader-evals`). Done in Stage 0.

**2026-09-28 — D11 Hidden generator tree.** A separate private repository at
`../swiftshader-evals-hidden`, pushed to a private GitHub remote.
`CorpusSpec.forbidden_hidden_roots` is the public repo root, so the loader
refuses a hidden tree inside it.

**2026-09-28 — D12 Fairness comparator.** Mesa lavapipe runs on every case and
is reported, and is never used for tolerance.

**2026-09-28 — D13 Shader authoring.** Generators write GLSL. A pinned
`glslangValidator` in the reference image compiles it to SPIR-V, and every
module passes `spirv-val`.

**2026-09-28 — D14 Metamorphic hidden cases.** Deferred to v2 unless Stage 3
shows that `spirv-fuzz` still ships in the pinned SPIRV-Tools.

**2026-09-29 — Oracle thread count and API version.** The oracle of record
runs with `SwiftShader.ini` `ThreadCount=4` (the default, 0, means all host
cores, which would make the oracle host-dependent); the `threads` perturbation
uses 1. SwiftShader fails `vkCreateInstance` with `VK_ERROR_INCOMPATIBLE_DRIVER`
when `apiVersion` > 1.3 (`libVulkan.cpp`), so every case requests 1.3; whether
that error is itself graded is decided with `errors_robust` in Stage 7.

**2026-09-29 — D14 confirmed deferred.** Stage 3 found `spirv-fuzz` still
ships in the pinned SPIRV-Tools (behind `SPIRV_BUILD_FUZZER`, needs
protobuf), which met D14's condition for inclusion. Metamorphic hidden cases
stay in v2 anyway: the extra dependency, the validity and determinism gates
every mutant would need, and the variety the 21 families already give.

**2026-09-29 — Replay driver design (Stage 4).** `vkreplay` executes an
Amber-like JSON op list through the real Vulkan loader. To keep cases short it
adds transfer usage to every resource, allocates from the first
host-visible-coherent memory type, keeps every image in `GENERAL`, records a
full barrier before each command outside rendering, and renders with dynamic
rendering. Errors are observed, not asserted: every `VkResult` is logged and a
failed creation skips dependent ops. The candidate runs in an untrusted child
process; the trusted parent owns the outputs and the clock (tested with a
hostile ICD, `tests/test_driver.py`). Render passes and framebuffers
(`mrt_renderpass` subpasses, input attachments) are not in the op vocabulary
yet; Stage 7 adds them with that family.

**2026-09-29 — Oracle determinism measured (Stage 4).** On the 56-case pilot
the oracle is byte-identical across repeats and across ThreadCount 4/1 for
every op type except atomic return values, which are therefore never
snapshotted. The n2 (ThreadCount=1) run is a determinism check with noise 0.
LLVM vs Subzero differ only in float compute (up to 4 ULP in transcendentals,
thousands after a matrix inverse) and 1 ULP of interpolated depth; `vtxjitter`
flips 1-3 edge texels; `texcoord_ulp` moves filtered texels by at most 1 LSB;
lavapipe differs by 1 LSB of rounding almost everywhere colour is computed.
Numbers: `docs/internal/determinism.md`. Which perturbations are tolerated is
decided in Stage 6 with these measurements and confirmed by the controls in
Stage 8.

**2026-09-29 — Scope corrections from the profile (Stage 4).** The oracle
supports no `logicOp`, dual-source blend, pipeline statistics queries,
8/16/64-bit shader types or storage, or `D24` depth formats. The families that
listed them (`blend`, `compute_types`, `queries_sync`, `depth_stencil`) no
longer do (`REQUIREMENTS.md`); requesting them is an `errors_robust` case
(`VK_ERROR_FEATURE_NOT_PRESENT`).
