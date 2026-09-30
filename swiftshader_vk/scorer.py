"""The ssvk scorer: fidelity distance over .ssnap snapshots, procedural checks, timings.

Fidelity distance D (metric ssvk-1.0; constants below, set with the controls
in Stage 8). For every item of a snapshot (an image aspect or a buffer range)
the candidate is compared with the reference element by element, per
component, in the data's own units:

    normalized / sRGB / D16 codes      e = |delta code|                     (LSB)
    floats (f16, f32, f64, UFLOAT)     e = |delta| / ulp(max(|ref|, 1/16))  (ULP, floored)
    integers, stencil, int buffers     e = 0 if equal, else EXACT_MISS      (exact)
    NaN vs non-NaN, +inf vs other      e = EXACT_MISS

then e_eff = max(0, e - allowance), where the allowance is FREE_LSB (1) for
normalized data, FREE_ULP (2) for floats, 0 for exact data, plus the item's own
`allow` if the case set one. The per-texel error is the max over components
(the signed error, for the bias term, is the mean over components).

Per block (8x8 texels of each layer/slice; 64 consecutive elements for buffers):

    d_mag    = mean(min(e_eff, cap)) / S                  (how wrong, broadly)
    d_bias   = |mean(clip(signed e_eff, +-bias_cap))| / B  (a uniform offset or gain)
    d_cov    = fraction(e_eff > K) / C                     (how many texels are wrong)
    d_block  = min(1, max(d_mag, d_bias, d_cov)),   0 if below FLOOR

The caps keep one badly wrong texel (an edge sample that went the other way)
from saturating its block: sparse errors are measured by the coverage term.
In images, where edge samples legitimately differ, a block saturates when a
quarter of its texels are wrong (C); in buffers, where every element is a
separate result, one wrong element saturates its run (C_buffer).

An item's defect is the root-mean-square of its blocks, and the snapshot's D
the root-mean-square over items (each item weighted equally):

    D = sqrt(mean over items of mean over blocks of d_block^2)

A missing item, a size/format mismatch, or a colour item that is uniform where
the reference's is not gives that item d = 1 in every block. The RMS (rather
than the mean) keeps one fully wrong block of 64 visible (D = 0.125) while
scattered single-texel edge flips stay small.

Procedural checks (all derived from the reference's ledger for the case):
`exit_ok` (gate), `output_matches_reference` (every snapshot within T), the
whole `vkresult_stream`, and one `query:<name>[:<key>]` check per query event
(per top-level key of its value). Fields that identify a build, not a behaviour,
are ignored: UUIDs, `conformanceVersion`, `driverInfo`.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

import snapshot as ss
from evalbase.interfaces import CaseScorer, generic_checks

METRIC_CONSTANTS = {
    "block": 8,              # image block edge (texels)
    "run": 64,               # buffer block length (elements)
    "free_lsb": 1.0,         # normalized data: this many LSB of difference are free
    "free_ulp": 2.0,         # float data: this many ULP are free
    "S": 4.0,                # magnitude scale (units of e_eff)
    "cap": 16.0,             # a texel's contribution to the magnitude term is capped here
    "B": 2.0,                # bias scale
    "bias_cap": 4.0,         # ... and to the bias term here (sparse outliers are the coverage term's job)
    "K": 1.0,                # a texel counts as wrong when e_eff > K
    "C": 0.25,               # fraction of wrong texels in an image block that saturates the coverage term
    "C_buffer": 1.0 / 64,    # buffers: one wrong element saturates its run (no edges: every element is a result)
    "FLOOR": 0.05,           # block defects below this cost nothing
    "EXACT_MISS": 1.0e6,     # error of an exact-data mismatch (saturates every term)
    "float_floor": 2.0 ** -4,  # floats: ULPs are those of max(|reference|, this)
    "mono_fraction": 0.999,  # a colour item this uniform is "uniform"
}
_C = METRIC_CONSTANTS

EXACT_KINDS = {"UINT", "SINT", "USCALED", "SSCALED"}
NORM_KINDS = {"UNORM", "SNORM", "SRGB"}
IGNORED_FIELDS = {"pipelineCacheUUID", "deviceUUID", "driverUUID", "deviceLUID", "deviceLUIDValid",
                  "deviceNodeMask", "conformanceVersion", "driverInfo"}


# ------------------------------------------------------------------ per-channel errors

def _float_mbits(ch: ss.Channel) -> int:
    if ch.numeric == "UFLOAT":
        return ch.bits - 5
    return {16: 10, 32: 23, 64: 52}.get(ch.bits, 23)


def _ulp(x: np.ndarray, mbits: int) -> np.ndarray:
    """Spacing of a float format with `mbits` mantissa bits at max(|x|, float_floor)."""
    ax = np.maximum(np.nan_to_num(np.abs(x), nan=0.0, posinf=0.0), _C["float_floor"])
    return np.exp2(np.floor(np.log2(ax)) - mbits)


def channel_error(ref: ss.Channel, cand: ss.Channel) -> tuple[np.ndarray, np.ndarray, float]:
    """(|error|, signed error, free allowance) per element, in the channel's units."""
    miss = _C["EXACT_MISS"]
    kind = ref.numeric
    if kind in EXACT_KINDS or ref.name == "S":
        d = (ref.codes != cand.codes).astype(np.float64) * miss
        return d, np.sign(cand.codes - ref.codes) * d, 0.0
    if ref.is_float:
        a, b = ref.codes.astype(np.float64), cand.codes.astype(np.float64)
        scale = _ulp(a, _float_mbits(ref))
        with np.errstate(invalid="ignore", over="ignore"):
            signed = (b - a) / scale
        nan_a, nan_b = np.isnan(a), np.isnan(b)
        inf_a, inf_b = np.isinf(a), np.isinf(b)
        bad_class = (nan_a != nan_b) | ((inf_a | inf_b) & ~(nan_a | nan_b) & (a != b))
        same_special = (nan_a & nan_b) | (inf_a & inf_b & (a == b))
        signed = np.where(same_special, 0.0, signed)
        signed = np.where(bad_class, miss, signed)
        signed = np.clip(np.nan_to_num(signed, nan=miss, posinf=miss, neginf=-miss), -miss, miss)
        return np.abs(signed), signed, _C["free_ulp"]
    # normalized codes (and D16 / D24 depth codes)
    d = (cand.codes - ref.codes).astype(np.float64)
    return np.abs(d), d, _C["free_lsb"]


