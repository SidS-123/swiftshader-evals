"""Family `compute_types`: vectors, matrices, structs, arrays, shared memory, buffer
device address (replay).

Variants (each a template with parameters):
  struct430  an std430 struct array with vec3/float/uvec2/ivec4/mat2 members (vec3
             alignment rules), transformed field by field
  ubo140     an std140 uniform block (arrays of floats and vec3 at 16-byte strides)
             indexed by data
  matrix     mat2/mat3/mat4 products, transpose, outer product, integer determinants
  arrays     local arrays with data-dependent indexing, nested arrays, struct copies
  shared     workgroup shared memory: a transpose through shared, with barriers
  bda        buffer device address: walking a linked list through pointers
Float operands are small dyadic values (k/8, k/16), so every product and sum is
exact in float32 and the results are compared exactly (no `allow`).
"""
from __future__ import annotations

import numpy as np

from common import compute_case, pub_name, rng, write_all

FAMILY = "compute_types"

PUBLIC = [
    {"tag": "struct430", "seed": 301, "variant": "struct430", "n": 128},
    {"tag": "ubo140", "seed": 302, "variant": "ubo140", "n": 128, "table": 32},
    {"tag": "matrix", "seed": 303, "variant": "matrix", "n": 128},
    {"tag": "shared_bda", "seed": 304, "variant": "shared", "n": 256, "tile": 8},
]


def dyadic(r, n, lo=-8, hi=8, den=8):
    return (r.randint(lo * den, hi * den + 1, size=n) / den).astype(np.float32)


def v_struct430(p, r):
    n = p["n"]
    # struct S { vec3 p; float w; uvec2 u; ivec4 q; mat2 m; }  -> 64 bytes in std430
    raw = np.zeros((n, 16), dtype=np.uint32)
    f = raw.view(np.float32)
    f[:, 0:3] = dyadic(r, 3 * n).reshape(n, 3)
    f[:, 3] = dyadic(r, n)
    raw[:, 4:6] = r.randint(0, 2**32, size=(n, 2), dtype=np.uint64).astype(np.uint32)
    raw[:, 8:12] = r.randint(-1000, 1000, size=(n, 4)).astype(np.int32).view(np.uint32)
    f[:, 12:16] = dyadic(r, 4 * n).reshape(n, 4)
    src = f"""#version 450
layout(local_size_x = 64) in;
struct S {{ vec3 p; float w; uvec2 u; ivec4 q; mat2 m; }};
layout(std430, set = 0, binding = 0) readonly buffer In {{ S s[]; }};
layout(std430, set = 0, binding = 1) writeonly buffer Out {{ S t[]; }};
layout(push_constant) uniform PC {{ float k; }} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x;
  S a = s[i], b = s[(i + 1u) % {n}u];
  precise vec3 p = a.p * pc.k + b.p.zxy;
  precise float w = dot(vec2(a.w, b.w), vec2(2.0, 0.5));
  S o;
  o.p = p;
  o.w = w;
  o.u = a.u ^ b.u.yx;
  o.q = a.q * ivec4(1, -2, 3, -4) + b.q.wzyx;
  o.m = a.m * b.m;
  t[i] = o;
}}"""
    return src, [{"name": "in", "size": n * 64, "array": raw, "binding": 0},
                 {"name": "out", "size": n * 64, "binding": 1}], 4, \
        [{"u32": None}], [{"name": "out", "buffer": "out", "elem": "u32"}], [[1.0], [0.5]], n // 64


def v_ubo140(p, r):
    n, t = p["n"], p["table"]
    # uniform U { float scale[T]; vec3 offs[T]; }  std140: both arrays at a 16-byte stride
    ubo = np.zeros((2 * t, 4), dtype=np.float32)
    ubo[:t, 0] = dyadic(r, t, -4, 4, 16)
    ubo[t:, 0:3] = dyadic(r, 3 * t, -4, 4, 16).reshape(t, 3)
    idx = r.randint(0, t, size=n).astype(np.uint32)
    src = f"""#version 450
layout(local_size_x = 64) in;
layout(std140, set = 0, binding = 0) uniform U {{ float scale[{t}]; vec3 offs[{t}]; }} u;
layout(std430, set = 0, binding = 1) readonly buffer I {{ uint idx[]; }};
layout(std430, set = 0, binding = 2) writeonly buffer O {{ vec4 o[]; }};
layout(push_constant) uniform PC {{ float k; }} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x;
  uint j = idx[i];
  precise vec4 r = vec4(u.offs[j] * u.scale[(j + 3u) % {t}u], u.scale[j] * pc.k);
  o[i] = r;
}}"""
    return src, [{"name": "ubo", "size": ubo.nbytes, "usage": ["uniform_buffer"], "array": ubo, "binding": 0,
                  "type": "uniform_buffer"},
                 {"name": "idx", "size": n * 4, "array": idx, "binding": 1},
                 {"name": "out", "size": n * 16, "binding": 2}], 4, None, \
        [{"name": "out", "buffer": "out", "elem": "f32"}], [[1.0], [-2.0]], n // 64


