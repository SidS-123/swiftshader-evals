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

## Fidelity distance (metric `ssvk-1.0`, `scorer.py`)

A snapshot is an `.ssnap` file of items (image aspects, buffer ranges); the
format of each item comes from the trusted parent's plan. Per component:

| Data | error `e` | free |
|---|---|---|
| integer formats, stencil, integer buffers | 0 or `EXACT_MISS` (1e6) | 0 |
| UNORM / SNORM / sRGB codes, D16 depth | code difference (LSB) | 1 LSB |
| floats (f16/f32/f64, UFLOAT, D32 depth) | difference / ULP at max(\|ref\|, 1/16) | 2 ULP |

NaN only matches NaN, infinity only the same infinity (else `EXACT_MISS`).
`e_eff = max(0, e - free - allow)`, with `allow` a per-item allowance a case
may set (ULP or LSB). Per element: max over components (signed: mean). Blocks:
8x8 texels per layer/slice; 64 elements for buffers. Per block:

```
d_mag   = mean(min(e_eff, 16)) / 4
d_bias  = |mean(clip(signed e_eff, -4, 4))| / 2
d_cov   = fraction(e_eff > 1) / C          C = 1/4 (images), 1/64 (buffers)
d_block = min(1, max(d_mag, d_bias, d_cov));  0 if < 0.05
D       = sqrt(mean over items of mean over blocks of d_block^2)
```

Overrides: a missing item, a shape/format mismatch, or a colour item that is
uniform (99.9 % one value) where the reference's is not sets that item's
blocks to 1. The free 1 LSB / 2 ULP absorbs last-bit rounding (a truncating
conversion, lavapipe's rounding); the caps keep single wrong texels (edge
samples) from saturating a block; the RMS over blocks keeps a localised error
visible (one wrong block of 64: D = 0.125). Unit tests:
`tests/test_scorer.py` (17).

## Per-case threshold

```
noise       = mean over snapshots of D(ref_N, ref_2N)       (N: ThreadCount 4, 2N: ThreadCount 1)
sensitivity = max over the TOLERATED perturbations of mean over snapshots of D(ref, ref_perturbed)
T           = clamp(max(k_noise * noise, k_sens * sensitivity), T_lo, T_hi)
```

Tolerated perturbations: `vtxjitter` (vertex x, y +-2^-17), `texcoord_ulp`
(texture coordinates +-1 ULP). Run but not tolerated: `subzero` (the other
JIT backend), `lavapipe` (Mesa, the fairness comparator). `k_noise = 2.0`,
`k_sens = 2.0`, `T_lo = 0.03`, `T_hi = 0.30`. Measured on the pilot: noise 0
everywhere (deterministic oracle), jitter sensitivity <= 0.0135, so T = 0.03
for every case except those with order-dependent output (excluded).

## Snapshot score, case score, category

```
s_snapshot  = 1 / (1 + (D / T)^4)
s_case   = mean over snapshots of s_snapshot
fidelity = mean over families of (mean over that family's cases of s_case)
```

## Procedural cases

A procedural case scores the fraction of its checks that pass; every check is
derived from the reference's own ledger for that case.

| Check | What it compares |
|---|---|
| `exit_ok` | gate: a candidate that did not exit `ok` fails the case (the only check then) |
| `output_matches_reference` | every snapshot of the case within the case's T (evalBase generic) |
| `vkresult_stream` | the whole `calls` list (op index, function, result), vkreplay's own calls included; one check |
| `query:<name>:<key>` | per top-level key of a `query` op's value (properties, features, formats, extensions, memory, queue families, versions) |
| `query:<name>` | the whole value of any other recording op (enumerate, image format props, fence/event/semaphore status, read_queries) |

Ignored fields (they identify a build, not a behaviour): `pipelineCacheUUID`,
`deviceUUID`, `driverUUID`, `deviceLUID`, `deviceLUIDValid`, `deviceNodeMask`,
`conformanceVersion`, `driverInfo`. Timestamps reach the ledger only as
validity (non-zero, non-decreasing, availability).

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

**2026-09-30 — The model's reference material (Stage 5, amends D4).** `spec/`
holds the Vulkan spec as **AsciiDoc sources** at Vulkan-Docs `v1.4.357` (the
headers' version; plain text the model can grep, instead of the 40+ MB
single-page HTML D4 named), `vk.xml`, the **SPIR-V** and **GLSL.std.450**
specs (the published HTML, committed with its download URL and sha256 because
the registry has no pinned revision) with the SPIR-V grammar and headers at
the SDK tag, and the **Khronos Data Format Specification** at `1.4.0-gh`, the
normative decoding of BC, ETC2, ASTC and packed formats (a fresh-reader review
pointed out the task forbids recreating such code from memory while providing
no written source). `tools/make_spec_dir.sh` rebuilds it; `--check` compares
the committed manifest.

