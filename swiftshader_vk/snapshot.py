"""Reader for vkreplay's .ssnap snapshot files, and per-format decoding.

    SSNAP1\\n | u32 header_len | header JSON | payload

The header lists items (images and buffers) with their byte range in the
payload, written by vkreplay's trusted parent from the case plan (never from
the ICD under test). Every image item carries its VkFormat's component layout
(names, bit widths, numeric formats, packed width) from the Vulkan registry,
so decoding needs no format table here.

`decode(item, raw)` returns one `Channel` per component, holding the raw codes
(for integer and normalized data: the integer the format stores; for floats:
the float value and its bit pattern for ULP distances). Distances in the
format's own units -- LSBs, ULPs -- are computed from these.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass

import numpy as np

MAGIC = b"SSNAP1\n"

INT_KINDS = {"UNORM", "SNORM", "UINT", "SINT", "USCALED", "SSCALED", "SRGB"}
FLOAT_KINDS = {"SFLOAT", "UFLOAT", "SFIXED5"}


@dataclass
class Snapshot:
    header: dict
    payload: bytes

    @property
    def items(self) -> list[dict]:
        return self.header.get("items", [])

    def item(self, name: str) -> dict | None:
        return next((i for i in self.items if i.get("name") == name), None)

    def raw(self, item: dict) -> bytes | None:
        if item.get("missing"):
            return None
        return self.payload[item["offset"]:item["offset"] + item["size"]]


def read_ssnap(path: str) -> Snapshot:
    with open(path, "rb") as f:
        data = f.read()
    if data[:7] != MAGIC or len(data) < 11:
        raise ValueError(f"{path}: not an SSNAP1 file")
    hl = struct.unpack("<I", data[7:11])[0]
    header = json.loads(data[11:11 + hl])
    return Snapshot(header, data[11 + hl:])


@dataclass
class Channel:
    name: str            # R, G, B, A, D, S, or the buffer elem
    numeric: str         # UNORM, SNORM, UINT, SINT, SRGB, SFLOAT, UFLOAT, ...
    bits: int
    codes: np.ndarray    # int64 codes (integer kinds) or float64 values (float kinds)
    fbits: np.ndarray | None = None   # float kinds: the bit pattern (for ULP distances)

    @property
    def is_float(self) -> bool:
        return self.numeric in FLOAT_KINDS


def _ufloat(v: np.ndarray, mbits: int) -> np.ndarray:
    """Unsigned small float (5-bit exponent, `mbits` mantissa) codes -> float64."""
    e = (v >> mbits) & 0x1F
    m = v & ((1 << mbits) - 1)
    out = np.where(e == 0, m / (1 << mbits) * 2.0 ** -14, (1 + m / (1 << mbits)) * 2.0 ** (e.astype(np.float64) - 15))
    out = np.where(e == 31, np.where(m == 0, np.inf, np.nan), out)
    return out.astype(np.float64)


def _float_bits(codes: np.ndarray, bits: int):
    if bits == 16:
        f = codes.astype(np.uint16).view(np.float16).astype(np.float64)
    elif bits == 32:
        f = codes.astype(np.uint32).view(np.float32).astype(np.float64)
    elif bits == 64:
        f = codes.astype(np.uint64).view(np.float64)
    else:
        raise ValueError(f"no {bits}-bit IEEE float")
    return f


def decode(item: dict, raw: bytes) -> list[Channel]:
    """Decode one item's bytes into per-component channels (flat, texel order)."""
    if item.get("kind") == "buffer":
        elem = item["elem"]
        dt = {"u8": "<u1", "i8": "<i1", "u16": "<u2", "i16": "<i2", "u32": "<u4", "i32": "<i4", "u64": "<u8",
              "i64": "<i8", "f16": "<u2", "f32": "<u4", "f64": "<u8"}[elem]
        a = np.frombuffer(raw, dtype=dt)
        if elem.startswith("f"):
            bits = int(elem[1:])
            return [Channel(elem, "SFLOAT", bits, _float_bits(a.astype(np.uint64), bits), a.astype(np.uint64))]
        kind = "UINT" if elem.startswith("u") else "SINT"
        return [Channel(elem, kind, int(elem[1:]), a.astype(np.int64))]

    comps = item["components"]
    tb = item["texel_bytes"]
    n = len(raw) // tb
    texels = np.frombuffer(raw[:n * tb], dtype=np.uint8).reshape(n, tb)
    kind = item.get("kind")
    out: list[Channel] = []
    if kind == "depth":
        c = comps[0]
        if tb == 2:
            v = texels.view("<u2").reshape(n).astype(np.int64)
            return [Channel("D", "UNORM", 16, v)]
        w = texels.view("<u4").reshape(n).astype(np.int64)
        if c["numeric"] == "SFLOAT":
            return [Channel("D", "SFLOAT", 32, _float_bits(w, 32), w)]
        return [Channel("D", "UNORM", 24, w & 0xFFFFFF)]
    if kind == "stencil":
        return [Channel("S", "UINT", 8, texels.reshape(n).astype(np.int64))]

    packed = int(item.get("packed") or 0)
    if packed:
        word = texels.view({8: "<u1", 16: "<u2", 32: "<u4"}[packed]).reshape(-1)
        word = word.astype(np.int64)
        if len(word) != n:                      # several packed words per texel (e.g. 4x16)
            word = word.reshape(n, -1)
        shift = packed
        if item["format"] == "VK_FORMAT_E5B9G9R9_UFLOAT_PACK32":
            e = (word >> 27) & 0x1F
            scale = 2.0 ** (e.astype(np.float64) - 15 - 9)
            vals = {"R": (word & 0x1FF) * scale, "G": ((word >> 9) & 0x1FF) * scale, "B": ((word >> 18) & 0x1FF) * scale}
            for c in comps:
                if c["name"] in vals:
                    out.append(Channel(c["name"], "UFLOAT", 9, vals[c["name"]].astype(np.float64)))
            return out
        for c in comps:                          # packed: components from MSB to LSB
            b = int(c["bits"])
            shift -= b
            v = (word >> shift) & ((1 << b) - 1)
            if c["numeric"] == "UFLOAT":
                out.append(Channel(c["name"], "UFLOAT", b, _ufloat(v, b - 5), v))
            elif c["numeric"] in ("SNORM", "SINT", "SSCALED"):
                v = np.where(v >= (1 << (b - 1)), v - (1 << b), v)
                out.append(Channel(c["name"], c["numeric"], b, v))
            else:
                out.append(Channel(c["name"], c["numeric"], b, v))
        return out

    pos = 0
    for c in comps:                              # non-packed: components in byte order
        b = int(c["bits"])
        nb = b // 8
        chunk = np.ascontiguousarray(texels[:, pos:pos + nb])
        pos += nb
        if nb == 1:
            v = chunk.reshape(n).astype(np.int64)
        elif nb == 2:
            v = chunk.view("<u2").reshape(n).astype(np.int64)
        elif nb == 4:
            v = chunk.view("<u4").reshape(n).astype(np.int64)
        elif nb == 8:
            v = chunk.view("<u8").reshape(n).astype(np.uint64).astype(np.int64)
        else:
            raise ValueError(f"unsupported component width {b}")
        if c["numeric"] == "SFLOAT":
            out.append(Channel(c["name"], "SFLOAT", b, _float_bits(v, b), v))
            continue
        if c["numeric"] in ("SNORM", "SINT", "SSCALED") and b < 64:
            v = np.where(v >= (1 << (b - 1)), v - (1 << b), v)
        out.append(Channel(c["name"], c["numeric"], b, v))
    return out


