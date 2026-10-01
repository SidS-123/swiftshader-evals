"""Family `compute_image`: storage images, image load/store, texel buffers (replay).

A compute shader reads a source image (storage image load with a format
qualifier, or a sampled image through texelFetch, or a uniform texel buffer)
and writes a transformed result to a storage image of another format and to a
storage texel buffer, then queries imageSize. The reference has no
shaderStorageImageReadWithoutFormat, so every storage image read declares its
format. Values written to UNORM/float images are exactly representable, so
conversions are exact.
"""
from __future__ import annotations

import numpy as np

from common import compute_case, pub_name, rng, write_all

FAMILY = "compute_image"

# storage format: (VkFormat, GLSL qualifier, GLSL image type, numpy dtype, channels)
FORMATS = {
    "rgba8": ("R8G8B8A8_UNORM", "rgba8", "image2D", np.uint8, 4),
    "rgba8ui": ("R8G8B8A8_UINT", "rgba8ui", "uimage2D", np.uint8, 4),
    "rgba16f": ("R16G16B16A16_SFLOAT", "rgba16f", "image2D", np.float16, 4),
    "rgba32f": ("R32G32B32A32_SFLOAT", "rgba32f", "image2D", np.float32, 4),
    "r32ui": ("R32_UINT", "r32ui", "uimage2D", np.uint32, 1),
    "rg32i": ("R32G32_SINT", "rg32i", "iimage2D", np.int32, 2),
    "r32f": ("R32_SFLOAT", "r32f", "image2D", np.float32, 1),
    "rgba16ui": ("R16G16B16A16_UINT", "rgba16ui", "uimage2D", np.uint16, 4),
}

PUBLIC = [
    {"tag": "load_store", "seed": 601, "src": "rgba8", "dst": "rgba32f", "size": [32, 16], "read": "storage"},
    {"tag": "fetch_int", "seed": 602, "src": "rgba8ui", "dst": "r32ui", "size": [16, 16], "read": "fetch"},
    {"tag": "texel_buffers", "seed": 603, "src": "rgba16f", "dst": "rg32i", "size": [24, 8], "read": "texel"},
]


def src_values(r, fmt, w, h):
    _, _, _, dt, ch = FORMATS[fmt]
    if dt == np.uint8:
        return r.randint(0, 256, size=(h, w, ch)).astype(np.uint8)
    if dt == np.float16:
        return (r.randint(-512, 512, size=(h, w, ch)) / 64).astype(np.float16)
    if dt == np.float32:
        return (r.randint(-2048, 2048, size=(h, w, ch)) / 128).astype(np.float32)
    return r.randint(0, 60000, size=(h, w, ch)).astype(dt)


