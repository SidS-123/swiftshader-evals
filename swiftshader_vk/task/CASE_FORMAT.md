# Case format

A case is a JSON file that `vkreplay` (the fixed replay driver) executes
against a Vulkan driver: yours, or the reference. Every public case in
`dev/cases/` is in this format, and you can write your own and run them with
the `oracle` tool (reference) and the `driver` tool (your build).

```json
{"version": 1, "name": "my_case", "family": "compute_arith", "category": "replay",
 "assets": {"<sha>.bin": {}},
 "meta": {},
 "ops": [ {"op": "instance"}, {"op": "device"}, ... ]}
```

`ops` run in order. Each op is an object with an `"op"` key. Names you give
objects (`"name"`) must match `[A-Za-z0-9_.-]{1,64}` and be unique in the
case; later ops refer to them by name.

## What vkreplay does for you

- **Loading.** vkreplay loads your library through the real Vulkan loader
  (`VK_DRIVER_FILES` names a manifest pointing at your
  `build/libvk_candidate.so`), so your library must implement the loader-ICD
  interface: `vk_icdNegotiateLoaderICDInterfaceVersion`,
  `vk_icdGetInstanceProcAddr`, `vk_icdGetPhysicalDeviceProcAddr`, and
  dispatchable handles that start with the loader's dispatch pointer.
- **Memory.** For every `buffer` and `image` it picks the first memory type
  that is `HOST_VISIBLE | HOST_COHERENT` and allowed by the requirements
  (else the first allowed type), allocates, binds, and maps buffers.
- **Usage.** It adds `TRANSFER_SRC | TRANSFER_DST` to every buffer and image
  (so it can upload and read back) unless the op says `"auto_usage": false`.
- **Layouts.** Right after creation every image is transitioned from
  `UNDEFINED` to `GENERAL` with a pipeline barrier, and every command
  vkreplay records uses `GENERAL`.
- **Barriers.** Inside `exec`, `record` and `run`, a full memory barrier
  (`ALL_COMMANDS -> ALL_COMMANDS`, `MEMORY_WRITE -> MEMORY_READ|MEMORY_WRITE`)
  is recorded before each command outside a rendering scope, unless the op
  says `"auto_barriers": false`.
- **Rendering** uses dynamic rendering (`vkCmdBeginRendering`), so raster
  cases enable `VkPhysicalDeviceVulkan13Features.dynamicRendering`.
- **Errors.** Every Vulkan call that returns a `VkResult` is logged. If a
  creation call fails, the object does not exist, later ops that need it are
  skipped (noted in the ledger), and the case continues. A crash in your
  library ends the case (`exit: crash`).

## Enum and flag values

Wherever an op takes a Vulkan enum or flag, give the full name
(`"VK_FORMAT_R8G8B8A8_UNORM"`), the name without its prefix
(`"R8G8B8A8_UNORM"`, `"src_alpha"`), in any case, or a number. Flags are a
list of names (`["storage_buffer", "transfer_src"]`); the `_BIT` suffix is
optional.

## Data

Ops that take data accept either `"data"` or `"asset"`:

- `"data": {"f32": [...]}`: one of `u8 i8 u16 i16 u32 i32 u64 i64 f16 f32 f64`
  (little endian), or `"hex": "00ff..."`. Several keys concatenate in order;
  `"repeat": n` repeats the whole block.
- `"asset": {"file": "<name>"}`: raw bytes from the case's assets directory
  (`dev/assets/` for public cases). SPIR-V shaders are assets.

## Ops

### Setup
| op | fields | notes |
|---|---|---|
| `instance` | `api_version` ("1.3"), `app_name`, `extensions`, `layers`, `device_index` (0) | creates the instance and picks the physical device |
| `device` | `extensions`, `features`: `{"VkPhysicalDeviceVulkan13Features": {"dynamicRendering": true}, "VkPhysicalDeviceFeatures": {...}}` | one queue from the first family with graphics; a command pool |
| `query` | `name`, `what`: any of `api_version`, `device_version`, `instance_extensions`, `device_extensions`, `memory`, `queue_families`, `properties` / `features` (`"all"` or a list of struct names), `formats` (`"all"` or a list) | records the values as a query event |
| `enumerate` | `name`, `what`: `instance_extensions` / `instance_layers` / `device_extensions` / `physical_devices`, `capacity` | the two-call idiom with a caller-sized array; records both results (`VK_INCOMPLETE` is observable) |
| `image_format_props` | `name`, `format`, `type`, `tiling`, `usage`, `flags` | `vkGetPhysicalDeviceImageFormatProperties`; records the result and properties |

