"""Family `descriptors_push`: descriptor types, dynamic offsets, descriptor arrays and
indexing, update-after-bind, multiple sets, push constants, specialization constants
(replay).

All variants are compute shaders writing u32/f32 results (exact).
  dynamic      uniform_buffer_dynamic + storage_buffer_dynamic, dynamic offsets
               (multiples of the 256-byte alignment) changing per dispatch
  arrays       a storage-buffer array indexed non-uniformly, partially bound, and
               rewritten after the command buffer was recorded (update-after-bind)
  push_spec    a 128-byte push constant block of mixed types; specialization
               constants for a loop bound, a scale and the workgroup size, with
               two pipelines from the same module
  multi_set    three descriptor sets bound with first = 0 and first = 1, rebound
               between dispatches
"""
from __future__ import annotations

import numpy as np

from common import Case, pub_name, rng, write_all

FAMILY = "descriptors_push"
N = 128

PUBLIC = [
    {"tag": "dynamic", "seed": 1601, "variant": "dynamic", "offsets": [[0, 256], [512, 0], [256, 768]]},
    {"tag": "arrays_uab", "seed": 1602, "variant": "arrays", "count": 4},
    {"tag": "push_spec", "seed": 1603, "variant": "push_spec", "specs": [[3, 2.5, 64], [7, -1.25, 32]]},
    {"tag": "multi_set", "seed": 1604, "variant": "multi_set"},
]


def run(c, pipeline, layout, sets, groups, push=None, dyn=None, first=0):
    cmds = [{"cmd": "bind_pipeline", "pipeline": pipeline},
            {"cmd": "bind_sets", "layout": layout, "sets": sets, "first": first, **({"dynamic_offsets": dyn} if dyn else {})}]
    if push is not None:
        cmds.append({"cmd": "push_constants", "layout": layout, "stages": ["compute"], "data": push})
    cmds.append({"cmd": "dispatch", "groups": groups})
    return cmds


