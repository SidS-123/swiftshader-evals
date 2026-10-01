"""Family `errors_robust`: error results of valid calls, and out-of-bounds behaviour
under `robustBufferAccess` / `robustImageAccess` (procedural).

The reference does not expose VK_EXT_robustness2; the out-of-bounds behaviour graded
is that of the core robustness features the profile reports (reads return zero,
writes are discarded), discoverable with the `oracle`.

  errors        `VK_ERROR_FORMAT_NOT_SUPPORTED` from image format queries,
                `VK_INCOMPLETE` from short enumerations, an image larger than
                `maxMemoryAllocationSize` (but within the heap)
  bad_device    a device created with an unsupported feature
                (`VK_ERROR_FEATURE_NOT_PRESENT`): every later op is skipped
  robust_buffer storage/uniform/texel buffers read and written past their bound range
  robust_image  storage-image loads and stores and texelFetch outside the image
"""
from __future__ import annotations

import numpy as np

from common import Case, pub_name, rng, write_all

FAMILY = "errors_robust"
N = 256

PUBLIC = [
    {"tag": "errors", "seed": 1801, "variant": "errors",
     "format_props": [  # (format, type, tiling, usage, flags)
         ["BC1_RGBA_UNORM_BLOCK", "2d", "optimal", ["storage"], []],
         ["D32_SFLOAT", "3d", "optimal", ["depth_stencil_attachment"], []],
         ["R8G8B8A8_UNORM", "2d", "linear", ["color_attachment"], []],
         ["E5B9G9R9_UFLOAT_PACK32", "2d", "optimal", ["color_attachment"], []],
         ["R32G32B32_SFLOAT", "2d", "optimal", ["sampled"], []],
         ["R16G16B16A16_SFLOAT", "2d", "optimal", ["sampled", "storage"], ["cube_compatible"]]],
     "capacities": {"instance_extensions": 2, "device_extensions": 5, "physical_devices": 0},
     "big_image": ["R32G32B32A32_SFLOAT", [16384, 6144]]},
    {"tag": "bad_device", "seed": 1804, "variant": "bad_device", "bad_feature": ["VkPhysicalDeviceFeatures", "geometryShader"]},
    {"tag": "robust_buffer", "seed": 1802, "variant": "robust_buffer", "range": 96, "span": 160},
    {"tag": "robust_image", "seed": 1803, "variant": "robust_image", "size": [24, 20], "reach": 12},
]


def v_errors(name, p, r):
    c = Case(name, FAMILY, "procedural")
    c.instance()
    for k, (fmt, typ, tiling, usage, flags) in enumerate(p["format_props"]):
        c.op("image_format_props", name=f"fmt{k}", format=fmt, type=typ, tiling=tiling, usage=usage, flags=flags)
    for what, cap in p["capacities"].items():
        c.op("enumerate", name=f"enum_{what}", what=what, capacity=cap)
    c.device()
    fmt, extent = p["big_image"]
    c.image("big", fmt, extent, ["sampled"], auto_usage=False)
    c.buffer("after", 64, ["storage_buffer"])          # still created after the failure
    c.snapshot([{"name": "after", "buffer": "after", "elem": "u32"}])
    return c


def v_bad_device(name, p, r):
    c = Case(name, FAMILY, "procedural")
    c.instance()
    struct, field = p["bad_feature"]
    c.device(features={struct: {field: True}})
    c.buffer("b", 64, ["storage_buffer"])               # skipped: no device
    c.query("after", {"api_version": True})
    return c


ROBUST_FEATURES = {"VkPhysicalDeviceFeatures": {"robustBufferAccess": True}}


