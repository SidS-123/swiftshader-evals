"""The Stage 4 pilot corpus: ~55 hand-written cases, one or more per op type.

    python3 swiftshader_vk/tools/pilot_corpus.py [OUT_DIR]      (default runs/pilot)

Not part of the graded corpus. It exists to (a) prove vkreplay replays every
op group on the oracle, twice identically, (b) run the validation-layer gate
over every op group, and (c) measure the oracle's determinism per op type
(tools/determinism.py, PLAN_v1.md §7 step 4). Writes OUT_DIR/cases/*.json and
OUT_DIR/assets/*.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "corpus" / "gen"))
sys.path.insert(0, str(HERE.parents[1] / "evalBase"))

from common import (COLOR_FS, FULLSCREEN_VS, POS_COL_INPUT, POSITION_PERTURB, Case,   # noqa: E402
                    pos_col_vertices, render_pass_cmds)
from evalbase.corpus.common import CorpusWriter                                          # noqa: E402
from evalbase.interfaces import CorpusSpec                                               # noqa: E402

W = H = 64
RNG = np.random.RandomState(20260929)

# ------------------------------------------------------------------ compute helpers

COMPUTE_HEAD = """#version 450
#extension GL_KHR_shader_subgroup_basic : enable
#extension GL_KHR_shader_subgroup_arithmetic : enable
#extension GL_KHR_shader_subgroup_ballot : enable
#extension GL_KHR_shader_subgroup_shuffle : enable
layout(local_size_x = 64) in;
layout(set = 0, binding = 0) buffer In { uint a[]; } src;
layout(set = 0, binding = 1) buffer Out { uint o[]; } dst;
"""


def compute_case(name, family, body, n=1024, inputs=None, out_elem="u32", groups=None, extra_ops=None,
                 features=None, head=COMPUTE_HEAD, out_words=None):
    """One dispatch of `body` (GLSL main) over n invocations: src.a[] in, dst.o[] out."""
    c = Case(name, family)
    c.instance()
    c.device(features=features)
    words = out_words or n
    c.buffer("in", 4 * n, ["storage_buffer"])
    c.buffer("out", 4 * words, ["storage_buffer"])
    if inputs is None:
        inputs = RNG.randint(0, 2**32, size=n, dtype=np.uint64).astype(np.uint32)
    c.upload("in", array=np.asarray(inputs, dtype=np.uint32))
    c.shader("cs", head + body, "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]},
                         {"binding": 1, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"], push_constants=[{"stages": ["compute"], "offset": 0, "size": 16}])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": "in"}]},
                            {"binding": 1, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    for op in extra_ops or []:
        c.ops.append(op)
    c.exec([{"cmd": "bind_pipeline", "pipeline": "p"},
            {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "push_constants", "layout": "pl", "stages": ["compute"], "data": {"u32": [3, 5, 7, 11]}},
            {"cmd": "dispatch", "groups": groups or [n // 64]}])
    c.snapshot([{"name": "out", "buffer": "out", "elem": out_elem}])
    return c


def compute_cases():
    cs = []
    cs.append(compute_case("p_c_int_arith", "compute_arith", """
void main() { uint i = gl_GlobalInvocationID.x; uint x = src.a[i]; uint y = src.a[(i * 7u + 3u) % 1024u] | 1u;
  dst.o[i] = (x + y * 3u) ^ (x >> (y & 31u)) ^ (x / y) ^ (x % y) ^ bitfieldReverse(x) ^ uint(bitCount(y)) ^ uint(findMSB(x)); }"""))
    cs.append(compute_case("p_c_int_signed", "compute_arith", """
void main() { uint i = gl_GlobalInvocationID.x; int x = int(src.a[i]); int y = int(src.a[(i + 1u) % 1024u] | 1u);
  dst.o[i] = uint((x / y) + (x % y) * 3 + (x >> 5) + abs(x) - min(x, y) + max(x, y) + clamp(x, -1000, 1000)); }"""))
    cs.append(compute_case("p_c_float_basic", "compute_arith", """
void main() { uint i = gl_GlobalInvocationID.x; float x = float(src.a[i] & 0xffffu) / 256.0 - 100.0;
  float y = float(src.a[(i + 5u) % 1024u] & 0xfffu) / 64.0 + 0.5;
  float r = x * y + x / y - sqrt(abs(x)) + fma(x, y, 1.5) + floor(x) + fract(y) + inversesqrt(y);
  dst.o[i] = floatBitsToUint(r); }""", out_elem="f32"))
    cs.append(compute_case("p_c_float_transc", "compute_arith", """
void main() { uint i = gl_GlobalInvocationID.x; float x = float(src.a[i] & 0xffffu) / 65536.0 * 6.2831853 - 3.1415926;
  float r = sin(x) + cos(x) * 0.5 + exp(x * 0.25) + log(abs(x) + 1.0) + pow(abs(x), 1.7) + atan(x, 1.3) + tan(x * 0.2);
  dst.o[i] = floatBitsToUint(r); }""", out_elem="f32"))
    cs.append(compute_case("p_c_convert_round", "compute_arith", """
void main() { uint i = gl_GlobalInvocationID.x; float x = float(int(src.a[i])) / 3.0e5;
  uint r = uint(int(round(x))) ^ (uint(int(roundEven(x))) << 8) ^ (uint(int(trunc(x))) << 16) ^ packHalf2x16(vec2(x, -x))
         ^ packUnorm4x8(vec4(fract(x))) ^ packSnorm2x16(vec2(sin(x)));
  dst.o[i] = r; }"""))
    cs.append(compute_case("p_c_cf_loops", "compute_cf", """
