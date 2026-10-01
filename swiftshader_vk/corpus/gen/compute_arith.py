"""Family `compute_arith`: integer and float arithmetic, conversions, bit operations,
GLSL.std.450 built-ins (replay).

Every case runs element-wise operations over two input arrays and writes one
output region per operation; each region is its own snapshot item, so a result
the spec only bounds (division, sqrt, transcendentals) carries its own `allow`
(common.spec_allow) and exactly defined results carry none. Float results are
`precise` (no contraction into fused multiply-adds, which would make the
rounding implementation-dependent). Two dispatches with different push
constants make each case a sequence.

Public parameters are in PUBLIC; the hidden split's are in the private tree.
"""
from __future__ import annotations

import numpy as np

from common import compute_case, pub_name, rng, spec_allow, write_all

FAMILY = "compute_arith"
N = 256           # elements per dispatch (4 workgroups of 64)

# op: (out type, GLSL expression over ua ub (uint), ia ib (int), fa fb (float), k (push uint), allow op)
OPS = {
    # integer
    "uadd": ("u", "ua + ub * k", None), "usub": ("u", "ua - ub", None), "umul": ("u", "ua * ub", None),
    "udiv": ("u", "ua / (ub | 1u)", None), "umod": ("u", "ua % (ub | 1u)", None),
    "idiv": ("i", "ia / ((ib | 1) & 0x7fffffff)", None), "imod": ("i", "ia % ((ib & 0xffff) | 1)", None),
    "ineg_abs": ("i", "abs(ia) - (-ib)", None), "shifts": ("u", "(ua << (ub & 31u)) ^ (ua >> (k & 31u))", None),
    "ishr": ("i", "ia >> (ib & 31)", None), "minmax": ("u", "min(ua, ub) ^ max(ua, ub * k)", None),
    "iclamp": ("i", "clamp(ia, -int(ub & 0xffffu), int(ub & 0xffffu))", None),
    "cmp": ("u", "uint(ua < ub) | (uint(ia < ib) << 1) | (uint(ua == ub) << 2) | (uint(ia >= ib) << 3)", None),
    # bit operations
    "bitcount": ("u", "uint(bitCount(ua)) + (uint(bitCount(ib)) << 8)", None),
    "findlsb_msb": ("u", "uint(findLSB(ua) + 1) | (uint(findMSB(ib) + 1) << 8) | (uint(findMSB(ua) + 1) << 16)", None),
    "reverse": ("u", "bitfieldReverse(ua) ^ ub", None),
    "extract": ("u", "bitfieldExtract(ua, int(ub & 15u), int((ub >> 4) & 15u))", None),
    "sextract": ("i", "bitfieldExtract(ia, int(ub & 15u), int((ub >> 4) & 15u))", None),
    "insert": ("u", "bitfieldInsert(ua, ub, int(k & 15u), int((ub >> 8) & 15u))", None),
    "carry": ("u", "addc(ua, ub)", None),
    "borrow": ("u", "subb(ua, ub)", None),
    "mulext": ("u", "umulx(ua, ub)", None),
    "imulext": ("i", "imulx(ia, ib)", None),
    # exactly defined float operations
    "fadd": ("f", "fa + fb", None), "fsub": ("f", "fa - fb * 2.0", None), "fmul": ("f", "fa * fb", None),
    "fminmax": ("f", "min(fa, fb) + max(fa, -fb)", None), "fclamp": ("f", "clamp(fa, -1.0, abs(fb))", None),
    "fround": ("f", "floor(fa) + ceil(fb) * 0.5 + trunc(fa * 0.25)", None),
    "froundeven": ("f", "roundEven(fa * 2.0) + roundEven(fb)", None),
    "ffract": ("f", "fract(fa) - fract(fb)", None),
    "fabs_sign": ("f", "abs(fa) * sign(fb)", None),
    "fstep": ("f", "step(fa, fb) + step(0.0, fa) * 2.0", None),
    "ldexp": ("f", "ldexp(fa, int(ub & 7u) - 3)", None),
    "cvt_f2i": ("i", "int(fa * 1000.0)", None), "cvt_f2u": ("u", "uint(abs(fa) * 1000.0)", None),
    "cvt_i2f": ("f", "float(ia >> 8)", None), "cvt_u2f": ("f", "float(ua >> 8)", None),
    "pack_unorm": ("u", "packUnorm4x8(vec4(float(ua & 255u), float(ub & 255u), 0.0, 255.0) / 255.0)", None),
    "pack_half": ("u", "packHalf2x16(vec2(float(int(ua & 2047u) - 1024), float(ub & 255u) * 0.25))", None),
    # spec-bounded float operations
    "fdiv": ("f", "fa / fb", "div"), "sqrt": ("f", "sqrt(abs(fa))", "sqrt"),
    "isqrt": ("f", "inversesqrt(abs(fa) + 0.5)", "inversesqrt"),
    "exp": ("f", "exp(fa)", "exp"), "exp2": ("f", "exp2(fa)", "exp2"),
    "log": ("f", "log(abs(fa) + 0.25)", "log"), "log2": ("f", "log2(abs(fa) + 0.25)", "log2"),
    "sin": ("f", "sin(fa)", "sin"), "cos": ("f", "cos(fb)", "cos"),
    "atan": ("f", "atan(fa, fb)", "atan"), "pow": ("f", "pow(abs(fa) + 0.5, fb)", "pow"),
    "tanh": ("f", "tanh(fa)", "tanh"),
}

