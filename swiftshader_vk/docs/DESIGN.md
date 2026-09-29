# Grading design

This document owns the scoring formulas and the control predictions;
`CASE_FORMAT.md` owns the case file format. The decision log at the end
records the decisions that shaped the metric, dated, with the evidence for
each.

## Trusted boundary

- The candidate owns `/task` and produces `<artifact>`.
- The trusted driver is the only program that calls the candidate. It never
  reads candidate stdout; it writes output files and a ledger, and the
  grader (a separate process, separate container) scores those.
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