def v_matrix(p, r):
    n = p["n"]
    m = dyadic(r, n * 16, -4, 4, 4).reshape(n, 16)
    ints = r.randint(-5, 6, size=(n, 9)).astype(np.float32)
    data = np.concatenate([m, ints, np.zeros((n, 7), np.float32)], axis=1)   # 32 floats per element
    src = f"""#version 450
layout(local_size_x = 64) in;
layout(std430, set = 0, binding = 0) readonly buffer In {{ float d[]; }};
layout(std430, set = 0, binding = 1) writeonly buffer Out {{ float o[]; }};
layout(push_constant) uniform PC {{ float k; }} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x, b = i * 32u, w = i * 32u;
  mat4 A = mat4(d[b+0u], d[b+1u], d[b+2u], d[b+3u], d[b+4u], d[b+5u], d[b+6u], d[b+7u],
                d[b+8u], d[b+9u], d[b+10u], d[b+11u], d[b+12u], d[b+13u], d[b+14u], d[b+15u]);
  mat3 Mi = mat3(d[b+16u], d[b+17u], d[b+18u], d[b+19u], d[b+20u], d[b+21u], d[b+22u], d[b+23u], d[b+24u]);
  precise vec4 v = A * vec4(1.0, -0.5, 0.25, pc.k);
  precise vec4 v2 = transpose(A) * v;
  precise mat2 op = outerProduct(vec2(A[0][0], A[1][1]), vec2(pc.k, 2.0));
  precise mat3 mm = matrixCompMult(Mi, transpose(Mi));
  precise float det = determinant(Mi);
  for (int c = 0; c < 4; ++c) {{ o[w + uint(c)] = v[c]; o[w + 4u + uint(c)] = v2[c]; }}
  o[w + 8u] = op[0][0]; o[w + 9u] = op[0][1]; o[w + 10u] = op[1][0]; o[w + 11u] = op[1][1];
  for (int c = 0; c < 3; ++c) for (int e = 0; e < 3; ++e) o[w + 12u + uint(c * 3 + e)] = mm[c][e];
  o[w + 21u] = det;
  for (uint z = 22u; z < 32u; ++z) o[w + z] = 0.0;
}}"""
    return src, [{"name": "in", "size": data.nbytes, "array": data, "binding": 0},
                 {"name": "out", "size": n * 128, "binding": 1}], 4, None, \
        [{"name": "out", "buffer": "out", "elem": "f32"}], [[1.0], [0.25]], n // 64


def v_shared(p, r):
    n, tile = p["n"], p["tile"]
    data = r.randint(0, 2**32, size=n, dtype=np.uint64).astype(np.uint32)
    # a linked list for the BDA part: node = {uint64 next; uint value; uint pad}
    nodes = 32
    order = r.permutation(nodes)
    src = f"""#version 450
#extension GL_EXT_buffer_reference : require
layout(local_size_x = {tile}, local_size_y = {tile}) in;
// The address arrives in push constants and is dereferenced directly. Dereferencing
// an address loaded from memory crashes the reference (Stage 7 finding), so cases
// never chase pointers.
layout(buffer_reference, std430, buffer_reference_align = 4) buffer Table {{ uint v[]; }};
layout(std430, set = 0, binding = 0) readonly buffer In {{ uint d[]; }};
layout(std430, set = 0, binding = 1) writeonly buffer Out {{ uint o[]; }};
layout(push_constant) uniform PC {{ Table table; uint k; }} pc;
shared uint tileA[{tile}][{tile}];
shared uint arr[{tile * tile}];
void main() {{
  uvec2 l = gl_LocalInvocationID.xy;
  uint g = gl_WorkGroupID.x * {tile * tile}u + l.y * {tile}u + l.x;
  tileA[l.y][l.x] = d[g] + pc.k;
  arr[l.y * {tile}u + l.x] = d[g] >> 3;
  barrier();
  uint t = tileA[l.x][l.y];
  uint s = 0u;
  for (uint k = 0u; k < {tile}u; ++k) s += arr[(l.y * {tile}u + k) % {tile * tile}u];
  uint walk = 0u;
  for (uint k = 0u; k < (l.x + l.y) % 7u; ++k) walk = walk * 31u + pc.table.v[(g * 5u + k * 3u) % {nodes}u];
  o[g] = t ^ (s * 3u) ^ walk;
}}"""
    table = r.randint(0, 2**31, size=nodes).astype(np.uint32)
    groups = n // (tile * tile)
    return src, [{"name": "in", "size": n * 4, "array": data, "binding": 0},
                 {"name": "out", "size": n * 4, "binding": 1},
                 {"name": "nodes", "size": nodes * 4, "usage": ["storage_buffer", "shader_device_address"],
                  "array": table}], 12, \
        None, [{"name": "out", "buffer": "out", "elem": "u32"}], None, groups


def build(name: str, p: dict):
    r = rng(p["seed"])
    variant = {"struct430": v_struct430, "ubo140": v_ubo140, "matrix": v_matrix, "shared": v_shared}[p["variant"]]
    src, buffers, push_bytes, extra, items, pushes, groups = variant(p, r)
    features = None
    if p["variant"] == "shared":
        features = {"VkPhysicalDeviceVulkan12Features": {"bufferDeviceAddress": True}}
        dispatches = [{"groups": [groups], "push": {"address": [{"buffer": "nodes"}], "u32": [k]}, "snapshot": items}
                      for k in (0, p["seed"] % 13 + 1)]
        return compute_case(name, FAMILY, source=src, buffers=buffers, push_bytes=push_bytes, features=features,
                            dispatches=dispatches, extra_setup=extra)
    dispatches = [{"groups": [groups], "push": {"f32": k}, "snapshot": items} for k in pushes]
    return compute_case(name, FAMILY, source=src, buffers=buffers, push_bytes=push_bytes, dispatches=dispatches)


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