def _allow_for(item: dict, ref: ss.Channel) -> float:
    allow = item.get("allow") or {}
    if not allow:
        return 0.0
    if ref.is_float:
        return float(allow.get("ulp", 0.0))
    if ref.numeric in NORM_KINDS or ref.name == "D":
        return float(allow.get("lsb", 0.0))
    return float(allow.get("lsb", 0.0))       # exact data: an explicit allowance in integer steps


def item_errors(item: dict, ref_raw: bytes, cand_item: dict, cand_raw: bytes):
    """(e_eff per element, signed e_eff per element) for one item, max/mean over components."""
    rc, cc = ss.decode(item, ref_raw), ss.decode(cand_item, cand_raw)
    errs, signs = [], []
    for r, c in zip(rc, cc):
        e, s, free = channel_error(r, c)
        if (r.numeric in EXACT_KINDS or r.name == "S") and item.get("allow"):
            # an explicit integer allowance turns an exact channel into a counted one
            e = np.abs((c.codes - r.codes).astype(np.float64))
            s = (c.codes - r.codes).astype(np.float64)
        free += _allow_for(item, r)
        eff = np.maximum(0.0, e - free)
        errs.append(eff)
        signs.append(np.sign(s) * eff)
    e = np.max(np.stack(errs), axis=0)
    s = np.mean(np.stack(signs), axis=0)
    return e, s


# ------------------------------------------------------------------ blocks

