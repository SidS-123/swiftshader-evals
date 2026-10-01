# Stage 7 — corpus generation findings (2026-10-01)

Generated with `ssvk-ref:1` (`sha256:82a05e1f0dc9…`, vkreplay with render-pass
objects) and the pinned glslang. PLAN_v1.md §11 sets the rules; this file
records what generation measured and what it changed.

## 7.1 Corpus

21 families (the four `perf_*` families come from one module, `perf.py`).
Public cases are in `corpus/public/` (committed); hidden cases are generated
from the private tree (`SSVK_HIDDEN_GEN`, `SidS-123/private-repo-swiftshader`)
into `corpus/hidden/` (git-ignored).

| # | Family | Category | Public (plan) | Hidden (plan) |
|---|---|---|---|---|
| 1 | `inst_dev` | procedural | 4 (4) | 8 (8) |
| 2 | `mem_buf` | replay + procedural | 4 (4) | 8 (8) |
| 3 | `compute_arith` | replay | 6 (6) | 12 (12) |
| 4 | `compute_cf` | replay | 4 (4) | 10 (10) |
| 5 | `compute_types` | replay | 4 (4) | 10 (10) |
| 6 | `compute_subgroup` | replay | 3 (3) | 6 (6) |
| 7 | `compute_atomics` | replay | 2 (2) | 5 (5) |
| 8 | `compute_image` | replay | 3 (3) | 6 (6) |
| 9 | `raster_tri` | replay | 6 (6) | 14 (14) |
| 10 | `raster_lines_points` | replay | 3 (3) | 6 (6) |
| 11 | `vertex_input` | replay | 4 (4) | 8 (8) |
| 12 | `blend` | replay | 4 (4) | 10 (10) |
| 13 | `depth_stencil` | replay | 4 (4) | 10 (10) |
| 14 | `tex_sample` | replay | 10 (6) | 16 (16) |
| 15 | `formats_copy_blit` | replay | 7 (6) | 16 (16) |
| 16 | `msaa` | replay | 4 (3) | 8 (8) |
| 17 | `mrt_renderpass` | replay | 3 (4) | 8 (8) |
| 18 | `descriptors_push` | replay | 4 (3) | 8 (8) |
| 19 | `queries_sync` | replay + procedural | 4 (4) | 8 (8) |
| 20 | `errors_robust` | procedural | 4 (3) | 6 (6) |
| 21 | `perf_*` | performance | 4 (4) | 4 (4) |
| | **Total** | | **91 (~84)** | **187 (~188)** |

Public counts differ from the plan where one variant per sub-feature was
clearer than packing several into one case (`tex_sample`: one case per
sampler feature; `errors_robust`: the failing device is its own case because
a case has one device). By category, public: replay 75, procedural 12,
performance 4; hidden: replay 161, procedural 22, performance 4.

Hidden cases reuse the public builders with private parameter tables:
different seeds (none shared, checked), and different values or compositions
(formats, op mixes, sizes, counts, modes). Variants whose only parameter is
the seed (`tex_sample` cube/3D/gather, `formats_copy_blit` copies/blits/
clears/compressed, `descriptors_push` multi_set) draw their content from the
seed. `tests/test_hidden_leak.py` checks all of this, and that no hidden seed or
hidden case name appears anywhere in the public tree.

## 7.2 Gates

| Gate | Result | Evidence |
|---|---|---|
| Validity (validation layer, 0 messages citing the spec) | public 91/91, hidden 187/187 | `tools/gate.sh public`, `tools/gate.sh hidden` (`runs/gate-*/determinism.json`) |
| Exit `ok`, no skipped op (except where the case means it) | all | same |
| Oracle repeat and ThreadCount 1 vs 4, byte-identical | all | same |
| Generation determinism (two generations, different numpy CPU dispatch) | 776/776 files identical (91 + 187 cases, 498 assets) | `python -m evalbase.corpus.determinism --split both` |
| Leak check | 4 tests pass | `pytest swiftshader_vk/tests/test_hidden_leak.py` |
| Timing (non-perf reference sub-second) | max 0.63 s public, 0.54 s hidden | refcache `reference_wall_seconds` (7.5) |
| Perf timed runs ≥ 2 s | see 7.4 | gate ledgers, refcache |