uint collatz(uint n) { uint s = 0u; while (n != 1u && s < 500u) { n = (n % 2u == 0u) ? n / 2u : 3u * n + 1u; s++; } return s; }
void main() { uint i = gl_GlobalInvocationID.x; uint x = (src.a[i] % 1000u) + 1u; uint acc = 0u;
  switch (x % 4u) { case 0u: acc = collatz(x); break; case 1u: for (uint k = 0u; k < x % 17u; k++) { if (k == 9u) break; acc += k * x; } break;
                   case 2u: acc = x * x; if (acc > 1000u) { acc -= 7u; } break; default: acc = ~x; }
  dst.o[i] = acc; }"""))
    cs.append(compute_case("p_c_matrix", "compute_types", """
void main() { uint i = gl_GlobalInvocationID.x; float s = float(src.a[i] & 1023u) / 512.0 + 0.25;
  mat4 m = mat4(s, 1.0, 0.5, 0.0, 0.0, s + 1.0, 0.25, 0.0, 0.125, 0.0, s * 2.0, 0.5, 1.0, 2.0, 3.0, 1.0);
  mat4 inv = inverse(m); vec4 v = inv * (m * vec4(1.0, 2.0, 3.0, 4.0)) + transpose(m)[1] * determinant(m);
  dst.o[i] = floatBitsToUint(v.x + v.y * 0.5 + v.z * 0.25 + v.w * 0.125); }""", out_elem="f32"))
    cs.append(compute_case("p_c_shared_reduce", "compute_types", """
shared uint sh[64];
void main() { uint l = gl_LocalInvocationID.x; sh[l] = src.a[gl_GlobalInvocationID.x] & 0xffffu; barrier();
  for (uint s = 32u; s > 0u; s >>= 1u) { if (l < s) sh[l] += sh[l + s]; barrier(); }
  dst.o[gl_GlobalInvocationID.x] = sh[0] + l; }"""))
    cs.append(compute_case("p_c_subgroup", "compute_subgroup", """
void main() { uint i = gl_GlobalInvocationID.x; uint x = src.a[i] & 0xffu;
  uvec4 b = subgroupBallot((x & 1u) == 1u);
  dst.o[i] = subgroupAdd(x) + (subgroupShuffle(x, (gl_SubgroupInvocationID + 1u) % gl_SubgroupSize) << 12) + (subgroupBallotBitCount(b) << 24)
           + (gl_SubgroupSize << 28); }"""))
    cs.append(compute_case("p_c_atomic_sum", "compute_atomics", """
void main() { uint i = gl_GlobalInvocationID.x; atomicAdd(dst.o[i % 16u], src.a[i] & 0xffffu); atomicMax(dst.o[16u + i % 8u], src.a[i]);
  atomicOr(dst.o[24u], 1u << (i % 32u)); }""", out_words=32))
    cs.append(compute_case("p_c_atomic_order", "compute_atomics", """
void main() { uint i = gl_GlobalInvocationID.x; uint old = atomicAdd(dst.o[0], 1u); dst.o[1u + i] = old; }""", out_words=1025))
    cs.append(compute_case("p_c_push_spec", "descriptors_push", """
layout(push_constant) uniform PC { uint k0, k1, k2, k3; } pc;
layout(constant_id = 0) const uint SPEC = 1u;
void main() { uint i = gl_GlobalInvocationID.x; dst.o[i] = src.a[i] * pc.k0 + pc.k1 * SPEC + pc.k2 + pc.k3; }"""))
    # spec constant set on the pipeline
    cs[-1].ops[[k for k, o in enumerate(cs[-1].ops) if o["op"] == "compute_pipeline"][0]]["spec"] = [{"id": 0, "u32": 42}]
    # dispatch_indirect
    c = compute_case("p_c_dispatch_indirect", "compute_arith", """
void main() { uint i = gl_GlobalInvocationID.x; dst.o[i] = src.a[i] + gl_WorkGroupID.x; }""")
    c.ops.insert(3, {"op": "buffer", "name": "ind", "size": 12, "usage": ["indirect_buffer"]})
    c.ops.insert(4, {"op": "upload", "buffer": "ind", "data": {"u32": [9, 1, 1]}})
    ex = [o for o in c.ops if o["op"] == "exec"][0]
    ex["cmds"][-1] = {"cmd": "dispatch_indirect", "buffer": "ind"}
    cs.append(c)
    # storage image store + snapshot
    c = Case("p_c_image_store", "compute_image")
    c.instance(); c.device()
    c.image("img", "R8G8B8A8_UNORM", [W, H], ["storage"])
    c.view("imgv", "img")
    c.shader("cs", """#version 450
