# Oracle determinism and perturbations (Stage 4, closes Stage 3 §3.4)

Measured 2026-09-29 on `ssvk-ref:1` = `sha256:10f433b809bf…` (SwiftShader
`1e80438d`, vkreplay 1.0.0), host: Intel Core Ultra 7 356H under WSL2, 16 vCPUs.
Corpus: the 56-case pilot (`tools/pilot_corpus.py`, one or more cases per op
type, not part of the graded corpus). Tool: `tools/determinism.py --jobs 4
--perturb vtxjitter texcoord_ulp lavapipe`; raw results in
`runs/pilot/determinism/determinism.json` (git-ignored). Every case ran:
3x base (LLVM, ThreadCount=4), 2x ThreadCount=1, 1x Subzero, 1x validation
layer, 1x each perturbation. Differences are in the format's own units (LSB
for integer and normalized formats, ULP for floats) and the number of
differing texels/elements.

## Findings

1. **The oracle is deterministic.** All 56 cases exit `ok`, and 55 of 56 are
   byte-identical across three repeats and across ThreadCount 4 vs 1
   (snapshots, query values and the VkResult stream). The one exception is
   `p_c_atomic_order`, which stores each invocation's `atomicAdd` return value:
   the order in which invocations reach an atomic is not defined, and it
   differs run to run. Commutative atomic results (`p_c_atomic_sum`: sums,
   max, or) are identical.
2. **Validity gate: 0 spec violations** on all 56 cases (after fixing the four
   case bugs and the two driver bugs the first sweep found, listed below).
3. **LLVM vs Subzero** (same SwiftShader commit, other JIT backend) differ only
   in float arithmetic: transcendentals up to 4 ULP (`p_c_float_transc`), a
   matrix inverse up to 5664 ULP (`p_c_matrix`, error amplified by the
   inverse), one packed value derived from `sin` (`p_c_convert_round`), and
   interpolated depth by 1 ULP on 48 pixels (`p_g_tri_basic`). Every integer,
   raster-coverage, blend, texture and format result is identical. The device
   name differs (a query).