def v_dynamic(name, p, r):
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    c.buffer("ubo", 1024 + 256, ["uniform_buffer"])
    c.upload("ubo", array=r.randint(0, 2**31, size=(1024 + 256) // 4).astype(np.uint32))
    c.buffer("ssbo", 1024 + N * 4, ["storage_buffer"])
    c.upload("ssbo", array=r.randint(0, 2**31, size=(1024 + N * 4) // 4).astype(np.uint32))
    c.buffer("out", N * 4 * len(p["offsets"]), ["storage_buffer"])
    c.shader("cs", f"""#version 450
layout(local_size_x = 64) in;
layout(std140, set = 0, binding = 0) uniform U {{ uvec4 u[16]; }};
layout(std430, set = 0, binding = 1) readonly buffer S {{ uint s[{N}]; }};
layout(std430, set = 0, binding = 2) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{ uint slot; }} pc;
void main() {{ uint i = gl_GlobalInvocationID.x; o[pc.slot * {N}u + i] = s[i] ^ u[i % 16u][i % 4u] ^ (pc.slot << 28); }}
""", "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "uniform_buffer_dynamic", "stages": ["compute"]},
                         {"binding": 1, "type": "storage_buffer_dynamic", "stages": ["compute"]},
                         {"binding": 2, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"], push_constants=[{"stages": ["compute"], "offset": 0, "size": 4}])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "uniform_buffer_dynamic", "buffers": [{"buffer": "ubo", "range": 256}]},
                            {"binding": 1, "type": "storage_buffer_dynamic", "buffers": [{"buffer": "ssbo", "range": N * 4}]},
                            {"binding": 2, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    for k, dyn in enumerate(p["offsets"]):
        c.exec(run(c, "p", "pl", ["ds"], [N // 64], push={"u32": [k]}, dyn=dyn))
        c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    return c


def v_arrays(name, p, r):
    n = p["count"]
    c = Case(name, FAMILY)
    c.instance()
    c.device(features={"VkPhysicalDeviceVulkan12Features": {
        "shaderStorageBufferArrayNonUniformIndexing": True, "descriptorBindingPartiallyBound": True,
        "descriptorBindingStorageBufferUpdateAfterBind": True, "runtimeDescriptorArray": True}})
    for k in range(n + 1):
        c.buffer(f"b{k}", N * 4, ["storage_buffer"])
        c.upload(f"b{k}", array=r.randint(0, 2**31, size=N).astype(np.uint32))
    c.buffer("out", N * 4, ["storage_buffer"])
    c.shader("cs", f"""#version 450
#extension GL_EXT_nonuniform_qualifier : require
layout(local_size_x = 64) in;
layout(std430, set = 0, binding = 0) readonly buffer B {{ uint v[]; }} bufs[{n}];
layout(std430, set = 0, binding = 1) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{ uint used; }} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x;
  uint k = (i * 7u) % pc.used;
  o[i] = bufs[nonuniformEXT(k)].v[i] + k * 1000u;
}}
""", "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_buffer", "count": n, "stages": ["compute"],
                          "flags": ["partially_bound", "update_after_bind"]},
                         {"binding": 1, "type": "storage_buffer", "stages": ["compute"], "flags": ["update_after_bind"]}],
                  flags=["update_after_bind_pool"])
    c.pipeline_layout("pl", ["dl"], push_constants=[{"stages": ["compute"], "offset": 0, "size": 4}])
    # only the first n-1 elements are written: element n-1 stays unbound (partially bound, never read)
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": f"b{k}"} for k in range(n - 1)]},
                            {"binding": 1, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    c.exec(run(c, "p", "pl", ["ds"], [N // 64], push={"u32": [n - 1]}))
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    # record, then rewrite element 0 and fill the last element, then submit: update-after-bind
    c.op("record", name="cb", cmds=run(c, "p", "pl", ["ds"], [N // 64], push={"u32": [n]}))
    c.op("desc_write", set="ds", writes=[{"binding": 0, "array_element": 0, "type": "storage_buffer",
                                          "buffers": [{"buffer": f"b{n}"}]},
                                         {"binding": 0, "array_element": n - 1, "type": "storage_buffer",
                                          "buffers": [{"buffer": "b1"}]}])
    c.op("fence", name="f")
    c.op("submit", cbs=["cb"], fence="f")
    c.op("wait_fence", name="wf", fences=["f"])
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    return c


def v_push_spec(name, p, r):
    c = Case(name, FAMILY)
    c.instance()
    c.device(features={"VkPhysicalDeviceVulkan13Features": {"maintenance4": True}})  # LocalSizeId
    c.buffer("out", N * 4 * 2, ["storage_buffer"])
    c.shader("cs", f"""#version 450
layout(local_size_x_id = 2) in;
layout(constant_id = 0) const uint LOOPS = 1u;
layout(constant_id = 1) const float SCALE = 1.0;
layout(std430, set = 0, binding = 0) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{
  uvec4 a; ivec4 b; vec4 c; mat2 m; uint slot; uint pad0; uint pad1; uint pad2; vec4 tail[3];
}} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x;
  uint acc = pc.a[i % 4u] ^ uint(pc.b[(i + 1u) % 4u]);
  for (uint k = 0u; k < LOOPS; ++k) acc = acc * 2654435761u + k;
  precise float f = pc.c[i % 4u] * SCALE + pc.m[i % 2u][(i / 2u) % 2u] + pc.tail[i % 3u].w;
  o[pc.slot * {N}u + i] = acc ^ floatBitsToUint(f);
}}
""", "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"], push_constants=[{"stages": ["compute"], "offset": 0, "size": 128}])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    for k, (loops, scale, lsize) in enumerate(p["specs"]):
        c.compute_pipeline(f"p{k}", "pl", "cs", spec=[{"id": 0, "u32": loops}, {"id": 1, "f32": scale},
                                                      {"id": 2, "u32": lsize}])
    for k, (_, _, lsize) in enumerate(p["specs"]):
        push = {"u32": [int(x) for x in r.randint(0, 2**31, 4)],
                "i32": [int(x) for x in r.randint(-2**30, 2**30, 4)],
                "f32": [float(x) for x in r.randint(-64, 64, 4) / 8] + [float(x) for x in r.randint(-64, 64, 4) / 8]}
        push2 = {"u32": [k, 0, 0, 0], "f32": [float(x) for x in r.randint(-64, 64, 12) / 8]}
        data = {"hex": "".join(np.concatenate([np.array(push["u32"], np.uint32).view(np.uint8),
                                               np.array(push["i32"], np.int32).view(np.uint8),
                                               np.array(push["f32"], np.float32).view(np.uint8),
                                               np.array(push2["u32"], np.uint32).view(np.uint8),
                                               np.array(push2["f32"], np.float32).view(np.uint8)]).tobytes().hex())}
        c.exec(run(c, f"p{k}", "pl", ["ds"], [N // lsize], push=data))
        c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    return c


def v_multi_set(name, p, r):
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    for k in range(4):
        c.buffer(f"in{k}", N * 4, ["storage_buffer"])
        c.upload(f"in{k}", array=r.randint(0, 2**31, size=N).astype(np.uint32))
    c.buffer("out", N * 4, ["storage_buffer"])
    c.shader("cs", f"""#version 450
layout(local_size_x = 64) in;
layout(std430, set = 0, binding = 0) writeonly buffer O {{ uint o[]; }};
layout(std430, set = 1, binding = 0) readonly buffer A {{ uint a[]; }};
layout(std430, set = 2, binding = 3) readonly buffer B {{ uint b[]; }};
void main() {{ uint i = gl_GlobalInvocationID.x; o[i] = a[i] * 3u + (b[{N - 1}u - i] >> 1); }}
""", "comp")
    c.desc_layout("dl0", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]}])
    c.desc_layout("dl1", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]}])
    c.desc_layout("dl2", [{"binding": 3, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl0", "dl1", "dl2"])
    c.desc_set("s0", "dl0", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    for k in range(4):
        c.desc_set(f"a{k}", "dl1", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": f"in{k}"}]}])
        c.desc_set(f"b{k}", "dl2", [{"binding": 3, "type": "storage_buffer", "buffers": [{"buffer": f"in{3 - k}"}]}])
    c.compute_pipeline("p", "pl", "cs")
    c.exec(run(c, "p", "pl", ["s0", "a0", "b0"], [N // 64]))
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    c.exec([*run(c, "p", "pl", ["s0", "a1", "b2"], [N // 64]),
            {"cmd": "bind_sets", "layout": "pl", "first": 1, "sets": ["a3"]},
            {"cmd": "dispatch", "groups": [N // 64]}])
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    return c


VARIANTS = {"dynamic": v_dynamic, "arrays": v_arrays, "push_spec": v_push_spec, "multi_set": v_multi_set}


def build(name, p):
    return VARIANTS[p["variant"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