~~The only skip in the corpus is intended~~ **Erratum (2026-10-01, found in
Stage 8):** this was wrong. The gate printed skipped ops but did not fail a
case on them, and four cases skipped everything on the reference:
`blend_pub_advanced` and three hidden advanced-blend cases requested
`advancedBlendCoherentOperations`, which the reference does not support, so
the device was never created and every snapshot was empty on both sides (any
candidate, even one that crashes, "matched" them). The `crash` control
exposed it. Fixed: the advanced-blend passes no longer request the feature and
draw non-overlapping triangles (overlap within a draw is undefined without
it); `tools/gate.sh` now fails any case with a skipped op unless its meta says
`expect_skips` (only `errors_robust` `bad_device`, which asks for an
unsupported feature on purpose and grades the failure). Both splits were
regenerated and re-gated under the new rule, and the stale cache entries
rebuilt (`docs/internal/stage8_findings.md`).

## 7.3 Findings that changed the surface

1. **Buffer device address: pointers loaded from memory crash the oracle.** A
   `buffer_reference` block that holds a pointer of its own type (a linked
   list, `Node { Node next; uint value; }`), walked by loading `next` from
   memory and dereferencing it, kills the oracle with SIGSEGV. Reading through
   a pointer that comes from push constants works. Bisected with
   `runs/bisect_bda.py` (scratch, git-ignored): the crash needs a pointer
   loaded from memory and dereferenced; shared memory and push-constant
   pointers alone are fine. `compute_types` keeps BDA through
   push-constant pointers only (`shared_bda`).
2. **Image atomics are nondeterministic on the oracle.** Commutative image
   atomic totals differed between repeated runs of the same case, which buffer
   and shared-memory atomics do not. Image atomics were removed from
   `compute_atomics`.
3. **No `VK_EXT_robustness2` on the oracle.** `errors_robust` grades the core
   features instead, measured: with `robustBufferAccess`, storage-, uniform-
   and texel-buffer reads past the bound range return 0 and storage writes past
   it are dropped (neighbouring words of the same buffer untouched); with
   `robustImageAccess`, `imageLoad` and `texelFetch` outside the image return
   (0, 0, 0, 0) and `imageStore` outside it is dropped.
4. **Error results the corpus grades** (all from valid calls):
   `VK_ERROR_FORMAT_NOT_SUPPORTED` from image format queries (BC with storage,
   linear colour attachments, 3-component 32-bit sampled, `E5B9G9R9` attachment);
   `VK_INCOMPLETE` from short two-call enumerations; `VK_ERROR_OUT_OF_DEVICE_MEMORY`
   from an image above `maxMemoryAllocationSize` (1 GiB) and within the 2 GiB
   heap (above the heap is a spec violation, so the gate refuses it);
   `VK_ERROR_FEATURE_NOT_PRESENT` from `geometryShader`, `shaderInt64`,
   `shaderFloat64`; `VK_TIMEOUT`, `VK_NOT_READY`, `VK_EVENT_SET/RESET` from
   zero-timeout waits and status queries (`queries_sync`).
5. **Loader-made results are not graded.** Enumerating instance layers reports
   what the loader finds (the validation layer during the gate, nothing during
   grading), so no case enumerates layers. `VK_ERROR_EXTENSION_NOT_PRESENT`
   for device extensions is produced by the loader before the driver sees the
   call, so it is not a driver behaviour either; no case relies on it.
6. **The oracle does not expose `VK_KHR_maintenance5`** (Vulkan 1.3 device);
   `VK_KHR_swapchain` and `VK_KHR_swapchain_mutable_format` need instance or
   device extensions the headless cases do not enable, so cases do not enable
   them.

## 7.4 Performance calibration

Iteration counts are fixed in the case tables. They were first set from the
gate (4 jobs in parallel), then raised where the reference cache, built on an
otherwise idle host, measured the timed run close to 2 s (fill and geometry:
2.05 and 2.07 s). Reference timed run as recorded in the cache (LLVM backend,
ThreadCount 4, `reference_run_seconds`):

| Case | Work per iteration | Iterations | Timed run (s) |
|---|---|---|---|
| `perf_fill_pub_a` | 512² RGBA8, 160 blended triangles, depth test | 650 | 3.11 |
| `perf_compute_pub_a` | 65,536 invocations x 256-step hash loop | 550 | 2.64 |
| `perf_geometry_pub_a` | 20,000 indexed triangles x 2 instances | 720 | 2.99 |
| `perf_texture_pub_a` | 512² quad, 8 trilinear anisotropic taps | 300 | 2.57 |

The four hidden perf cases (other seeds and work sizes) measured 2.64-3.03 s.

In the gate, the Subzero backend was 1.1-4.8x slower and ThreadCount 1
1.5-3.4x slower on these cases (recorded only; perf is graded against the
LLVM reference). Under parallel load the same runs took 15-45 % longer, which
is why the reference is timed alone (the refcache is built with nothing else
running) and why the margin above 2 s is kept.

## 7.5 Reference caches

