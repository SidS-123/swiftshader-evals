# Case format

A case is a JSON file that `vkreplay` (the fixed replay driver) executes
against a Vulkan driver: yours, or the reference. Every public case in
`dev/cases/` is in this format. You can write your own and run them with the
`oracle` tool (reference) and the `driver` tool (your build), or run
`vkreplay` yourself (below).

```json
{"version": 1, "name": "my_case", "family": "compute_arith", "category": "replay",
 "assets": {"<file>": {}},
 "meta": {},
 "ops": [ {"op": "instance"}, {"op": "device"}, ... ]}
```

- `ops` run in order; everything else is informational. `category` is
  `replay`, `procedural` or `performance` (it decides how the case is scored,
  see `SPEC.md`). `assets` lists the asset files the case uses (content-hash
  names in the public corpus; any plain file name in yours). `meta` holds
  grading hints such as `timed_run_index` for performance cases.
- Each op is an object with an `"op"` key. Names you give objects (`"name"`)
  must match `[A-Za-z0-9_.-]{1,64}` and be unique in the case; later ops refer
  to them by name. A missing optional field takes the default shown.

## Running vkreplay yourself

```
vkreplay --candidate /task/build CASE OUTDIR ASSETS_DIR
vkreplay --candidate /task/build --validate CASE OUTDIR ASSETS_DIR   # with the Khronos validation layer
```

`--candidate DIR` loads `DIR/libvk_candidate.so`. `--validate` enables
`VK_LAYER_KHRONOS_validation` (installed in `/opt/vk`) and records its
messages in the ledger's `validation` list: use it to check that a case you
wrote is valid Vulkan. You may run `vkreplay` under `gdb`, `strace` or
`valgrind` to debug your library; `vkreplay` runs your library in a child
process (`vkreplay --child ...`), so attach to or follow that process.

## What vkreplay does for you

- **Loading.** vkreplay loads your library through the real Vulkan loader
  (1.4.357, in `/opt/vk`), from a manifest that names your
  `libvk_candidate.so`, with no other driver and no implicit layers visible.
- **Memory.** For every `buffer` and `image` it calls
  `vkGet*MemoryRequirements`, picks the first memory type that is
  `HOST_VISIBLE | HOST_COHERENT` and allowed (else the first allowed type),
  allocates, binds at offset 0, and maps buffers persistently.
- **Usage.** It adds `TRANSFER_SRC | TRANSFER_DST` to every buffer's and
  image's usage (so it can upload and read back) unless the op says
  `"auto_usage": false`.
- **Layouts.** Right after creation every image goes from `UNDEFINED` to
  `GENERAL` (one pipeline barrier over all its subresources, submitted and
  waited for). Everything vkreplay records uses `GENERAL`.
- **Uploads.** `upload` writes a buffer through its mapping (or a staging
  copy if it is not host-visible). `upload_image` writes a staging buffer and
  records `vkCmdCopyBufferToImage` in `GENERAL` layout, then submits and waits.
- **Barriers.** Inside `exec`, `record` and `run`, a full memory barrier
  (`ALL_COMMANDS -> ALL_COMMANDS`, `MEMORY_WRITE -> MEMORY_READ | MEMORY_WRITE`)
  is recorded before *every* command (binds and state-setting included) that
  is outside a rendering scope, unless the op says `"auto_barriers": false`.
  Nothing is recorded between `begin_rendering` and `end_rendering`.
- **Rendering** uses dynamic rendering (`vkCmdBeginRendering`). vkreplay
  enables no features on its own: a case that renders lists
  `"VkPhysicalDeviceVulkan13Features": {"dynamicRendering": true}` (and
  whatever else it uses) in its `device` op.
- **Every call is logged.** Every Vulkan call that returns a `VkResult`, the
  ones vkreplay makes on the case's behalf included (allocation, binding,
  layout transitions, staging copies, submissions, fence waits), is recorded
  in `ledger.calls` under the op that caused it. If a creation call fails, the
  object does not exist, later ops that need it are skipped (noted in
  `ledger.notes`), and the case continues. A crash in your library ends the
  case.

## Enum and flag values

Wherever an op takes a Vulkan enum or flag, give the full name
(`"VK_FORMAT_R8G8B8A8_UNORM"`), the name without its prefix
(`"R8G8B8A8_UNORM"`, `"src_alpha"`), in any case, or a number. Flags are a
list of names (`["storage_buffer", "transfer_src"]`) or one name; the `_BIT`
suffix is optional.

