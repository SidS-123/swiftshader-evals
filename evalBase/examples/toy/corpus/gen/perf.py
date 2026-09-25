"""The `perf_many` family: performance cases, graded by wall-time ratio.

A warm-up run precedes the timed one (`meta.timed_run_index`), and the
timed draw is sized so the oracle takes on the order of a second: below
that, run-to-run spread is start-up cost, not a measurement.
"""
from __future__ import annotations

import random

from shapes import rect_ops, draw_snapshot, canvas_op

FAMILY = "perf_many"


def perf_case(corpus, name: str, split: str, *, seed: int, size: int, n: int, samples: int):
    rng = random.Random(seed)
    ops = [canvas_op(size, size, 0.1, seed)] + rect_ops(rng, n, sizes=(0.05, 0.3))
    ops += [{"op": "draw", "samples": 1}]                       # warm-up, not timed
    ops += draw_snapshot(samples, 0)
    corpus.write(name, FAMILY, "performance", ops, split=split, meta={"timed_run_index": 1})


def generate(split: str, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    perf_case(corpus, "perf_many_pub_a", split, seed=41, size=144, n=50, samples=40)
