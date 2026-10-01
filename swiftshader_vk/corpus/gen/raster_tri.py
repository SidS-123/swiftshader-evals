"""Family `raster_tri`: triangle rasterization -- fill rules, culling, winding, viewport,
scissor, clipping, interpolation, polygon modes, provoking vertex (replay).

Every case is a sequence of passes into the same targets (the first clears, the
next ones load and add), with a snapshot after each pass, so state carried
across passes and draws is exercised. Positions get the `vtxjitter`
perturbation (sub-pixel nudges) through common.POSITION_PERTURB.

Variants:
  random      overlapping random triangles, depth-tested in later passes
  fill_rule   grids of quads and fans sharing edges and vertices at sub-pixel offsets
  cull        cull mode and front face changed per pass (dynamic state)
  viewport    odd, offset and flipped (negative-height) viewports with scissors
  clip        triangles crossing the near/far planes and w <= 0, depth clamp on/off
  interp      perspective / flat / noperspective varyings, gl_FragCoord, gl_FrontFacing
  polymode    (with clip) triangles drawn as lines and points (fillModeNonSolid)
"""
from __future__ import annotations

import numpy as np

from common import (COLOR_FS, FULLSCREEN_VS, pos_col_vertices, pub_name, random_triangles, raster_scene, rng,
                    write_all)

FAMILY = "raster_tri"

PUBLIC = [
    {"tag": "random", "seed": 701, "variant": "random", "n": [60, 40, 30], "size": [64, 64]},
    {"tag": "fill_rule", "seed": 702, "variant": "fill_rule", "cells": 6, "offset": [0.013, 0.007], "size": [64, 64]},
    {"tag": "cull", "seed": 703, "variant": "cull", "n": 50, "size": [64, 48],
     "modes": [["none", "counter_clockwise"], ["back", "counter_clockwise"], ["front", "clockwise"]]},
    {"tag": "viewport", "seed": 704, "variant": "viewport", "n": 40, "size": [80, 64],
     "views": [[5, 3, 61, 50], [0, 64, 80, -64], [17, 9, 32, 40]], "scissors": [[0, 0, 80, 64], [10, 6, 50, 40], [20, 10, 30, 30]]},
    {"tag": "interp", "seed": 705, "variant": "interp", "n": 12, "size": [64, 64]},
    {"tag": "clip_polymode", "seed": 706, "variant": "clip", "n": 30, "size": [64, 64], "polymode": True},
]

INTERP_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_s;
layout(location = 1) flat out vec4 v_f;
layout(location = 2) noperspective out vec4 v_n;
void main() { gl_Position = pos; v_s = col; v_f = col.bgra; v_n = col.gbra; }
"""
INTERP_FS = """#version 450
layout(location = 0) in vec4 v_s;
layout(location = 1) flat in vec4 v_f;
layout(location = 2) noperspective in vec4 v_n;
layout(location = 0) out vec4 o0;
layout(location = 1) out vec4 o1;
void main() {
  o0 = vec4(v_s.r, v_n.g, v_f.b, gl_FrontFacing ? 1.0 : 0.25);
  o1 = vec4(fract(gl_FragCoord.xy / 16.0), gl_FragCoord.z, 1.0);
}
"""
CLIP_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_col;
void main() { gl_Position = pos; gl_PointSize = 1.0; v_col = col; }
"""


def v_random(p, r):
    passes = []
    for i, n in enumerate(p["n"]):
        verts = random_triangles(r, n, z_range=(0.1, 0.9))
        passes.append({"verts": verts, "pipeline": {"depth_stencil": {"test": i > 0, "write": True, "compare": "less"}}})
    return dict(passes=passes, depth="D32_SFLOAT")


