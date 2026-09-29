"""Family `inst_dev`: instance/device creation, enumeration, properties,
features, limits, format properties, queue families, memory types
(procedural). The public cases, and the helpers both splits share.

Public and hidden must be different draws: different seeds AND different
parameter values and compositions. The hidden cases live in the private tree
(`$SSVK_HIDDEN_GEN/gen/inst_dev.py`), which imports the helpers from here.

Placeholder until Stage 7 (PLAN_v1.md §11): the op vocabulary is fixed in
Stage 4, so no cases are written yet.
"""
from __future__ import annotations

FAMILY = "inst_dev"


def generate(split: str, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    # Stage 7: public cases `inst_dev_pub_<x>`.