## Data

Ops that take data accept either `"data"` or `"asset"`:

- `"data": {"f32": [...]}` with one or more of `u8 i8 u16 i16 u32 i32 u64 i64
  f16 f32 f64` (little endian; `f16` values are given as numbers and rounded
  to half precision), `"hex": "00ff..."`, or `"address": [{"buffer": name,
  "offset": 0}]` (each a 64-bit `vkGetBufferDeviceAddress` of that buffer plus
  the offset; the buffer needs `shader_device_address` usage and the device
  `VkPhysicalDeviceVulkan12Features.bufferDeviceAddress`). Several keys
  concatenate in the order given; `"repeat": n` repeats the whole block.
- `"asset": {"file": "<name>"}`: raw bytes from the assets directory
  (`dev/assets/` for the public cases). SPIR-V shaders are assets.

## Ops

### Setup
| op | fields (default) | records |
|---|---|---|
| `instance` | `api_version` ("1.3"), `app_name` ("vkreplay"), `extensions` ([]), `layers` ([]), `device_index` (0) | picks physical device `device_index` |
| `device` | `extensions` ([]), `features` ({}): `{"VkPhysicalDeviceFeatures": {"fillModeNonSolid": true}, "VkPhysicalDeviceVulkan13Features": {...}, ...}` | one queue from the first family with graphics, a command pool (`RESET_COMMAND_BUFFER`) |
| `query` | `name`, `what`: any of `api_version`, `device_version`, `instance_extensions`, `device_extensions`, `memory`, `queue_families`: `true`; `properties`, `features`: `"all"` or a list of struct names; `formats`: `"all"` or a list of formats | the values, as a query event |
| `enumerate` | `name`, `what`: `instance_extensions`, `instance_layers`, `device_extensions` or `physical_devices`; `capacity` (the total) | the two-call idiom with a caller-sized array: both results, total, names |
| `image_format_props` | `name`, `format`, `type` ("2d"), `tiling` ("optimal"), `usage`, `flags` ([]) | `vkGetPhysicalDeviceImageFormatProperties` result and properties |

`properties: "all"` chains every `VkPhysicalDeviceProperties2` extension
struct the Vulkan registry defines whose core version or extension your
device reports (so what you advertise decides what is queried); `features:
"all"` likewise for `VkPhysicalDeviceFeatures2`. `formats: "all"` is every
core format plus the 4444 formats (and the `VK_KHR_maintenance5` formats if
you advertise it), each through `vkGetPhysicalDeviceFormatProperties2` with
`VkFormatProperties3` chained.