def v_fill_rule(p, r):
    cells, (ox, oy) = p["cells"], p["offset"]
    passes = []
    for k in range(2):
        verts = []
        step = 2.0 / cells
        for gx in range(cells):
            for gy in range(cells):
                x0, y0 = -1 + gx * step + ox * (k + 1), -1 + gy * step + oy * (k + 1)
                x1, y1 = x0 + step, y0 + step
                col = (((gx * 37 + k * 90) % 256) / 255, ((gy * 91) % 256) / 255, (((gx + gy) * 53) % 256) / 255, 1)
                if (gx + gy + k) % 2:
                    verts += [((x0, y0, .5, 1), col), ((x1, y0, .5, 1), col), ((x0, y1, .5, 1), col),
                              ((x1, y0, .5, 1), col), ((x1, y1, .5, 1), col), ((x0, y1, .5, 1), col)]
                else:          # the other diagonal
                    verts += [((x0, y0, .5, 1), col), ((x1, y1, .5, 1), col), ((x0, y1, .5, 1), col),
                              ((x0, y0, .5, 1), col), ((x1, y0, .5, 1), col), ((x1, y1, .5, 1), col)]
        # a fan of thin triangles around a shared centre vertex
        cx, cy = r.uniform(-0.3, 0.3, 2)
        m = 12
        for j in range(m):
            a0, a1 = 2 * np.pi * j / m, 2 * np.pi * (j + 1) / m
            col = (j / m, 1 - j / m, 0.5, 1)
            verts += [((cx, cy, .4, 1), col), ((cx + .6 * np.cos(a0), cy + .6 * np.sin(a0), .4, 1), col),
                      ((cx + .6 * np.cos(a1), cy + .6 * np.sin(a1), .4, 1), col)]
        passes.append({"verts": verts, "load": "clear"})
    return dict(passes=passes, perturb=False)


def v_cull(p, r):
    verts = random_triangles(r, p["n"], z_range=(0.5, 0.5), spread=0.6)
    passes = []
    for i, (cull, face) in enumerate(p["modes"]):
        passes.append({"verts": verts if i == 0 else random_triangles(r, p["n"] // 2, spread=0.6),
                       "pipeline": {"dynamic": ["cull_mode", "front_face"]},
                       "pre": [{"cmd": "set_cull_mode", "value": cull}, {"cmd": "set_front_face", "value": face}]})
    return dict(passes=passes)


def v_viewport(p, r):
    passes = []
    for vp, sc in zip(p["views"], p["scissors"]):
        passes.append({"verts": random_triangles(r, p["n"], spread=0.7),
                       "pipeline": {"dynamic": ["viewport", "scissor"]},
                       "pre": [{"cmd": "set_viewport", "viewports": [vp + [0, 1]]},
                               {"cmd": "set_scissor", "scissors": [sc]}]})
    return dict(passes=passes)


def v_interp(p, r):
    passes = []
    for k in range(2):
        verts = []
        for _ in range(p["n"]):
            for _ in range(3):
                w = r.uniform(0.3, 3.0)
                x, y = r.uniform(-1.2, 1.2, 2)
                verts.append(((x * w, y * w, r.uniform(0.1, 0.9) * w, w), tuple(r.uniform(0, 1, 4))))
        passes.append({"verts": verts, "pipeline": {"rasterization": {"cull": "none"}}})
    return dict(passes=passes, targets=("R8G8B8A8_UNORM", "R16G16B16A16_SFLOAT"), vs=INTERP_VS, fs=INTERP_FS)


def v_clip(p, r):
    passes = []
    for k, clamp in enumerate((False, True)):
        verts = []
        for _ in range(p["n"]):
            col = tuple(r.uniform(0, 1, 3)) + (1.0,)
            for _ in range(3):
                w = r.uniform(-0.5, 2.0) if r.rand() < 0.2 else r.uniform(0.5, 2.0)
                z = r.uniform(-0.6, 1.6)                  # outside [0, 1] for some vertices
                x, y = r.uniform(-1.6, 1.6, 2)
                verts.append(((x * abs(w), y * abs(w), z * w, w), col))
        passes.append({"verts": verts, "pipeline": {"rasterization": {"depth_clamp": clamp},
                                                    "depth_stencil": {"test": True, "write": True, "compare": "less"}}})
    if p.get("polymode"):
        tri = random_triangles(r, p["n"] // 2, z_range=(0.2, 0.2), spread=0.7)
        passes.append({"verts": tri, "pipeline": {"rasterization": {"polygon_mode": "line"},
                                                  "depth_stencil": {"test": False}}})
        passes.append({"verts": random_triangles(r, p["n"] // 2, z_range=(0.2, 0.2), spread=0.7),
                       "pipeline": {"rasterization": {"polygon_mode": "point"}, "depth_stencil": {"test": False}}})
    return dict(passes=passes, depth="D32_SFLOAT", vs=CLIP_VS,
                features={"VkPhysicalDeviceFeatures": {"depthClamp": True, "fillModeNonSolid": True}})


VARIANTS = {"random": v_random, "fill_rule": v_fill_rule, "cull": v_cull, "viewport": v_viewport,
            "interp": v_interp, "clip": v_clip}


def build(name, p):
    r = rng(p["seed"])
    kw = VARIANTS[p["variant"]](p, r)
    passes = kw.pop("passes")
    return raster_scene(name, FAMILY, passes=passes, size=tuple(p["size"]), **kw)


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
