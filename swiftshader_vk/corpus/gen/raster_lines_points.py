"""Family `raster_lines_points`: line lists and strips, points and point sizes,
gl_PointCoord (replay).

The reference supports only 1-pixel lines (lineWidthRange [1, 1], strictLines)
and points up to 1023 pixels. Lines are drawn with depth testing in a later pass;
points take their size from a vertex attribute and shade with gl_PointCoord.
"""
from __future__ import annotations

import numpy as np

from common import pub_name, raster_scene, rng, write_all

FAMILY = "raster_lines_points"

POINT_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_col;
void main() { gl_Position = vec4(pos.xyz, 1.0); gl_PointSize = pos.w; v_col = col; }
"""
POINT_FS = """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
void main() { o = vec4(v_col.rgb * (1.0 - 0.5 * gl_PointCoord.x), gl_PointCoord.y); }
"""
LINE_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_col;
void main() { gl_Position = pos; gl_PointSize = 1.0; v_col = col; }
"""

PUBLIC = [
    {"tag": "lines", "seed": 901, "lines": [60, 40], "strip": 30, "size": [64, 64]},
    {"tag": "points", "seed": 902, "points": [50, 30], "max_size": 9.0, "size": [64, 64]},
    {"tag": "mixed", "seed": 903, "lines": [30], "strip": 20, "points": [40], "max_size": 17.0, "size": [96, 64]},
]


def line_verts(r, n, z=(0.2, 0.8)):
    vs = []
    for _ in range(n):
        col = tuple(r.uniform(0, 1, 3)) + (1.0,)
        zz = r.uniform(*z)
        for _ in range(2):
            vs.append(((r.uniform(-1.1, 1.1), r.uniform(-1.1, 1.1), zz, 1.0), col))
    return vs


def build(name, p):
    r = rng(p["seed"])
    passes = []
    for k, n in enumerate(p.get("lines", [])):
        passes.append({"verts": line_verts(r, n), "vs": LINE_VS,
                       "pipeline": {"topology": "line_list",
                                    "depth_stencil": {"test": k > 0, "write": True, "compare": "less"}}})
    if p.get("strip"):
        strip = [((r.uniform(-1, 1), r.uniform(-1, 1), 0.3, 1.0), tuple(r.uniform(0, 1, 3)) + (1.0,))
                 for _ in range(p["strip"])]
        passes.append({"verts": strip, "vs": LINE_VS,
                       "pipeline": {"topology": "line_strip", "depth_stencil": {"test": True, "write": True,
                                                                                "compare": "less_or_equal"}}})
    for k, n in enumerate(p.get("points", [])):
        pts = []
        for _ in range(n):
            size = float(r.randint(1, int(p["max_size"]) + 1))
            pts.append(((r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(0.1, 0.9), size), tuple(r.uniform(0, 1, 4))))
        passes.append({"verts": pts, "vs": POINT_VS, "fs": POINT_FS,
                       "pipeline": {"topology": "point_list",
                                    "depth_stencil": {"test": True, "write": True, "compare": "less"}}})
    return raster_scene(name, FAMILY, passes=passes, size=tuple(p["size"]), depth="D32_SFLOAT",
                        features={"VkPhysicalDeviceFeatures": {"largePoints": True}})


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