layout(local_size_x = 8, local_size_y = 8) in;
layout(set = 0, binding = 0, rgba8) uniform writeonly image2D img;
void main() { ivec2 p = ivec2(gl_GlobalInvocationID.xy); vec2 f = vec2(p) / 63.0;
  imageStore(img, p, vec4(f.x, f.y, sin(f.x * 9.0) * 0.5 + 0.5, 1.0)); }""", "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_image", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_image", "images": [{"view": "imgv", "layout": "general"}]}])
    c.compute_pipeline("p", "pl", "cs")
    c.exec([{"cmd": "bind_pipeline", "pipeline": "p"}, {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "dispatch", "groups": [W // 8, H // 8]}])
    c.snapshot([{"name": "img", "image": "img"}])
    cs.append(c)
    return cs


# ------------------------------------------------------------------ raster helpers

def raster_case(name, family, verts, *, fmt="R8G8B8A8_UNORM", topology="triangle_list", pipeline_extra=None,
                depth_fmt=None, features=None, vs=FULLSCREEN_VS, fs=COLOR_FS, clear=(0.0, 0.0, 0.0, 1.0),
                draw=None, extra_cmds_before=(), extra_setup=None, snapshot_depth=False, stencil=False,
                color_formats=None, perturb=True):
    c = Case(name, family)
    c.instance()
    c.device(features=features, raster=True)
    formats = color_formats or [fmt]
    views = []
    for k, f in enumerate(formats):
        c.image(f"color{k}", f, [W, H], ["color_attachment"])
        c.view(f"color{k}_v", f"color{k}")
        views.append(f"color{k}_v")
    if depth_fmt:
        c.image("depth", depth_fmt, [W, H], ["depth_stencil_attachment"])
        c.view("depth_v", "depth")
    data = pos_col_vertices(verts)
    c.buffer("vb", 4 * len(data), ["vertex_buffer"])
    c.upload("vb", data={"f32": data}, perturb=POSITION_PERTURB if perturb else None)
    if extra_setup:
        extra_setup(c)
    c.shader("vs", vs, "vert")
    c.shader("fs", fs, "frag")
    c.pipeline_layout("pl")
    rendering = {"color_formats": formats}
    if depth_fmt:
        rendering["depth_format"] = depth_fmt
        if stencil:
            rendering["stencil_format"] = depth_fmt
    kw = dict(vertex_input=POS_COL_INPUT, topology=topology, viewport=[0, 0, W, H], rendering=rendering)
    kw.update(pipeline_extra or {})
    c.graphics_pipeline("gp", "pl", "vs", "fs", **kw)
    body = [*extra_cmds_before, {"cmd": "bind_pipeline", "pipeline": "gp"},
            {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}]},
            *(draw or [{"cmd": "draw", "vertices": len(verts)}])]
    c.exec(render_pass_cmds(views, [0, 0, W, H], clear=clear, depth_view="depth_v" if depth_fmt else None,
                            stencil_view="depth_v" if stencil else None, body=body))
    items = [{"name": f"color{k}", "image": f"color{k}"} for k in range(len(formats))]
    if snapshot_depth:
        items.append({"name": "depth", "image": "depth", "aspect": "depth"})
    if stencil:
        items.append({"name": "stencil", "image": "depth", "aspect": "stencil"})
    c.snapshot(items)
    return c


def rand_tris(n, z=True, alpha=1.0):
    vs = []
    for _ in range(n):
        cx, cy = RNG.uniform(-1, 1, 2)
        col = list(RNG.uniform(0, 1, 3)) + [alpha]
        depth = RNG.uniform(0.05, 0.95) if z else 0.5
        for _ in range(3):
            vs.append(((cx + RNG.uniform(-0.5, 0.5), cy + RNG.uniform(-0.5, 0.5), depth, 1.0), col))
    return vs


def raster_cases():
    cs = []
    cs.append(raster_case("p_g_tri_basic", "raster_tri",
                          [((-0.8, -0.7, 0.25, 1), (1, 0, 0, 1)), ((0.9, -0.2, 0.5, 1), (0, 1, 0, 1)),
                           ((-0.1, 0.85, 0.75, 1), (0, 0, 1, 1))], depth_fmt="D32_SFLOAT", snapshot_depth=True,
                          pipeline_extra={"depth_stencil": {"test": True, "write": True, "compare": "less"}}))
    cs.append(raster_case("p_g_tri_many", "raster_tri", rand_tris(150, z=False)))
    cs.append(raster_case("p_g_tri_depth", "depth_stencil", rand_tris(120), depth_fmt="D32_SFLOAT", snapshot_depth=True,
                          pipeline_extra={"depth_stencil": {"test": True, "write": True, "compare": "less_or_equal"}}))
    # shared edges at sub-pixel positions: the top-left rule
    grid = []
    for gx in range(6):
        for gy in range(6):
            x0, y0 = -1 + gx / 3 + 0.013, -1 + gy / 3 + 0.007
            x1, y1 = x0 + 1 / 3, y0 + 1 / 3
            col = ((gx * 37 % 255) / 255, (gy * 91 % 255) / 255, ((gx + gy) * 53 % 255) / 255, 1)
            grid += [((x0, y0, 0.5, 1), col), ((x1, y0, 0.5, 1), col), ((x0, y1, 0.5, 1), col),
                     ((x1, y0, 0.5, 1), col), ((x1, y1, 0.5, 1), col), ((x0, y1, 0.5, 1), col)]
    cs.append(raster_case("p_g_fill_rules", "raster_tri", grid, perturb=False))
    cs.append(raster_case("p_g_cull_winding", "raster_tri", rand_tris(60, z=False),
                          pipeline_extra={"rasterization": {"cull": "back", "front_face": "clockwise"}}))
    cs.append(raster_case("p_g_viewport_scissor", "raster_tri", rand_tris(40, z=False),
                          pipeline_extra={"viewport": [8, 4, 40, 50, 0, 1], "scissor": [10, 10, 30, 40]}))
    cs.append(raster_case("p_g_depth_bias", "depth_stencil", rand_tris(60), depth_fmt="D16_UNORM", snapshot_depth=True,
                          pipeline_extra={"depth_stencil": {"test": True, "write": True, "compare": "less"},
                                          "rasterization": {"depth_bias": [4.0, 0.0, 1.5]}}))
    lines = [((RNG.uniform(-1, 1), RNG.uniform(-1, 1), 0.5, 1), tuple(RNG.uniform(0, 1, 3)) + (1,)) for _ in range(80)]
    cs.append(raster_case("p_g_lines", "raster_lines_points", lines, topology="line_list"))
    point_vs = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_col;
void main() { gl_Position = pos; gl_PointSize = 1.0 + col.a * 7.0; v_col = vec4(col.rgb, 1.0); }
"""
    pts = [((RNG.uniform(-1, 1), RNG.uniform(-1, 1), 0.5, 1), tuple(RNG.uniform(0, 1, 4))) for _ in range(60)]
    cs.append(raster_case("p_g_points", "raster_lines_points", pts, topology="point_list", vs=point_vs,
                          features={"VkPhysicalDeviceFeatures": {"largePoints": True}}))
    blend = {"blend": {"attachments": [{"enable": True, "src_color": "src_alpha", "dst_color": "one_minus_src_alpha",
                                        "src_alpha": "one", "dst_alpha": "one_minus_src_alpha"}]}}
    cs.append(raster_case("p_g_blend_alpha", "blend", rand_tris(80, z=False, alpha=0.4), pipeline_extra=blend))
    blend2 = {"blend": {"constants": [0.2, 0.4, 0.6, 0.8],
                        "attachments": [{"enable": True, "src_color": "constant_color", "dst_color": "src_color",
                                         "color_op": "reverse_subtract", "src_alpha": "dst_alpha", "dst_alpha": "one",
                                         "alpha_op": "max"}]}}
    cs.append(raster_case("p_g_blend_modes", "blend", rand_tris(60, z=False, alpha=0.7), pipeline_extra=blend2,
                          clear=(0.5, 0.25, 0.75, 0.5)))
    cs.append(raster_case("p_g_write_mask", "blend", rand_tris(40, z=False),
                          pipeline_extra={"blend": {"attachments": [{"write_mask": "rb"}]}}, clear=(0.2, 0.4, 0.6, 0.8)))
    stencil = {"depth_stencil": {"test": True, "write": True, "compare": "less",
                                 "stencil": {"front": {"compare": "always", "pass": "increment_and_clamp", "fail": "keep",
                                                       "depth_fail": "invert"},
                                             "back": {"compare": "always", "pass": "decrement_and_wrap"}}}}
    cs.append(raster_case("p_g_stencil", "depth_stencil", rand_tris(80), depth_fmt="D32_SFLOAT_S8_UINT", stencil=True,
                          snapshot_depth=True, pipeline_extra=stencil))
    mrt_fs = """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o0;
layout(location = 1) out vec4 o1;
layout(location = 2) out uvec4 o2;
void main() { o0 = v_col; o1 = v_col * 2.0 - 0.5; o2 = uvec4(v_col * 65535.0); }
"""
    cs.append(raster_case("p_g_mrt", "mrt_renderpass", rand_tris(50, z=False), fs=mrt_fs,
                          color_formats=["R8G8B8A8_UNORM", "R16G16B16A16_SFLOAT", "R16G16B16A16_UINT"],
                          pipeline_extra={"blend": {"attachments": [{}, {}, {}]}}))
    cs.append(raster_case("p_g_srgb", "formats_copy_blit", rand_tris(50, z=False, alpha=0.5), fmt="R8G8B8A8_SRGB",
                          pipeline_extra=blend))
    cs.append(raster_case("p_g_formats_rt", "formats_copy_blit", rand_tris(50, z=False, alpha=0.5),
                          color_formats=["R5G6B5_UNORM_PACK16", "A2B10G10R10_UNORM_PACK32", "B10G11R11_UFLOAT_PACK32"],
                          fs="""#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o0;
layout(location = 1) out vec4 o1;
layout(location = 2) out vec4 o2;
void main() { o0 = v_col; o1 = v_col.bgra; o2 = v_col * 3.0; }
""", pipeline_extra={"blend": {"attachments": [{}, {}, {}]}}))
    # MSAA: 4 samples, resolve in the rendering pass
    c = Case("p_g_msaa_resolve", "msaa")
    c.instance(); c.device(raster=True)
    c.image("ms", "R8G8B8A8_UNORM", [W, H], ["color_attachment"], samples=4, auto_usage=False)
    c.view("ms_v", "ms")
    c.image("color0", "R8G8B8A8_UNORM", [W, H], ["color_attachment"])
    c.view("color0_v", "color0")
    tv = rand_tris(40, z=False)
    data = pos_col_vertices(tv)
    c.buffer("vb", 4 * len(data), ["vertex_buffer"])
    c.upload("vb", data={"f32": data}, perturb=POSITION_PERTURB)
    c.shader("vs", FULLSCREEN_VS, "vert"); c.shader("fs", COLOR_FS, "frag")
    c.pipeline_layout("pl")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        multisample={"samples": 4}, rendering={"color_formats": ["R8G8B8A8_UNORM"]})
    c.exec([{"cmd": "begin_rendering", "area": [0, 0, W, H],
             "color": [{"view": "ms_v", "load": "clear", "store": "dont_care", "clear": {"f32": [0, 0, 0, 1]},
                        "resolve_view": "color0_v", "resolve_mode": "average"}]},
            {"cmd": "bind_pipeline", "pipeline": "gp"}, {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}]},
            {"cmd": "draw", "vertices": len(tv)}, {"cmd": "end_rendering"}])
    c.snapshot([{"name": "color0", "image": "color0"}])
    cs.append(c)
    a2c = raster_case("p_g_msaa_a2c", "msaa", rand_tris(40, z=False, alpha=0.5))
    # rebuild as 4x with alpha to coverage + resolve via resolve_image
    a2c.ops = [o for o in a2c.ops]
    for o in a2c.ops:
        if o["op"] == "image" and o["name"] == "color0":
            o["samples"] = 4
            o["usage"] = ["color_attachment"]
            o["auto_usage"] = False
        if o["op"] == "graphics_pipeline":
            o["multisample"] = {"samples": 4, "alpha_to_coverage": True}
    snap = a2c.ops.pop()   # the snapshot
    a2c.ops.insert(3, {"op": "image", "name": "res", "format": "R8G8B8A8_UNORM", "extent": [W, H], "usage": []})
    a2c.ops.append({"op": "exec", "cmds": [{"cmd": "resolve_image", "src": "color0", "dst": "res",
                                             "regions": [{"extent": [W, H]}]}]})
    snap["items"] = [{"name": "res", "image": "res"}]
    a2c.ops.append(snap)
    # the attachment needs transfer_src for resolve: auto_usage off means add it explicitly
    for o in a2c.ops:
        if o["op"] == "image" and o["name"] == "color0":
            o["usage"] = ["color_attachment", "transfer_src"]
    cs.append(a2c)
    # instancing + index buffer + primitive restart
    inst_vs = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_col;
