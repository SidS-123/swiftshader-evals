"""Families `perf_fill`, `perf_compute`, `perf_geometry`, `perf_texture`: timed runs
(performance).

Each case sets up its scene, then one `run` op submits the same command buffer
`iterations` times after `warmup` submissions; vkreplay's parent times it, and
the run's snapshot must also be correct (SPEC.md: the score is 0 if it scores
below 0.5). Every iteration clears and redraws (or recomputes from the inputs),
so the snapshot does not depend on the iteration count.

`iterations` is calibrated so the reference's timed run takes at least 2 s
(PLAN_v1.md §11.1; measured values in docs/internal/stage7_findings.md), and is
fixed in the case: re-calibrate only by editing the table and regenerating.

  fill      512x512 RGBA8, 160 large overlapping alpha-blended triangles with a depth
            test (fill rate and blending)
  compute   65536 invocations of an integer/float hash loop (ALU throughput)
  geometry  20000 small indexed triangles with an instanced attribute stream
            (vertex processing, setup, small-triangle rasterization)
  texture   a full-screen quad sampling a mipmapped 512x512 texture eight times per
            fragment with trilinear filtering and anisotropy
"""
from __future__ import annotations

import numpy as np

from common import Case, POS_COL_INPUT, FULLSCREEN_VS, TEX_VS, pos_col_vertices, pub_name, quad, \
    random_triangles, render_pass_cmds, rng, upload_mips, write_all

FAMILIES = ("perf_fill", "perf_compute", "perf_geometry", "perf_texture")
META = {"timed_run_index": 0}

PUBLIC = [
    {"family": "perf_fill", "tag": "a", "seed": 2001, "size": 512, "tris": 160, "iterations": 650},
    {"family": "perf_compute", "tag": "a", "seed": 2002, "invocations": 65536, "loop": 256, "iterations": 550},
    {"family": "perf_geometry", "tag": "a", "seed": 2003, "size": 512, "tris": 20000, "instances": 2, "iterations": 720},
    {"family": "perf_texture", "tag": "a", "seed": 2004, "size": 512, "tex": 512, "taps": 8, "iterations": 300},
]

BLEND_ALPHA = {"attachments": [{"enable": True, "src_color": "src_alpha", "dst_color": "one_minus_src_alpha",
                                "src_alpha": "one", "dst_alpha": "one_minus_src_alpha"}]}


def raster_base(c, W, depth=True):
    c.instance()
    c.device(raster=True, features={"VkPhysicalDeviceFeatures": {"samplerAnisotropy": True}})
    c.image("color0", "R8G8B8A8_UNORM", [W, W], ["color_attachment"])
    c.view("color0_v", "color0")
    if depth:
        c.image("depth", "D32_SFLOAT", [W, W], ["depth_stencil_attachment"])
        c.view("depth_v", "depth")


def p_fill(name, p, r):
    W = p["size"]
    c = Case(name, p["family"], "performance", META)
    raster_base(c, W)
    tris = random_triangles(r, p["tris"], spread=1.6, extent=0.6, alpha=0.6)
    data = pos_col_vertices(tris)
    c.buffer("vb", 4 * len(data), ["vertex_buffer"])
    c.upload("vb", data={"f32": data})
    c.shader("vs", FULLSCREEN_VS, "vert")
    c.shader("fs", """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
void main() { o = vec4(v_col.rgb * (0.75 + 0.25 * fract(gl_FragCoord.x * 0.0625)), v_col.a); }
""", "frag")
    c.pipeline_layout("pl")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, W],
                        depth_stencil={"test": True, "write": False, "compare": "less_or_equal"}, blend=BLEND_ALPHA,
                        rendering={"color_formats": ["R8G8B8A8_UNORM"], "depth_format": "D32_SFLOAT"})
    cmds = render_pass_cmds(["color0_v"], [0, 0, W, W], depth_view="depth_v", body=[
        {"cmd": "bind_pipeline", "pipeline": "gp"}, {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}]},
        {"cmd": "draw", "vertices": len(tris)}])
    c.run("timed", p["iterations"], cmds, [{"name": "color0", "image": "color0"}])
    return c


