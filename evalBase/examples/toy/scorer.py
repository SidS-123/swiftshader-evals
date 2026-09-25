"""The toy's CaseScorer: a block distance on greyscale snapshots, two procedural checks.

Fidelity distance. Snapshots are 8-bit greyscale in [0, 1]. Both snapshots are
box-downsampled 2x (per-pixel Monte Carlo noise averages out) and cut into
BLOCK x BLOCK blocks; per block

    d_struct = min(1, rms(ref - cand) / STRUCT_SCALE)
    d_lum    = min(1, |log2((m_cand + LUM_EPS) / (m_ref + LUM_EPS))| / LUM_SCALE)
    d_b      = max(d_struct, d_lum),  then d_b < FLOOR -> 0

and the snapshot defect D is the mean of d_b. The luminance term is what makes a
uniform gain visible (a structural term alone is gain-invariant, and a
control that doubles every value would score well without it). The
floor makes the defect grow with noise variance rather than its standard
deviation, so unbiased noise below the floor costs nothing and bias does not
average away. A candidate snapshot that is missing, the wrong size, or nearly
uniform while the reference is not scores D = 1 outright.

Procedural checks, every one derived from the reference's own ledger:
`query:<name>` compares each query the reference answered (numeric within
1e-3 relative; None/errors must match exactly), `error_codes` compares the
whole error stream with consecutive duplicates collapsed. The two generic
checks (`exit_ok` gate, `output_matches_reference`) come from evalbase.
"""
from __future__ import annotations

import os

import numpy as np

from evalbase.interfaces import CaseScorer, generic_checks

BLOCK = 8
DOWNSAMPLE = 2
FLOOR = 0.15
STRUCT_SCALE = 0.25
LUM_SCALE = 1.0
LUM_EPS = 0.02
MONO_FRACTION = 0.999


def read_pgm(path: str) -> np.ndarray:
    """A binary 8-bit PGM as float32 HxW in [0, 1]."""
    with open(path, "rb") as f:
        magic = f.readline().strip()
        if magic != b"P5":
            raise ValueError(f"not a P5 PGM: {path}")
        dims = f.readline().split()
        while len(dims) < 2:
            dims += f.readline().split()
        w, h = int(dims[0]), int(dims[1])
        maxval = int(f.readline().strip())
        data = np.frombuffer(f.read(w * h), dtype=np.uint8)
    return (data.reshape(h, w).astype(np.float32) / float(maxval))


def _downsample(a: np.ndarray, f: int = DOWNSAMPLE) -> np.ndarray:
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    a = a[:h, :w]
    return a.reshape(h // f, f, w // f, f).mean(axis=(1, 3))


def _blocks(a: np.ndarray, block: int = BLOCK) -> np.ndarray:
    h, w = a.shape
    hb, wb = h // block, w // block
    a = a[: hb * block, : wb * block]
    return a.reshape(hb, block, wb, block).transpose(0, 2, 1, 3).reshape(hb * wb, block * block)


def block_defect_grid(ref: np.ndarray, cand: np.ndarray) -> np.ndarray:
    """Per-block defects as a 2-D grid, before the floor."""
    r, c = _downsample(ref), _downsample(cand)
    br, bc = _blocks(r), _blocks(c)
    d_struct = np.clip(np.sqrt(((br - bc) ** 2).mean(1)) / STRUCT_SCALE, 0.0, 1.0)
    mr, mc = br.mean(1), bc.mean(1)
    d_lum = np.clip(np.abs(np.log2((mc + LUM_EPS) / (mr + LUM_EPS))) / LUM_SCALE, 0.0, 1.0)
    d = np.maximum(d_struct, d_lum)
    hb, wb = r.shape[0] // BLOCK, r.shape[1] // BLOCK
    return d.reshape(hb, wb)


def is_monochrome(a: np.ndarray, fraction: float = MONO_FRACTION) -> bool:
    q = np.round(a * 255).astype(np.int32).ravel()
    _, counts = np.unique(q, return_counts=True)
    return counts.max() >= fraction * q.size


class ToyScorer(CaseScorer):
    primary_channel = "gray"

    def load_output(self, outdir, event, channel=None):
        f = (event.get("files") or {}).get(channel or self.primary_channel)
        if not f:
            return None
        p = os.path.join(outdir, f)
        if not os.path.exists(p):
            return None
        try:
            return read_pgm(p)
        except Exception:
            return None

    def distance(self, ref, cand) -> float:
        if cand is None or cand.shape != ref.shape:
            return 1.0
        if is_monochrome(cand) and not is_monochrome(ref):
            return 1.0
        d = block_defect_grid(ref, cand)
        if d.size == 0:
            return 1.0
        d = np.where(d < FLOOR, 0.0, d)
        return float(d.mean())

    def preview_rgb8(self, output):
        g = np.clip(np.asarray(output, dtype=np.float32) * 255.0, 0, 255).astype(np.uint8)
        return np.repeat(g[:, :, None], 3, axis=2)

    def block_defects(self, ref, cand):
        if cand is None or cand.shape != ref.shape:
            return None
        return block_defect_grid(ref, cand)

    def block_pixels(self) -> int:
        return BLOCK * DOWNSAMPLE

    def floor(self) -> float:
        return FLOOR

    def procedural_checks(self, case, ref, cand, ref_dir, cand_dir, threshold):
        checks = generic_checks(self, ref, cand, ref_dir, cand_dir, threshold)
        if checks and checks[0]["check"] == "exit_ok":
            return checks

        def add(check, ok, detail=""):
            checks.append({"check": check, "ok": bool(ok), "detail": detail})

        rq = {e.get("name"): e for e in ref.get("events", []) if e.get("op") == "query"}
        cq = {e.get("name"): e for e in cand.get("events", []) if e.get("op") == "query"}
        for name, e in rq.items():
            c = cq.get(name)
            if c is None:
                add(f"query:{name}", False, "missing")
                continue
            if "error" in e or "error" in c:
                add(f"query:{name}", e.get("error") == c.get("error"), c.get("error"))
                continue
            add(f"query:{name}", _close(e.get("value"), c.get("value")), c.get("value"))
        rcodes = _collapse([x.get("code") for x in ref.get("errors", [])])
        ccodes = _collapse([x.get("code") for x in cand.get("errors", [])])
        add("error_codes", rcodes == ccodes, {"want": rcodes[:12], "got": ccodes[:12]})
        return checks


def _collapse(seq):
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def _close(a, b, rel=1e-3, atol=1e-3) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, (list, tuple)):
        return (isinstance(b, (list, tuple)) and len(a) == len(b)
                and all(_close(x, y, rel, atol) for x, y in zip(a, b)))
    if isinstance(a, (int, float)):
        return isinstance(b, (int, float)) and abs(a - b) <= atol + rel * abs(a)
    return a == b
