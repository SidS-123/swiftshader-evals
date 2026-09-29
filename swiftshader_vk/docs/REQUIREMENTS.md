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

## Out of scope (v1)

WSI and presentation, video, ray tracing, mesh shaders, sparse resources,
multi-device groups, external memory/semaphores, and any behaviour the
Stage 3 determinism report marks as order-dependent.
