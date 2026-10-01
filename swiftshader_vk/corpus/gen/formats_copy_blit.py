"""Family `formats_copy_blit`: image formats as attachments, sampled sources and copy
targets; copies, blits, clears, resolves; compressed formats (replay).

Formats are drawn from the frozen device profile (spec/device_profile.json):
only formats whose optimal-tiling features include what the case uses
(COLOR_ATTACHMENT, SAMPLED_IMAGE, BLIT_SRC/DST, TRANSFER_SRC/DST), so every
choice is valid on the reference and the seed alone gives the hidden split a
different set.

Variants:
  rt_formats     one pass rendering the same geometry into 3-4 colour formats
                 (float/unorm, uint and sint targets each get matching outputs)
  sample_formats texels of several formats sampled with nearest filtering into an
                 R32G32B32A32_SFLOAT target: every format's decode is checked exactly
  copies         buffer -> image with row length / image height, image -> image across
                 compatible formats, image -> buffer, mips and array layers
  blits          scaled, flipped, filtered blits with format conversion
  clears         clear_color_image on mip/layer ranges, clear_depth_stencil, partial
                 clear_attachments inside rendering
  compressed     BC / ETC2 / EAC / ASTC (LDR) textures sampled with nearest filtering
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from common import (POS_COL_INPUT, RASTER_FEATURES, ROOT, TEX_VS, Case, pos_col_vertices, pub_name, quad, random_triangles,
                    raster_scene, render_pass_cmds, rng, write_all)

FAMILY = "formats_copy_blit"
PROFILE = json.loads((ROOT / "spec" / "device_profile.json").read_text())["device"]["formats"]

PUBLIC = [
    {"tag": "rt_formats", "seed": 1301, "variant": "rt_formats", "count": 4},
    {"tag": "sample_formats", "seed": 1302, "variant": "sample_formats", "count": 4},
    {"tag": "copies", "seed": 1303, "variant": "copies"},
    {"tag": "blits", "seed": 1304, "variant": "blits"},
    {"tag": "clears", "seed": 1305, "variant": "clears"},
    {"tag": "compressed_bc", "seed": 1306, "variant": "compressed", "family": "BC"},
    {"tag": "compressed_etc_astc", "seed": 1307, "variant": "compressed", "family": "ETC2_ASTC"},
]


def features(fmt):
    p = PROFILE.get("VK_FORMAT_" + fmt, {}).get("VkFormatProperties", {})
    return {f.replace("VK_FORMAT_FEATURE_", "").replace("_BIT", "") for f in p.get("optimalTilingFeatures", [])}


def formats_with(*needed, exclude=("D", "S8", "X8", "G8", "G1", "B8G8R8G8", "G8B8G8R8", "R10X6", "R12X4", "G10X6",
                                   "G12X4", "G16", "B16G16R16G16", "G16B16G16R16", "BC", "ETC", "EAC", "ASTC", "PVRTC")):
    out = []
    for name in sorted(PROFILE):
        f = name.replace("VK_FORMAT_", "")
        if any(f.startswith(e) for e in exclude):
            continue
        if set(needed) <= features(f):
            out.append(f)
    return out


def numeric(fmt):
    return "uint" if "UINT" in fmt else ("sint" if "SINT" in fmt else "float")


# ------------------------------------------------------------------ variants

def v_rt_formats(name, p, r):
    pool = formats_with("COLOR_ATTACHMENT")
    targets = list(r.choice(pool, p["count"], replace=False))
    outs, writes = [], []
    for k, f in enumerate(targets):
        kind = numeric(f)
        t = {"uint": "uvec4", "sint": "ivec4", "float": "vec4"}[kind]
        outs.append(f"layout(location = {k}) out {t} o{k};")
        if kind == "float":
            writes.append(f"  o{k} = v_col * vec4(1.0, 0.75, 1.5, 1.0) - vec4(0.0, 0.0, 0.25, 0.0);")
        elif kind == "uint":
            writes.append(f"  o{k} = uvec4(v_col * 255.0) + uvec4({k}u);")
        else:
            writes.append(f"  o{k} = ivec4(v_col * 200.0) - ivec4(100);")
    fs = "#version 450\nlayout(location = 0) in vec4 v_col;\n" + "\n".join(outs) + "\nvoid main() {\n" + \
        "\n".join(writes) + "\n}\n"
    clear = (0.25, 0.5, 0.75, 1.0)
    passes = [{"verts": random_triangles(r, 30, spread=0.6)}, {"verts": random_triangles(r, 20, spread=0.6)}]
    # integer targets cannot be cleared with float values: clear every target to zero bits instead
    if any(numeric(f) != "float" for f in targets):
        clear = {"u32": [0, 0, 0, 0]}
    return raster_scene(name, FAMILY, passes=passes, targets=tuple(targets), fs=fs, clear=clear,
                        pipeline_layout=None)


def v_sample_formats(name, p, r):
    pool = [f for f in formats_with("SAMPLED_IMAGE") if numeric(f) == "float"]
    picks = list(r.choice(pool, p["count"], replace=False))
    # one textured quad per format, side by side, each sampled at texel centres (nearest)
    c = Case(name, FAMILY)
    c.instance()
    c.device(raster=True)
    W, H = 64, 16
    c.image("color0", "R32G32B32A32_SFLOAT", [W, H], ["color_attachment"])
    c.view("color0_v", "color0")
    c.sampler("s", mag="nearest", min="nearest")
    c.desc_layout("dl", [{"binding": 0, "type": "combined_image_sampler", "stages": ["fragment"]}])
    c.pipeline_layout("pl", ["dl"])
    fs = """#version 450