def v_robust_buffer(name, p, r):
    rng_, span = p["range"], p["span"]                   # bound range in u32 elements, indices touched
    c = Case(name, FAMILY, "procedural")
    c.instance()
    c.device(features=ROBUST_FEATURES)
    c.buffer("src", N * 4, ["storage_buffer", "uniform_buffer", "uniform_texel_buffer"])
    c.upload("src", array=r.randint(1, 2**31, size=N).astype(np.uint32))
    c.buffer("dst", N * 4, ["storage_buffer"])
    c.upload("dst", array=np.full(N, 0xA5A5A5A5, np.uint32))
    c.buffer("out", 3 * span * 4, ["storage_buffer"])
    c.op("buffer_view", name="tb", buffer="src", format="R32_UINT", offset=256, range=rng_ * 4)
    c.shader("cs", f"""#version 450
layout(local_size_x = 32) in;
layout(std430, set = 0, binding = 0) readonly buffer S {{ uint s[]; }};
layout(std140, set = 0, binding = 1) uniform U {{ uvec4 u[{N // 4}]; }};   // bound range is smaller
layout(set = 0, binding = 2) uniform utextureBuffer tb;
layout(std430, set = 0, binding = 3) writeonly buffer D {{ uint d[]; }};
layout(std430, set = 0, binding = 4) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{ uint dyn; }} pc;   // keeps the indices opaque to the compiler
void main() {{
  uint i = gl_GlobalInvocationID.x + pc.dyn;
  o[i] = s[i];
  o[{span}u + i] = u[i / 4u][i % 4u];
  o[{2 * span}u + i] = texelFetch(tb, int(i)).x;
  d[i] = i * 3u + 1u;
}}
""", "comp")
    c.desc_layout("dl", [{"binding": b, "type": t, "stages": ["compute"]} for b, t in
                         enumerate(["storage_buffer", "uniform_buffer", "uniform_texel_buffer",
                                    "storage_buffer", "storage_buffer"])])
    c.pipeline_layout("pl", ["dl"], push_constants=[{"stages": ["compute"], "offset": 0, "size": 4}])
    c.desc_set("ds", "dl", [
        {"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": "src", "offset": 256, "range": rng_ * 4}]},
        {"binding": 1, "type": "uniform_buffer", "buffers": [{"buffer": "src", "offset": 512, "range": rng_ * 4}]},
        {"binding": 2, "type": "uniform_texel_buffer", "texel_buffers": ["tb"]},
        {"binding": 3, "type": "storage_buffer", "buffers": [{"buffer": "dst", "offset": 256, "range": rng_ * 4}]},
        {"binding": 4, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    c.exec([{"cmd": "bind_pipeline", "pipeline": "p"}, {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "push_constants", "layout": "pl", "stages": ["compute"], "data": {"u32": [0]}},
            {"cmd": "dispatch", "groups": [span // 32]}])
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}, {"name": "dst", "buffer": "dst", "elem": "u32"}])
    return c


def v_robust_image(name, p, r):
    W, H = p["size"]
    reach = p["reach"]
    c = Case(name, FAMILY, "procedural")
    c.instance()
    c.device(features={"VkPhysicalDeviceVulkan13Features": {"robustImageAccess": True}})
    c.image("simg", "R32_UINT", [W, H], ["storage"])
    c.upload_image("simg", array=r.randint(1, 2**31, size=W * H).astype(np.uint32))
    c.image("timg", "R8G8B8A8_UNORM", [W, H], ["sampled"])
    c.upload_image("timg", array=r.randint(0, 256, size=W * H * 4).astype(np.uint8))
    c.view("simg_v", "simg")
    c.view("timg_v", "timg")
    c.image("dimg", "R32_UINT", [W, H], ["storage"])
    c.view("dimg_v", "dimg")
    c.exec([{"cmd": "clear_color_image", "image": "dimg", "color": {"u32": [7, 0, 0, 0]}}])
    OW, OH = W + 2 * reach, H + 2 * reach
    c.buffer("out", OW * OH * 4 * 2, ["storage_buffer"])
    c.shader("cs", f"""#version 450
#extension GL_EXT_samplerless_texture_functions : require
layout(local_size_x = 8, local_size_y = 8) in;
layout(set = 0, binding = 0, r32ui) uniform readonly uimage2D simg;
layout(set = 0, binding = 1) uniform texture2D timg;
layout(set = 0, binding = 2, r32ui) uniform writeonly uimage2D dimg;
layout(std430, set = 0, binding = 3) writeonly buffer O {{ uint o[]; }};
void main() {{
  ivec2 q = ivec2(gl_GlobalInvocationID.xy);
  if (q.x >= {OW} || q.y >= {OH}) return;
  ivec2 t = q - ivec2({reach});
  uint k = uint(q.y * {OW} + q.x);
  o[k] = imageLoad(simg, t).x;
  o[{OW * OH}u + k] = packUnorm4x8(texelFetch(timg, t, 0));
  imageStore(dimg, t * 2 - ivec2({W // 2}, {H // 2}), uvec4(k));
}}
""", "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_image", "stages": ["compute"]},
                         {"binding": 1, "type": "sampled_image", "stages": ["compute"]},
                         {"binding": 2, "type": "storage_image", "stages": ["compute"]},
                         {"binding": 3, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_image", "images": [{"view": "simg_v"}]},
                            {"binding": 1, "type": "sampled_image", "images": [{"view": "timg_v"}]},
                            {"binding": 2, "type": "storage_image", "images": [{"view": "dimg_v"}]},
                            {"binding": 3, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    c.exec([{"cmd": "bind_pipeline", "pipeline": "p"}, {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "dispatch", "groups": [-(-OW // 8), -(-OH // 8)]}])
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}, {"name": "dimg", "image": "dimg"}])
    return c


VARIANTS = {"errors": v_errors, "bad_device": v_bad_device, "robust_buffer": v_robust_buffer,
            "robust_image": v_robust_image}


def build(name, p):
    return VARIANTS[p["variant"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
