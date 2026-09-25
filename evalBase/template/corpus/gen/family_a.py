"""Family `family_a`: the public cases, and the helpers both splits share.

Public and hidden must be different draws: different seeds AND different
parameter values and compositions. The hidden cases live in the private tree
(`<hidden tree>/gen/family_a.py`), which imports the helpers from here.
"""
from __future__ import annotations

import random

FAMILY = "family_a"


def build_case(corpus, name: str, split: str, *, seed: int, category: str = "replay"):
    """Write one case. Replace the ops with your driver's own format."""
    rng = random.Random(seed)
    ops = [{"op": "setup", "seed": seed, "param": rng.uniform(0.0, 1.0)},
           {"op": "draw", "samples": 8}, {"op": "snapshot", "name": "snap_000"}]
    corpus.write(name, FAMILY, category, ops, split=split)


def generate(split: str, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    build_case(corpus, "family_a_pub_a", split, seed=11)
    build_case(corpus, "family_a_pub_b", split, seed=12)
