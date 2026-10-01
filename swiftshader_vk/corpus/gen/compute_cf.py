"""Family `compute_cf`: control flow -- branches, loops, switch, function calls,
early exit (replay).

Each case is a random structured program, generated from a seed and size
parameters: nested if/else, bounded for/while loops with data-dependent
break/continue, switch statements, helper functions with their own branches,
and early returns. The program size is capped (`budget` statements, `calls`
call sites; every call is inlined when the reference compiles it) so the
reference compiles a case well under a second (Stage 7 timing gate: uncapped
programs took up to 6.4 s, all of it LLVM compile time). All arithmetic is on 32-bit unsigned integers (exact), and
every loop has a constant bound (no infinite loops, no undefined behaviour:
shifts by constants below 32, no division). Different invocations take
different paths (the data decides), so divergence is exercised.

Public parameters are in PUBLIC; the hidden split's are in the private tree.
"""
from __future__ import annotations

import numpy as np

from common import compute_case, pub_name, rng, write_all

FAMILY = "compute_cf"
N = 256
VARS = ["v0", "v1", "v2", "v3"]

PUBLIC = [
    {"tag": "a", "seed": 201, "depth": 3, "stmts": 6, "funcs": 2},
    {"tag": "b", "seed": 202, "depth": 4, "stmts": 5, "funcs": 3},
    {"tag": "c", "seed": 203, "depth": 2, "stmts": 10, "funcs": 1},
    {"tag": "d", "seed": 204, "depth": 5, "stmts": 4, "funcs": 2},
]


class Gen:
    def __init__(self, r: np.random.RandomState, funcs: int, budget: int = 40, calls: int = 6):
        self.r = r
        self.funcs = funcs
        self.loop_var = 0
        self.budget = budget          # statements left; when spent, only plain assignments
        self.calls = calls            # call sites left

    def const(self):
        return f"{int(self.r.randint(1, 1 << 16))}u"

    def var(self):
        return VARS[self.r.randint(len(VARS))]

    def expr(self, depth=2):
        r = self.r
        if depth == 0 or r.rand() < 0.3:
            return self.var() if r.rand() < 0.7 else self.const()
        op = r.choice(["+", "-", "*", "^", "&", "|", ">>", "<<", "min", "max", "call"])
        a, b = self.expr(depth - 1), self.expr(depth - 1)
        if op in (">>", "<<"):
            return f"({a} {op} {int(r.randint(1, 31))}u)"
        if op in ("min", "max"):
            return f"{op}({a}, {b})"
        if op == "call" and self.funcs and self.calls > 0:
            self.calls -= 1
            return f"f{int(r.randint(self.funcs))}({a}, {b})"
        if op == "call":
            op = "+"
        return f"({a} {op} {b})"

    def cond(self):
        r = self.r
        kind = r.randint(4)
        if kind == 0:
            return f"(({self.var()} & {int(1 << r.randint(0, 16))}u) != 0u)"
        if kind == 1:
            return f"({self.var()} < {self.expr(1)})"
        if kind == 2:
            return f"(({self.var()} % {int(r.randint(2, 9))}u) == {int(r.randint(0, 2))}u)"
        return f"({self.var()} != {self.var()})"

    def block(self, depth, n, indent, in_loop=False):
        return "\n".join(self.stmt(depth, indent, in_loop) for _ in range(n))

    def stmt(self, depth, indent, in_loop):
        r = self.r
        p = " " * indent
        choices = ["assign", "assign"]
        self.budget -= 1
        if depth > 0 and self.budget > 0:
            choices += ["if", "for", "while", "switch"]
        if in_loop:
            choices += ["break", "continue"]
        k = r.choice(choices)
        if k == "assign":
            return f"{p}{self.var()} = {self.expr(3)};"
        if k == "break":
            return f"{p}if {self.cond()} break;"
        if k == "continue":
            return f"{p}if {self.cond()} {{ {self.var()} += 1u; continue; }}"
        inner = int(r.randint(1, 4))
        if k == "if":
            s = f"{p}if {self.cond()} {{\n{self.block(depth - 1, inner, indent + 2, in_loop)}\n{p}}}"
            if r.rand() < 0.6:
                s += f" else {{\n{self.block(depth - 1, inner, indent + 2, in_loop)}\n{p}}}"
            return s
        if k == "for":
            j = f"j{self.loop_var}"
            self.loop_var += 1
            bound = f"({self.var()} & {int(r.randint(1, 16))}u)" if r.rand() < 0.5 else f"{int(r.randint(1, 12))}u"
            return (f"{p}for (uint {j} = 0u; {j} < {bound}; {j}++) {{\n{p}  {self.var()} += {j} * {self.const()};\n"
                    f"{self.block(depth - 1, inner, indent + 2, True)}\n{p}}}")
        if k == "while":
            g = f"g{self.loop_var}"
            self.loop_var += 1
            return (f"{p}{{ uint {g} = 0u;\n{p}while ({g} < {int(r.randint(2, 10))}u && {self.cond()}) {{\n{p}  {g}++;\n"
                    f"{self.block(depth - 1, inner, indent + 2, True)}\n{p}}} }}")
        # switch
        cases = int(r.randint(2, 6))
        body = []
        for ci in range(cases):
            fall = r.rand() < 0.2
            body.append(f"{p}  case {ci}u:\n{self.block(depth - 1, 1, indent + 4, False)}" + ("" if fall else f"\n{p}    break;"))
        body.append(f"{p}  default:\n{self.block(depth - 1, 1, indent + 4, False)}\n{p}    break;")
        return f"{p}switch ({self.var()} % {cases + 1}u) {{\n" + "\n".join(body) + f"\n{p}}}"

    def function(self, k, depth):
        save, self.funcs = self.funcs, k          # f_k may call only f_0 .. f_(k-1): no recursion
        body = self.block(depth, 3, 2)
        self.funcs = save
        return (f"uint f{k}(uint a, uint b) {{\n  uint v0 = a, v1 = b, v2 = a ^ {self.const()}, v3 = b + {self.const()};\n"
                f"{body}\n  return v0 ^ (v1 << 3) ^ (v2 >> 2) ^ v3;\n}}")


