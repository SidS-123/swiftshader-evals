"""Family `vertex_input`: vertex formats, strides, offsets, instancing, index types,
primitive restart, topologies, indirect draws (replay).

Pass 1 draws every vertex as a 4x4 point on a grid; each attribute is passed
`flat` to its own R32G32B32A32_SFLOAT target, so every fetched value lands
exactly in a snapshot (a misdecoded format, stride, offset or instance step is
visible texel by texel). Vertices are fetched through an index buffer of the
case's index type, and a per-instance attribute moves each instance.
Pass 2 draws indexed triangle strips with primitive restart (flat colour from
the provoking vertex). Pass 3 repeats pass 2 through draw_indexed_indirect.
"""
from __future__ import annotations

import numpy as np

from common import pub_name, raster_scene, rng, write_all

FAMILY = "vertex_input"

# format: (numpy dtype, components, GLSL type, kind) ; kind: unorm snorm float uint sint scaled packed
VFORMATS = {
    "R8G8B8A8_UNORM": (np.uint8, 4, "vec4", "unorm"),
    "B8G8R8A8_UNORM": (np.uint8, 4, "vec4", "unorm"),
    "R8G8_SNORM": (np.int8, 2, "vec2", "snorm"),
    "R16G16B16A16_UNORM": (np.uint16, 4, "vec4", "unorm"),
    "R16G16_SNORM": (np.int16, 2, "vec2", "snorm"),
    "R16G16_SINT": (np.int16, 2, "ivec2", "sint"),
    "R16G16B16A16_UINT": (np.uint16, 4, "uvec4", "uint"),
    "R32G32B32_SFLOAT": (np.float32, 3, "vec3", "float"),
    "R32G32_SFLOAT": (np.float32, 2, "vec2", "float"),
    "R16G16B16A16_SFLOAT": (np.float16, 4, "vec4", "float"),
    "R32_UINT": (np.uint32, 1, "uint", "uint"),
    "R32G32B32A32_SINT": (np.int32, 4, "ivec4", "sint"),
    "R8G8B8A8_USCALED": (np.uint8, 4, "vec4", "scaled"),
    "R16G16_SSCALED": (np.int16, 2, "vec2", "scaled"),
    "A2B10G10R10_UNORM_PACK32": (np.uint32, 1, "vec4", "packed"),
}

PUBLIC = [
    {"tag": "formats_a", "seed": 801, "attrs": ["R8G8B8A8_UNORM", "R16G16_SINT", "R32G32B32_SFLOAT"],
     "pos": "R32G32_SFLOAT", "stride_pad": 4, "index": "uint16", "instances": 2},
    {"tag": "formats_b", "seed": 802, "attrs": ["B8G8R8A8_UNORM", "R16G16B16A16_SFLOAT", "R32_UINT"],
     "pos": "R16G16_SNORM", "stride_pad": 0, "index": "uint32", "instances": 1},
    {"tag": "formats_c", "seed": 803, "attrs": ["A2B10G10R10_UNORM_PACK32", "R8G8B8A8_USCALED", "R16G16B16A16_UINT"],
     "pos": "R32G32_SFLOAT", "stride_pad": 12, "index": "uint8", "instances": 3},
    {"tag": "strips_indirect", "seed": 804, "attrs": ["R8G8_SNORM", "R32G32B32A32_SINT"],
     "pos": "R32G32_SFLOAT", "stride_pad": 8, "index": "uint16", "instances": 2},
]


def random_values(r, fmt, n):
    dt, comps, _, kind = VFORMATS[fmt]
    if kind == "float":
        return (r.randint(-512, 512, size=(n, comps)) / 64).astype(dt)
    if kind == "packed":
        return r.randint(0, 2**32, size=(n, 1), dtype=np.uint64).astype(np.uint32)
    info = np.iinfo(dt)
    return r.randint(info.min, int(info.max) + 1, size=(n, comps)).astype(dt)


def pack(fields, n):
    """Interleave per-vertex fields [(array (n, k) of a dtype)] with 4-byte alignment; returns (bytes, offsets, stride)."""
    offsets, off = [], 0
    for a in fields:
        offsets.append(off)
        off += a.itemsize * a.shape[1]
        off = (off + 3) // 4 * 4
    stride = off
    buf = np.zeros((n, stride), dtype=np.uint8)
    for a, o in zip(fields, offsets):
        raw = np.ascontiguousarray(a).view(np.uint8).reshape(n, -1)
        buf[:, o:o + raw.shape[1]] = raw
    return buf, offsets, stride


def pos_values(fmt, xy):
    dt = VFORMATS[fmt][0]
    if dt == np.int16:                                   # SNORM16 positions
        return np.round(np.clip(xy, -1, 1) * 32767).astype(np.int16)
    return xy.astype(dt)


