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
| `errors_robust` | error results of valid calls (discoverable with the `oracle`), out-of-bounds behaviour with the core `robustBufferAccess` and `robustImageAccess` features |
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

Each snapshot item is compared with the reference's, element by element and
component by component, in the data's own units. For each component, the
error `e` is:

| Data | `e` | Free |
|---|---|---|
| integer formats, stencil, integer buffer elements (`u8` ... `i64`) | 0 if equal, otherwise "wrong" (saturates every term below) | 0 |
| normalized formats (UNORM, SNORM, sRGB codes), `D16_UNORM` depth | difference of the stored integers (LSB) | 1 LSB |
| float formats, `D32_SFLOAT` depth, float buffer elements (`f16`, `f32`, `f64`), packed floats | difference / ULP of the format at max(\|reference\|, 1/16) | 2 ULP |

NaN matches only NaN, an infinity only the same infinity; anything else
against them is "wrong". The **free** amount is subtracted
(`e_eff = max(0, e - free)`): a last-bit difference in rounding costs
nothing. An item with an `allow` field ({"ulp": n}, {"lsb": n}) adds that to
the free amount; cases set it, with the specification's bound, for results
whose precision the Vulkan specification bounds rather than defines (appendix
"SPIR-V Environment", "Precision of Individual Operations": `exp`, `log`,
`sin`, `pow`, division, ...). An element's error is the largest over its
components; its signed error (for the bias term) the mean.

Elements are grouped into **blocks**: 8x8 texels of each layer and depth
slice of an image, 64 consecutive elements of a buffer. For each block:

```
d_mag   = mean(min(e_eff, 16)) / 4               how wrong, broadly
d_bias  = |mean(clip(signed e_eff, -4, 4))| / 2  a uniform offset or gain
d_cov   = fraction of elements with e_eff > 1 / C   how many are wrong
          (C = 1/4 for images: a quarter of a block wrong saturates it, since
           edge samples legitimately differ; C = 1/64 for buffers: one wrong
           element saturates its run, since every element is a result)
d_block = min(1, max(d_mag, d_bias, d_cov)), and 0 if below 0.05
```

An item's defect is the root-mean-square of its blocks, and the snapshot's
`D` the root-mean-square over its items (items weigh equally):
`D = sqrt(mean over items of mean over blocks of d_block^2)`. One fully wrong
block in a 64x64 image gives `D = 0.125`; three stray texels, about 0.01. A
missing item, a size or format mismatch, or a colour item that is uniform
where the reference's is not, sets all of that item's blocks to 1.

Each case has one tolerance `T`, shared by its snapshots, derived from how
much the **reference's own** output moves (the same `D`) when its vertex
positions are nudged by less than its sub-pixel precision or its texture
coordinates by 1 ULP: `T = clamp(2 x that movement, 0.03, 0.30)`. The
reference is deterministic, so most cases have `T = 0.03`. A snapshot scores
`1 / (1 + (D / T)^4)` (0.94 at `D = T/2`, 0.5 at `D = T`, 0.06 at
`D = 2T`), and a replay case scores the mean of its snapshots. The `grade_dev`
tool reports `D`, `T` and the score of every snapshot.

### Procedural cases

A procedural case scores the fraction of its checks that pass. A crash or
timeout fails the whole case. The checks compare your results with the
reference's on the same case:

- the case ran to the end (`exit: ok`);
- every snapshot it takes is within the case's tolerance (as above);
- the whole sequence of `VkResult`s in the ledger's `calls`, including the
  calls `vkreplay` makes on the case's behalf (one check);
- every `query` event: one check per top-level key of its value (for example
  `properties`, `features`, `formats`, `device_extensions` of a `query` op;
  the whole value of an `enumerate`, `fence_status`, `read_queries`, ... op),
  each passing only if every value under it matches: reported properties,
  features, formats, extensions, enumeration results, fence, event and
  semaphore status and values, query results;
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
