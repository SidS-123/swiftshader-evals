"""The `proc_queries` family: procedural cases (queries and error codes)."""
from __future__ import annotations

import random

from shapes import rect_ops, draw_snapshot, canvas_op

FAMILY = "proc_queries"


def query_case(corpus, name: str, split: str, *, seed: int, size: int, n: int, samples: int,
               background: float, bad_ops: bool):
    rng = random.Random(seed)
    ops = [canvas_op(size, size, background, seed)] + rect_ops(rng, n)
    ops += [{"op": "query", "what": "bounds", "name": "bounds_0"},
            {"op": "query", "what": "coverage", "name": "coverage_0"},
            {"op": "query", "what": "mean", "name": "mean_0"}]
    if bad_ops:
        ops += [{"op": "set", "id": "r0", "key": "w", "value": -0.5},          # INVALID_ARGUMENT
                {"op": "set", "id": "nope", "key": "x", "value": 0.1},         # UNKNOWN_ID
                {"op": "rect", "id": "bad", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "value": 1.5},
                {"op": "query", "what": "volume", "name": "unknown_query"}]    # INVALID_ARGUMENT
    ops += draw_snapshot(samples, 0)
    ops += [{"op": "set", "id": "camera", "key": "offset", "value": [0.05, 0.0]},
            {"op": "query", "what": "bounds", "name": "bounds_1"},
            {"op": "remove", "id": "r0"},
            {"op": "query", "what": "coverage", "name": "coverage_1"}]
    ops += draw_snapshot(samples, 1)
    corpus.write(name, FAMILY, "procedural", ops, split=split)


def generate(split: str, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    query_case(corpus, "proc_queries_pub_a", split, seed=31, size=48, n=4, samples=6,
               background=0.2, bad_ops=True)
    query_case(corpus, "proc_queries_pub_b", split, seed=32, size=32, n=3, samples=8,
               background=0.0, bad_ops=False)
