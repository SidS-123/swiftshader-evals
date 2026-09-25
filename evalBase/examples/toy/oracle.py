"""The toy oracle: a seeded, noisy greyscale rectangle painter in pure Python.

This is the program the toy evaluation grades against. It stands in for a
real oracle (a shipped painter, an emulator, a database engine) and has the
two properties the methodology needs: it is deterministic per accumulation
index, so the reference cache is reproducible, and its output carries Monte
Carlo noise whose level depends on the sample count, so tolerance derived from
"the oracle at half the samples" means something.

A canvas is a background value and an ordered list of axis-aligned rectangles
in normalised [0, 1] coordinates. A pixel's noiseless value is the background
composited with every rectangle's analytic coverage of that pixel, in order.
A draw at N samples averages N independent noisy copies of the noiseless
image, where sample i uses `random.Random(seed * 1000003 + i)` and draws one
`gauss(0, SIGMA * sqrt(v + NOISE_BIAS))` per pixel in row-major order, each
noisy sample clipped to [0, 1] before averaging.

The candidate a model writes must expose the same `Painter` class with the
same three methods; the driver (`driver.py`) does the rest.
"""
from __future__ import annotations

import math
import random

SIGMA = 0.25
NOISE_BIAS = 0.02
SEED_STRIDE = 1000003

ERR_INVALID = "INVALID_ARGUMENT"
ERR_UNKNOWN = "UNKNOWN_ID"


class Painter:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.background = 0.0
        self.seed = 0
        self.offset = [0.0, 0.0]
        self.jitter = [0.0, 0.0]
        self.rects: list[dict] = []          # ordered; later rectangles are on top

    # ------------------------------------------------------------ ops
    def apply(self, op: dict):
        """Apply one op; return None, a query value, or {"error": code}."""
        kind = op.get("op")
        if kind == "canvas":
            w, h = op.get("width"), op.get("height")
            if not (isinstance(w, int) and isinstance(h, int) and 1 <= w <= 1024 and 1 <= h <= 1024):
                return {"error": ERR_INVALID}
            bg = op.get("background", 0.0)
            if not isinstance(bg, (int, float)) or not 0.0 <= bg <= 1.0:
                return {"error": ERR_INVALID}
            self.width, self.height = w, h
            self.background = float(bg)
            self.seed = int(op.get("seed", 0))
            self.offset, self.jitter = [0.0, 0.0], [0.0, 0.0]
            self.rects = []
            return None
        if kind == "rect":
            rect = {k: op.get(k) for k in ("x", "y", "w", "h", "value")}
            if not _valid_rect(rect):
                return {"error": ERR_INVALID}
            rect = {k: float(v) for k, v in rect.items()}
            rect["id"] = op.get("id")
            for i, r in enumerate(self.rects):
                if r["id"] == rect["id"]:
                    self.rects[i] = rect
                    return None
            self.rects.append(rect)
            return None
        if kind == "set":
            ident, key, value = op.get("id"), op.get("key"), op.get("value")
            if ident == "camera":
                if key not in ("offset", "jitter") or not _valid_vec2(value):
                    return {"error": ERR_INVALID}
                setattr(self, key, [float(value[0]), float(value[1])])
                return None
            rect = next((r for r in self.rects if r["id"] == ident), None)
            if rect is None:
                return {"error": ERR_UNKNOWN}
            if key not in ("x", "y", "w", "h", "value"):
                return {"error": ERR_INVALID}
            trial = dict(rect, **{key: value})
            if not _valid_rect(trial):
                return {"error": ERR_INVALID}
            rect[key] = float(value)
            return None
        if kind == "remove":
            before = len(self.rects)
            self.rects = [r for r in self.rects if r["id"] != op.get("id")]
            return None if len(self.rects) < before else {"error": ERR_UNKNOWN}
        if kind == "query":
            return self.query(op.get("what"))
        return {"error": ERR_INVALID}

    # ------------------------------------------------------------ image
    def noiseless(self) -> list[list[float]]:
        w, h = self.width, self.height
        dx = self.offset[0] + self.jitter[0]
        dy = self.offset[1] + self.jitter[1]
        img = [[self.background] * w for _ in range(h)]
        for r in self.rects:
            x0, y0 = (r["x"] + dx) * w, (r["y"] + dy) * h
            x1, y1 = x0 + r["w"] * w, y0 + r["h"] * h
            px0, px1 = max(0, int(math.floor(x0))), min(w, int(math.ceil(x1)))
            py0, py1 = max(0, int(math.floor(y0))), min(h, int(math.ceil(y1)))
            v = r["value"]
            for py in range(py0, py1):
                cy = max(0.0, min(py + 1.0, y1) - max(float(py), y0))
                if cy <= 0.0:
                    continue
                row = img[py]
                for px in range(px0, px1):
                    cx = max(0.0, min(px + 1.0, x1) - max(float(px), x0))
                    c = cx * cy
                    if c > 0.0:
                        row[px] = (1.0 - c) * row[px] + c * v
        return img

    def draw(self, samples: int) -> list[list[float]]:
        """Average `samples` noisy copies of the noiseless image; deterministic per sample index."""
        if not isinstance(samples, int) or samples < 1:
            raise ValueError("samples must be a positive integer")
        base = self.noiseless()
        w, h = self.width, self.height
        acc = [[0.0] * w for _ in range(h)]
        for i in range(samples):
            rng = random.Random(self.seed * SEED_STRIDE + i)
            for py in range(h):
                brow, arow = base[py], acc[py]
                for px in range(w):
                    v = brow[px]
                    n = v + rng.gauss(0.0, SIGMA * math.sqrt(v + NOISE_BIAS))
                    arow[px] += 0.0 if n < 0.0 else (1.0 if n > 1.0 else n)
        inv = 1.0 / samples
        return [[a * inv for a in row] for row in acc]

    # ------------------------------------------------------------ queries
    def query(self, what: str):
        if what == "bounds":
            if not self.rects:
                return None
            dx = self.offset[0] + self.jitter[0]
            dy = self.offset[1] + self.jitter[1]
            xs0 = [r["x"] + dx for r in self.rects]
            ys0 = [r["y"] + dy for r in self.rects]
            xs1 = [r["x"] + r["w"] + dx for r in self.rects]
            ys1 = [r["y"] + r["h"] + dy for r in self.rects]
            return [min(xs0), min(ys0), max(xs1), max(ys1)]
        if what == "coverage":
            img = self.noiseless()
            n = sum(1 for row in img for v in row if v > 0.5)
            return n / float(self.width * self.height)
        if what == "mean":
            img = self.noiseless()
            return sum(sum(row) for row in img) / float(self.width * self.height)
        return {"error": ERR_INVALID}


def _valid_vec2(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) and abs(v) <= 1.0 for v in value))


def _valid_rect(rect: dict) -> bool:
    for key in ("x", "y", "w", "h", "value"):
        v = rect.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            return False
    return rect["w"] > 0 and rect["h"] > 0 and 0.0 <= rect["value"] <= 1.0 \
        and -1.0 <= rect["x"] <= 2.0 and -1.0 <= rect["y"] <= 2.0 and rect["w"] <= 3.0 and rect["h"] <= 3.0