**2026-09-30 — `apiVersion` above 1.3 is not graded.** SwiftShader fails
`vkCreateInstance` with `VK_ERROR_INCOMPATIBLE_DRIVER` for `apiVersion` > 1.3,
which the Vulkan spec forbids for 1.1+ implementations. Grading it would reward
reproducing a conformance bug, so cases always request 1.3 (resolves the
2026-09-29 open question).

**2026-09-30 — Fresh-reader review of the task text (Stage 5).** A reviewer
that saw only what the model sees returned 42 findings. Fixed in the task
text: aggregation at every level, which checks each category contributes, the
performance curve and its relation to the case timeout, every command's
fields and defaults, `elem` values, the upload/readback mechanism, internal
calls being logged and compared, what the rebuild copies and the allowed
dynamic libraries, oracle limits, debugging with `vkreplay`, the validation
layer, combined depth/stencil readback, the wording of the from-scratch rule.
Added to `vkreplay`: buffer device addresses as data, per-item `allow`
allowances, loading a private copy of the candidate library, and each public
case's reference ledger in `dev/reference/`. Deferred to their stages: the
exact `D` formula in `SPEC.md` (Stage 6), render-pass ops (Stage 7, with
`mrt_renderpass`), the `errors_robust` scenario list (Stage 7), and a
"success everywhere" control that reports the profile and returns
`VK_SUCCESS` without doing work, to measure how much of the procedural
category is gameable (Stage 8). Accepted knowingly: the device name in the
profile identifies the reference as SwiftShader; the `oracle` tool would
reveal it anyway, and the plan's contamination measures (hidden split,
similarity audit, lavapipe fairness number) stand.

**2026-09-30 — Metric `ssvk-1.0` (Stage 6).** The distance above replaces the
toy's (which would have passed a 9.6/255 bias and every 1-LSB error). Design
choices, each driven by a synthetic test (`tests/test_scorer.py`) or the pilot:
errors in the format's own units with 1 LSB / 2 ULP free (last-bit rounding is
implementation latitude: lavapipe differs by exactly that almost everywhere);
magnitude and bias terms that cap each element's contribution, because the
first version let one flipped edge texel saturate its block (three stray
texels gave D = 0.22); the coverage term for sparse errors, saturating at a
quarter of an image block but at one element of a buffer run (buffers have no
edges; every element is a result); the RMS over blocks so one fully wrong
block of 64 fails (D = 0.125) where a mean would have passed it (0.016);
floats in ULPs of max(|ref|, 1/16) so near-zero results are not judged in
absurdly fine units. The constants are provisional until the Stage 8 controls
confirm them; any change is a new metric version.

**2026-09-30 — Subzero is not a tolerated perturbation (Stage 6).** Measured
with `ssvk-1.0` on the pilot (`tools/perturb_distance.py`): `subzero` moves
only float compute (`p_c_float_transc` D = 0.35, `p_c_convert_round` 0.25,
`p_c_matrix` 1.0), which would put those cases' T at the 0.30 cap; and a
candidate with the same kind of differences would still score about 0,
because when every element differs, D saturates whatever T is. Tolerance for
spec-bounded float results is therefore per element: generators set `allow`
on those snapshot items with the bound from the Vulkan precision appendix.
Tolerated: `vtxjitter` (D <= 0.0135 on the pilot) and `texcoord_ulp` (0 on the
pilot at this metric). With noise 0, T = 0.03 for 47 of 51 pilot cases (the
others are the float cases above, and the excluded atomic-order case).

**2026-09-30 — `allow` policy for generators (Stage 6, applied in Stage 7).**
`allow` is set only where the Vulkan specification bounds a result instead of
defining it, with that bound: transcendental and division results in compute
(the "Precision of Individual Operations" table; composites such as a matrix
inverse get the bound of their worst operation times the operation count, or
are not snapshotted raw). Depth from interpolation and filtered sampling (LOD,
filter weights) are the other places the spec leaves latitude, and where
lavapipe differs from the reference (525 ULP of D32 depth; 4-5 LSB in
trilinear sampling); whether to allow for them is decided in Stage 8 from the
lavapipe run and the controls, not assumed here. First lavapipe number at
`ssvk-1.0` with no allowances: mean snapshot score 0.773 over 51 pilot cases.