def program(p: dict) -> str:
    r = rng(p["seed"])
    g = Gen(r, p["funcs"], p.get("budget", 40), p.get("calls", 6))
    funcs = "\n".join(g.function(k, max(1, p["depth"] - 2)) for k in range(p["funcs"]))
    g.funcs = p["funcs"]
    body = g.block(p["depth"], p["stmts"], 2)
    early = f"  if {g.cond()} {{ o[i] = v0 ^ 0xdeadbeefu; return; }}"
    tail = g.block(max(1, p["depth"] - 1), 3, 2)
    return f"""#version 450
layout(local_size_x = 64) in;
layout(set = 0, binding = 0) readonly buffer A {{ uint a[]; }};
layout(set = 0, binding = 1) writeonly buffer O {{ uint o[]; }};
layout(push_constant) uniform PC {{ uint k; }} pc;
{funcs}
void main() {{
  uint i = gl_GlobalInvocationID.x;
  uint v0 = a[i], v1 = a[(i * 7u + 3u) % {N}u] + pc.k, v2 = i * 2654435761u, v3 = pc.k;
{body}
{early}
{tail}
  o[i] = v0 ^ (v1 * 3u) ^ (v2 >> 1) ^ (v3 << 5);
}}
"""


def build(name: str, p: dict):
    r = rng(p["seed"] + 7)
    a = r.randint(0, 2**32, size=N, dtype=np.uint64).astype(np.uint32)
    a[::5] &= 0xFF                                      # small values take other paths
    items = [{"name": "out", "buffer": "out", "elem": "u32"}]
    return compute_case(
        name, FAMILY, source=program(p), push_bytes=4,
        buffers=[{"name": "a", "size": N * 4, "array": a, "binding": 0},
                 {"name": "out", "size": N * 4, "binding": 1}],
        dispatches=[{"groups": [N // 64], "push": {"u32": [1]}, "snapshot": items},
                    {"groups": [N // 64], "push": {"u32": [p["seed"] % 97 + 2]}, "snapshot": items}])


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