def shaders(attrs, inst_loc):
    ins = [f"layout(location = 0) in vec2 pos;", f"layout(location = {inst_loc}) in vec2 inst_off;"]
    outs, assigns, fs_ins, fs_outs = [], [], [], []
    for k, f in enumerate(attrs):
        gtype = VFORMATS[f][2]
        ins.append(f"layout(location = {k + 1}) in {gtype} a{k};")
        outs.append(f"layout(location = {k}) flat out vec4 o{k};")
        n = {"vec4": 4, "vec3": 3, "vec2": 2, "ivec2": 2, "ivec4": 4, "uvec4": 4, "uint": 1}[gtype]
        pad = ", 0.0" * (4 - n)
        if gtype == "uint":
            expr = f"vec4(float(a{k} & 0xffffu), float(a{k} >> 16), 0.0, 1.0)"
        elif gtype[0] in "iu":                           # integer vectors: converted (correctly rounded)
            expr = f"vec4(vec{n}(a{k}){pad})" if n < 4 else f"vec4(a{k})"
        else:
            expr = f"vec4(a{k}{pad})" if n < 4 else f"a{k}"
        assigns.append(f"  o{k} = {expr};")
        fs_ins.append(f"layout(location = {k}) flat in vec4 o{k};")
        fs_outs.append(f"layout(location = {k}) out vec4 t{k};")
    vs = "#version 450\n" + "\n".join(ins + outs) + """
void main() {
  gl_Position = vec4(pos + inst_off, 0.5, 1.0);
  gl_PointSize = 4.0;
""" + "\n".join(assigns) + "\n}\n"
    fs = "#version 450\n" + "\n".join(fs_ins + fs_outs) + "\nvoid main() {\n" + \
        "\n".join(f"  t{k} = o{k};" for k in range(len(attrs))) + "\n}\n"
    return vs, fs


def build(name, p):
    r = rng(p["seed"])
    W = H = 64
    attrs = p["attrs"]
    grid = 16                                             # 16 x 16 points of 4x4 texels
    rows = grid // max(1, p["instances"])                 # each instance covers its own band of rows
    n = grid * rows
    gx, gy = np.meshgrid(np.arange(grid), np.arange(rows))
    xy = np.stack([(gx.ravel() + 0.5) * 2 / grid - 1, (gy.ravel() + 0.5) * 2 / grid - 1], axis=1)[:n]
    fields = [pos_values(p["pos"], xy)] + [random_values(r, f, n) for f in attrs]
    if p["stride_pad"]:
        fields.append(np.zeros((n, p["stride_pad"]), np.uint8))
    vbuf, offsets, stride = pack(fields, n)
    inst_off = np.stack([np.zeros(p["instances"]), np.arange(p["instances"]) * 2.0 / p["instances"]], axis=1).astype(np.float32)
    inst_loc = len(attrs) + 1
    vertex_input = {
        "bindings": [{"binding": 0, "stride": stride}, {"binding": 1, "stride": 8, "rate": "instance"}],
        "attributes": [{"location": 0, "binding": 0, "format": p["pos"], "offset": offsets[0]}] +
                      [{"location": k + 1, "binding": 0, "format": f, "offset": offsets[k + 1]} for k, f in enumerate(attrs)] +
                      [{"location": inst_loc, "binding": 1, "format": "R32G32_SFLOAT", "offset": 0}]}
    itype = p["index"]
    idt = {"uint8": np.uint8, "uint16": np.uint16, "uint32": np.uint32}[itype]
    order = r.permutation(n).astype(idt)
    vs, fs = shaders(attrs, inst_loc)
    targets = tuple("R32G32B32A32_SFLOAT" for _ in attrs)
    features = {"VkPhysicalDeviceFeatures": {"largePoints": True}}
    exts = None
    if itype == "uint8":
        features["VkPhysicalDeviceIndexTypeUint8FeaturesKHR"] = {"indexTypeUint8": True}
        exts = ["VK_KHR_index_type_uint8"]
    passes = [{"arrays": [vbuf, inst_off], "index": (order, itype), "vertex_input": vertex_input,
               "pipeline": {"topology": "point_list"},
               "draw": [{"cmd": "draw_indexed", "indices": n, "instances": p["instances"]}]}]
    # strips with primitive restart: groups of 4 indices then a restart index
    restart = {"uint8": 0xFF, "uint16": 0xFFFF, "uint32": 0xFFFFFFFF}[itype]
    strip = []
    for s in range(min(n // 4, 24)):
        strip += [int(x) for x in r.choice(n, 4, replace=False)] + [restart]
    strip = np.array(strip, dtype=idt)
    tri_vs = vs.replace("gl_PointSize = 4.0;", "gl_PointSize = 1.0;")
    passes.append({"arrays": [vbuf, inst_off], "index": (strip, itype), "vertex_input": vertex_input, "vs": tri_vs,
                   "load": "clear", "pipeline": {"topology": "triangle_strip", "primitive_restart": True},
                   "draw": [{"cmd": "draw_indexed", "indices": len(strip), "instances": 1, "vertex_offset": 0}]})
    # the same through draw_indexed_indirect: {indexCount, instanceCount, firstIndex, vertexOffset, firstInstance}
    cmd = np.array([len(strip), 1, 0, 0, 0], dtype=np.uint32)
    passes.append({"arrays": [vbuf, inst_off], "index": (strip, itype), "vertex_input": vertex_input, "vs": tri_vs,
                   "load": "clear", "pipeline": {"topology": "triangle_strip", "primitive_restart": True},
                   "extra_buffers": {f"ind{len(passes)}": cmd},
                   "draw": [{"cmd": "draw_indexed_indirect", "buffer": f"ind{len(passes)}"}]})
    return raster_scene(name, FAMILY, passes=passes, size=(W, H), targets=targets, vs=vs, fs=fs,
                        features=features, extensions=exts, perturb=False)


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
