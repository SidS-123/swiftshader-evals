# Requirements: the graded API surface

What a candidate ICD must implement for the graded cases to pass. This file
owns the family list; `task/SPEC.md` is its model-facing version. The
coverage and weights are set from Stage 3's CTS pass-list breakdown
(PLAN_v1.md §7, §11.1).

## Target

- Vulkan 1.3, headless: no `VK_KHR_surface` / swapchain / WSI.
- One physical device whose reported properties, features, limits, format
  properties, queue families and memory types equal
  `spec/device_profile.json` (frozen from the oracle in Stage 3).
- The loader–ICD interface: `vk_icdNegotiateLoaderICDInterfaceVersion`,
  `vk_icdGetInstanceProcAddr`, `vk_icdGetPhysicalDeviceProcAddr`, and the
  loader magic in dispatchable handles.

## Families

| # | Family | Category | Covers |
|---|---|---|---|
| 1 | `inst_dev` | procedural | instance/device creation, enumeration, properties, features, limits, format props, queue families, memory types |
| 2 | `mem_buf` | replay+proc | buffers, memory types, map/unmap, copy/fill/update, alignment |
| 3 | `compute_arith` | replay | int/float arithmetic, conversions, bit ops, built-ins |
| 4 | `compute_cf` | replay | branches, loops, switch, functions, early exit |
| 5 | `compute_types` | replay | vectors, matrices, structs, arrays, shared memory, buffer device address through push-constant pointers only (no pointers loaded from memory: oracle crash, Stage 7) (no 8/16-bit or 64-bit types: unsupported) |
| 6 | `compute_subgroup` | replay | subgroup ops at size 4 |
| 7 | `compute_atomics` | replay | commutative atomic totals only, on buffers and shared memory (no image atomics: nondeterministic on the oracle, Stage 7) |
| 8 | `compute_image` | replay | storage images, image load/store, texel buffers |
| 9 | `raster_tri` | replay | fill rules, culling, winding, viewport, scissor, depth bias, clipping |
| 10 | `raster_lines_points` | replay | lines, points, point size |
| 11 | `vertex_input` | replay | vertex formats, strides, instancing, index types, primitive restart, topologies |
| 12 | `blend` | replay | factors/ops, constants, write masks, advanced blend ops (`VK_EXT_blend_operation_advanced`); no logic ops or dual-source blend (unsupported) |
| 13 | `depth_stencil` | replay | compare ops, stencil ops, depth formats (D16, D32, D32S8, S8), bounds, clamp |
| 14 | `tex_sample` | replay | filters, mip modes, address modes, borders, LOD, compare, cube/3D/arrays, gather |
| 15 | `formats_copy_blit` | replay | copies, blits, clears, resolves, format conversions, BC/ETC2/ASTC-LDR sampling |
| 16 | `msaa` | replay | 4x raster, sample shading, masks, alpha-to-coverage, resolve |
| 17 | `mrt_renderpass` | replay | multiple attachments, subpasses, input attachments, load/store ops, dynamic rendering |
| 18 | `descriptors_push` | replay | descriptor types, dynamic offsets, push/spec constants, descriptor indexing |
| 19 | `queries_sync` | replay+proc | occlusion and timestamp queries (no pipeline statistics: unsupported), fences, events, timeline semaphores, barriers, multi-submit |
| 20 | `errors_robust` | procedural | error results of valid calls (`VK_ERROR_FORMAT_NOT_SUPPORTED`, `VK_INCOMPLETE`, `VK_ERROR_OUT_OF_DEVICE_MEMORY`, `VK_ERROR_FEATURE_NOT_PRESENT`), out-of-bounds behaviour under the core `robustBufferAccess` / `robustImageAccess` (the oracle has no `VK_EXT_robustness2`) |
| 21 | `perf_*` | performance | fill rate, compute throughput, geometry, texture-heavy |

## Measured facts that shape the surface (Stage 3)

- The oracle is Vulkan 1.3 and rejects `apiVersion` > 1.3 at instance
  creation; every case requests 1.3.