### Resources
| op | fields |
|---|---|
| `buffer` | `name`, `size`, `usage`, `flags`, `auto_usage`, `zero` (true: mapped memory starts zeroed) |
| `upload` | `buffer`, `offset`, `data` / `asset` |
| `image` | `name`, `format`, `extent` ([w], [w,h] or [w,h,d]), `type` (1d/2d/3d), `mips`, `layers`, `samples`, `tiling`, `usage`, `flags`, `auto_usage` |
| `upload_image` | `image`, `aspect`, `mip`, `base_layer`, `layers`, `offset`, `extent`, `data` / `asset` (tightly packed texels, or whole blocks for compressed formats) |
| `view` | `name`, `image`, `view_type` (1d, 2d, 3d, cube, 1d_array, 2d_array, cube_array), `format`, `swizzle` [r,g,b,a], `aspect`, `base_mip`, `mips`, `base_layer`, `layers` |
| `buffer_view` | `name`, `buffer`, `format`, `offset`, `range` |
| `sampler` | `name`, `mag`, `min`, `mipmap`, `address_u/v/w`, `lod_bias`, `min_lod`, `max_lod`, `anisotropy`, `compare`, `border`, `unnormalized` |
| `shader` | `name`, `asset` (SPIR-V) |
| `desc_layout` | `name`, `flags`, `bindings`: [{`binding`, `type`, `count`, `stages`, `flags`, `immutable_samplers`}] |
| `pipeline_layout` | `name`, `set_layouts`, `push_constants`: [{`stages`, `offset`, `size`}] |
| `desc_set` | `name`, `layout`, `writes` (below) |
| `desc_write` | `set`, `writes`: [{`binding`, `array_element`, `type`, and one of `buffers` [{`buffer`, `offset`, `range`}], `images` [{`view`, `sampler`, `layout`}], `texel_buffers` [names]}] |

### Pipelines
| op | fields |
|---|---|
| `compute_pipeline` | `name`, `layout`, `shader`, `entry` ("main"), `spec` [{`id`, one of `u32 i32 f32 bool u64 f64`}] |
| `graphics_pipeline` | `name`, `layout`, `vs`, `fs`, `vs_entry`, `fs_entry`, `vs_spec`, `fs_spec`, `vertex_input` {`bindings` [{`binding`, `stride`, `rate`}], `attributes` [{`location`, `binding`, `format`, `offset`}]}, `topology`, `primitive_restart`, `viewport` [x, y, w, h, min, max], `scissor` [x, y, w, h], `rasterization` {`polygon_mode`, `cull`, `front_face`, `depth_clamp`, `discard`, `depth_bias` [constant, clamp, slope], `line_width`}, `multisample` {`samples`, `sample_shading`, `sample_mask`, `alpha_to_coverage`, `alpha_to_one`}, `depth_stencil` {`test`, `write`, `compare`, `bounds` [min, max], `stencil` {`front`, `back`: {`fail`, `pass`, `depth_fail`, `compare`, `compare_mask`, `write_mask`, `reference`}}}, `blend` {`logic_op`, `constants`, `attachments` [{`enable`, `src_color`, `dst_color`, `color_op`, `src_alpha`, `dst_alpha`, `alpha_op`, `write_mask` ("rgba")}]}, `dynamic` [states], `rendering` {`color_formats`, `depth_format`, `stencil_format`} |

### Commands and submission
| op | fields | notes |
|---|---|---|
| `exec` | `cmds`, `auto_barriers` | record, submit, wait for completion |
| `record` | `name`, `cmds`, `simultaneous`, `auto_barriers` | a named command buffer |
| `submit` | `cbs`, `wait` [{`semaphore`, `value`, `stage`}], `signal` [{`semaphore`, `value`}], `fence` | one `vkQueueSubmit` |

