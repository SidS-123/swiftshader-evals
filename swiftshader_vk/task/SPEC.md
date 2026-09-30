# Specification

What your driver must implement, what is compared with the reference, and
what "the same" means. Everything graded is described here; if a behaviour is
not covered by `spec/`, this file or `dev/CASE_FORMAT.md`, ask the `oracle`.

## The driver

- **Loader interface.** Export `vk_icdNegotiateLoaderICDInterfaceVersion`,
  `vk_icdGetInstanceProcAddr` and `vk_icdGetPhysicalDeviceProcAddr`
  (`vulkan/vk_icd.h`). Every dispatchable handle (`VkInstance`,
  `VkPhysicalDevice`, `VkDevice`, `VkQueue`, `VkCommandBuffer`) starts with the
  loader's dispatch slot (`set_loader_magic_value`). The loader in `/opt/vk`
  (1.4.357) skips an ICD whose `vk_icdGetInstanceProcAddr` does not return the
  core 1.0 instance and physical-device functions (the starter returns the set
  it needs), and treats an ICD without `vkEnumerateInstanceVersion` as Vulkan
  1.0.
- **One physical device**, Vulkan **1.3**, reporting exactly what
  `spec/device_profile.json` lists: `VkPhysicalDeviceProperties` (including
  `apiVersion`, `driverVersion`, `vendorID`, `deviceID`, `deviceType`,
  `deviceName` and every limit), every feature struct, every property struct,
  `VkFormatProperties` (and the matching `VkFormatProperties3`) of every
  format, the queue family, the memory heap and type, and the device and
  instance extension lists with their spec versions. UUIDs
  (`pipelineCacheUUID`, `deviceUUID`, `driverUUID`) and `conformanceVersion`
  are not compared. The profile, not the spec's version, decides what the
  device exposes.
- Cases create their instance with `apiVersion` 1.3. Requesting an extension
  or a feature the device does not support at device creation fails with
  `VK_ERROR_EXTENSION_NOT_PRESENT` / `VK_ERROR_FEATURE_NOT_PRESENT`.

## What the cases exercise

Cases are grouped in families. Public cases of every family are in
`dev/cases/`; hidden cases come from the same families.

| Family | What it exercises |
|---|---|
| `inst_dev` | instance and device creation, enumeration, properties, features, limits, format properties, queue families, memory types |
| `mem_buf` | buffers, memory types, map/unmap, copy/fill/update, alignment |
| `compute_arith` | integer and float arithmetic, conversions, bit operations, GLSL.std.450 built-ins |
| `compute_cf` | branches, loops, switch, function calls, early exit |
| `compute_types` | vectors, matrices, structs, arrays, shared memory, buffer device address |
| `compute_subgroup` | subgroup operations (subgroup size 4) |
| `compute_atomics` | atomic operations whose final results do not depend on execution order |
| `compute_image` | storage images, image load/store, texel buffers |
| `raster_tri` | triangle rasterization: fill rules, culling, winding, viewport, scissor, depth bias, clipping, interpolation |
| `raster_lines_points` | lines and points, point size |
| `vertex_input` | vertex formats, strides, instancing, index types, primitive restart, topologies |
| `blend` | blend factors and operations, constants, write masks, advanced blend operations |
| `depth_stencil` | depth and stencil tests and operations, depth formats, depth bounds, clamp |
| `tex_sample` | sampling: filters, mipmaps, address modes, borders, LOD, compare, cube/3D/array images, gather |
| `formats_copy_blit` | image formats as attachments and copy targets, copies, blits, clears, resolves, BC/ETC2/ASTC (LDR) sampling |
| `msaa` | 4x multisampling, sample shading, sample masks, alpha-to-coverage, resolve |
| `mrt_renderpass` | multiple colour attachments, load/store operations, dynamic rendering, render passes and subpasses |
| `descriptors_push` | descriptor types, dynamic offsets, push constants, specialization constants, descriptor indexing |
| `queries_sync` | occlusion and timestamp queries, fences, events, binary and timeline semaphores, barriers, multiple submissions |
| `errors_robust` | error results of valid calls (discoverable with the `oracle`), `robustness2` out-of-bounds behaviour |
| `perf_*` | timed runs: fill rate, compute throughput, geometry, texture-heavy rendering |