- One queue family (graphics+compute+transfer, 1 queue), one memory type
  (device-local, host-visible, coherent, cached), one 2 GiB heap.
- Sample counts {1, 4}; subgroup size 4; `lineWidthRange` [1, 1] (no wide
  lines); points up to 1023; `maxBoundDescriptorSets` 4;
  `maxPushConstantsSize` 128; buffer offset alignments 256.
- Supported compressed formats: BC, ETC2, ASTC LDR (no ASTC HDR).
- Depth/stencil formats with optimal-tiling attachment support: `D16_UNORM`,
  `D32_SFLOAT`, `D32_SFLOAT_S8_UINT`, `S8_UINT`. **No `D24` format.**
- Core features the oracle does **not** support (Stage 4, from the frozen
  profile): `logicOp`, `dualSrcBlend`, `alphaToOne`, `pipelineStatisticsQuery`,
  `shaderInt16`, `shaderInt64`, `shaderFloat64`, `shaderImageGatherExtended`,
  `shaderStorageImageReadWithoutFormat`, `multiViewport`, `wideLines`,
  `inheritedQueries`, geometry/tessellation; Vulkan 1.1 `shaderDrawParameters`,
  16-bit storage, variable pointers; Vulkan 1.2 `shaderFloat16`, `shaderInt8`,
  8-bit storage, 64-bit atomics, `drawIndirectCount`, `samplerFilterMinmax`,
  `shaderOutputLayer`/`ViewportIndex`; Vulkan 1.3 `textureCompressionASTC_HDR`.
  Requesting one gives `VK_ERROR_FEATURE_NOT_PRESENT`, which `errors_robust`
  may grade.
- `discard` in GLSL compiles to demote-to-helper (SPIR-V 1.6), so raster cases
  enable `shaderDemoteToHelperInvocation` (supported).
- Float precision the grader may demand exactly: only ops the spec calls
  correctly rounded or correct result (Vulkan-Docs v1.4.357, "Precision of
  Individual Operations"). Transcendentals are excluded or tolerated at the
  spec bound (decided in Stage 6).
- **Determinism (Stage 4, `docs/internal/determinism.md`):** the oracle is
  byte-identical across runs and thread counts on every op type except the
  return values of atomics, whose order is undefined.

## Narrowed in Stage 7 (generation; `docs/internal/stage7_findings.md`)

- **Buffer device addresses** are only dereferenced when they come from push
  constants. Dereferencing an address loaded from memory (a linked list of
  `buffer_reference` blocks pointing to their own type) crashes the oracle
  (SIGSEGV), so no case chases pointers.
- **Image atomics** are not graded: their results on the oracle differ between
  repeated runs of the same case. Buffer and shared-memory atomics stay
  (commutative totals only).
- **Robustness** is the core `robustBufferAccess` (reads outside the bound
  range return 0, writes are dropped) and `robustImageAccess` (reads outside
  the image return 0, stores are dropped), as the oracle behaves; the oracle
  does not expose `VK_EXT_robustness2`.
- **Layers and loader-made results** are never graded: enumerating instance
  layers reports what the loader finds (the validation layer during the gate),
  not the driver.

## Out of scope (v1)

- **Not supported by the oracle:** geometry and tessellation shaders,
  `shaderFloat64`, sparse binding/residency, wide lines, multiple viewports,
  ASTC HDR, transform feedback, mesh shaders, ray tracing, fragment shading
  rate, conditional rendering, video.
- **Supported but excluded:** WSI and presentation (headless, D1),
  external memory/fences/semaphores, device groups, protected memory,
  YCbCr conversion, multiview, host image copy, `VK_EXT_external_memory_host`,
  and pipeline libraries (the CTS library variants duplicate monolithic
  behaviour; graphics pipelines are built monolithically).
- Order-dependent results: the return values of atomic operations (which
  invocation got which old value). Only commutative atomic results (sums,
  min/max, and/or/xor) are snapshotted (`docs/internal/determinism.md`).
