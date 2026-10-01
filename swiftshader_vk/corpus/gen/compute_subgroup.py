"""Family `compute_subgroup`: subgroup operations at the device's subgroup size (4) (replay).

Integer data only (a float reduction's result depends on the order of
additions, which the spec leaves open). Each case evaluates a list of subgroup
operations per invocation, some inside data-dependent branches (only the active
invocations take part), and writes one output region per operation.
"""
from __future__ import annotations

import numpy as np

from common import compute_case, pub_name, rng, write_all

FAMILY = "compute_subgroup"
N = 256

OPS = {
    "add": "subgroupAdd(x)", "mul": "subgroupMul(x | 1u)", "min": "subgroupMin(x)", "max": "subgroupMax(x)",
    "and": "subgroupAnd(x)", "or": "subgroupOr(x)", "xor": "subgroupXor(x)",
    "iadd": "subgroupInclusiveAdd(x)", "eadd": "subgroupExclusiveAdd(x)", "imax": "subgroupInclusiveMax(x)",
    "ballot": "subgroupBallotBitCount(subgroupBallot((x & 1u) == 1u)) | (subgroupBallotFindLSB(subgroupBallot(x > 99u)) << 8)",
    "bcast": "subgroupBroadcast(x, 2u) ^ subgroupBroadcastFirst(x + 1u)",
    "shuffle": "subgroupShuffle(x, (gl_SubgroupInvocationID + k) % gl_SubgroupSize)",
    "shufxor": "subgroupShuffleXor(x, 1u) + subgroupShuffleXor(x, 2u) * 3u",
    "shufupdown": "subgroupShuffleUp(x, 1u) ^ subgroupShuffleDown(x, 1u)",
    "quad": "subgroupQuadBroadcast(x, 1u) ^ subgroupQuadSwapHorizontal(x) ^ subgroupQuadSwapDiagonal(x * 3u)",
    "vote": "uint(subgroupAll(x > 3u)) | (uint(subgroupAny(x == 7u)) << 1) | (uint(subgroupAllEqual(x & 1u)) << 2)",
    "elect": "uint(subgroupElect()) | (gl_SubgroupInvocationID << 4) | (gl_SubgroupSize << 8)",
    "div_add": "((x & 3u) != 0u) ? subgroupAdd(x) : subgroupMax(x)",
    "div_ballot": "((x & 1u) == 0u) ? subgroupBallotBitCount(subgroupBallot(true)) : 77u",
}

PUBLIC = [
    {"tag": "reduce", "seed": 401, "ops": ["add", "mul", "min", "max", "and", "or", "xor", "iadd", "eadd", "imax"]},
    {"tag": "ballot_shuffle", "seed": 402, "ops": ["ballot", "bcast", "shuffle", "shufxor", "shufupdown", "quad"]},
    {"tag": "vote_divergent", "seed": 403, "ops": ["vote", "elect", "div_add", "div_ballot", "add"]},
]


def shader(ops):
    lines = "\n".join(f"  o[{k}u * {N}u + i] = {OPS[op]};" for k, op in enumerate(ops))
    return f"""#version 450
#extension GL_KHR_shader_subgroup_basic : require
#extension GL_KHR_shader_subgroup_arithmetic : require
#extension GL_KHR_shader_subgroup_ballot : require
#extension GL_KHR_shader_subgroup_shuffle : require
#extension GL_KHR_shader_subgroup_shuffle_relative : require
#extension GL_KHR_shader_subgroup_quad : require
#extension GL_KHR_shader_subgroup_vote : require
layout(local_size_x = 64) in;
layout(set = 0, binding = 0) readonly buffer A {{ uint a[]; }};
layout(set = 0, binding = 1) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{ uint k; }} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x, k = pc.k;
  uint x = a[i] + k;
{lines}
}}
"""


def build(name, p):
    r = rng(p["seed"])
    a = r.randint(0, 200, size=N).astype(np.uint32)
    a[::9] = 7
    ops = p["ops"]
    items = [{"name": op, "buffer": "out", "offset": k * N * 4, "size": N * 4, "elem": "u32"} for k, op in enumerate(ops)]
    return compute_case(name, FAMILY, source=shader(ops), push_bytes=4,
                        buffers=[{"name": "a", "size": N * 4, "array": a, "binding": 0},
                                 {"name": "out", "size": len(ops) * N * 4, "binding": 1}],
                        dispatches=[{"groups": [N // 64], "push": {"u32": [1]}, "snapshot": items},
                                    {"groups": [N // 64], "push": {"u32": [p["seed"] % 5 + 2]}, "snapshot": items}])


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
