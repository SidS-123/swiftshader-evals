"""Family `msaa`: 4x multisampling -- coverage, attachment and command resolves, sample
shading, sample masks, alpha-to-coverage, per-sample interpolation (replay).

The reference supports sample counts 1 and 4. Multisampled targets are resolved
(average) at the end of each pass into single-sample images, which are
snapshotted; one variant resolves with vkCmdResolveImage instead.
"""
from __future__ import annotations

from common import Case, pub_name, random_triangles, raster_scene, render_pass_cmds, rng, write_all, FULLSCREEN_VS, \
    COLOR_FS, POS_COL_INPUT, pos_col_vertices

FAMILY = "msaa"

SAMPLE_FS = """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
void main() { o = vec4(v_col.rgb * (0.4 + 0.2 * float(gl_SampleID)), v_col.a); }
"""
INTERP_FS = """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
void main() { o = interpolateAtSample(v_col, 2) * 0.5 + interpolateAtOffset(v_col, vec2(-0.25, 0.125)) * 0.5; }
"""

PUBLIC = [
    {"tag": "coverage_resolve", "seed": 1401, "variant": "coverage", "n": [40, 25]},
    {"tag": "sample_shading_mask", "seed": 1402, "variant": "shading", "n": 30, "mask": 0b1011},
    {"tag": "alpha_to_coverage", "seed": 1403, "variant": "a2c", "n": 35},
    {"tag": "cmd_resolve_depth", "seed": 1404, "variant": "cmd_resolve", "n": 30},
]


def build(name, p):
    r = rng(p["seed"])
    v = p["variant"]
    feats = {"VkPhysicalDeviceFeatures": {"sampleRateShading": True}}
    if v == "coverage":
        passes = [{"verts": random_triangles(r, n, spread=0.6)} for n in p["n"]]
        passes[1]["pipeline"] = {"depth_stencil": {"test": True, "write": True, "compare": "less"}}
        passes[0]["pipeline"] = {"depth_stencil": {"test": True, "write": True, "compare": "less"}}
        return raster_scene(name, FAMILY, passes=passes, samples=4, depth="D32_SFLOAT")
    if v == "shading":
        passes = [{"verts": random_triangles(r, p["n"], spread=0.6), "fs": SAMPLE_FS},
                  {"verts": random_triangles(r, p["n"], spread=0.6),
                   "pipeline": {"multisample": {"sample_mask": p["mask"], "sample_shading": 1.0}}},
                  {"verts": random_triangles(r, p["n"], spread=0.6), "fs": INTERP_FS}]
        return raster_scene(name, FAMILY, passes=passes, samples=4, features=feats)
    if v == "a2c":
        tris = [(pos, col[:3] + (float(r.uniform(0, 1)),)) for pos, col in random_triangles(r, p["n"], spread=0.6)]
        passes = [{"verts": tris, "pipeline": {"multisample": {"alpha_to_coverage": True}}},
                  {"verts": [(pos, col[:3] + (float(r.uniform(0, 1)),)) for pos, col in random_triangles(r, p["n"], spread=0.6)],
                   "pipeline": {"multisample": {"alpha_to_coverage": True, "sample_mask": 0b0110}}}]
        return raster_scene(name, FAMILY, passes=passes, samples=4)
    # cmd_resolve: render into a 4x image without attachment resolves, then vkCmdResolveImage
    W = H = 64
    c = Case(name, FAMILY)
    c.instance()
    c.device(raster=True)
    c.image("ms", "R16G16B16A16_SFLOAT", [W, H], ["color_attachment", "transfer_src"], samples=4, auto_usage=False)
    c.view("ms_v", "ms")
    c.image("res", "R16G16B16A16_SFLOAT", [W, H], [])
    c.image("msd", "D32_SFLOAT", [W, H], ["depth_stencil_attachment"], samples=4, auto_usage=False)
    c.view("msd_v", "msd")
    tris = random_triangles(r, p["n"], spread=0.6)
    data = pos_col_vertices(tris)
    c.buffer("vb", 4 * len(data), ["vertex_buffer"])
    c.upload("vb", data={"f32": data})
    c.shader("vs", FULLSCREEN_VS, "vert")
    c.shader("fs", COLOR_FS, "frag")
    c.pipeline_layout("pl")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        multisample={"samples": 4}, depth_stencil={"test": True, "write": True, "compare": "less"},
                        rendering={"color_formats": ["R16G16B16A16_SFLOAT"], "depth_format": "D32_SFLOAT"})
    c.exec([*render_pass_cmds(["ms_v"], [0, 0, W, H], depth_view="msd_v", body=[
        {"cmd": "bind_pipeline", "pipeline": "gp"}, {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}]},
        {"cmd": "draw", "vertices": len(tris)}]),
        {"cmd": "resolve_image", "src": "ms", "dst": "res", "regions": [{"extent": [W, H, 1]}]}])
    c.snapshot([{"name": "resolved", "image": "res"}])
    c.exec([{"cmd": "resolve_image", "src": "ms", "dst": "res", "regions": [
        {"src_offset": [8, 4, 0], "dst_offset": [0, 0, 0], "extent": [W - 8, H - 4, 1]}]}])
    c.snapshot([{"name": "resolved_offset", "image": "res"}])
    return c


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
