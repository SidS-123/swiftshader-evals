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
| 5 | `compute_types` | replay | vectors, matrices, structs, arrays, 8/16-bit, pointers |
| 6 | `compute_subgroup` | replay | subgroup ops at size 4 |
| 7 | `compute_atomics` | replay | commutative atomic totals only |
| 8 | `compute_image` | replay | storage images, image load/store, texel buffers |
| 9 | `raster_tri` | replay | fill rules, culling, winding, viewport, scissor, depth bias, clipping |
| 10 | `raster_lines_points` | replay | lines, points, point size |
| 11 | `vertex_input` | replay | vertex formats, strides, instancing, index types, primitive restart, topologies |
| 12 | `blend` | replay | factors/ops, constants, write masks, logic ops |
| 13 | `depth_stencil` | replay | compare ops, stencil ops, depth formats, bounds, clamp |
| 14 | `tex_sample` | replay | filters, mip modes, address modes, borders, LOD, compare, cube/3D/arrays, gather |
| 15 | `formats_copy_blit` | replay | copies, blits, clears, resolves, format conversions, BC/ETC2/ASTC-LDR sampling |
| 16 | `msaa` | replay | 4x raster, sample shading, masks, alpha-to-coverage, resolve |
| 17 | `mrt_renderpass` | replay | multiple attachments, subpasses, input attachments, load/store ops, dynamic rendering |
| 18 | `descriptors_push` | replay | descriptor types, dynamic offsets, push/spec constants, descriptor indexing |
| 19 | `queries_sync` | replay+proc | occlusion queries, fences, events, timeline semaphores, barriers, multi-submit |
| 20 | `errors_robust` | procedural | graded error codes, robustness2 out-of-bounds behaviour |
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
- Float precision the grader may demand exactly: only ops the spec calls
  correctly rounded or correct result (Vulkan-Docs v1.4.357, "Precision of
  Individual Operations"). Transcendentals are excluded or tolerated at the
  spec bound (decided in Stage 6).

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
- Anything the Stage 3/4 determinism report marks as order-dependent
  (pending; see `docs/internal/stage3_findings.md` §3.4).