Public `runs/refcache/` (91 entries), hidden `runs/refcache-hidden/` (187),
metric `ssvk-1.0`, built in 31 min for both, then the stale entries rebuilt
after the `compute_cf` and perf changes. Summary: `tools/refcache_summary.py`.

**Tolerance distribution.** T is 0.03 (`t_lo`) for 89/91 public and 181/187
hidden cases: the oracle is deterministic and the two tolerated
perturbations move nothing in most cases. At `t_hi` (0.30): public 2
(`raster_tri` 1, `queries_sync` 1), hidden 4 (`raster_tri` 3, `queries_sync`
1); in between, hidden `blend` 1 (0.144) and `formats_copy_blit` 1 (0.088).
These are the cases where a 2^-17 vertex nudge flips edge pixels (raster) or
changes an occlusion count (`queries_sync` occlusion, whose result buffer is a
snapshot): the tolerance follows the definition (2x the movement, clamped).
Whether `t_hi` cases are too loose is a Stage 8 question (controls).

Mean recorded perturbation defect per family (public / hidden), non-zero ones:

| Family | lavapipe | subzero | vtxjitter |
|---|---|---|---|
| `compute_subgroup` | 0.939 / 0.952 | 0 | 0 |
| `formats_copy_blit` | 0.548 / 0.477 | 0.080 / 0.064 | 0 / 0.003 |
| `perf_geometry` | 0.705 / 0.705 | 0.003 | 0 |
| `perf_texture` | 0.528 / 0.690 | 0 | 0 |
| `msaa` | 0.180 / 0.185 | 0 | 0.004 |
| `mrt_renderpass` | 0.120 / 0.136 | 0 | 0 |
| `raster_tri` | 0.078 / 0.126 | 0.014 / 0.027 | 0.056 / 0.084 |
| `depth_stencil` | 0.060 / 0.051 | 0 | 0.002 |
| `blend` | 0.024 / 0.016 | 0 | 0.002 / 0.009 |
| `queries_sync` | 0 | 0 | 0.084 / 0.042 |
| `compute_arith` | 0 / 0.026 | 0 | 0 |

lavapipe's subgroup size is 8 (the cases assume 4, Stage 3), so its
`compute_subgroup` distance is expected. `texcoord_ulp` moved nothing anywhere.

**Timing gate.** Every non-performance case runs under a second on the
reference: max 0.63 s public, 0.54 s hidden, median 0.36 s (mostly container
start-up). Before the `compute_cf` cap, three public `compute_cf` cases took
2.4-6.4 s, all of it LLVM compile time (one workgroup took as long as the
full dispatch; the 46 KB SPIR-V came from nested blocks and inlined helper
calls).

**One infrastructure stall.** In the first hidden build,
one `compute_atomics` case recorded a 600 s reference wall time although every
run's ledger was `ok`; six fresh runs took 0.32-0.35 s and the forced rebuild
0.3 s. The time was spent outside the case (container start or teardown under
WSL). Reference wall times above a few seconds in a cache are therefore
re-measured before they are trusted; `refcache_summary.py` lists them.

## 7.6 Generator problems the gates caught

- glslang has no `umulExtendedHi`: helpers use `umulExtended` /
  `imulExtended` / `uaddCarry` / `usubBorrow` out-parameters.
- `textureGather` needs a constant component: the shader selects among
  constant-component calls.
- `LocalSizeId` (workgroup size from a specialization constant) needs
  `maintenance4`; `texelFetch` on a `texture2D` needs
  `GL_EXT_samplerless_texture_functions`.
- Validation: `polygonMode` point needs `gl_PointSize` written;
  `independentBlend` must be enabled for differing attachment blends;
  `vkCmdWaitEvents` `srcStageMask` must be exactly the stages the events were
  set with (`HOST` for host-set events); storage-buffer and texel-buffer
  offsets must be multiples of 256.
- `"image2D".startswith("i")` classified a float image as an integer one
  (explicit sampled/destination kinds now).
- **Caught only by the hidden split:** the compressed-format block size table
  matched `ETC2_R8G8B8A8_*` (16-byte blocks) with the 8-byte `ETC2_R8G8B8`
  prefix; the public seed never drew it. Prefixes now end at `_`. Hidden
  generation is gated in full for this reason.
- `compute_cf` programs were unbounded in size (nested blocks, every helper
  call inlined): capped at `budget` statements and `calls` call sites (7.5).
- `common.py` resolved `images/pins.lock` relative to its own file, which
  breaks when evalBase's determinism check runs a copy of `gen/` from a
  temporary directory; it now follows `EVALBASE_INSTANCE`.