def _block_view(a: np.ndarray, item: dict) -> np.ndarray:
    """Group per-element values into blocks: returns (n_blocks, block_size) with NaN padding."""
    if item.get("kind") == "buffer":
        n = _C["run"]
        pad = (-len(a)) % n
        a = np.concatenate([a, np.full(pad, np.nan)]) if pad else a
        return a.reshape(-1, n)
    w, h = int(item["width"]), int(item["height"])
    planes = max(1, len(a) // max(1, w * h))
    a = a[: planes * w * h].reshape(planes, h, w)
    b = _C["block"]
    ph, pw = (-h) % b, (-w) % b
    if ph or pw:
        a = np.pad(a, ((0, 0), (0, ph), (0, pw)), constant_values=np.nan)
    P, H, W = a.shape
    return a.reshape(P, H // b, b, W // b, b).transpose(0, 1, 3, 2, 4).reshape(-1, b * b)


def block_defects_of(e: np.ndarray, s: np.ndarray, item: dict) -> np.ndarray:
    E, Sg = _block_view(e, item), _block_view(s, item)
    valid = ~np.isnan(E)
    n = np.maximum(valid.sum(axis=1), 1)
    E0, S0 = np.where(valid, E, 0.0), np.where(valid, Sg, 0.0)
    d_mag = np.minimum(E0, _C["cap"]).sum(axis=1) / n / _C["S"]
    d_bias = np.abs(np.clip(S0, -_C["bias_cap"], _C["bias_cap"]).sum(axis=1) / n) / _C["B"]
    c = _C["C_buffer"] if item.get("kind") == "buffer" else _C["C"]
    d_cov = ((E0 > _C["K"]) & valid).sum(axis=1) / n / c
    d = np.minimum(1.0, np.nan_to_num(np.maximum(np.maximum(d_mag, d_bias), d_cov), nan=1.0, posinf=1.0))
    return np.where(d < _C["FLOOR"], 0.0, d)


def _uniform(item: dict, raw: bytes) -> bool:
    chans = ss.decode(item, raw)
    if not chans or chans[0].codes.size == 0:
        return False
    stack = np.stack([np.asarray(c.codes, dtype=np.float64) for c in chans], axis=1)
    _, counts = np.unique(stack, axis=0, return_counts=True)
    return counts.max() >= _C["mono_fraction"] * stack.shape[0]


def _same_shape(a: dict, b: dict) -> bool:
    keys = ("kind", "format", "width", "height", "depth", "layers", "elem", "size", "texel_bytes")
    return all(a.get(k) == b.get(k) for k in keys)


def item_block_defects(item: dict, ref: ss.Snapshot, cand: ss.Snapshot | None) -> np.ndarray:
    ref_raw = ref.raw(item)
    blocks_shape = _block_view(np.zeros(len(ss.decode(item, ref_raw)[0].codes)), item).shape[0]
    ones = np.ones(blocks_shape)
    if cand is None:
        return ones
    ci = cand.item(item["name"])
    if ci is None or ci.get("missing") or not _same_shape(item, ci):
        return ones
    cand_raw = cand.raw(ci)
    if cand_raw is None or len(cand_raw) != len(ref_raw):
        return ones
    if item.get("kind") == "color" and _uniform(ci, cand_raw) and not _uniform(item, ref_raw):
        return ones
    e, s = item_errors(item, ref_raw, ci, cand_raw)
    return block_defects_of(e, s, item)


def snapshot_distance(ref: ss.Snapshot, cand: ss.Snapshot | None) -> float:
    items = [i for i in ref.items if not i.get("missing")]
    if not items:
        return 0.0 if cand is not None else 1.0
    per_item = [float(np.mean(item_block_defects(i, ref, cand) ** 2)) for i in items]
    return float(math.sqrt(float(np.mean(per_item))))


# ------------------------------------------------------------------ procedural helpers

def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in IGNORED_FIELDS}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def _first_difference(a, b, path="") -> str | None:
    if type(a) is not type(b):
        return f"{path or '.'}: {json.dumps(a)[:60]} vs {json.dumps(b)[:60]}"
    if isinstance(a, dict):
        for k in a:
            if k not in b:
                return f"{path}.{k}: missing"
            d = _first_difference(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        for k in b:
            if k not in a:
                return f"{path}.{k}: unexpected"
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} entries vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path or '.'}: {json.dumps(a)[:60]} vs {json.dumps(b)[:60]}"


def _calls(ledger: dict) -> list:
    # The timed loop of a `run` logs one combined entry only on failure: not part of the stream.
    return [tuple(c) for c in ledger.get("calls", []) if not str(c[1]).startswith("vkQueueSubmit+")]


# ------------------------------------------------------------------ the scorer

