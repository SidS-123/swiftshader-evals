"""Shared helpers for the toy generators, and the `rects` family's public cases.

Both splits import the helpers here; the hidden cases and their draws live in
`hidden-private/gen/shapes.py`. The rule the split has to satisfy is
statistical, not secret: the two splits are different draws in seed *and* in
canvas size, rectangle count, sample count and composition.
"""
from __future__ import annotations

import random

FAMILY_STATIC = "rects_static"
FAMILY_ANIM = "rects_anim"


def canvas_op(width: int, height: int, background: float, seed: int) -> dict:
    return {"op": "canvas", "width": width, "height": height, "background": background, "seed": seed}


def rect_ops(rng: random.Random, n: int, values=(0.3, 1.0), sizes=(0.1, 0.5)) -> list[dict]:
    """`n` random rectangles; every float is a float64 draw narrowed at write time."""
    ops = []
    for i in range(n):
        ops.append({"op": "rect", "id": f"r{i}",
                    "x": rng.uniform(0.0, 0.85), "y": rng.uniform(0.0, 0.85),
                    "w": rng.uniform(*sizes), "h": rng.uniform(*sizes),
                    "value": rng.uniform(*values)})
    return ops


def draw_snapshot(samples: int, index: int) -> list[dict]:
    return [{"op": "draw", "samples": samples}, {"op": "snapshot", "name": f"snap_{index:03d}"}]


def static_case(corpus, name: str, split: str, *, seed: int, size: int, n: int, samples: int,
                background: float):
    rng = random.Random(seed)
    ops = [canvas_op(size, size, background, seed)] + rect_ops(rng, n) + draw_snapshot(samples, 0)
    corpus.write(name, FAMILY_STATIC, "replay", ops, split=split)


def anim_case(corpus, name: str, split: str, *, seed: int, size: int, n: int, samples: int,
              steps: int, background: float, pan: bool):
    """A sequence: rectangles move and change value between snapshots, or the camera pans."""
    rng = random.Random(seed)
    ops = [canvas_op(size, size, background, seed)] + rect_ops(rng, n)
    for step in range(steps):
        if step:
            if pan:
                ops.append({"op": "set", "id": "camera", "key": "offset",
                            "value": [0.03 * step, -0.02 * step]})
            else:
                which = f"r{rng.randrange(n)}"
                key = rng.choice(["x", "y", "value"])
                value = rng.uniform(0.0, 0.85) if key != "value" else rng.uniform(0.3, 1.0)
                ops.append({"op": "set", "id": which, "key": key, "value": value})
                if rng.random() < 0.3:
                    ops.append({"op": "remove", "id": f"r{rng.randrange(n)}"})
        ops += draw_snapshot(samples, step)
    corpus.write(name, FAMILY_ANIM, "replay", ops, split=split)


def generate(split: str, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    static_case(corpus, "rects_static_pub_a", split, seed=11, size=64, n=4, samples=8, background=0.1)
    static_case(corpus, "rects_static_pub_b", split, seed=12, size=64, n=6, samples=4, background=0.3)
    anim_case(corpus, "rects_anim_pub_move", split, seed=21, size=64, n=5, samples=6, steps=5,
              background=0.15, pan=False)
    anim_case(corpus, "rects_anim_pub_pan", split, seed=22, size=48, n=4, samples=8, steps=4,
              background=0.05, pan=True)