void main() { vec2 off = vec2(float(gl_InstanceIndex % 3) * 0.6 - 0.6, float(gl_InstanceIndex / 3) * 0.6 - 0.6);
  gl_Position = vec4(pos.xy * 0.25 + off, pos.z, 1.0); v_col = col * (0.5 + 0.1 * float(gl_InstanceIndex)); }
"""
    strip = [((-1, -1, 0.5, 1), (1, 0, 0, 1)), ((1, -1, 0.5, 1), (0, 1, 0, 1)), ((-1, 1, 0.5, 1), (0, 0, 1, 1)),
             ((1, 1, 0.5, 1), (1, 1, 0, 1)), ((0, 1.6, 0.5, 1), (0, 1, 1, 1))]

    def setup_ib(c):
        c.buffer("ib", 64, ["index_buffer"])
        c.upload("ib", data={"u16": [0, 1, 2, 3, 0xFFFF, 2, 3, 4, 0xFFFF, 1, 3, 4]})
    cs.append(raster_case("p_g_instanced_restart", "vertex_input", strip, vs=inst_vs, topology="triangle_strip",
                          pipeline_extra={"primitive_restart": True}, extra_setup=setup_ib,
                          draw=[{"cmd": "bind_index_buffer", "buffer": "ib", "type": "uint16"},
                                {"cmd": "draw_indexed", "indices": 12, "instances": 6}]))
    interp_vs = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_s;
layout(location = 1) flat out vec4 v_f;
layout(location = 2) noperspective out vec4 v_n;
void main() { gl_Position = pos; v_s = col; v_f = col; v_n = col; }
"""
    interp_fs = """#version 450
layout(location = 0) in vec4 v_s;
layout(location = 1) flat in vec4 v_f;
layout(location = 2) noperspective in vec4 v_n;
layout(location = 0) out vec4 o;
void main() { o = vec4(v_s.r, v_f.g, v_n.b, 1.0) + vec4(dFdx(v_s.r), dFdy(v_n.b), 0.0, 0.0) * 4.0;
  if (gl_FragCoord.x > 60.0 && gl_FragCoord.y < 5.0) discard; }
"""
    persp = [((-0.9 * 0.5, -0.9 * 0.5, 0.2, 0.5), (1, 0, 0, 1)), ((0.9 * 2, -0.8 * 2, 0.8, 2), (0, 1, 0, 1)),
             ((0.0, 0.9, 0.5, 1), (0, 0, 1, 1))]
    cs.append(raster_case("p_g_interp_discard", "raster_tri", persp, vs=interp_vs, fs=interp_fs))
    return cs