layout(location = 0) in vec4 v_uv;
layout(location = 0) out vec4 o;
layout(set = 0, binding = 0) uniform sampler2D t;
void main() { o = texture(t, v_uv.xy); }
"""
    c.shader("vs", TEX_VS, "vert")
    c.shader("fs", fs, "frag")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        rendering={"color_formats": ["R32G32B32A32_SFLOAT"]})
    body = []
    for k, f in enumerate(picks):
        c.image(f"t{k}", f, [16, 16], ["sampled"])
        tb = texel_bytes(f)
        c.upload_image(f"t{k}", array=r.randint(0, 256, size=16 * 16 * tb).astype(np.uint8))
        c.view(f"t{k}_v", f"t{k}")
        c.desc_set(f"ds{k}", "dl", [{"binding": 0, "type": "combined_image_sampler",
                                     "images": [{"view": f"t{k}_v", "sampler": "s", "layout": "general"}]}])
        x0 = -1 + 2 * k / len(picks)
        x1 = x0 + 2 / len(picks)
        data = pos_col_vertices(quad(0, 0, 1, 1, x0, -1, x1, 1))
        c.buffer(f"vb{k}", 4 * len(data), ["vertex_buffer"])
        c.upload(f"vb{k}", data={"f32": data})
        body += [{"cmd": "bind_pipeline", "pipeline": "gp"},
                 {"cmd": "bind_sets", "layout": "pl", "bind_point": "graphics", "sets": [f"ds{k}"]},
                 {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": f"vb{k}"}]}, {"cmd": "draw", "vertices": 6}]
    c.exec(render_pass_cmds(["color0_v"], [0, 0, W, H], body=body))
    c.snapshot([{"name": "decoded", "image": "color0"}])
    c.meta["formats"] = picks
    return c


def texel_bytes(fmt):
    """Bytes per texel from the format name: packed formats carry their width, others sum their components."""
    if "PACK32" in fmt:
        return 4
    if "PACK16" in fmt:
        return 2
    if "PACK8" in fmt:
        return 1
    import re
    bits = sum(int(b) for b in re.findall(r"[RGBAX](\d+)", fmt.split("_")[0]))
    return max(1, bits // 8)


def v_copies(name, p, r):
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    # buffer -> image (row length 40 for a 32-wide region), image (2 mips, 2 layers)
    c.buffer("src", 40 * 24 * 4 * 2, [])
    c.upload("src", array=r.randint(0, 256, size=40 * 24 * 4 * 2).astype(np.uint8))
    c.image("a", "R8G8B8A8_UNORM", [32, 24], [], mips=2, layers=2)
    c.image("b", "R32_UINT", [32, 24], [], mips=2, layers=2)            # size-compatible with RGBA8
    c.buffer("dst", 32 * 24 * 4, [])
    regions = [{"buffer_offset": 0, "row_length": 40, "image_height": 24, "sub": {"mip": 0, "base_layer": 0, "layers": 2},
                "extent": [32, 24, 1]},
               {"buffer_offset": 512, "row_length": 0, "sub": {"mip": 1, "base_layer": 1, "layers": 1},
                "offset": [3, 2, 0], "extent": [13, 10, 1]}]
    c.exec([{"cmd": "clear_color_image", "image": "a", "color": {"f32": [0.1, 0.2, 0.3, 0.4]}},
            {"cmd": "copy_buffer_to_image", "buffer": "src", "image": "a", "regions": regions},
            {"cmd": "copy_image", "src": "a", "dst": "b", "regions": [
                {"src_sub": {"mip": 0, "base_layer": 1}, "dst_sub": {"mip": 0, "base_layer": 0},
                 "src_offset": [4, 3, 0], "dst_offset": [0, 0, 0], "extent": [28, 21, 1]},
                {"src_sub": {"mip": 1, "base_layer": 1}, "dst_sub": {"mip": 1, "base_layer": 1}, "extent": [16, 12, 1]}]},
            {"cmd": "copy_image_to_buffer", "buffer": "dst", "image": "b", "regions": [
                {"buffer_offset": 0, "row_length": 32, "sub": {"mip": 0, "base_layer": 0}, "extent": [32, 24, 1]}]}])
    c.snapshot([{"name": "a_mip0", "image": "a", "mip": 0}, {"name": "a_mip1", "image": "a", "mip": 1},
                {"name": "b_mip1", "image": "b", "mip": 1}, {"name": "dst", "buffer": "dst", "elem": "u32"}])
    c.exec([{"cmd": "copy_buffer", "src": "dst", "dst": "src", "regions": [[64, 0, 1024], [0, 2048, 256]]}])
    c.snapshot([{"name": "src", "buffer": "src", "elem": "u32"}])
    return c


def v_blits(name, p, r):
    pool_src = formats_with("BLIT_SRC", "SAMPLED_IMAGE_FILTER_LINEAR")
    pool_dst = [f for f in formats_with("BLIT_DST") if numeric(f) == "float"]
    sfmt = str(r.choice([f for f in pool_src if numeric(f) == "float"]))
    dfmts = list(r.choice(pool_dst, 2, replace=False))
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    c.image("src", sfmt, [24, 16], [])
    c.upload_image("src", array=r.randint(0, 256, size=24 * 16 * texel_bytes(sfmt)).astype(np.uint8))
    c.image("d0", dfmts[0], [48, 40], [])
    c.image("d1", dfmts[1], [20, 12], [])
    c.exec([{"cmd": "clear_color_image", "image": "d0", "color": {"f32": [0, 0, 0, 1]}},
            {"cmd": "clear_color_image", "image": "d1", "color": {"f32": [0, 0, 0, 1]}},
            {"cmd": "blit_image", "src": "src", "dst": "d0", "filter": "linear",
             "regions": [{"src_offsets": [[0, 0, 0], [24, 16, 1]], "dst_offsets": [[2, 3, 0], [46, 37, 1]]}]},
            {"cmd": "blit_image", "src": "src", "dst": "d1", "filter": "nearest",
             "regions": [{"src_offsets": [[24, 0, 0], [0, 16, 1]], "dst_offsets": [[0, 12, 0], [20, 0, 1]]}]}])
    c.snapshot([{"name": "d0", "image": "d0"}, {"name": "d1", "image": "d1"}])
    c.exec([{"cmd": "blit_image", "src": "d0", "dst": "d1", "filter": "linear",
             "regions": [{"src_offsets": [[5, 5, 0], [41, 33, 1]], "dst_offsets": [[1, 1, 0], [19, 11, 1]]}]}])
    c.snapshot([{"name": "d1_again", "image": "d1"}])
    c.meta["formats"] = [sfmt] + dfmts
    return c


def v_clears(name, p, r):
    c = Case(name, FAMILY)
    c.instance()
    c.device(raster=True)
    c.image("img", "R16G16B16A16_UNORM", [32, 32], ["color_attachment"], mips=3, layers=3)
    c.view("img_v", "img", mips=1, layers=1, base_layer=1)
    c.image("ds", "D32_SFLOAT_S8_UINT", [32, 32], ["depth_stencil_attachment"])
    col = [float(x) for x in r.uniform(0, 1, 4)]
    c.exec([{"cmd": "clear_color_image", "image": "img", "color": {"f32": col}},
            {"cmd": "clear_color_image", "image": "img", "color": {"f32": [0.5, 0.25, 0.125, 1.0]},
             "ranges": [{"base_mip": 1, "mips": 2, "base_layer": 1, "layers": 2}]},
            {"cmd": "clear_depth_stencil_image", "image": "ds", "depth": float(r.uniform(0, 1)),
             "stencil": int(r.randint(0, 256))},
            *render_pass_cmds(["img_v"], [0, 0, 32, 32], load="load", body=[
                {"cmd": "clear_attachments", "attachments": [{"aspect": ["color"], "color_attachment": 0,
                                                              "clear": {"f32": [float(x) for x in r.uniform(0, 1, 4)]}}],
                 "rects": [[int(r.randint(0, 16)), int(r.randint(0, 16)), 9, 13], [20, 2, 10, 5]]}])])
    c.snapshot([{"name": "mip0_l1", "image": "img", "mip": 0, "base_layer": 1, "layers": 1},
                {"name": "mip1", "image": "img", "mip": 1}, {"name": "mip2", "image": "img", "mip": 2},
                {"name": "depth", "image": "ds", "aspect": "depth"}, {"name": "stencil", "image": "ds", "aspect": "stencil"}])
    return c


# 8-byte blocks; every other compressed format has 16. Prefixes end at the "_" so that
# ETC2_R8G8B8A8 (16) is not taken for ETC2_R8G8B8 and EAC_R11G11 (16) not for EAC_R11.
BLOCK = {"BC1_": 8, "BC4_": 8, "ETC2_R8G8B8_": 8, "ETC2_R8G8B8A1_": 8, "EAC_R11_": 8}


def block_bytes(fmt):
    for k, v in BLOCK.items():
        if fmt.startswith(k):
            return v
    return 16


def block_dims(fmt):
    import re
    m = re.match(r"ASTC_(\d+)x(\d+)_", fmt)
    return (int(m.group(1)), int(m.group(2))) if m else (4, 4)


def v_compressed(name, p, r):
    prefixes = ("BC",) if p["family"] == "BC" else ("ETC2", "EAC", "ASTC")
    pool = [n.replace("VK_FORMAT_", "") for n in sorted(PROFILE)
            if n.replace("VK_FORMAT_", "").startswith(prefixes) and "SAMPLED_IMAGE" in features(n.replace("VK_FORMAT_", ""))]
    picks = list(r.choice(pool, 3, replace=False))
    c = Case(name, FAMILY)
    c.instance()
    feats = {"VkPhysicalDeviceFeatures": {"textureCompressionBC": True}} if p["family"] == "BC" else \
        {"VkPhysicalDeviceFeatures": {"textureCompressionETC2": True, "textureCompressionASTC_LDR": True}}
    feats.update({k: dict(v) for k, v in RASTER_FEATURES.items()})
    c.device(features=feats)
    W, H = 48, 16
    c.image("color0", "R32G32B32A32_SFLOAT", [W, H], ["color_attachment"])
    c.view("color0_v", "color0")
    c.sampler("s", mag="nearest", min="nearest")
    c.desc_layout("dl", [{"binding": 0, "type": "combined_image_sampler", "stages": ["fragment"]}])
    c.pipeline_layout("pl", ["dl"])
    fs = """#version 450
