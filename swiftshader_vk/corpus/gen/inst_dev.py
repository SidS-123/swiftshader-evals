"""Family `inst_dev`: instance/device creation, enumeration, properties,
features, limits, format properties, queue families, memory types
(procedural).

Every check is a comparison with the reference's reported values (`query`,
`enumerate`, `image_format_props` events) and the `VkResult` stream.

  a               versions, extensions, queue families, memory properties
  props_features  every properties and features struct (`"all"`), then a device
                  created with a set of features and extensions
  formats         every format's properties, and image format properties over a
                  table of (format, type, tiling, usage, flags)
  enum_create     two-call enumerations at several capacities, and an instance and
                  device created with extensions enabled

Layers are never enumerated: what the loader finds (the validation layer during
the gate) is not the driver's behaviour.
"""
from __future__ import annotations

from common import Case, pub_name, write_all

FAMILY = "inst_dev"

PUBLIC = [
    {"tag": "a", "variant": "basic",
     "what": {"api_version": True, "device_version": True, "instance_extensions": True,
              "device_extensions": True, "queue_families": True, "memory": True}},
    {"tag": "props_features", "variant": "props_features",
     "features": {"VkPhysicalDeviceFeatures": {"samplerAnisotropy": True, "fillModeNonSolid": True, "independentBlend": True},
                  "VkPhysicalDeviceVulkan12Features": {"timelineSemaphore": True, "bufferDeviceAddress": True},
                  "VkPhysicalDeviceVulkan13Features": {"dynamicRendering": True, "synchronization2": True}},
     "extensions": ["VK_EXT_custom_border_color", "VK_EXT_depth_clip_enable"]},
    {"tag": "formats", "variant": "formats",
     "image_formats": [
         ["R8G8B8A8_UNORM", "2d", "optimal", ["sampled", "color_attachment", "storage"], []],
         ["R8G8B8A8_SRGB", "2d", "linear", ["sampled"], []],
         ["B8G8R8A8_UNORM", "3d", "optimal", ["sampled", "storage"], []],
         ["R16G16B16A16_SFLOAT", "2d", "optimal", ["color_attachment"], ["cube_compatible"]],
         ["D32_SFLOAT_S8_UINT", "2d", "optimal", ["depth_stencil_attachment", "sampled"], []],
         ["BC3_UNORM_BLOCK", "2d", "optimal", ["sampled"], ["block_texel_view_compatible", "mutable_format", "extended_usage"]],
         ["ETC2_R8G8B8A8_UNORM_BLOCK", "3d", "optimal", ["sampled"], []],
         ["R32_UINT", "1d", "optimal", ["storage"], []],
         ["A2B10G10R10_UNORM_PACK32", "2d", "optimal", ["color_attachment", "input_attachment"], []]]},
    {"tag": "enum_create", "variant": "enum_create",
     "capacities": [["instance_extensions", 0], ["instance_extensions", 100], ["device_extensions", 0], ["device_extensions", 40], ["device_extensions", 200],
                    ["physical_devices", 1], ["physical_devices", 3]],
     "instance_extensions": ["VK_KHR_get_physical_device_properties2", "VK_KHR_external_memory_capabilities"],
     "device_extensions": ["VK_EXT_host_query_reset", "VK_EXT_line_rasterization"]},
]


def build(name, p):
    v = p["variant"]
    c = Case(name, FAMILY, "procedural")
    if v == "basic":
        c.instance()
        c.query("info", p["what"])
        c.device()
        c.query("after_device", {"device_version": True, "queue_families": True})
    elif v == "props_features":
        c.instance()
        c.query("props", {"properties": "all", "features": "all"})
        c.device(features=p["features"], extensions=p["extensions"])
        c.buffer("b", 256, ["storage_buffer"])          # the device works
        c.snapshot([{"name": "b", "buffer": "b", "elem": "u32"}])
    elif v == "formats":
        c.instance()
        c.query("formats", {"formats": "all"})
        for k, (fmt, typ, tiling, usage, flags) in enumerate(p["image_formats"]):
            c.op("image_format_props", name=f"ifp{k}", format=fmt, type=typ, tiling=tiling, usage=usage, flags=flags)
    else:
        c.instance(extensions=p["instance_extensions"])
        for k, (what, cap) in enumerate(p["capacities"]):
            c.op("enumerate", name=f"enum{k}_{what}", what=what, capacity=cap)
        c.device(extensions=p["device_extensions"])
        c.query("ext", {"device_extensions": True})
    return c


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