PUBLIC = [
    {"tag": "int_a", "seed": 101, "inputs": "u32", "ops": ["uadd", "usub", "umul", "udiv", "umod", "shifts", "minmax", "cmp"]},
    {"tag": "int_b", "seed": 102, "inputs": "i32", "ops": ["idiv", "imod", "ineg_abs", "ishr", "iclamp", "cmp"]},
    {"tag": "bits", "seed": 103, "inputs": "u32", "ops": ["bitcount", "findlsb_msb", "reverse", "extract", "sextract",
                                                        "insert", "carry", "borrow", "mulext", "imulext"]},
    {"tag": "float_exact", "seed": 104, "inputs": "f32", "range": [-8.0, 8.0],
     "ops": ["fadd", "fsub", "fmul", "fminmax", "fclamp", "fround", "froundeven", "ffract", "fabs_sign", "fstep"]},
    {"tag": "convert", "seed": 105, "inputs": "f32", "range": [-100.0, 100.0],
     "ops": ["cvt_f2i", "cvt_f2u", "cvt_i2f", "cvt_u2f", "pack_unorm", "pack_half", "ldexp"]},
    {"tag": "transcendental", "seed": 106, "inputs": "f32", "range": [-3.0, 3.0],
     "ops": ["fdiv", "sqrt", "isqrt", "exp", "log", "sin", "cos", "atan", "pow"]},
]


def make_inputs(r: np.random.RandomState, kind: str, lo=-1.0, hi=1.0):
    if kind == "u32":
        return r.randint(0, 2**32, size=N, dtype=np.uint64).astype(np.uint32)
    if kind == "i32":
        return r.randint(-2**31, 2**31, size=N, dtype=np.int64).astype(np.int32).view(np.uint32)
    # f32: the requested range, with small integers and halves mixed in. No negative zero:
    # min/max/sign of +0 against -0 may return either (implementation-defined).
    f = r.uniform(lo, hi, size=N)
    f[::7] = np.round(f[::7])
    f[3::11] = np.round(f[3::11] * 2) / 2
    f[f == 0] = 0.0
    return f.astype(np.float32).view(np.uint32)


def shader(ops: list[str]) -> str:
    lines = []
    for k, op in enumerate(ops):
        t, expr, _ = OPS[op]
        if t == "f":
            lines.append(f"  {{ precise float r = {expr}; o[{k}u * {N}u + i] = floatBitsToUint(r); }}")
        elif t == "i":
            lines.append(f"  {{ int r = {expr}; o[{k}u * {N}u + i] = uint(r); }}")
        else:
            lines.append(f"  {{ uint r = {expr}; o[{k}u * {N}u + i] = r; }}")
    return f"""#version 450
layout(local_size_x = 64) in;
layout(set = 0, binding = 0) readonly buffer A {{ uint a[]; }};
layout(set = 0, binding = 1) readonly buffer B {{ uint b[]; }};
layout(set = 0, binding = 2) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{ uint k; float s; }} pc;
uint addc(uint x, uint y) {{ uint c; uint s = uaddCarry(x, y, c); return s ^ (c << 31); }}
uint subb(uint x, uint y) {{ uint c; uint s = usubBorrow(x, y, c); return s ^ (c << 30); }}
uint umulx(uint x, uint y) {{ uint h, l; umulExtended(x, y, h, l); return h ^ (l >> 7); }}
int imulx(int x, int y) {{ int h, l; imulExtended(x, y, h, l); return h ^ (l >> 9); }}
void main() {{
  uint i = gl_GlobalInvocationID.x;
  uint ua = a[i], ub = b[i], k = pc.k;
  int ia = int(ua), ib = int(ub);
  float fa = uintBitsToFloat(ua) * pc.s, fb = uintBitsToFloat(ub);
{chr(10).join(lines)}
}}
"""


def build(name: str, p: dict):
    r = rng(p["seed"])
    lo, hi = p.get("range", [-1.0, 1.0])
    a = make_inputs(r, p["inputs"], lo, hi)
    b = make_inputs(r, p["inputs"], lo, hi)
    if p["inputs"] == "f32" and any(OPS[o][2] == "div" for o in p["ops"]):
        bf = b.view(np.float32).copy()
        bf[np.abs(bf) < 0.125] = 0.5                       # keep divisors away from 0
        b = bf.astype(np.float32).view(np.uint32)
    ops = p["ops"]
    items = []
    for k, op in enumerate(ops):
        t, _, allow_op = OPS[op]
        it = {"name": op, "buffer": "out", "offset": k * N * 4, "size": N * 4,
              "elem": {"f": "f32", "i": "i32", "u": "u32"}[t]}
        if allow_op:
            it["allow"] = spec_allow(allow_op, x_max=max(abs(lo), abs(hi)) * 2)
        items.append(it)
    scale2 = float(p.get("scale2", 0.5))
    return compute_case(
        name, FAMILY, source=shader(ops), push_bytes=8,
        buffers=[{"name": "a", "size": N * 4, "array": a, "binding": 0},
                 {"name": "b", "size": N * 4, "array": b, "binding": 1},
                 {"name": "out", "size": len(ops) * N * 4, "binding": 2}],
        dispatches=[{"groups": [N // 64], "push": {"u32": [3], "f32": [1.0]}, "snapshot": items},
                    {"groups": [N // 64], "push": {"u32": [p["seed"] % 29 + 1], "f32": [scale2]}, "snapshot": items}])


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