layout(location = 0) in vec4 v_uv;
layout(location = 0) out vec4 o;
layout(set = 0, binding = 0) uniform sampler2D t;
void main() { o = texture(t, v_uv.xy); }
"""
    c.shader("vs", TEX_VS, "vert")
    c.shader("fs", fs, "frag")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        rendering={"color_formats": ["R32G32B32A32_SFLOAT"]})
    body = []
    for k, f in enumerate(picks):
        bw, bh = block_dims(f)
        tw, th = 4 * bw, 4 * bh
        c.image(f"t{k}", f, [tw, th], ["sampled"])
        c.upload_image(f"t{k}", array=r.randint(0, 256, size=16 * block_bytes(f)).astype(np.uint8))
        c.view(f"t{k}_v", f"t{k}")
        c.desc_set(f"ds{k}", "dl", [{"binding": 0, "type": "combined_image_sampler",
                                     "images": [{"view": f"t{k}_v", "sampler": "s", "layout": "general"}]}])
        x0 = -1 + 2 * k / len(picks)
        data = pos_col_vertices(quad(0, 0, 1, 1, x0, -1, x0 + 2 / len(picks), 1))
        c.buffer(f"vb{k}", 4 * len(data), ["vertex_buffer"])
        c.upload(f"vb{k}", data={"f32": data})
        body += [{"cmd": "bind_pipeline", "pipeline": "gp"},
                 {"cmd": "bind_sets", "layout": "pl", "bind_point": "graphics", "sets": [f"ds{k}"]},
                 {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": f"vb{k}"}]}, {"cmd": "draw", "vertices": 6}]
    c.exec(render_pass_cmds(["color0_v"], [0, 0, W, H], body=body))
    c.snapshot([{"name": "decoded", "image": "color0"}])
    c.meta["formats"] = picks
    return c


VARIANTS = {"rt_formats": v_rt_formats, "sample_formats": v_sample_formats, "copies": v_copies, "blits": v_blits,
            "clears": v_clears, "compressed": v_compressed}


def build(name, p):
    return VARIANTS[p["variant"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