Commands (`{"cmd": ...}` inside `cmds`): `begin_rendering` {`area`, `layers`,
`color` [{`view`, `load`, `store`, `clear`, `resolve_view`, `resolve_mode`}],
`depth`, `stencil`}, `end_rendering`, `bind_pipeline`, `bind_vertex_buffers`
{`first`, `buffers` [{`buffer`, `offset`}]}, `bind_index_buffer` {`buffer`,
`offset`, `type`}, `bind_sets` {`layout`, `bind_point`, `first`, `sets`,
`dynamic_offsets`}, `push_constants` {`layout`, `stages`, `offset`, `data`},
`set_viewport`, `set_scissor`, `set_blend_constants`, `set_depth_bias`,
`set_depth_bounds`, `set_stencil_compare_mask` / `_write_mask` / `_reference`,
`set_line_width`, `set_cull_mode`, `set_front_face`, `set_primitive_topology`,
`set_depth_test_enable`, `set_depth_write_enable`, `set_depth_compare_op`,
`set_depth_bounds_test_enable`, `set_stencil_test_enable`, `set_stencil_op`,
`set_rasterizer_discard_enable`, `set_depth_bias_enable`,
`set_primitive_restart_enable`, `draw` {`vertices`, `instances`,
`first_vertex`, `first_instance`}, `draw_indexed` {`indices`, ...,
`vertex_offset`}, `draw_indirect`, `draw_indexed_indirect`, `dispatch`
{`groups`}, `dispatch_base`, `dispatch_indirect`, `copy_buffer` {`src`, `dst`,
`regions` [[src_offset, dst_offset, size]]}, `fill_buffer`, `update_buffer`,
`copy_image`, `copy_buffer_to_image`, `copy_image_to_buffer`, `blit_image`,
`resolve_image`, `clear_color_image`, `clear_depth_stencil_image`,
`clear_attachments`, `barrier` {`src_stage`, `dst_stage`, `src_access`,
`dst_access`, `by_region`, `images` [{`image`, `old`, `new`, `aspect`, ...}]},
`reset_query_pool`, `begin_query`, `end_query`, `write_timestamp`,
`copy_query_results`, `set_event`, `reset_event`, `wait_events`.

### Synchronization and queries
| op | fields | records |
|---|---|---|
| `fence` | `name`, `signaled` | |
| `reset_fence` | `fences` | |
| `wait_fence` | `name`, `fences`, `all`, `timeout_ns` | the result |
| `fence_status` | `name`, `fence` | the result |
| `semaphore` | `name`, `timeline`, `initial` | |
| `signal_semaphore` | `semaphore`, `value` | |
| `wait_semaphores` | `name`, `semaphores` [[name, value]], `any`, `timeout_ns` | the result |
| `semaphore_value` | `name`, `semaphore` | result and value |
| `event` | `name` | (a `VkEvent`) |
| `set_event` / `reset_event` | `event` | (host side) |
| `event_status` | `name`, `event` | the result |
| `wait_idle` | `device` (false: the queue) | |
| `query_pool` | `name`, `type`, `count`, `statistics` | |
| `reset_query_pool` | `pool`, `first`, `count` | (host reset) |
| `read_queries` | `name`, `pool`, `first`, `count`, `flags` | results; timestamps only as validity (non-zero, non-decreasing) |

### Output
| op | fields |
|---|---|
| `snapshot` | `name`, `items`: [{`name`, `image`, `aspect` (color / depth / stencil), `mip`, `base_layer`, `layers`} or {`name`, `buffer`, `offset`, `size`, `elem`}] |
| `run` | `name`, `iterations`, `warmup`, `cmds` or `cb`, `snapshot` {`name`, `items`} |

A `snapshot` waits for the queue to go idle, then reads each item back.
Images are read with `vkCmdCopyImageToBuffer` (tightly packed, in `GENERAL`
layout); multisampled images must be resolved first. Buffers are read through
their mapping.

A `run` is a timed block: vkreplay records `cmds` once, submits it `warmup`
times, then submits it `iterations` times (waiting for each), then reads back
its snapshot. The time from the start of the first timed submission to the
arrival of the snapshot is the performance measurement, so the snapshot must
also be correct.

## What vkreplay writes

`<outdir>/ledger.json`:

- `exit`: `ok`, `crash`, `timeout` or `driver_error` (the case itself is malformed).
- `events`: in order, `{"op": "snapshot", "name", "files": {"snap": "<name>.ssnap"}, "items": [...]}`, `{"op": "query", "kind", "name", "value"}` and `{"op": "run", "name", "wall_seconds"}`.
- `calls`: `[op_index, "vkFunction", "VK_RESULT"]` for every call that returns a `VkResult`.
- `notes`: skipped ops and why.

`<outdir>/<name>.ssnap`: `SSNAP1\n`, a little-endian `u32` header length, a
JSON header listing each item (`name`, `kind`, `format`, `width`, `height`,
`depth`, `layers`, `texel_bytes`, `components` with bit widths and numeric
formats, `offset`, `size`, `missing`), then the raw bytes. Depth in `D24`
formats is read back as 32-bit words with the top 8 bits cleared.

The reference outputs of the public cases are in `dev/reference/<case>/`.
