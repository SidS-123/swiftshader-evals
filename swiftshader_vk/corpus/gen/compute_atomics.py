"""Family `compute_atomics`: atomic operations whose final results do not depend on the
order in which invocations reach them (replay).

Targets: a storage buffer and workgroup shared memory (written out after a
barrier). Operations: add, min, max, and, or, xor (commutative and associative).
Return values of atomics are never stored: which invocation sees which old
value is not defined (Stage 4 determinism finding). Storage-image atomics are
not exercised: the reference's image atomics lose updates under concurrency
(counts differ run to run; Stage 7 finding), so no reference output exists to
grade them against.
"""
from __future__ import annotations

import numpy as np

from common import compute_case, pub_name, rng, write_all

FAMILY = "compute_atomics"
N = 1024

PUBLIC = [
    {"tag": "buffer", "seed": 501, "bins": 16, "targets": ["buffer"]},
    {"tag": "shared", "seed": 502, "bins": 8, "targets": ["shared", "buffer"]},
]


def build(name, p):
    r = rng(p["seed"])
    bins = p["bins"]
    a = r.randint(0, 2**20, size=N).astype(np.uint32)
    body, setup_imgs, extra_b, extra_w, items = [], None, [], [], []
    tg = p["targets"]
    if "buffer" in tg:
        body.append(f"""  uint b = x % {bins}u;
  atomicAdd(o[b], x & 0xffffu); atomicMin(o[{bins}u + b], x); atomicMax(o[{2 * bins}u + b], x ^ k);
  atomicAnd(o[{3 * bins}u + b], ~(1u << (x & 31u))); atomicOr(o[{4 * bins}u + b], 1u << (x % 31u));
  atomicXor(o[{5 * bins}u + b], x * 2654435761u);""")
    if "shared" in tg:
        body.append(f"""  atomicAdd(sh[x % {bins}u], 1u + (x & 7u)); atomicMax(shm[x % {bins}u], x);
  barrier();
  if (gl_LocalInvocationIndex < {bins}u) {{
    atomicAdd(o[{6 * bins}u + gl_LocalInvocationIndex], sh[gl_LocalInvocationIndex]);
    atomicMax(o[{7 * bins}u + gl_LocalInvocationIndex], shm[gl_LocalInvocationIndex]);
  }}""")
    if "image" in tg:
        body.append("""  imageAtomicAdd(img, ivec2(int(x & 15u), int((x >> 4) & 15u)), 1u);
  imageAtomicMax(img, ivec2(int(i & 15u), int((i >> 4) & 15u)), x);""")
    shared_decl = f"shared uint sh[{bins}];\nshared uint shm[{bins}];" if "shared" in tg else ""
    image_decl = "layout(set = 0, binding = 2, r32ui) uniform coherent uimage2D img;" if "image" in tg else ""
    init_shared = (f"  if (gl_LocalInvocationIndex < {bins}u) {{ sh[gl_LocalInvocationIndex] = 0u; "
                   f"shm[gl_LocalInvocationIndex] = 0u; }}\n  barrier();") if "shared" in tg else ""
    src = f"""#version 450
layout(local_size_x = 64) in;
layout(set = 0, binding = 0) readonly buffer A {{ uint a[]; }};
layout(set = 0, binding = 1) buffer O {{ uint o[]; }};
{image_decl}
layout(push_constant) uniform PC {{ uint k; }} pc;
{shared_decl}
void main() {{
  uint i = gl_GlobalInvocationID.x, k = pc.k;
  uint x = a[i] + k;
{init_shared}
{chr(10).join(body)}
}}
"""
    # initial values: min targets start at 0xffffffff, and targets at 0xffffffff, others 0
    init = np.zeros(8 * bins, dtype=np.uint32)
    init[bins:2 * bins] = 0xFFFFFFFF
    init[3 * bins:4 * bins] = 0xFFFFFFFF
    items.append({"name": "buffer_totals", "buffer": "out", "elem": "u32"})

    def setup(c):
        if "image" in tg:
            c.image("img", "R32_UINT", [16, 16], ["storage"])
            c.view("img_v", "img")
            c.exec([{"cmd": "clear_color_image", "image": "img", "color": {"u32": [0, 0, 0, 0]}}])
    if "image" in tg:
        extra_b = [{"binding": 2, "type": "storage_image", "stages": ["compute"]}]
        extra_w = [{"binding": 2, "type": "storage_image", "images": [{"view": "img_v", "layout": "general"}]}]
        items.append({"name": "image_counts", "image": "img"})
    return compute_case(name, FAMILY, source=src, push_bytes=4,
                        buffers=[{"name": "a", "size": N * 4, "array": a, "binding": 0},
                                 {"name": "out", "size": init.nbytes, "array": init, "binding": 1}],
                        extra_setup=setup, extra_bindings=extra_b, extra_writes=extra_w,
                        dispatches=[{"groups": [N // 64], "push": {"u32": [0]}, "snapshot": items},
                                    {"groups": [N // 64], "push": {"u32": [p["seed"] % 11 + 1]}, "snapshot": items}])


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
