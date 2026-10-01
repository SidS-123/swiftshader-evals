"""Family `depth_stencil`: depth compare ops, depth write, depth bounds, depth bias,
stencil ops, compare/write masks, reference values, depth formats (replay).

Formats are those the reference supports as attachments: D16_UNORM, D32_SFLOAT,
D32_SFLOAT_S8_UINT, S8_UINT. Each pass changes the depth/stencil state (through
dynamic state for stencil reference and masks) and draws overlapping
triangles; the depth and stencil aspects are snapshotted with the colour.
"""
from __future__ import annotations

from common import pub_name, random_triangles, raster_scene, rng, write_all

FAMILY = "depth_stencil"

COMPARE = ["never", "less", "equal", "less_or_equal", "greater", "not_equal", "greater_or_equal", "always"]
STENCIL_OPS = ["keep", "zero", "replace", "increment_and_clamp", "decrement_and_clamp", "invert",
               "increment_and_wrap", "decrement_and_wrap"]

PUBLIC = [
    {"tag": "depth_compare_d32", "seed": 1101, "format": "D32_SFLOAT", "passes": 4, "n": 30, "stencil": False},
    {"tag": "depth_d16_bias_bounds", "seed": 1102, "format": "D16_UNORM", "passes": 3, "n": 30, "stencil": False,
     "bounds": True, "bias": True},
    {"tag": "stencil_d32s8", "seed": 1103, "format": "D32_SFLOAT_S8_UINT", "passes": 4, "n": 30, "stencil": True},
    {"tag": "stencil_only_s8", "seed": 1104, "format": "S8_UINT", "passes": 3, "n": 30, "stencil": True},
]


def stencil_face(r):
    return {"fail": r.choice(STENCIL_OPS), "pass": r.choice(STENCIL_OPS), "depth_fail": r.choice(STENCIL_OPS),
            "compare": r.choice(COMPARE), "compare_mask": int(r.randint(1, 256)), "write_mask": int(r.randint(1, 256)),
            "reference": int(r.randint(0, 256))}


def build(name, p):
    r = rng(p["seed"])
    fmt = p["format"]
    has_depth = fmt != "S8_UINT"
    passes = []
    features = {"VkPhysicalDeviceFeatures": {"depthBounds": True}} if p.get("bounds") else None
    for i in range(p["passes"]):
        ds = {}
        if has_depth:
            ds.update({"test": True, "write": bool(r.rand() < 0.8), "compare": COMPARE[(i * 3 + 1) % 8] if i else "less"})
            if p.get("bounds") and i > 0:
                lo = float(r.uniform(0.1, 0.4))
                ds["bounds"] = [lo, lo + float(r.uniform(0.2, 0.5))]
        if p["stencil"]:
            front = stencil_face(r)
            back = stencil_face(r)
            ds["stencil"] = {"front": front, "back": back}
        pipe = {"depth_stencil": ds, "rasterization": {"cull": "none"}}
        pre = []
        if p["stencil"] and i % 2 == 1:
            pipe["dynamic"] = ["stencil_reference", "stencil_compare_mask", "stencil_write_mask"]
            pre = [{"cmd": "set_stencil_reference", "face": "front_and_back", "value": int(r.randint(0, 256))},
                   {"cmd": "set_stencil_compare_mask", "face": "front", "value": int(r.randint(1, 256))},
                   {"cmd": "set_stencil_compare_mask", "face": "back", "value": 255},
                   {"cmd": "set_stencil_write_mask", "face": "front_and_back", "value": int(r.randint(1, 256))}]
        if p.get("bias") and i > 0:
            pipe["rasterization"]["depth_bias"] = [float(r.choice([-8.0, 2.0, 16.0])), 0.0, float(r.uniform(-2, 2))]
        tris = random_triangles(r, p["n"], spread=0.7, z_range=(0.05, 0.95))
        # both windings, so both stencil faces are exercised
        tris = [v for k in range(0, len(tris), 3)
                for v in (tris[k:k + 3] if (k // 3) % 2 else [tris[k], tris[k + 2], tris[k + 1]])]
        passes.append({"verts": tris, "pipeline": pipe, "pre": pre})
    return raster_scene(name, FAMILY, passes=passes, depth=fmt, stencil=p["stencil"] and has_depth,
                        features=features, clear_depth=0.75 if has_depth else 1.0, clear_stencil=int(r.randint(0, 256)))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