# ------------------------------------------------------------------ texture helpers

TEX_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 uv;
layout(location = 0) out vec4 v_uv;
void main() { gl_Position = pos; v_uv = uv; }
"""


def quad(u0=0.0, v0=0.0, u1=1.0, v1=1.0):
    """Two triangles covering the viewport; texcoords (u0, v0)..(u1, v1) in the colour slot."""
    p = [(-1, -1), (1, -1), (-1, 1), (1, -1), (1, 1), (-1, 1)]
    t = [(u0, v0), (u1, v0), (u0, v1), (u1, v0), (u1, v1), (u0, v1)]
    return [((x, y, 0.5, 1), (s, tt, 0, 0)) for (x, y), (s, tt) in zip(p, t)]


def tex_case(name, family, tex_setup, sampler, fs, uv=(0, 0, 1, 1), features=None, sampler_binding_type="combined_image_sampler",
             view_kw=None):
    c = Case(name, family)
    c.instance()
    c.device(features=features, raster=True)
    c.image("color0", "R8G8B8A8_UNORM", [W, H], ["color_attachment"])
    c.view("color0_v", "color0")
    tex_setup(c)
    c.view("tex_v", "tex", **(view_kw or {}))
    c.sampler("s", **sampler)
    verts = quad(*uv)
    data = pos_col_vertices(verts)
    c.buffer("vb", 4 * len(data), ["vertex_buffer"])
    c.upload("vb", data={"f32": data}, perturb={"class": "texcoord", "stride": 32, "offset": 16, "components": 2})
    c.shader("vs", TEX_VS, "vert")
    c.shader("fs", fs, "frag")
    c.desc_layout("dl", [{"binding": 0, "type": sampler_binding_type, "stages": ["fragment"]}])
    c.pipeline_layout("pl", ["dl"])
    c.desc_set("ds", "dl", [{"binding": 0, "type": sampler_binding_type,
                             "images": [{"view": "tex_v", "sampler": "s", "layout": "general"}]}])
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        rendering={"color_formats": ["R8G8B8A8_UNORM"]})
    c.exec(render_pass_cmds(["color0_v"], [0, 0, W, H], body=[
        {"cmd": "bind_pipeline", "pipeline": "gp"}, {"cmd": "bind_sets", "layout": "pl", "bind_point": "graphics", "sets": ["ds"]},
        {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb"}]}, {"cmd": "draw", "vertices": 6}]))
    c.snapshot([{"name": "color0", "image": "color0"}])
    return c


SAMPLE_FS = """#version 450
layout(location = 0) in vec4 v_uv;
layout(set = 0, binding = 0) uniform sampler2D t;
layout(location = 0) out vec4 o;
void main() { o = texture(t, v_uv.xy); }
"""


def rgba_tex(c, w=16, h=16, mips=1, fmt="R8G8B8A8_UNORM"):
    c.image("tex", fmt, [w, h], ["sampled"], mips=mips)
    for m in range(mips):
        mw, mh = max(1, w >> m), max(1, h >> m)
        arr = RNG.randint(0, 256, size=(mh, mw, 4)).astype(np.uint8)
        arr[..., 3] = 255
        c.upload_image("tex", array=arr, mip=m)


def texture_cases():
    cs = []
    cs.append(tex_case("p_t_nearest", "tex_sample", rgba_tex, {"mag": "nearest", "min": "nearest"}, SAMPLE_FS))
    cs.append(tex_case("p_t_linear", "tex_sample", rgba_tex, {"mag": "linear", "min": "linear"}, SAMPLE_FS))
    lod_fs = """#version 450