### Resources
| op | fields (default) |
|---|---|
| `buffer` | `name`, `size`, `usage` ([]), `flags` ([]), `auto_usage` (true), `zero` (true: memset to 0 through the mapping) |
| `upload` | `buffer`, `offset` (0), `data` or `asset` |
| `image` | `name`, `format`, `extent` ([w], [w, h] or [w, h, d]), `type` ("2d"; "1d", "3d"), `mips` (1), `layers` (1), `samples` (1), `tiling` ("optimal"), `usage` ([]), `flags` ([]), `auto_usage` (true) |
| `upload_image` | `image`, `aspect` (the image's; depth for depth/stencil), `mip` (0), `base_layer` (0), `layers` (1), `offset` ([0, 0, 0]), `extent` (the mip's), `data` or `asset`: tightly packed texels of the aspect's readback layout (below), or whole blocks for compressed formats |
| `view` | `name`, `image`, `view_type` ("2d", or the image's type; "1d", "3d", "cube", "1d_array", "2d_array", "cube_array"), `format` (the image's), `swizzle` ([r, g, b, a] identity), `aspect` (the image's aspects), `base_mip` (0), `mips` (all), `base_layer` (0), `layers` (all) |
| `buffer_view` | `name`, `buffer`, `format`, `offset` (0), `range` (whole) |
| `sampler` | `name`, `mag` ("nearest"), `min` ("nearest"), `mipmap` ("nearest"), `address_u/v/w` ("repeat"), `lod_bias` (0), `min_lod` (0), `max_lod` (none), `anisotropy` (off; a number enables it), `compare` (off; a compare op enables it), `border` ("float_transparent_black"), `unnormalized` (false) |
| `shader` | `name`, `asset` (SPIR-V) |
| `desc_layout` | `name`, `flags` ([]), `bindings`: [{`binding`, `type`, `count` (1), `stages` ("all"), `flags` ([]), `immutable_samplers` ([])}] |
| `pipeline_layout` | `name`, `set_layouts` ([]), `push_constants` ([]): [{`stages` ("all"), `offset` (0), `size`}] |
| `desc_set` | `name`, `layout`, `writes` ([]); vkreplay creates a descriptor pool sized for the layout |
| `desc_write` | `set`, `writes`: [{`binding`, `array_element` (0), `type`, and one of `buffers` [{`buffer`, `offset` (0), `range` (whole)}], `images` [{`view`, `sampler`, `layout` ("general")}], `texel_buffers` [buffer view names]}] |

### Pipelines
| op | fields (default) |
|---|---|
| `compute_pipeline` | `name`, `layout`, `shader`, `entry` ("main"), `spec` ([{`id`, one of `u32 i32 f32 bool u64 f64`}]) |
| `graphics_pipeline` | see below |

`graphics_pipeline`: `name`, `layout`, `vs`, `fs` (none: no fragment
shader), `vs_entry` / `fs_entry` ("main"), `vs_spec` / `fs_spec`;
`vertex_input` {`bindings` [{`binding`, `stride`, `rate` ("vertex")}],
`attributes` [{`location`, `binding` (0), `format`, `offset` (0)}]};
`topology` ("triangle_list"); `primitive_restart` (false); `viewport`
([x, y, w, h, min_depth (0), max_depth (1)], default [0, 0, 1, 1]); `scissor`
([x, y, w, h], default: the viewport's rectangle); `rasterization`
{`polygon_mode` ("fill"), `cull` ("none"), `front_face`
("counter_clockwise"), `depth_clamp` (false), `discard` (false), `depth_bias`
([constant, clamp, slope]; absent: disabled), `line_width` (1)};
`multisample` {`samples` (1), `sample_shading` (absent: off; a number is
`minSampleShading`), `sample_mask` (all), `alpha_to_coverage` (false),
`alpha_to_one` (false)}; `depth_stencil` {`test` (false), `write` (false),
`compare` ("less"), `bounds` ([min, max]; absent: off), `stencil` (absent:
off) {`front`, `back` (= front): {`fail`, `pass`, `depth_fail` ("keep"),
`compare` ("always"), `compare_mask` (255), `write_mask` (255), `reference`
(0)}}}; `blend` {`logic_op` (absent: off), `constants` ([0, 0, 0, 0]),
`attachments`, one per colour format: [{`enable` (false), `src_color`
("one"), `dst_color` ("zero"), `color_op` ("add"), `src_alpha` ("one"),
`dst_alpha` ("zero"), `alpha_op` ("add"), `write_mask` ("rgba")}]};
`dynamic` ([] dynamic states); `rendering` {`color_formats` ([]),
`depth_format`, `stencil_format`}. Depth/stencil state is used when a depth
or stencil format is given; colour blend state when there are colour formats.

### Execution
| op | fields (default) | notes |
|---|---|---|
| `exec` | `cmds`, `auto_barriers` (true) | allocate, record, submit with a fence, wait, free |
| `record` | `name`, `cmds`, `simultaneous` (false), `auto_barriers` (true) | a named command buffer for `submit` and `run` |
| `submit` | `cbs` ([]), `wait` ([{`semaphore`, `value` (0), `stage` ("all_commands")}]), `signal` ([{`semaphore`, `value` (0)}]), `fence` (none) | one `vkQueueSubmit`; timeline values are chained when a timeline semaphore is involved |

### Commands (`{"cmd": ...}` inside `cmds`)

A subresource `sub` is {`aspect` (the image's; depth for depth/stencil),
`mip` (0), `base_layer` (0), `layers` (1)}; a range is {`aspect` (all),
`base_mip` (0), `mips` (all), `base_layer` (0), `layers` (all)}; offsets are
[x, y, z] (0), extents [w, h, d].

| cmd | fields (default) |
|---|---|
| `begin_rendering` | `area` [x, y, w, h], `layers` (1), `color` [{`view`, `layout` ("general"), `load` ("load"), `store` ("store"), `clear` {`f32` \| `u32` \| `i32`: [4]}, `resolve_view` (none), `resolve_mode` ("average")}], `depth` / `stencil` {`view`, `layout`, `load`, `store`, `clear` {`depth` (1.0), `stencil` (0)}} |
| `end_rendering` | |
| `bind_pipeline` | `pipeline` |
| `bind_vertex_buffers` | `first` (0), `buffers` [{`buffer`, `offset` (0)}] |
| `bind_index_buffer` | `buffer`, `offset` (0), `type` ("uint32") |
| `bind_sets` | `layout`, `bind_point` ("compute"; "graphics"), `first` (0), `sets`, `dynamic_offsets` ([]) |
| `push_constants` | `layout`, `stages` ("all"), `offset` (0), `data` |
| `set_viewport` | `viewports` [[x, y, w, h, min (0), max (1)]] |
| `set_scissor` | `scissors` [[x, y, w, h]] |
| `set_blend_constants` | `values` [r, g, b, a] |
| `set_depth_bias` | `values` [constant, clamp, slope] |
| `set_depth_bounds` | `values` [min, max] |
| `set_stencil_compare_mask`, `set_stencil_write_mask`, `set_stencil_reference` | `face` ("front_and_back"), `value` |
| `set_stencil_op` | `face` ("front_and_back"), `fail`, `pass`, `depth_fail` ("keep"), `compare` ("always") |
| `set_line_width` | `value` (1) |
| `set_cull_mode`, `set_front_face`, `set_primitive_topology`, `set_depth_compare_op` | `value` |
| `set_depth_test_enable`, `set_depth_write_enable`, `set_depth_bounds_test_enable`, `set_stencil_test_enable`, `set_rasterizer_discard_enable`, `set_depth_bias_enable`, `set_primitive_restart_enable` | `value` (true) |
| `draw` | `vertices`, `instances` (1), `first_vertex` (0), `first_instance` (0) |
| `draw_indexed` | `indices`, `instances` (1), `first_index` (0), `vertex_offset` (0), `first_instance` (0) |
| `draw_indirect`, `draw_indexed_indirect` | `buffer`, `offset` (0), `count` (1), `stride` (16 / 20) |
| `dispatch` | `groups` [x, y (1), z (1)] |
| `dispatch_base` | `base` [x, y, z], `groups` [x, y, z] |
| `dispatch_indirect` | `buffer`, `offset` (0) |
| `copy_buffer` | `src`, `dst`, `regions` [[src_offset, dst_offset, size]] |
| `fill_buffer` | `buffer`, `offset` (0), `size` (whole), `value` (u32) |
| `update_buffer` | `buffer`, `offset` (0), `data` |
| `copy_image` | `src`, `dst`, `regions` [{`src_sub`, `src_offset`, `dst_sub`, `dst_offset`, `extent`}] |
| `copy_buffer_to_image`, `copy_image_to_buffer` | `buffer`, `image`, `regions` [{`buffer_offset` (0), `row_length` (0), `image_height` (0), `sub`, `offset`, `extent`}] |
| `blit_image` | `src`, `dst`, `filter` ("nearest"), `regions` [{`src_sub`, `dst_sub`, `src_offsets` [[x, y, z], [x, y, z]], `dst_offsets` [...]}] |
| `resolve_image` | `src`, `dst`, `regions` [{`src_sub`, `src_offset`, `dst_sub`, `dst_offset`, `extent`}] |
| `clear_color_image` | `image`, `color` {`f32` \| `u32` \| `i32`: [4]}, `ranges` (whole image) |
| `clear_depth_stencil_image` | `image`, `depth` (1.0), `stencil` (0), `ranges` (whole image) |
| `clear_attachments` | `attachments` [{`aspect` ("color"), `color_attachment` (0), `clear`}], `rects` [[x, y, w, h, base_layer (0), layers (1)]] |
| `barrier` | `src_stage` / `dst_stage` ("all_commands"), `src_access` ("memory_write"), `dst_access` (["memory_read", "memory_write"]), `by_region` (false), `images` ([{`image`, `old` / `new` ("general"), and the range fields}]) |
| `reset_query_pool` | `pool`, `first` (0), `count` (all) |
| `begin_query` | `pool`, `query` (0), `precise` (false) |
| `end_query` | `pool`, `query` (0) |
| `write_timestamp` | `pool`, `query` (0), `stage` ("bottom_of_pipe") |
| `copy_query_results` | `pool`, `first` (0), `count` (all), `buffer`, `offset` (0), `stride` (8), `flags` ([]) |
| `set_event`, `reset_event` | `event`, `stage` ("all_commands") |
| `wait_events` | `events`, `src_stage` / `dst_stage` ("all_commands") |

### Synchronization and queries
| op | fields (default) | records |
|---|---|---|
| `fence` | `name`, `signaled` (false) | |
| `reset_fence` | `fences` | |
| `wait_fence` | `name`, `fences`, `all` (true), `timeout_ns` (infinite) | the result |
| `fence_status` | `name`, `fence` | the result |
| `semaphore` | `name`, `timeline` (false), `initial` (0) | |
| `signal_semaphore` | `semaphore`, `value` | (host signal) |
| `wait_semaphores` | `name`, `semaphores` [[name, value]], `any` (false), `timeout_ns` (infinite) | the result |
| `semaphore_value` | `name`, `semaphore` | result and value |
| `event` | `name` | (creates a `VkEvent`) |
| `set_event`, `reset_event` | `event` | (host side) |
| `event_status` | `name`, `event` | the result |
| `wait_idle` | `device` (false: `vkQueueWaitIdle`; true: `vkDeviceWaitIdle`) | |
| `query_pool` | `name`, `type`, `count`, `statistics` ([]) | |
| `reset_query_pool` | `pool`, `first` (0), `count` (all) | (host reset, `vkResetQueryPool`) |
| `read_queries` | `name`, `pool`, `first` (0), `count` (all), `flags` (["64", "wait"]; 64-bit is always added) | the result, and per query its values and availability; timestamps only as validity: non-zero, and non-decreasing in query order |

### Output
| op | fields (default) |
|---|---|
| `snapshot` | `name`, `items` (1 to 16): image items {`name`, `image`, `aspect` ("color"; "depth", "stencil"), `mip` (0), `base_layer` (0), `layers` (the rest), `allow`} or buffer items {`name`, `buffer`, `offset` (0), `size` (the rest), `elem` ("u8"), `allow`} |
| `run` | `name`, `iterations`, `warmup` (1), `cmds` or `cb` (a `record`ed buffer), `auto_barriers` (true), `snapshot` {`name`, `items`} |

A `snapshot` waits for the queue to go idle, then reads each item back:
images with `vkCmdCopyImageToBuffer` (tightly packed, `GENERAL` layout, the
whole mip level including all depth slices of a 3D image) into a staging
buffer; buffers through their mapping. Multisampled images must be resolved
first. `elem` is one of `u8 i8 u16 i16 u32 i32 u64 i64 f16 f32 f64` and
decides how the item is compared (`SPEC.md`). `allow` ({"ulp": n} or
{"lsb": n}), set by some cases, is a per-item allowance: differences up to it
count as zero.

**Readback layout of each aspect** (also the layout `upload_image` expects):
colour aspects in the format's own texel layout; `D16_UNORM` depth as 16-bit
words; `D32_SFLOAT` and the depth of `D32_SFLOAT_S8_UINT` as 32-bit floats;
the depth of `X8_D24_UNORM_PACK32` / `D24_UNORM_S8_UINT` (which the reference
does not support) as 32-bit words with the top 8 bits cleared; every stencil
aspect as 8-bit integers.

A `run` is a timed block. vkreplay records `cmds` once (or uses `cb`),
submits it `warmup` times, signals the parent, then submits it `iterations`
times, waiting for each with a fence, then reads back its snapshot. The time
from the parent's go-ahead to the arrival of the snapshot bytes is the
performance measurement; the snapshot is graded like any other.

## What vkreplay writes

`<outdir>/ledger.json`:

- `exit`: `ok` (every op ran), `crash` (your library crashed, exited, or hung
  on exit), `timeout`, or `driver_error` (the case itself is malformed: a
  problem with the case, never with your library).
- `events`, in order: `{"op": "snapshot", "name", "files": {"snap":
  "<name>.ssnap"}, "items": [...]}`, `{"op": "query", "kind", "name",
  "value"}` and `{"op": "run", "name", "wall_seconds"}`.
- `calls`: `[op_index, "vkFunction", "VK_RESULT"]` for every call that returns
  a `VkResult`.
- `notes`: skipped ops and why. `validation` (with `--validate`).

`<outdir>/<name>.ssnap`: `SSNAP1\n`, a little-endian `u32` header length, a
JSON header listing each item (`name`, `kind` color / depth / stencil /
buffer, `format`, `width`, `height`, `depth`, `layers`, `mip`,
`texel_bytes`, `components` with bit widths and numeric formats, `packed`,
`elem`, `offset`, `size`, `missing`, `allow`), then the raw bytes of the
items that are not missing.

The reference's outputs for each public case are in `dev/reference/<case>/`:
its `.ssnap` files, a PNG preview of each snapshot's first colour item, and
its `ledger.json`.
