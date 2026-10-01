"""Family `mrt_renderpass`: multiple colour attachments, load/store operations, dynamic
rendering, render passes with subpasses and input attachments, render-pass resolves
(replay).

Variants:
  subpasses   a VkRenderPass with two subpasses: the first writes two colour
              attachments (and depth), the second reads them as input attachments
              (subpassLoad) and writes a third; dependencies between them
  loadstore   dynamic rendering over pre-filled attachments: load / clear per attachment,
              several draws per pass
  rp_resolve  a render pass with a 4x colour attachment resolved into a single-sample one
"""
from __future__ import annotations

from common import (COLOR_FS, FULLSCREEN_VS, POS_COL_INPUT, Case, pos_col_vertices, pub_name, random_triangles,
                    render_pass_cmds, rng, write_all)

FAMILY = "mrt_renderpass"

MRT2_FS = """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o0;
layout(location = 1) out vec4 o1;
void main() { o0 = v_col; o1 = vec4(v_col.bgr * 0.5, 1.0 - v_col.a); }
"""
COMBINE_FS = """#version 450
layout(input_attachment_index = 0, set = 0, binding = 0) uniform subpassInput a0;
layout(input_attachment_index = 1, set = 0, binding = 1) uniform subpassInput a1;
layout(location = 0) out vec4 o;
layout(push_constant) uniform PC { vec4 w; } pc;
void main() { o = subpassLoad(a0) * pc.w.x + subpassLoad(a1) * pc.w.y; }
"""
FULL_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
void main() { gl_Position = pos; }
"""

PUBLIC = [
    {"tag": "subpasses", "seed": 1501, "variant": "subpasses", "n": 30, "formats": ["R8G8B8A8_UNORM", "R16G16B16A16_SFLOAT",
                                                                                     "R8G8B8A8_UNORM"]},
    {"tag": "loadstore", "seed": 1502, "variant": "loadstore", "n": 20,
     "formats": ["R8G8B8A8_UNORM", "R32_SFLOAT", "R16G16_UNORM"]},
    {"tag": "rp_resolve", "seed": 1503, "variant": "rp_resolve", "n": 30},
]

FULLSCREEN = [((-1, -1, 0.5, 1), (0, 0, 0, 0)), ((3, -1, 0.5, 1), (0, 0, 0, 0)), ((-1, 3, 0.5, 1), (0, 0, 0, 0))]


def v_subpasses(name, p, r):
    W = H = 64
    f0, f1, f2 = p["formats"]
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    for k, f in enumerate((f0, f1, f2)):
        c.image(f"a{k}", f, [W, H], ["color_attachment", "input_attachment"])
        c.view(f"a{k}_v", f"a{k}")
    c.image("depth", "D32_SFLOAT", [W, H], ["depth_stencil_attachment"])
    c.view("depth_v", "depth")
    c.op("render_pass", name="rp",
         attachments=[{"format": f0, "load": "clear"}, {"format": f1, "load": "clear"},
                      {"format": f2, "load": "clear"}, {"format": "D32_SFLOAT", "load": "clear", "store": "store"}],
         subpasses=[{"color": [{"attachment": 0}, {"attachment": 1}], "depth": {"attachment": 3}},
                    {"color": [{"attachment": 2}], "input": [{"attachment": 0}, {"attachment": 1}]}],
         dependencies=[{"src": 0, "dst": 1, "src_stage": ["color_attachment_output"], "dst_stage": ["fragment_shader"],
                        "src_access": ["color_attachment_write"], "dst_access": ["input_attachment_read"]}])
    c.op("framebuffer", name="fb", render_pass="rp", views=["a0_v", "a1_v", "a2_v", "depth_v"], extent=[W, H])
    c.shader("vs", FULLSCREEN_VS, "vert")
    c.shader("fs0", MRT2_FS, "frag")
    c.shader("vsf", FULL_VS, "vert")
    c.shader("fs1", COMBINE_FS, "frag")
    c.pipeline_layout("pl0")
    c.desc_layout("dl", [{"binding": 0, "type": "input_attachment", "stages": ["fragment"]},
                         {"binding": 1, "type": "input_attachment", "stages": ["fragment"]}])
    c.pipeline_layout("pl1", ["dl"], push_constants=[{"stages": ["fragment"], "offset": 0, "size": 16}])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "input_attachment", "images": [{"view": "a0_v", "layout": "general"}]},
                            {"binding": 1, "type": "input_attachment", "images": [{"view": "a1_v", "layout": "general"}]}])
    c.graphics_pipeline("gp0", "pl0", "vs", "fs0", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H], render_pass="rp",
                        subpass=0, depth_stencil={"test": True, "write": True, "compare": "less"})
    c.graphics_pipeline("gp1", "pl1", "vsf", "fs1", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H], render_pass="rp",
                        subpass=1)
    tris = random_triangles(r, p["n"], spread=0.6)
    for k, (verts, name_) in enumerate(((tris, "vb0"), (FULLSCREEN, "vb1"))):
        data = pos_col_vertices(verts)
        c.buffer(name_, 4 * len(data), ["vertex_buffer"])
        c.upload(name_, data={"f32": data})
    for w in ([0.75, 0.25, 0, 0], [0.25, 1.0, 0, 0]):
        c.exec([{"cmd": "begin_render_pass", "render_pass": "rp", "framebuffer": "fb", "area": [0, 0, W, H],
                 "clears": [{"f32": [0.1, 0.1, 0.2, 1]}, {"f32": [0, 0, 0, 0]}, {"f32": [0, 0, 0, 1]}, {"depth": 1.0}]},
                {"cmd": "bind_pipeline", "pipeline": "gp0"}, {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb0"}]},
                {"cmd": "draw", "vertices": len(tris)},
                {"cmd": "next_subpass"},
                {"cmd": "bind_pipeline", "pipeline": "gp1"},
                {"cmd": "bind_sets", "layout": "pl1", "bind_point": "graphics", "sets": ["ds"]},
                {"cmd": "push_constants", "layout": "pl1", "stages": ["fragment"], "data": {"f32": w}},
                {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": "vb1"}]}, {"cmd": "draw", "vertices": 3},
                {"cmd": "end_render_pass"}])
        c.snapshot([{"name": "a0", "image": "a0"}, {"name": "a1", "image": "a1"}, {"name": "a2", "image": "a2"},
                    {"name": "depth", "image": "depth", "aspect": "depth"}])
    return c


def v_loadstore(name, p, r):
    W, H = 48, 40
    fmts = p["formats"]
    c = Case(name, FAMILY)
    c.instance()
    c.device(raster=True, features={"VkPhysicalDeviceFeatures": {"independentBlend": True}})
    for k, f in enumerate(fmts):
        c.image(f"t{k}", f, [W, H], ["color_attachment"])
        c.view(f"t{k}_v", f"t{k}")
    fs = "#version 450\nlayout(location = 0) in vec4 v_col;\n" + \
        "\n".join(f"layout(location = {k}) out vec4 o{k};" for k in range(len(fmts))) + \
        "\nvoid main() {\n" + "\n".join(f"  o{k} = v_col.{'rgba'[k % 4]}{'gbar'[k % 4]}{'barg'[k % 4]}{'argb'[k % 4]};"
                                         for k in range(len(fmts))) + "\n}\n"
    c.shader("vs", FULLSCREEN_VS, "vert")
    c.shader("fs", fs, "frag")
    c.pipeline_layout("pl")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        rendering={"color_formats": fmts},
                        blend={"attachments": [{"enable": True, "src_color": "src_alpha", "dst_color": "one_minus_src_alpha"}
                                               if k == 0 else {} for k in range(len(fmts))]})
    prefill = [{"cmd": "clear_color_image", "image": f"t{k}", "color": {"f32": [float(x) for x in r.uniform(0, 1, 4)]}}
               for k in range(len(fmts))]
    c.exec(prefill)
    for i in range(3):
        tris = random_triangles(r, p["n"], spread=0.6, alpha=0.6)
        data = pos_col_vertices(tris)
        c.buffer(f"vb{i}", 4 * len(data), ["vertex_buffer"])
        c.upload(f"vb{i}", data={"f32": data})
        loads = [("clear" if (i + k) % 3 == 0 else "load") for k in range(len(fmts))]
        begin = {"cmd": "begin_rendering", "area": [int(r.randint(0, 8)), int(r.randint(0, 8)), 36, 30],
                 "color": [{"view": f"t{k}_v", "load": loads[k], "clear": {"f32": [0.0, 0.5, 1.0, 0.5]}}
                           for k in range(len(fmts))]}
        half = len(tris) // 2
        c.exec([begin, {"cmd": "bind_pipeline", "pipeline": "gp"},
                {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": f"vb{i}"}]},
                {"cmd": "draw", "vertices": half - half % 3},
                {"cmd": "draw", "vertices": len(tris) - (half - half % 3), "first_vertex": half - half % 3},
                {"cmd": "end_rendering"}])
        c.snapshot([{"name": f"t{k}", "image": f"t{k}"} for k in range(len(fmts))])
    return c


def v_rp_resolve(name, p, r):
    W = H = 64
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    c.image("ms", "R8G8B8A8_UNORM", [W, H], ["color_attachment"], samples=4, auto_usage=False)
    c.view("ms_v", "ms")
    c.image("res", "R8G8B8A8_UNORM", [W, H], ["color_attachment"])
    c.view("res_v", "res")
    c.op("render_pass", name="rp",
         attachments=[{"format": "R8G8B8A8_UNORM", "samples": 4, "load": "clear", "store": "dont_care"},
                      {"format": "R8G8B8A8_UNORM", "load": "dont_care", "store": "store"}],
         subpasses=[{"color": [{"attachment": 0}], "resolve": [{"attachment": 1}]}])
    c.op("framebuffer", name="fb", render_pass="rp", views=["ms_v", "res_v"], extent=[W, H])
    c.shader("vs", FULLSCREEN_VS, "vert")
    c.shader("fs", COLOR_FS, "frag")
    c.pipeline_layout("pl")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H], render_pass="rp",
                        subpass=0, multisample={"samples": 4})
    for i in range(2):
        tris = random_triangles(r, p["n"], spread=0.6)
        data = pos_col_vertices(tris)
        c.buffer(f"vb{i}", 4 * len(data), ["vertex_buffer"])
        c.upload(f"vb{i}", data={"f32": data})
        c.exec([{"cmd": "begin_render_pass", "render_pass": "rp", "framebuffer": "fb", "area": [0, 0, W, H],
                 "clears": [{"f32": [0.2, 0.0, 0.3, 1.0]}, {"f32": [0, 0, 0, 0]}]},
                {"cmd": "bind_pipeline", "pipeline": "gp"}, {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": f"vb{i}"}]},
                {"cmd": "draw", "vertices": len(tris)}, {"cmd": "end_render_pass"}])
        c.snapshot([{"name": "resolved", "image": "res"}])
    return c


VARIANTS = {"subpasses": v_subpasses, "loadstore": v_loadstore, "rp_resolve": v_rp_resolve}


def build(name, p):
    return VARIANTS[p["variant"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