layout(location = 0) in vec4 v_uv;
layout(set = 0, binding = 0) uniform sampler2D t;
layout(location = 0) out vec4 o;
void main() { o = textureLod(t, v_uv.xy, v_uv.x * 5.0) * 0.5 + texture(t, v_uv.xy * (1.0 + v_uv.y * 6.0)) * 0.5; }
"""
    cs.append(tex_case("p_t_mip_trilinear", "tex_sample", lambda c: rgba_tex(c, 32, 32, mips=6),
                       {"mag": "linear", "min": "linear", "mipmap": "linear"}, lod_fs))
    cs.append(tex_case("p_t_address_modes", "tex_sample", rgba_tex,
                       {"mag": "linear", "min": "nearest", "address_u": "mirrored_repeat", "address_v": "clamp_to_border",
                        "border": "float_opaque_white"}, SAMPLE_FS, uv=(-1.3, -0.7, 2.2, 1.9)))

    def depth_tex(c):
        c.image("tex", "D32_SFLOAT", [16, 16], ["sampled"])
        c.upload_image("tex", array=RNG.uniform(0, 1, (16, 16)).astype(np.float32), aspect=["depth"])
    shadow_fs = """#version 450
layout(location = 0) in vec4 v_uv;
layout(set = 0, binding = 0) uniform sampler2DShadow t;
layout(location = 0) out vec4 o;
void main() { float r = texture(t, vec3(v_uv.xy, v_uv.x)); o = vec4(r, r * 0.5, 1.0 - r, 1.0); }
"""
    cs.append(tex_case("p_t_compare", "tex_sample", depth_tex, {"mag": "linear", "min": "linear", "compare": "less"},
                       shadow_fs))

    def cube_tex(c):
        c.image("tex", "R8G8B8A8_UNORM", [8, 8], ["sampled"], layers=6, flags=["cube_compatible"])
        arr = RNG.randint(0, 256, size=(6, 8, 8, 4)).astype(np.uint8)
        c.upload_image("tex", array=arr, layers=6)
    cube_fs = """#version 450
layout(location = 0) in vec4 v_uv;
layout(set = 0, binding = 0) uniform samplerCube t;
layout(location = 0) out vec4 o;
void main() { vec2 a = (v_uv.xy * 2.0 - 1.0) * 3.14159; o = texture(t, vec3(cos(a.x) * cos(a.y), sin(a.y), sin(a.x) * cos(a.y))); }
"""
    cs.append(tex_case("p_t_cube", "tex_sample", cube_tex, {"mag": "linear", "min": "linear"}, cube_fs,
                       view_kw={"view_type": "cube"}))
    gather_fs = """#version 450