4. **`vtxjitter`** (x, y of positions nudged by 2^-17) is identical except for
   single-pixel edge flips on edges that pass exactly through a sample: 1-3
   texels in `p_g_blend_modes`, `p_g_stencil`, `p_g_formats_rt`, `p_g_srgb`,
   `p_g_lines`, `p_perf_fill`. A flipped texel differs by up to hundreds of LSB
   (it takes another triangle's colour).
5. **`texcoord_ulp`** (texture coordinates moved 1 ULP) changes only filtered
   sampling, by at most 1 LSB on tens of texels (`p_t_linear`,
   `p_t_mip_trilinear`, `p_t_address_modes`, `p_t_cube`).
6. **lavapipe** (Mesa's conformant CPU Vulkan) differs from SwiftShader almost
   everywhere colour is computed: 1-LSB rounding over hundreds of texels in
   most raster, blend, MSAA and filtered-texture cases; larger in line
   rasterization (up to 240 LSB on 4-5 texels), linear blits (122 LSB),
   float transcendentals (1523 ULP), matrix inverse (3423 ULP), f16 blits
   (up to 121 ULP everywhere), the subgroup case (subgroup size 8 vs 4), and
   ETC2/ASTC sampling (not supported there). Integer compute, shared memory,
   control flow, descriptors, queries and sync are identical.

## Consequences (inputs to Stages 6-8)

- **Order-dependent atomics are excluded** from graded snapshots: only
  commutative results (totals, min/max, and/or/xor) are snapshotted.
  Generators must not snapshot `atomic*` return values.
- **The n2 (ThreadCount=1) run gives noise = 0** for every other case, so T
  comes from the tolerated perturbations or sits at `t_lo`. As planned (§10.4),
  the thread-count run is a determinism check, not tolerance.
- **Tolerance candidates, measured cost:** `texcoord_ulp` admits 1-LSB
  filtering differences only (cheap, and the kind of difference the spec
  allows). `vtxjitter` admits a few edge-texel flips per raster case (the
  coverage term's job). `subzero` admits float-precision differences in
  compute (up to thousands of ULP after amplification): tolerating it widens
  T for exactly the float-heavy cases where the task asks for SwiftShader's
  result. Stage 6 decides with this table; Stage 8 measures what each costs
  the controls.
- **Transcendentals:** SwiftShader's LLVM and Subzero builds disagree at 4 ULP
  and lavapipe at 1523 ULP, all within the Vulkan precision bounds. Stage 6
  grades them at the spec bound (Stage 3 §3.5), not at SwiftShader's bits.
- **The lavapipe fairness number will be low for colour cases** mostly because
  of 1-LSB rounding: whether the distance tolerates 1 LSB decides how much of
  "correct Vulkan" scores as correct. Stage 6 must set the 1-LSB floor with
  this in view (the `round_trunc` control, §12).

## Defects found by this measurement and fixed

| Defect | Fix |
|---|---|
| `enumerate` with `capacity: 0` passed a NULL array (turning the call into a count query) and read past it | vkreplay always passes an array of at least one element |
| `formats: "all"` queried two `VK_KHR_maintenance5` formats on a device without it (a VUID) | only when the device advertises maintenance5 |
| Enum names were upper-cased before lookup, so `ASTC_4x4` never matched | case-insensitive index |
| The case builder recorded shader/asset targets by op index; ops inserted later shifted them | it keeps the op objects |
| Pilot cases used `D24_UNORM_S8_UINT` and `logicOp`, which SwiftShader does not support | `D32_SFLOAT_S8_UINT`; the logicOp case became a `VK_ERROR_FEATURE_NOT_PRESENT` case |
| `discard` compiles to demote-to-helper, which needs `shaderDemoteToHelperInvocation` | enabled in every raster case (`common.RASTER_FEATURES`) |

## Per-case results (repeats, thread count, backend, validation)

| Case | Family | Exit | Validation | Repeats | Threads | LLVM vs Subzero | Largest difference |
|---|---|---|---|---|---|---|---|
| p_g_blend_alpha | blend | ok | 0 | same | same | same |  |
| p_g_blend_modes | blend | ok | 0 | same | same | same |  |
| p_g_write_mask | blend | ok | 0 | same | same | same |  |
| p_c_convert_round | compute_arith | ok | 0 | same | same | **differs** | out: {"u32": {"max_lsb": 65537, "differing": 1}} |
| p_c_dispatch_indirect | compute_arith | ok | 0 | same | same | same |  |
| p_c_float_basic | compute_arith | ok | 0 | same | same | same |  |
| p_c_float_transc | compute_arith | ok | 0 | same | same | **differs** | out: {"f32": {"max_ulp": 4, "differing": 208, "nan_mismatch": 0}} |
| p_c_int_arith | compute_arith | ok | 0 | same | same | same |  |
| p_c_int_signed | compute_arith | ok | 0 | same | same | same |  |
| p_c_atomic_order | compute_atomics | ok | 0 | **differs** | **differs** | **differs** | out: {"u32": {"max_lsb": 640, "differing": 960}}; out: {"u32": {"max_lsb": 704, "differing": 1018}}; out: {"u32": {"max_lsb": 704, "differing": 1024}} |
| p_c_atomic_sum | compute_atomics | ok | 0 | same | same | same |  |
| p_c_cf_loops | compute_cf | ok | 0 | same | same | same |  |
| p_c_image_store | compute_image | ok | 0 | same | same | same |  |
| p_c_subgroup | compute_subgroup | ok | 0 | same | same | same |  |
| p_c_matrix | compute_types | ok | 0 | same | same | **differs** | out: {"f32": {"max_ulp": 5664, "differing": 429, "nan_mismatch": 0}} |
| p_c_shared_reduce | compute_types | ok | 0 | same | same | same |  |
| p_g_depth_bias | depth_stencil | ok | 0 | same | same | same |  |
| p_g_stencil | depth_stencil | ok | 0 | same | same | same |  |
| p_g_tri_depth | depth_stencil | ok | 0 | same | same | same |  |
| p_c_push_spec | descriptors_push | ok | 0 | same | same | same |  |
| p_i_errors | errors_robust | ok | 0 | same | same | same |  |
| p_i_feature_missing | errors_robust | ok | 0 | same | same | same |  |
| p_g_formats_rt | formats_copy_blit | ok | 0 | same | same | same |  |
| p_g_srgb | formats_copy_blit | ok | 0 | same | same | same |  |
| p_t_astc | formats_copy_blit | ok | 0 | same | same | same |  |
| p_t_bc1 | formats_copy_blit | ok | 0 | same | same | same |  |
| p_t_bc7 | formats_copy_blit | ok | 0 | same | same | same |  |
| p_t_etc2 | formats_copy_blit | ok | 0 | same | same | same |  |
| p_x_blit_formats | formats_copy_blit | ok | 0 | same | same | same |  |
| p_x_clears | formats_copy_blit | ok | 0 | same | same | same |  |
| p_x_copy_blit | formats_copy_blit | ok | 0 | same | same | same |  |
| p_i_query_all | inst_dev | ok | 0 | same | same | **differs** | query all |
| p_g_mrt | mrt_renderpass | ok | 0 | same | same | same |  |
| p_g_msaa_a2c | msaa | ok | 0 | same | same | same |  |
| p_g_msaa_resolve | msaa | ok | 0 | same | same | same |  |
| p_perf_fill | perf_fill | ok | 0 | same | same | same |  |
| p_q_occlusion | queries_sync | ok | 0 | same | same | same |  |
| p_q_timestamps | queries_sync | ok | 0 | same | same | same |  |
| p_s_fence_event | queries_sync | ok | 0 | same | same | same |  |
| p_s_timeline | queries_sync | ok | 0 | same | same | same |  |
| p_g_lines | raster_lines_points | ok | 0 | same | same | same |  |
| p_g_points | raster_lines_points | ok | 0 | same | same | same |  |
| p_g_cull_winding | raster_tri | ok | 0 | same | same | same |  |
| p_g_fill_rules | raster_tri | ok | 0 | same | same | same |  |
| p_g_interp_discard | raster_tri | ok | 0 | same | same | same |  |
| p_g_tri_basic | raster_tri | ok | 0 | same | same | **differs** | depth: {"D": {"max_ulp": 1, "differing": 48, "nan_mismatch": 0}} |
| p_g_tri_many | raster_tri | ok | 0 | same | same | same |  |
| p_g_viewport_scissor | raster_tri | ok | 0 | same | same | same |  |
| p_t_address_modes | tex_sample | ok | 0 | same | same | same |  |
| p_t_compare | tex_sample | ok | 0 | same | same | same |  |
| p_t_cube | tex_sample | ok | 0 | same | same | same |  |
| p_t_gather | tex_sample | ok | 0 | same | same | same |  |
| p_t_linear | tex_sample | ok | 0 | same | same | same |  |
| p_t_mip_trilinear | tex_sample | ok | 0 | same | same | same |  |
| p_t_nearest | tex_sample | ok | 0 | same | same | same |  |
| p_g_instanced_restart | vertex_input | ok | 0 | same | same | same |  |

## Per-case results against the other oracle variants

Each cell: items that differ from the base run, with the largest difference per channel and the number of differing texels.

| Case | Family | lavapipe | texcoord_ulp | vtxjitter |
|---|---|---|---|---|
| p_g_blend_alpha | blend | color0[R:1LSB/540px G:2LSB/725px B:1LSB/644px A:0LSB/0px] | same | same |
| p_g_blend_modes | blend | color0[R:2LSB/210px G:0LSB/0px B:1LSB/381px A:1LSB/816px] | same | color0[R:113LSB/1px G:64LSB/1px B:181LSB/1px A:51LSB/283px] |
| p_g_write_mask | blend | same | same | same |
| p_c_convert_round | compute_arith | out[u32:196874156LSB/928px] | same | same |
| p_c_dispatch_indirect | compute_arith | same | same | same |
| p_c_float_basic | compute_arith | same | same | same |
| p_c_float_transc | compute_arith | out[f32:1523ULP/1021px] | same | same |
| p_c_int_arith | compute_arith | same | same | same |
| p_c_int_signed | compute_arith | same | same | same |
| p_c_atomic_order | compute_atomics | out[u32:640LSB/960px] | out[u32:896LSB/972px] | out[u32:704LSB/1024px] |
| p_c_atomic_sum | compute_atomics | same | same | same |
| p_c_cf_loops | compute_cf | same | same | same |
| p_c_image_store | compute_image | img[R:0LSB/0px G:0LSB/0px B:1LSB/64px A:0LSB/0px] | same | same |
| p_c_subgroup | compute_subgroup | out[u32:1141600784LSB/1024px] | same | same |
| p_c_matrix | compute_types | out[f32:3423ULP/474px] | same | same |
| p_c_shared_reduce | compute_types | same | same | same |
| p_g_depth_bias | depth_stencil | depth[D:1LSB/685px] | same | same |
| p_g_stencil | depth_stencil | same | same | color0[R:20LSB/1px G:167LSB/1px B:183LSB/1px A:0LSB/0px]; depth[D:2603854ULP/1px]; stencil[S:254LSB/1px] |
| p_g_tri_depth | depth_stencil | same | same | same |
| p_c_push_spec | descriptors_push | same | same | same |
| p_i_errors | errors_robust | query dev_exts3; query ifp_3d_depth; query ifp_ok; query inst_exts0 | same | same |
| p_i_feature_missing | errors_robust | calls | same | same |
| p_g_formats_rt | formats_copy_blit | color0[R:1LSB/597px G:1LSB/378px B:1LSB/546px]; color1[A:0LSB/0px B:1LSB/816px G:1LSB/662px R:1LSB/674px]; color2[B:0.0abs/0px G:0.03125abs/62px R:0.0078125abs/ | same | color0[R:21LSB/2px G:54LSB/2px B:30LSB/2px]; color1[A:1LSB/2px B:708LSB/2px G:881LSB/2px R:993LSB/2px]; color2[B:2.875abs/2px G:2.5625abs/2px R:2.0625abs/2px] |
| p_g_srgb | formats_copy_blit | color0[R:1LSB/164px G:1LSB/414px B:1LSB/238px A:0LSB/0px] | same | color0[R:7LSB/1px G:4LSB/1px B:7LSB/1px A:0LSB/0px] |
| p_t_astc | formats_copy_blit | calls; color0[None] | same | same |
| p_t_bc1 | formats_copy_blit | color0[R:1LSB/192px G:1LSB/336px B:1LSB/480px A:0LSB/0px] | same | same |
| p_t_bc7 | formats_copy_blit | same | same | same |
| p_t_etc2 | formats_copy_blit | calls; color0[None] | same | same |
| p_x_blit_formats | formats_copy_blit | f16[R:72ULP/3751px G:64ULP/3724px B:121ULP/3744px A:80ULP/3734px]; u565[R:1LSB/772px G:1LSB/640px B:1LSB/876px] | same | same |
| p_x_clears | formats_copy_blit | color0[R:0LSB/0px G:0LSB/0px B:1LSB/4096px A:0LSB/0px] | same | same |
| p_x_copy_blit | formats_copy_blit | b[R:1LSB/528px G:1LSB/501px B:1LSB/480px A:1LSB/513px]; d[R:122LSB/724px G:121LSB/637px B:122LSB/621px A:122LSB/631px] | same | same |
| p_i_query_all | inst_dev | query all | same | same |
| p_g_mrt | mrt_renderpass | color1[R:1ULP/1019px G:1ULP/1100px B:1ULP/1218px A:0ULP/0px]; color2[R:1LSB/48px G:0LSB/0px B:0LSB/0px A:0LSB/0px] | same | same |
| p_g_msaa_a2c | msaa | res[R:1LSB/162px G:1LSB/230px B:1LSB/138px A:1LSB/271px] | same | same |
| p_g_msaa_resolve | msaa | color0[R:1LSB/177px G:1LSB/159px B:1LSB/249px A:0LSB/0px] | same | same |
| p_perf_fill | perf_fill | color0[R:19LSB/1753px G:37LSB/1848px B:7LSB/1929px A:1LSB/708px] | same | color0[R:22LSB/2px G:7LSB/3px B:16LSB/3px A:1LSB/285px] |
| p_q_occlusion | queries_sync | same | same | same |
| p_q_timestamps | queries_sync | same | same | same |
| p_s_fence_event | queries_sync | same | same | same |
| p_s_timeline | queries_sync | same | same | same |
| p_g_lines | raster_lines_points | color0[R:145LSB/4px G:240LSB/5px B:159LSB/5px A:0LSB/0px] | same | color0[R:0LSB/0px G:0LSB/0px B:1LSB/1px A:0LSB/0px] |
| p_g_points | raster_lines_points | same | same | same |
| p_g_cull_winding | raster_tri | same | same | same |
| p_g_fill_rules | raster_tri | same | same | same |
| p_g_interp_discard | raster_tri | color0[R:2LSB/278px G:0LSB/0px B:1LSB/3px A:0LSB/0px] | same | same |
| p_g_tri_basic | raster_tri | color0[R:1LSB/3px G:1LSB/1px B:1LSB/8px A:0LSB/0px]; depth[D:525ULP/1164px] | same | same |
| p_g_tri_many | raster_tri | same | same | same |
| p_g_viewport_scissor | raster_tri | same | same | same |
| p_t_address_modes | tex_sample | color0[R:1LSB/8px G:1LSB/3px B:1LSB/5px A:0LSB/0px] | color0[R:1LSB/8px G:1LSB/3px B:0LSB/0px A:0LSB/0px] | same |
| p_t_compare | tex_sample | same | same | same |
| p_t_cube | tex_sample | color0[R:1LSB/45px G:1LSB/71px B:1LSB/48px A:1LSB/69px] | color0[R:0LSB/0px G:0LSB/0px B:0LSB/0px A:1LSB/1px] | same |
| p_t_gather | tex_sample | same | same | same |
| p_t_linear | tex_sample | color0[R:1LSB/858px G:1LSB/788px B:1LSB/838px A:0LSB/0px] | color0[R:1LSB/53px G:1LSB/35px B:1LSB/44px A:0LSB/0px] | same |
| p_t_mip_trilinear | tex_sample | color0[R:4LSB/2034px G:4LSB/1931px B:5LSB/2054px A:0LSB/0px] | color0[R:1LSB/32px G:1LSB/29px B:1LSB/20px A:0LSB/0px] | same |
| p_t_nearest | tex_sample | same | same | same |
| p_g_instanced_restart | vertex_input | color0[R:1LSB/15px G:1LSB/1px B:1LSB/15px A:0LSB/0px] | same | same |