def ulp_distance(a_bits: np.ndarray, b_bits: np.ndarray, bits: int) -> np.ndarray:
    """ULP distance between IEEE floats given as bit patterns (sign-magnitude -> ordered ints)."""
    sign = np.int64(1) << (bits - 1)

    def ordered(x):
        x = x.astype(np.int64) & ((np.int64(1) << bits) - 1) if bits < 64 else x.astype(np.int64)
        return np.where(x & sign, sign - x, x) if bits < 64 else np.where(x < 0, np.int64(-2**63) - x, x)
    return np.abs(ordered(a_bits) - ordered(b_bits))


def max_unit_diff(a: list[Channel], b: list[Channel]) -> dict:
    """Largest per-channel difference in the format's units: LSB for integer kinds, ULP for floats."""
    out = {}
    for ca, cb in zip(a, b):
        if ca.codes.shape != cb.codes.shape:
            out[ca.name] = "shape"
            continue
        if ca.is_float and ca.fbits is not None and cb.fbits is not None and ca.bits in (16, 32, 64):
            nan_mismatch = int(np.sum(np.isnan(ca.codes) != np.isnan(cb.codes)))
            d = ulp_distance(ca.fbits, cb.fbits, ca.bits)
            d = np.where(np.isnan(ca.codes) & np.isnan(cb.codes), 0, d)
            out[ca.name] = {"max_ulp": int(d.max()) if d.size else 0, "differing": int(np.sum(d > 0)),
                            "nan_mismatch": nan_mismatch}
        elif ca.is_float:
            d = np.abs(np.nan_to_num(ca.codes) - np.nan_to_num(cb.codes))
            out[ca.name] = {"max_abs": float(d.max()) if d.size else 0.0, "differing": int(np.sum(d > 0))}
        else:
            d = np.abs(ca.codes - cb.codes)
            out[ca.name] = {"max_lsb": int(d.max()) if d.size else 0, "differing": int(np.sum(d > 0))}
    return out
