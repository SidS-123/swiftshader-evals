"""Minimal PNG writer (zlib only) for previews and the site.

Turning an instance's own output format into an RGB8 picture belongs to its
scorer (`CaseScorer.preview_rgb8`); this module only encodes arrays.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np


def write_png(path: str, rgb8: np.ndarray) -> None:
    """Write an HxWx3 (or HxW, expanded to grey) uint8 array as an 8-bit RGB PNG."""
    rgb8 = np.asarray(rgb8)
    if rgb8.ndim == 2:
        rgb8 = np.repeat(rgb8[:, :, None], 3, axis=2)
    h, w = rgb8.shape[:2]
    raw = b"".join(b"\x00" + rgb8[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def grey_to_rgb8(values: np.ndarray, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    """Map a 2-D float array to an RGB8 picture, linearly between `lo` and `hi`.

    Without bounds the finite 1st and 99th percentiles are used, so a depth or
    a value channel gets a readable picture whatever its range.
    """
    x = np.asarray(values, dtype=np.float64)
    fin = np.isfinite(x)
    if not fin.any():
        return np.zeros(x.shape + (3,), np.uint8)
    if lo is None:
        lo = float(np.percentile(x[fin], 1))
    if hi is None:
        hi = float(np.percentile(x[fin], 99))
    g = np.zeros_like(x)
    g[fin] = (x[fin] - lo) / max(hi - lo, 1e-9)
    grey = np.clip(g * 255, 0, 255).astype(np.uint8)
    return np.repeat(grey[:, :, None], 3, axis=2)
