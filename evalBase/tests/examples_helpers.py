"""Helpers for tests that build toy-shaped run directories without the driver."""
import json
import os

import numpy as np


def write_pgm(path, img):
    img = np.asarray(img, dtype=np.float32)
    h, w = img.shape
    q = np.clip(np.round(img * 255.0), 0, 255).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(f"P5\n{w} {h}\n255\n".encode())
        f.write(q.tobytes())


def write_output(outdir, snapshots, wall=0.5, samples=4, extra_events=None, errors=None):
    """A toy-shaped output directory: PGM images + a ledger under the contract."""
    os.makedirs(outdir, exist_ok=True)
    events = []
    for i, f in enumerate(snapshots):
        fname = f"snap_{i:03d}.pgm"
        write_pgm(os.path.join(outdir, fname), f)
        events += [{"op": "run", "samples": samples, "wall_seconds": wall},
                   {"op": "snapshot", "name": f"snap_{i:03d}", "files": {"gray": fname}}]
    events += list(extra_events or [])
    ledger = {"exit": "ok", "events": events, "errors": list(errors or []), "replay": os.path.basename(outdir)}
    with open(os.path.join(outdir, "ledger.json"), "w") as f:
        json.dump(ledger, f)
    return ledger


def write_case(corpus, name, category="replay", snapshots=2, family="fam", size=32, meta=None, samples=4):
    ops = [{"op": "canvas", "width": size, "height": size, "background": 0.1, "seed": 1},
           {"op": "rect", "id": "r0", "x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4, "value": 0.9}]
    for i in range(snapshots):
        ops += [{"op": "draw", "samples": samples}, {"op": "snapshot", "name": f"snap_{i:03d}"}]
    doc = {"version": 1, "name": name, "family": family, "category": category,
           "assets": {}, "meta": meta or {}, "split": "public", "ops": ops}
    path = corpus / f"{name}.json"
    path.write_text(json.dumps(doc, indent=1))
    return path


def picture(seed=0, size=32):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    img = 0.15 + 0.3 * x + 0.2 * y
    for _ in range(4):
        cx, cy, r = rng.uniform(0, 1, 3)
        img[(x - cx) ** 2 + (y - cy) ** 2 < (0.08 + 0.15 * r) ** 2] = rng.uniform(0.5, 1.0)
    return np.clip(img, 0, 1).astype(np.float32)


def noisier(snapshots, sigma, seed):
    rng = np.random.default_rng(seed)
    return [np.clip(f + rng.normal(0, sigma, f.shape), 0, 1).astype(np.float32) for f in snapshots]