Every case is valid Vulkan usage: it replays on the reference with the Khronos
validation layer reporting no errors, and within the `oracle` tool's limits.

## Not graded

- Not supported by the reference (your device must still *report* these as
  the profile does): geometry and tessellation shaders, 64-bit floats and
  integers in shaders, 8- and 16-bit shader types and storage, sparse
  resources, wide lines, multiple viewports, logic operations, dual-source
  blending, pipeline statistics queries, `D24` depth formats, ASTC HDR,
  transform feedback, mesh shaders, ray tracing, fragment shading rate,
  conditional rendering, video.
- Supported by the reference but not exercised: presentation (surfaces,
  swapchains), external memory and external fence/semaphore handles, device
  groups, protected memory, YCbCr conversion, multiview, host image copy,
  pipeline libraries, the contents of pipeline caches.

## What "the same" means

### Replay cases

Each snapshot item is compared with the reference's, texel by texel (element
by element for buffers), in the data's own units:

- integer formats, stencil, and integer buffer elements (`u8` ... `i64`):
  exactly;
- normalized formats (UNORM, SNORM, sRGB) and `D16_UNORM` depth: in steps of
  the stored integer (1 LSB is one step);
- float formats, `D32_SFLOAT` depth and float buffer elements (`f16`, `f32`,
  `f64`): in ULPs, with an absolute floor near zero; NaN must match NaN and an
  infinity the same infinity;
- an item with an `allow` field ({"ulp": n}, {"lsb": n} or {"abs": x})
  ignores differences up to that allowance. Cases use it for results whose
  precision the Vulkan specification bounds rather than defines (appendix
  "SPIR-V Environment", "Precision of Individual Operations": e.g. `exp`,
  `log`, `sin`, `pow`, division), with the specification's bound.

These per-texel differences are summarised per 8x8 block of texels (per fixed
run of elements for buffers) into a snapshot defect `D` between 0 and 1 that
is sensitive to a uniform bias, to scattered large errors (a wrong edge, a
wrong texel) and to structure. A missing item, a wrong size, or an output that
is uniform where the reference's is not, gives `D = 1`.

Each case has one tolerance `T`, shared by its snapshots, derived from how
much the **reference's own** output moves when a sub-pixel amount is added to
vertex positions, texture coordinates move by 1 ULP, or another build of the
reference runs the case: `T = clamp(2 x that movement, 0.03, 0.30)`. A
snapshot scores `1 / (1 + (D / T)^4)` (0.94 at `D = T/2`, 0.5 at `D = T`,
0.06 at `D = 2T`), and a replay case scores the mean of its snapshots. The
`grade_dev` tool reports `D`, `T` and the score of every snapshot.

### Procedural cases

A procedural case scores the fraction of its checks that pass. A crash or
timeout fails the whole case. The checks compare your results with the
reference's on the same case:

- the case ran to the end (`exit: ok`);
- every snapshot it takes is within the case's tolerance (as above);
- the sequence of `VkResult`s in the ledger's `calls`, including the calls
  `vkreplay` makes on the case's behalf;
- every value in every `query` event: reported properties, features, formats,
  extensions, enumeration results, fence, event and semaphore status and
  values, query results;
- timestamps are never compared by value, only for validity: non-zero,
  non-decreasing in query order, and the same availability.

The order in which invocations reach an atomic operation is not defined;
cases never compare atomic return values that depend on it.

### Performance cases

A timed `run` op is measured by `vkreplay`'s trusted parent from the start of
the timed submissions to the arrival of the read-back snapshot. With `r` =
your time / the reference's time on the same case (both measured the same
way, in the same sandbox configuration), the case scores
`1 / (1 + (r / 16)^4)`: about 1 for `r` up to 4, 0.94 at 8, 0.5 at 16, 0.06
at 32. The score is 0 if the timed run's snapshot scores below 0.5 or the
case does not finish. Full success needs every ratio at most 8.
