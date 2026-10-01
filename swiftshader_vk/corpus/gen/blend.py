"""Family `blend`: blend factors and operations, blend constants, write masks, float /
sRGB / UNORM targets, advanced blend operations (replay).

Each pass draws semi-transparent triangles over what earlier passes left, with
its own blend state (drawn from the parameter tables), so every pass's snapshot
shows one blend configuration composed over the last. Multiple targets get
different states (independent blending). The advanced-blend pass uses
VK_EXT_blend_operation_advanced (the fragment shader declares
blend_support_all_equations). The reference does not support
`advancedBlendCoherentOperations`, and without it advanced blending of
primitives that overlap within one draw is undefined, so the advanced passes
draw non-overlapping triangles (one per grid cell); each pass is its own
rendering scope, separated by vkreplay's full barrier.
"""
from __future__ import annotations

import numpy as np

from common import pub_name, random_triangles, raster_scene, rng, write_all

FAMILY = "blend"

FACTORS = ["zero", "one", "src_color", "one_minus_src_color", "dst_color", "one_minus_dst_color", "src_alpha",
           "one_minus_src_alpha", "dst_alpha", "one_minus_dst_alpha", "constant_color", "one_minus_constant_color",
           "constant_alpha", "one_minus_constant_alpha", "src_alpha_saturate"]
OPS = ["add", "subtract", "reverse_subtract", "min", "max"]
ADVANCED = ["multiply_ext", "screen_ext", "overlay_ext", "darken_ext", "lighten_ext", "difference_ext", "exclusion_ext"]

PUBLIC = [
    {"tag": "factors_unorm", "seed": 1001, "targets": ["R8G8B8A8_UNORM"], "passes": 4, "n": 25},
    {"tag": "mrt_float_srgb", "seed": 1002, "targets": ["R16G16B16A16_SFLOAT", "R8G8B8A8_SRGB", "B8G8R8A8_UNORM"],
     "passes": 3, "n": 20},
    {"tag": "masks_constants", "seed": 1003, "targets": ["R8G8B8A8_UNORM", "R5G6B5_UNORM_PACK16"], "passes": 3,
     "n": 20, "masks": True},
    {"tag": "advanced", "seed": 1004, "targets": ["R8G8B8A8_UNORM"], "passes": 2, "n": 20, "advanced": True},
]

MRT_FS = """#version 450
layout(location = 0) in vec4 v_col;
{outs}
void main() {{
{writes}
}}
"""
ADV_FS = """#version 450
#extension GL_KHR_blend_equation_advanced : require
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
layout(blend_support_all_equations) out;
void main() { o = v_col; }
"""


def mrt_fs(k):
    outs = "\n".join(f"layout(location = {i}) out vec4 o{i};" for i in range(k))
    writes = "\n".join(f"  o{i} = vec4(v_col.{'rgba'[i % 4]}{'gbar'[i % 4]}{'barg'[i % 4]}, v_col.a);" for i in range(k))
    return MRT_FS.format(outs=outs, writes=writes)


def random_state(r, mask=False):
    s = {"enable": True, "src_color": r.choice(FACTORS), "dst_color": r.choice(FACTORS), "color_op": r.choice(OPS),
         "src_alpha": r.choice(FACTORS), "dst_alpha": r.choice(FACTORS), "alpha_op": r.choice(OPS)}
    if mask:
        s["write_mask"] = "".join(c for c in "rgba" if r.rand() < 0.6) or "g"
    return s


def cell_triangles(r, n, alpha_lo=0.2):
    """n triangles, each strictly inside its own grid cell (no two share a pixel)."""
    k = int(np.ceil(np.sqrt(n)))
    cells = r.choice(k * k, size=n, replace=False)
    tris = []
    for c in cells:
        x0, y0 = -1.0 + 2.0 * (c % k) / k, -1.0 + 2.0 * (c // k) / k
        w = 2.0 / k
        col = (*r.uniform(0, 1, 3), float(r.uniform(alpha_lo, 1.0)))
        z = float(r.uniform(0.05, 0.95))
        for _ in range(3):
            tris.append(((x0 + w * float(r.uniform(0.1, 0.9)), y0 + w * float(r.uniform(0.1, 0.9)), z, 1.0), col))
    return tris


def build(name, p):
    r = rng(p["seed"])
    targets = p["targets"]
    passes = []
    features, exts = {"VkPhysicalDeviceFeatures": {"independentBlend": True}}, None
    for i in range(p["passes"]):
        tris = random_triangles(r, p["n"], alpha=float(r.uniform(0.2, 0.9)), spread=0.7)
        # alpha varies per triangle: rewrite the colour alpha
        tris = [(pos, col[:3] + (float(r.uniform(0.1, 1.0)),)) for pos, col in tris]
        blend = {"constants": [float(x) for x in r.uniform(0, 1, 4)],
                 "attachments": [random_state(r, p.get("masks", False)) for _ in targets]}
        passes.append({"verts": tris, "pipeline": {"blend": blend}})
    fs = mrt_fs(len(targets))
    if p.get("advanced"):
        exts = ["VK_EXT_blend_operation_advanced"]
        for op in r.choice(ADVANCED, 2, replace=False):
            tris = cell_triangles(r, p["n"])
            passes.append({"verts": tris, "fs": ADV_FS,
                           "pipeline": {"blend": {"attachments": [{"enable": True, "color_op": str(op),
                                                                   "alpha_op": str(op)}]}}})
    return raster_scene(name, FAMILY, passes=passes, targets=tuple(targets), fs=fs, features=features,
                        extensions=exts, clear=(0.2, 0.4, 0.6, 0.8))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
