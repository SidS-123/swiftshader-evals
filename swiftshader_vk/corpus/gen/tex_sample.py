"""Family `tex_sample`: texture sampling -- filters, mipmaps, LOD, address modes, borders,
depth compare, cube / 3D / array images, gather, texel fetch and queries,
unnormalized coordinates, view swizzles and formats (replay).

Each case draws textured quads in two or three passes that change the texture
coordinates (magnification, minification, out-of-range) or a push-constant
parameter (explicit LOD, layer, compare reference). Texture coordinates get
the `texcoord_ulp` perturbation. Anisotropic filtering is not exercised: its
result is implementation-defined beyond what a comparison can grade.
"""
from __future__ import annotations

import numpy as np

from common import pub_name, quad, rng, texture_scene, upload_mips, write_all

FAMILY = "tex_sample"

HEAD = """#version 450
layout(location = 0) in vec4 v_uv;
layout(location = 0) out vec4 o;
layout(push_constant) uniform PC { vec4 p; } pc;
"""
FS = {
    "plain": HEAD + "layout(set = 0, binding = 0) uniform sampler2D t;\nvoid main() { o = texture(t, v_uv.xy); }\n",
    "lod": HEAD + "layout(set = 0, binding = 0) uniform sampler2D t;\n"
                  "void main() { o = textureLod(t, v_uv.xy, pc.p.x + v_uv.x * pc.p.y); }\n",
    "bias": HEAD + "layout(set = 0, binding = 0) uniform sampler2D t;\nvoid main() { o = texture(t, v_uv.xy * pc.p.z, pc.p.x); }\n",
    "shadow": HEAD + "layout(set = 0, binding = 0) uniform sampler2DShadow t;\n"
                     "void main() { float s = texture(t, vec3(v_uv.xy, pc.p.x + v_uv.x * pc.p.y)); o = vec4(s, 1.0 - s, s * 0.5, 1.0); }\n",
    "cube": HEAD + "layout(set = 0, binding = 0) uniform samplerCube t;\n"
                   "void main() { vec2 a = (v_uv.xy * 2.0 - 1.0) * vec2(3.14159, 1.5); "
                   "o = texture(t, vec3(cos(a.x) * cos(a.y), sin(a.y), sin(a.x) * cos(a.y)) + pc.p.xyz); }\n",
    "tex3d": HEAD + "layout(set = 0, binding = 0) uniform sampler3D t;\nvoid main() { o = texture(t, vec3(v_uv.xy, pc.p.x)); }\n",
    "array": HEAD + "layout(set = 0, binding = 0) uniform sampler2DArray t;\n"
                    "void main() { o = texture(t, vec3(v_uv.xy, pc.p.x + floor(v_uv.x * 2.0))); }\n",
    "gather": HEAD + "layout(set = 0, binding = 0) uniform sampler2D t;\n"
                     "void main() { int c = int(pc.p.x); vec4 g = c == 0 ? textureGather(t, v_uv.xy, 0) : "
                     "c == 1 ? textureGather(t, v_uv.xy, 1) : c == 2 ? textureGather(t, v_uv.xy, 2) : "
                     "textureGather(t, v_uv.xy, 3); "
                     "o = g * 0.5 + textureGatherOffset(t, v_uv.xy, ivec2(1, -1), 2) * 0.5; }\n",
    "fetch": HEAD + "layout(set = 0, binding = 0) uniform sampler2D t;\n"
                    "void main() { ivec2 sz = textureSize(t, int(pc.p.x)); ivec2 q = ivec2(v_uv.xy * vec2(sz)); "
                    "o = texelFetch(t, clamp(q, ivec2(0), sz - 1), int(pc.p.x)) + "
                    "vec4(float(textureQueryLevels(t)) / 16.0, float(sz.x) / 64.0, 0.0, 0.0) + "
                    "textureOffset(t, v_uv.xy, ivec2(-2, 3)) * 0.25; }\n",
    "unnorm": HEAD + "layout(set = 0, binding = 0) uniform sampler2D t;\n"
                     "void main() { o = textureLod(t, v_uv.xy * pc.p.xy, 0.0); }\n",
}