class SsvkScorer(CaseScorer):
    primary_channel = "snap"

    def load_output(self, outdir, event, channel=None):
        rel = (event.get("files") or {}).get(channel or self.primary_channel)
        if not rel:
            return None
        path = os.path.join(outdir, rel)
        if not os.path.isfile(path):
            return None
        try:
            return ss.read_ssnap(path)
        except (ValueError, OSError, json.JSONDecodeError):
            return None

    def distance(self, ref, cand) -> float:
        if ref is None:
            return 0.0
        try:
            return snapshot_distance(ref, cand)
        except Exception:
            return 1.0

    def preview_rgb8(self, output):
        """The first colour item of a snapshot as RGB8 (layer 0), for previews and the site."""
        if output is None:
            return None
        item = next((i for i in output.items if i.get("kind") == "color" and not i.get("missing")), None)
        if item is None:
            item = next((i for i in output.items if i.get("kind") in ("depth", "stencil") and not i.get("missing")), None)
        if item is None:
            return None
        chans = ss.decode(item, output.raw(item))
        w, h = int(item["width"]), int(item["height"])
        n = w * h

        def unit(ch: ss.Channel) -> np.ndarray:
            v = np.asarray(ch.codes[:n], dtype=np.float64)
            if ch.is_float:
                return np.clip(np.nan_to_num(v), 0.0, 1.0)
            if ch.numeric in NORM_KINDS or ch.name == "D":
                top = (1 << (ch.bits - 1)) - 1 if ch.numeric == "SNORM" else (1 << ch.bits) - 1
                return np.clip(v / float(top), 0.0, 1.0)
            lo, hi = float(v.min()), float(v.max())          # integers: stretch to the range present
            return (v - lo) / (hi - lo) if hi > lo else np.zeros_like(v)

        by_name = {c.name: c for c in chans}
        if all(k in by_name for k in "RG"):
            planes = [unit(by_name["R"]), unit(by_name["G"]),
                      unit(by_name["B"]) if "B" in by_name else np.zeros(n)]
        else:
            g = unit(chans[0])                                # one channel (depth, stencil, R): grey
            planes = [g, g, g]
        img = np.stack(planes, axis=1).reshape(h, w, 3)
        return (img * 255.0 + 0.5).astype(np.uint8)

    def block_defects(self, ref, cand):
        if ref is None:
            return None
        item = next((i for i in ref.items if i.get("kind") != "buffer" and not i.get("missing")), None)
        if item is None:
            return None
        d = item_block_defects(item, ref, cand)
        b = _C["block"]
        hb, wb = -(-int(item["height"]) // b), -(-int(item["width"]) // b)
        return d[: hb * wb].reshape(hb, wb)

    def block_pixels(self) -> int:
        return _C["block"]

    def floor(self) -> float:
        return _C["FLOOR"]

    def procedural_checks(self, case, ref_ledger, cand_ledger, ref_dir, cand_dir, threshold):
        checks = generic_checks(self, ref_ledger, cand_ledger, ref_dir, cand_dir, threshold)
        if checks and checks[0]["check"] == "exit_ok":
            return checks          # the crash gate

        def add(check, ok, detail=""):
            checks.append({"check": check, "ok": bool(ok), "detail": detail})

        rc, cc = _calls(ref_ledger), _calls(cand_ledger)
        if rc == cc:
            add("vkresult_stream", True)
        else:
            k = next((i for i, (a, b) in enumerate(zip(rc, cc)) if a != b), min(len(rc), len(cc)))
            want = list(rc[k]) if k < len(rc) else "end"
            got = list(cc[k]) if k < len(cc) else "end"
            add("vkresult_stream", False, f"call {k}: reference {want}, yours {got}")

        rq = [e for e in ref_ledger.get("events", []) if e.get("op") == "query"]
        cq = {e.get("name"): e for e in cand_ledger.get("events", []) if e.get("op") == "query"}
        for e in rq:
            name = e.get("name")
            got = cq.get(name)
            value = _strip(e.get("value"))
            if isinstance(value, dict) and len(value) > 1 and e.get("kind") == "query":
                for key, sub in value.items():
                    if got is None:
                        add(f"query:{name}:{key}", False, "not produced")
                        continue
                    other = _strip((got.get("value") or {}).get(key))
                    diff = _first_difference(sub, other)
                    add(f"query:{name}:{key}", diff is None, diff or "")
            else:
                if got is None:
                    add(f"query:{name}", False, "not produced")
                    continue
                diff = _first_difference(value, _strip(got.get("value")))
                add(f"query:{name}", diff is None, diff or "")
        return checks