def build(name, p):
    r = rng(p["seed"])
    w, h = p["size"]
    sfmt, sq, styp, _, sch = FORMATS[p["src"]]
    dfmt, dq, dtyp, _, dch = FORMATS[p["dst"]]
    data = src_values(r, p["src"], w, h)
    skind = "u" if styp.startswith("uimage") else ("i" if styp.startswith("iimage") else "")   # "" = float
    dkind = "u" if dtyp.startswith("uimage") else ("i" if dtyp.startswith("iimage") else "")
    sint = skind != ""
    if p["read"] == "storage":
        decl = f"layout(set = 0, binding = 0, {sq}) uniform readonly {styp} src;"
        read = "imageLoad(src, q)"
    elif p["read"] == "fetch":
        decl = f"layout(set = 0, binding = 0) uniform {skind}texture2D srcTex;\nlayout(set = 0, binding = 3) uniform sampler smp;"
        read = f"texelFetch({skind}sampler2D(srcTex, smp), q, 0)"
    else:
        decl = f"layout(set = 0, binding = 0) uniform {skind}samplerBuffer srcBuf;"
        read = f"texelFetch(srcBuf, q.y * {w} + q.x)"
    dst_scalar = {"u": "uvec4", "i": "ivec4", "": "vec4"}[dkind]
    # transform: exact arithmetic in the destination's type
    if dst_scalar == "vec4":
        xform = f"vec4({read}) * 2.0 + vec4(float(q.x), float(q.y), 0.5, -1.0)"
    elif dst_scalar == "uvec4":
        xform = f"uvec4({read}) * 3u + uvec4(uint(q.x), uint(q.y), pc.k, 1u)"
    else:
        xform = f"ivec4({read}) - ivec4(q.x, q.y, int(pc.k), 7)"
    src = f"""#version 450
layout(local_size_x = 8, local_size_y = 8) in;
{decl}
layout(set = 0, binding = 1, {dq}) uniform writeonly {dtyp} dst;
layout(set = 0, binding = 2) writeonly buffer Sz {{ ivec4 sz[]; }};
layout(set = 0, binding = 4, r32ui) uniform writeonly uimageBuffer tbOut;
layout(push_constant) uniform PC {{ uint k; }} pc;
void main() {{
  ivec2 q = ivec2(gl_GlobalInvocationID.xy);
  if (q.x >= {w} || q.y >= {h}) return;
  {dst_scalar} v = {xform};
  imageStore(dst, q, v);
  imageStore(tbOut, q.y * {w} + q.x, uvec4(uint(q.x * 7 + q.y) ^ pc.k));
  if (q == ivec2(0)) sz[0] = ivec4(imageSize(dst), {"imageSize(src)" if p["read"] == "storage" else "ivec2(0)"});
}}
"""
    bindings = [{"binding": 1, "type": "storage_image", "stages": ["compute"]},
                {"binding": 4, "type": "storage_texel_buffer", "stages": ["compute"]}]
    writes = [{"binding": 1, "type": "storage_image", "images": [{"view": "dst_v", "layout": "general"}]},
              {"binding": 4, "type": "storage_texel_buffer", "texel_buffers": ["tb_out_v"]}]
    if p["read"] == "storage":
        bindings.append({"binding": 0, "type": "storage_image", "stages": ["compute"]})
        writes.append({"binding": 0, "type": "storage_image", "images": [{"view": "src_v", "layout": "general"}]})
    elif p["read"] == "fetch":
        bindings += [{"binding": 0, "type": "sampled_image", "stages": ["compute"]},
                     {"binding": 3, "type": "sampler", "stages": ["compute"]}]
        writes += [{"binding": 0, "type": "sampled_image", "images": [{"view": "src_v", "layout": "general"}]},
                   {"binding": 3, "type": "sampler", "images": [{"sampler": "s"}]}]
    else:
        bindings.append({"binding": 0, "type": "uniform_texel_buffer", "stages": ["compute"]})
        writes.append({"binding": 0, "type": "uniform_texel_buffer", "texel_buffers": ["tb_in_v"]})

    def setup(c):
        usage = {"storage": ["storage"], "fetch": ["sampled"], "texel": []}[p["read"]]
        if p["read"] != "texel":
            c.image("srcimg", sfmt, [w, h], usage)
            c.upload_image("srcimg", array=data)
            c.view("src_v", "srcimg")
        else:
            c.buffer("tb_in", data.nbytes, ["uniform_texel_buffer"])
            c.upload("tb_in", array=data.reshape(-1))
            c.op("buffer_view", name="tb_in_v", buffer="tb_in", format=sfmt)
        if p["read"] == "fetch":
            c.sampler("s")
        c.image("dstimg", dfmt, [w, h], ["storage"])
        c.view("dst_v", "dstimg")
        c.buffer("tb_out", w * h * 4, ["storage_texel_buffer"])
        c.op("buffer_view", name="tb_out_v", buffer="tb_out", format="R32_UINT")

    items = [{"name": "dst", "image": "dstimg"}, {"name": "texel_out", "buffer": "tb_out", "elem": "u32"},
             {"name": "sizes", "buffer": "sz", "elem": "i32"}]
    return compute_case(name, FAMILY, source=src, push_bytes=4,
                        buffers=[{"name": "sz", "size": 16, "binding": 2}],
                        extra_setup=setup, extra_bindings=bindings, extra_writes=writes,
                        dispatches=[{"groups": [(w + 7) // 8, (h + 7) // 8, 1], "push": {"u32": [1]}, "snapshot": items},
                                    {"groups": [(w + 7) // 8, (h + 7) // 8, 1], "push": {"u32": [p["seed"] % 9 + 3]},
                                     "snapshot": items}])


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