PUBLIC = [
    {"tag": "filters", "seed": 1201, "variant": "filters", "tex": [16, 16], "mag": "linear", "min": "nearest"},
    {"tag": "mip_lod", "seed": 1202, "variant": "mip_lod", "tex": [64, 32], "mipmap": "linear", "min_lod": 0.5, "max_lod": 4.0},
    {"tag": "address_border", "seed": 1203, "variant": "address", "modes": ["mirrored_repeat", "clamp_to_border", "repeat"],
     "border": "float_opaque_white"},
    {"tag": "shadow", "seed": 1204, "variant": "shadow", "compare": "greater_or_equal"},
    {"tag": "array", "seed": 1205, "variant": "array", "layers": 4},
    {"tag": "fetch_swizzle_srgb", "seed": 1206, "variant": "fetch", "swizzle": ["b", "r", "one", "g"],
     "format": "R8G8B8A8_SRGB"},
    {"tag": "cube", "seed": 1207, "variant": "cube"},
    {"tag": "tex3d", "seed": 1208, "variant": "tex3d"},
    {"tag": "gather", "seed": 1209, "variant": "gather"},
    {"tag": "unnormalized", "seed": 1210, "variant": "unnorm"},
]


def tex2d(r, w, h, mips=1, fmt="R8G8B8A8_UNORM", smooth=False):
    def setup(c):
        c.image("tex", fmt, [w, h], ["sampled"], mips=mips)
        upload_mips(c, r, w=w, h=h, mips=mips, smooth=smooth)
    return setup