layout(location = 0) in vec4 v_uv;
layout(set = 0, binding = 0) uniform sampler2D t;
layout(location = 0) out vec4 o;
void main() { o = textureGather(t, v_uv.xy, 1); }
"""
    cs.append(tex_case("p_t_gather", "tex_sample", rgba_tex, {"mag": "linear", "min": "linear"}, gather_fs))

    def comp_tex(fmt, block_bytes, feature):
        def setup(c):
            c.image("tex", fmt, [16, 16], ["sampled"])
            c.upload_image("tex", array=RNG.randint(0, 256, size=(16 // 4) * (16 // 4) * block_bytes).astype(np.uint8))
        return setup
    cs.append(tex_case("p_t_bc1", "formats_copy_blit", comp_tex("BC1_RGBA_UNORM_BLOCK", 8, None),
                       {"mag": "nearest", "min": "nearest"}, SAMPLE_FS,
                       features={"VkPhysicalDeviceFeatures": {"textureCompressionBC": True}}))
    cs.append(tex_case("p_t_bc7", "formats_copy_blit", comp_tex("BC7_UNORM_BLOCK", 16, None),
                       {"mag": "nearest", "min": "nearest"}, SAMPLE_FS,
                       features={"VkPhysicalDeviceFeatures": {"textureCompressionBC": True}}))
    cs.append(tex_case("p_t_etc2", "formats_copy_blit", comp_tex("ETC2_R8G8B8A8_UNORM_BLOCK", 16, None),
                       {"mag": "nearest", "min": "nearest"}, SAMPLE_FS,
                       features={"VkPhysicalDeviceFeatures": {"textureCompressionETC2": True}}))
    cs.append(tex_case("p_t_astc", "formats_copy_blit", comp_tex("ASTC_4x4_UNORM_BLOCK", 16, None),
                       {"mag": "nearest", "min": "nearest"}, SAMPLE_FS,
                       features={"VkPhysicalDeviceFeatures": {"textureCompressionASTC_LDR": True}}))
    return cs


# ------------------------------------------------------------------ transfer, queries, sync, instance

def transfer_cases():
    cs = []
    c = Case("p_x_copy_blit", "formats_copy_blit")
    c.instance(); c.device()
    c.buffer("src", 16 * 16 * 4, [])
    c.upload("src", array=RNG.randint(0, 256, size=16 * 16 * 4).astype(np.uint8))
    c.image("a", "R8G8B8A8_UNORM", [16, 16], [])
    c.image("b", "R8G8B8A8_UNORM", [W, H], [])
    c.image("d", "R8G8B8A8_UNORM", [W, H], [])
    c.exec([{"cmd": "copy_buffer_to_image", "buffer": "src", "image": "a", "regions": [{"extent": [16, 16]}]},
            {"cmd": "clear_color_image", "image": "b", "color": {"f32": [0.25, 0.5, 0.75, 1.0]}},
            {"cmd": "blit_image", "src": "a", "dst": "b", "filter": "linear",
             "regions": [{"src_offsets": [[0, 0, 0], [16, 16, 1]], "dst_offsets": [[3, 5, 0], [59, 50, 1]]}]},
            {"cmd": "copy_image", "src": "b", "dst": "d", "regions": [{"src_offset": [8, 8, 0], "dst_offset": [0, 0, 0],
                                                                        "extent": [40, 40, 1]}]}])
    c.snapshot([{"name": "b", "image": "b"}, {"name": "d", "image": "d"}])
    cs.append(c)
    c = Case("p_x_blit_formats", "formats_copy_blit")
    c.instance(); c.device()
    c.image("a", "R8G8B8A8_UNORM", [32, 32], [])
    c.upload_image("a", array=RNG.randint(0, 256, size=(32, 32, 4)).astype(np.uint8))
    c.image("f16", "R16G16B16A16_SFLOAT", [W, H], [])
    c.image("u565", "R5G6B5_UNORM_PACK16", [W, H], [])
    c.exec([{"cmd": "blit_image", "src": "a", "dst": "f16", "filter": "linear",
             "regions": [{"src_offsets": [[0, 0, 0], [32, 32, 1]], "dst_offsets": [[0, 0, 0], [W, H, 1]]}]},
            {"cmd": "blit_image", "src": "a", "dst": "u565", "filter": "nearest",
             "regions": [{"src_offsets": [[32, 0, 0], [0, 32, 1]], "dst_offsets": [[0, 0, 0], [W, H, 1]]}]}])
    c.snapshot([{"name": "f16", "image": "f16"}, {"name": "u565", "image": "u565"}])
    cs.append(c)
    c = Case("p_x_clears", "formats_copy_blit")
    c.instance(); c.device(raster=True)
    c.image("color0", "R8G8B8A8_UNORM", [W, H], ["color_attachment"])
    c.view("color0_v", "color0")
    c.image("ds", "D32_SFLOAT_S8_UINT", [W, H], ["depth_stencil_attachment"])
    c.view("ds_v", "ds")
    c.buffer("fb", 256, [])
    c.exec([{"cmd": "clear_depth_stencil_image", "image": "ds", "depth": 0.375, "stencil": 77},
            {"cmd": "fill_buffer", "buffer": "fb", "offset": 16, "size": 128, "value": 0xDEADBEEF},
            {"cmd": "update_buffer", "buffer": "fb", "offset": 200, "data": {"u32": [1, 2, 3, 4]}},
            *render_pass_cmds(["color0_v"], [0, 0, W, H], clear=(0.1, 0.2, 0.3, 0.4), body=[
                {"cmd": "clear_attachments", "attachments": [{"aspect": ["color"], "color_attachment": 0,
                                                              "clear": {"f32": [0.9, 0.8, 0.7, 0.6]}}],
                 "rects": [[5, 7, 20, 30], [30, 30, 30, 20]]}])])
    c.snapshot([{"name": "color0", "image": "color0"}, {"name": "depth", "image": "ds", "aspect": "depth"},
                {"name": "stencil", "image": "ds", "aspect": "stencil"}, {"name": "fb", "buffer": "fb", "elem": "u32"}])
    cs.append(c)
    return cs


def query_sync_cases():
    cs = []
    c = raster_case("p_q_occlusion", "queries_sync", rand_tris(30), depth_fmt="D32_SFLOAT",
                    pipeline_extra={"depth_stencil": {"test": True, "write": True, "compare": "less"}},
                    features={"VkPhysicalDeviceFeatures": {"occlusionQueryPrecise": True}})
    c.ops.insert(2, {"op": "query_pool", "name": "qp", "type": "occlusion", "count": 2})
    ex = [o for o in c.ops if o["op"] == "exec"][0]
    ex["cmds"].insert(0, {"cmd": "reset_query_pool", "pool": "qp"})
    body_start = [k for k, x in enumerate(ex["cmds"]) if x["cmd"] == "bind_pipeline"][0]
    ex["cmds"].insert(body_start, {"cmd": "begin_query", "pool": "qp", "query": 0, "precise": True})
    end = [k for k, x in enumerate(ex["cmds"]) if x["cmd"] == "end_rendering"][0]
    ex["cmds"].insert(end, {"cmd": "end_query", "pool": "qp", "query": 0})
    ex["cmds"].insert(end + 1, {"cmd": "begin_query", "pool": "qp", "query": 1})
    ex["cmds"].insert(end + 2, {"cmd": "draw", "vertices": 3})
    ex["cmds"].insert(end + 3, {"cmd": "end_query", "pool": "qp", "query": 1})
    c.ops.append({"op": "read_queries", "name": "occ", "pool": "qp", "flags": ["64", "wait"]})
    cs.append(c)
    c = Case("p_q_timestamps", "queries_sync", category="procedural")
    c.instance(); c.device(features={"VkPhysicalDeviceVulkan12Features": {"hostQueryReset": True}})
    c.op("query_pool", name="ts", type="timestamp", count=4)
    c.op("reset_query_pool", pool="ts")
    c.exec([{"cmd": "write_timestamp", "pool": "ts", "query": k, "stage": ["bottom_of_pipe"]} for k in range(4)])
    c.op("read_queries", name="tsr", pool="ts", flags=["64", "wait"])
    c.op("read_queries", name="tsr_avail", pool="ts", flags=["64", "with_availability"])
    cs.append(c)
    c = Case("p_s_fence_event", "queries_sync", category="procedural")
    c.instance(); c.device()
    c.op("fence", name="f", signaled=False)
    c.op("fence", name="fs", signaled=True)
    c.op("fence_status", name="st0", fence="f")
    c.op("fence_status", name="st1", fence="fs")
    c.op("wait_fence", name="w0", fences=["f"], timeout_ns=0)
    c.op("event", name="e")
    c.op("event_status", name="es0", event="e")
    c.op("set_event", event="e")
    c.op("event_status", name="es1", event="e")
    c.op("record", name="cb", cmds=[{"cmd": "reset_event", "event": "e"}])
    c.op("submit", cbs=["cb"], fence="f")
    c.op("wait_fence", name="w1", fences=["f"])
    c.op("fence_status", name="st2", fence="f")
    c.op("event_status", name="es2", event="e")
    c.op("reset_fence", fences=["f", "fs"])
    c.op("fence_status", name="st3", fence="fs")
    cs.append(c)
    c = Case("p_s_timeline", "queries_sync", category="procedural")
    c.instance(); c.device(features={"VkPhysicalDeviceVulkan12Features": {"timelineSemaphore": True}})
    c.op("semaphore", name="t", timeline=True, initial=5)
    c.op("semaphore_value", name="v0", semaphore="t")
    c.op("signal_semaphore", semaphore="t", value=9)
    c.op("semaphore_value", name="v1", semaphore="t")
    c.op("wait_semaphores", name="w0", semaphores=[["t", 9]], timeout_ns=0)
    c.op("wait_semaphores", name="w1", semaphores=[["t", 12]], timeout_ns=0)
    c.op("record", name="cb", cmds=[])
    c.op("submit", cbs=["cb"], wait=[{"semaphore": "t", "value": 9}], signal=[{"semaphore": "t", "value": 20}])
    c.op("wait_semaphores", name="w2", semaphores=[["t", 20]])
    c.op("semaphore_value", name="v2", semaphore="t")
    cs.append(c)
    return cs


def inst_cases():
    cs = []
    c = Case("p_i_query_all", "inst_dev", category="procedural")
    c.instance(); c.device()
    c.query("all", {"api_version": True, "device_version": True, "properties": "all", "features": "all",
                    "formats": "all", "memory": True, "queue_families": True, "device_extensions": True,
                    "instance_extensions": True})
    cs.append(c)
    c = Case("p_i_errors", "errors_robust", category="procedural")
    c.instance()
    c.op("enumerate", name="dev_exts3", what="device_extensions", capacity=3)
    c.op("enumerate", name="inst_exts0", what="instance_extensions", capacity=0)
    c.op("enumerate", name="phys", what="physical_devices")
    c.op("image_format_props", name="ifp_ok", format="R8G8B8A8_UNORM", usage=["sampled", "color_attachment"])
    c.op("image_format_props", name="ifp_bad", format="BC1_RGB_UNORM_BLOCK", usage=["color_attachment"])
    c.op("image_format_props", name="ifp_3d_depth", format="D32_SFLOAT", type="3d", usage=["depth_stencil_attachment"])
    c.device(extensions=["VK_KHR_not_a_real_extension"])
    cs.append(c)
    c = Case("p_i_feature_missing", "errors_robust", category="procedural")
    c.instance()
    c.device(features={"VkPhysicalDeviceFeatures": {"logicOp": True}})    # unsupported: VK_ERROR_FEATURE_NOT_PRESENT
    c.op("buffer", name="b", size=64)                                       # skipped: no device
    cs.append(c)
    return cs


def perf_cases():
    c = raster_case("p_perf_fill", "perf_fill", rand_tris(300, z=False, alpha=0.3),
                    pipeline_extra={"blend": {"attachments": [{"enable": True, "src_color": "src_alpha",
                                                                "dst_color": "one_minus_src_alpha"}]}})
    c.category = "performance"
    c.meta["timed_run_index"] = 0
    snap = c.ops.pop()
    ex = c.ops.pop()
    c.run("fill", 40, ex["cmds"], snap["items"], warmup=2)
    return [c]


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else HERE.parent / "runs" / "pilot"
    spec = CorpusSpec(root=out, gen_dir=out, public_dir=out / "cases", hidden_dir=out / "unused",
                      assets_dir=out / "assets", double_keys=("f64",))
    writer = CorpusWriter(spec, verbose=False)
    cases = compute_cases() + raster_cases() + texture_cases() + transfer_cases() + query_sync_cases() + inst_cases() + perf_cases()
    for c in cases:
        c.write(writer, "public")
    print(f"pilot corpus: {len(cases)} cases -> {out / 'cases'}")


if __name__ == "__main__":
    main(sys.argv)