def p_compute(name, p, r):
    n = p["invocations"]
    c = Case(name, p["family"], "performance", META)
    c.instance()
    c.device()
    c.buffer("in", n * 4, ["storage_buffer"])
    c.upload("in", array=r.randint(0, 2**31, size=n).astype(np.uint32))
    c.buffer("out", n * 8, ["storage_buffer"])
    c.shader("cs", f"""#version 450
layout(local_size_x = 64) in;
layout(std430, set = 0, binding = 0) readonly buffer I {{ uint x[]; }};
layout(std430, set = 0, binding = 1) writeonly buffer O {{ uvec2 o[]; }};
void main() {{
  uint i = gl_GlobalInvocationID.x;
  uint h = x[i];
  precise float f = float(h & 0xffffu) * (1.0 / 65536.0);
  for (uint k = 0u; k < {p["loop"]}u; ++k) {{
    h ^= h << 13; h ^= h >> 17; h ^= h << 5;
    h = h * 0x9E3779B1u + k;
    f = f * 0.9990234375 + float(h >> 24) * 0.0009765625;
  }}
  o[i] = uvec2(h, floatBitsToUint(f));
}}
""", "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]},
                         {"binding": 1, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": "in"}]},
                            {"binding": 1, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    cmds = [{"cmd": "bind_pipeline", "pipeline": "p"}, {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "dispatch", "groups": [n // 64]}]
    c.run("timed", p["iterations"], cmds, [{"name": "out", "buffer": "out", "elem": "u32"}])
    return c


def p_geometry(name, p, r):
    W = p["size"]
    n = p["tris"]
    c = Case(name, p["family"], "performance", META)
    raster_base(c, W)
    # a grid of shared vertices; each triangle indexes three nearby ones
    g = int(np.ceil(np.sqrt(n))) + 2
    xs, ys = np.meshgrid(np.linspace(-0.98, 0.98, g), np.linspace(-0.98, 0.98, g))
    pos = np.stack([xs + r.uniform(-0.3, 0.3, xs.shape) / g, ys + r.uniform(-0.3, 0.3, ys.shape) / g,
                    r.uniform(0.05, 0.95, xs.shape), np.ones_like(xs)], -1).reshape(-1, 4)
    col = np.concatenate([r.uniform(0, 1, (g * g, 3)), np.ones((g * g, 1))], 1)
    verts = np.concatenate([pos, col], 1).astype(np.float32)
    cells = r.choice((g - 1) * (g - 1), size=n, replace=n > (g - 1) * (g - 1))
    cy, cx = cells // (g - 1), cells % (g - 1)
    flip = r.randint(0, 2, n)
    a = cy * g + cx
    idx = np.stack([a, a + 1 + (g - 1) * flip, a + g + flip], 1).astype(np.uint32).reshape(-1)
    inst = np.array([[0.0, 0.0, 0.0, 0.0], [0.004, -0.003, 0.01, 0.0]][: p["instances"]], np.float32)
    c.buffer("vb", verts.nbytes, ["vertex_buffer"])
    c.upload("vb", array=verts.reshape(-1))
    c.buffer("ib", idx.nbytes, ["index_buffer"])
    c.upload("ib", array=idx)
    c.buffer("inst", inst.nbytes, ["vertex_buffer"])
    c.upload("inst", array=inst.reshape(-1))
    c.shader("vs", """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 2) in vec4 off;
layout(location = 0) out vec4 v_col;
void main() { gl_Position = pos + off; v_col = col * (1.0 - 0.3 * float(gl_InstanceIndex)); }
""", "vert")
    c.shader("fs", """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
void main() { o = v_col; }
""", "frag")
    vi = {"bindings": [{"binding": 0, "stride": 32}, {"binding": 1, "stride": 16, "rate": "instance"}],
          "attributes": [{"location": 0, "format": "R32G32B32A32_SFLOAT", "offset": 0},
                         {"location": 1, "format": "R32G32B32A32_SFLOAT", "offset": 16},
                         {"location": 2, "binding": 1, "format": "R32G32B32A32_SFLOAT", "offset": 0}]}
    c.pipeline_layout("pl")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=vi, viewport=[0, 0, W, W],
                        depth_stencil={"test": True, "write": True, "compare": "less"},
                        rendering={"color_formats": ["R8G8B8A8_UNORM"], "depth_format": "D32_SFLOAT"})
    cmds = render_pass_cmds(["color0_v"], [0, 0, W, W], depth_view="depth_v", body=[
        {"cmd": "bind_pipeline", "pipeline": "gp"},
        {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}, {"buffer": "inst"}]},
        {"cmd": "bind_index_buffer", "buffer": "ib", "type": "uint32"},
        {"cmd": "draw_indexed", "indices": int(idx.size), "instances": p["instances"]}])
    c.run("timed", p["iterations"], cmds, [{"name": "color0", "image": "color0"},
                                           {"name": "depth", "image": "depth", "aspect": "depth"}])
    return c


def p_texture(name, p, r):
    W, T = p["size"], p["tex"]
    mips = int(np.log2(T)) + 1
    c = Case(name, p["family"], "performance", META)
    raster_base(c, W, depth=False)
    c.image("tex", "R8G8B8A8_UNORM", [T, T], ["sampled"], mips=mips)
    upload_mips(c, r, w=T, h=T, mips=mips, smooth=True)
    c.view("tex_v", "tex")
    c.sampler("s", mag="linear", min="linear", mipmap="linear", anisotropy=4.0)
    c.desc_layout("dl", [{"binding": 0, "type": "combined_image_sampler", "stages": ["fragment"]}])
    c.pipeline_layout("pl", ["dl"])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "combined_image_sampler",
                             "images": [{"view": "tex_v", "sampler": "s", "layout": "general"}]}])
    offs = r.uniform(-0.02, 0.02, (p["taps"], 2))
    taps = " + ".join(f"texture(t, v_uv.xy * {1.0 + 0.37 * k:.4f} + vec2({o[0]:.5f}, {o[1]:.5f}))"
                      for k, o in enumerate(offs))
    c.shader("vs", TEX_VS, "vert")
    c.shader("fs", f"""#version 450
layout(set = 0, binding = 0) uniform sampler2D t;
layout(location = 0) in vec4 v_uv;
layout(location = 0) out vec4 o;
void main() {{ o = ({taps}) * {1.0 / p["taps"]:.6f}; }}
""", "frag")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, W],
                        rendering={"color_formats": ["R8G8B8A8_UNORM"]})
    verts = quad(-0.3, -0.2, 2.6, 1.9)
    data = pos_col_vertices(verts)
    c.buffer("vb", 4 * len(data), ["vertex_buffer"])
    c.upload("vb", data={"f32": data})
    cmds = render_pass_cmds(["color0_v"], [0, 0, W, W], body=[
        {"cmd": "bind_pipeline", "pipeline": "gp"},
        {"cmd": "bind_sets", "layout": "pl", "bind_point": "graphics", "sets": ["ds"]},
        {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}]},
        {"cmd": "draw", "vertices": len(verts)}])
    c.run("timed", p["iterations"], cmds, [{"name": "color0", "image": "color0"}])
    return c


BUILDERS = {"perf_fill": p_fill, "perf_compute": p_compute, "perf_geometry": p_geometry, "perf_texture": p_texture}
FAMILY = "perf"


def build(name, p):
    return BUILDERS[p["family"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(p["family"], p["tag"]), p) for p in PUBLIC], corpus, split)