def build(name, p):
    r = rng(p["seed"])
    v = p["variant"]
    feats = None
    if v == "filters":
        w, h = p["tex"]
        passes = [{"quads": [quad(0, 0, 1, 1)]},                           # mild magnification
                  {"quads": [quad(-0.3, 0.1, 2.7, 3.4, -1, -1, 0.2, 0.3)]},  # minification, partial quad
                  {"quads": [quad(0.31, 0.27, 0.47, 0.44, -0.4, -0.8, 1, 1)]}]  # strong magnification
        return texture_scene(name, FAMILY, tex_setup=tex2d(r, w, h, smooth=True), fs=FS["plain"], passes=passes,
                             sampler={"mag": p["mag"], "min": p["min"], "address_u": "repeat", "address_v": "repeat"})
    if v == "mip_lod":
        w, h = p["tex"]
        mips = int(np.log2(max(w, h))) + 1
        sampler = {"mag": "linear", "min": "linear", "mipmap": p["mipmap"], "min_lod": p["min_lod"],
                   "max_lod": p["max_lod"], "lod_bias": 0.25}
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.0, 5.0, 1.0, 0.0]}},
                  {"quads": [quad(0, 0, 1, 1)], "push": {"f32": [1.5, -1.0, 1.0, 0.0]}}]
        c = texture_scene(name, FAMILY, tex_setup=tex2d(r, w, h, mips=mips), fs=FS["lod"], passes=passes,
                          sampler=sampler, push_bytes=16)
        return c
    if v == "address":
        passes = []
        sampler = {"mag": "linear", "min": "linear", "address_u": p["modes"][0], "address_v": p["modes"][1],
                   "border": p["border"]}
        passes = [{"quads": [quad(-1.4, -0.8, 2.3, 1.9)]}, {"quads": [quad(-3.1, 2.2, 0.4, -1.7, -1, -1, 0.5, 0.6)]}]
        return texture_scene(name, FAMILY, tex_setup=tex2d(r, 8, 8), fs=FS["plain"], passes=passes, sampler=sampler)
    if v == "shadow":
        def setup(c):
            c.image("tex", "D32_SFLOAT", [16, 16], ["sampled"])
            c.upload_image("tex", array=r.uniform(0, 1, (16, 16)).astype(np.float32), aspect=["depth"])
        sampler = {"mag": "linear", "min": "linear", "compare": p["compare"], "address_u": "clamp_to_edge",
                   "address_v": "clamp_to_edge"}
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.2, 0.6, 0.0, 0.0]}},
                  {"quads": [quad(0.1, 0.9, 0.8, 0.1)], "push": {"f32": [0.7, -0.4, 0.0, 0.0]}}]
        return texture_scene(name, FAMILY, tex_setup=setup, fs=FS["shadow"], passes=passes, sampler=sampler,
                             push_bytes=16)
    if v == "array":
        layers = p["layers"]

        def setup(c):
            c.image("tex", "R8G8B8A8_UNORM", [8, 8], ["sampled"], layers=layers)
            upload_mips(c, r, w=8, h=8, layers=layers)
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.0, 0, 0, 0]}},
                  {"quads": [quad(0.2, -0.3, 1.7, 1.1)], "push": {"f32": [float(layers - 2), 0, 0, 0]}}]
        return texture_scene(name, FAMILY, tex_setup=setup, fs=FS["array"], passes=passes,
                             sampler={"mag": "linear", "min": "nearest"}, view={"view_type": "2d_array"}, push_bytes=16)
    if v == "fetch":
        def setup(c):
            c.image("tex", p["format"], [16, 16], ["sampled"], mips=3)
            upload_mips(c, r, w=16, h=16, mips=3)
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.0, 0, 0, 0]}},
                  {"quads": [quad(0.1, 0.0, 0.9, 1.0)], "push": {"f32": [1.0, 0, 0, 0]}}]
        return texture_scene(name, FAMILY, tex_setup=setup, fs=FS["fetch"], passes=passes,
                             sampler={"mag": "nearest", "min": "nearest", "mipmap": "nearest"},
                             view={"swizzle": p["swizzle"]}, push_bytes=16)
    if v == "cube":
        def setup(c):
            c.image("tex", "R8G8B8A8_UNORM", [8, 8], ["sampled"], layers=6, flags=["cube_compatible"])
            upload_mips(c, r, w=8, h=8, layers=6)
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.0, 0.0, 0.0, 0]}},
                  {"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.3, -0.2, 0.1, 0]}}]
        return texture_scene(name, FAMILY, tex_setup=setup, fs=FS["cube"], passes=passes,
                             sampler={"mag": "linear", "min": "linear"}, view={"view_type": "cube"}, push_bytes=16)
    if v == "tex3d":
        def setup(c):
            c.image("tex", "R8G8B8A8_UNORM", [8, 8, 4], ["sampled"], type="3d")
            c.upload_image("tex", array=r.randint(0, 256, size=(4, 8, 8, 4)).astype(np.uint8))
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.3, 0, 0, 0]}},
                  {"quads": [quad(-0.5, 0, 1.5, 1)], "push": {"f32": [0.81, 0, 0, 0]}}]
        return texture_scene(name, FAMILY, tex_setup=setup, fs=FS["tex3d"], passes=passes,
                             sampler={"mag": "linear", "min": "linear", "address_w": "clamp_to_edge"}, push_bytes=16)
    if v == "gather":
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [0.0, 0, 0, 0]}},
                  {"quads": [quad(0.3, 0.2, 1.3, 0.9)], "push": {"f32": [3.0, 0, 0, 0]}}]
        return texture_scene(name, FAMILY, tex_setup=tex2d(r, 16, 16), fs=FS["gather"], passes=passes,
                             sampler={"mag": "linear", "min": "linear"}, push_bytes=16)
    if v == "unnorm":
        passes = [{"quads": [quad(0, 0, 1, 1)], "push": {"f32": [16.0, 16.0, 0, 0]}},
                  {"quads": [quad(0.2, 0.1, 0.7, 0.9)], "push": {"f32": [20.0, 12.0, 0, 0]}}]
        return texture_scene(name, FAMILY, tex_setup=tex2d(r, 16, 16), fs=FS["unnorm"], passes=passes,
                             sampler={"mag": "linear", "min": "linear", "unnormalized": True, "address_u": "clamp_to_edge",
                                      "address_v": "clamp_to_edge", "max_lod": 0.0}, push_bytes=16)
    raise KeyError(v)


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
