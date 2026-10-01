"""Family `mem_buf`: buffers, memory types, map/unmap, copy/fill/update, alignment
(replay + procedural).

  copies       (replay) vkCmdCopyBuffer with many regions at unaligned offsets and
               sizes, between buffers and within one buffer (non-overlapping),
               snapshotted after each step
  fill_update  (replay) vkCmdFillBuffer / vkCmdUpdateBuffer at 4-byte granularity,
               whole-size fills, interleaved with host writes through the mapping
  large        (replay) a multi-MiB buffer filled and copied in strides, snapshotted
               in sub-ranges
  memory       (procedural) memory properties, then buffers of many sizes and usages
               created, allocated and bound (the `VkResult` stream) and their
               zeroed contents read back
"""
from __future__ import annotations

import numpy as np

from common import Case, pub_name, rng, write_all

FAMILY = "mem_buf"

PUBLIC = [
    {"tag": "copies", "seed": 1901, "variant": "copies", "size": 4099, "steps": 4, "regions": 7},
    {"tag": "fill_update", "seed": 1902, "variant": "fill_update", "size": 2048, "steps": 5},
    {"tag": "large", "seed": 1903, "variant": "large", "mib": 6, "chunks": 5},
    {"tag": "memory", "seed": 1904, "variant": "memory",
     "buffers": [[1, ["uniform_buffer"]], [3, ["storage_buffer"]], [4097, ["vertex_buffer", "index_buffer"]],
                 [65536, ["indirect_buffer", "storage_buffer"]], [1 << 20, ["uniform_texel_buffer"]],
                 [(1 << 22) + 12, ["storage_texel_buffer", "transfer_src"]]]},
]


def random_regions(r, size, n, align=1):
    """n non-overlapping (src, dst, len) regions inside [0, size): src and dst ranges in disjoint halves."""
    regions = []
    half = size // 2
    cuts = np.sort(r.choice(np.arange(1, half // align), size=2 * n, replace=False)) * align
    for k in range(n):
        a, b = int(cuts[2 * k]), int(cuts[2 * k + 1])
        length = b - a
        dst = half + a + int(r.randint(0, max(1, (size - half - b) // align))) * align // 4
        dst = min(dst, size - length)
        regions.append([a, dst, length])
    # dst ranges must not overlap each other either: keep only a non-overlapping subset
    out, taken = [], []
    for s, d, ln in regions:
        if all(d + ln <= t0 or d >= t1 for t0, t1 in taken):
            out.append([s, d, ln])
            taken.append((d, d + ln))
    return out


def v_copies(name, p, r):
    size = p["size"]
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    for b in ("x", "y"):
        c.buffer(b, size)
        c.upload(b, array=r.randint(0, 256, size=size).astype(np.uint8))
    for k in range(p["steps"]):
        between = random_regions(r, size, p["regions"])
        within = random_regions(r, size, max(1, p["regions"] // 2))
        c.exec([{"cmd": "copy_buffer", "src": "x", "dst": "y", "regions": between},
                {"cmd": "copy_buffer", "src": "y", "dst": "y", "regions": within},
                {"cmd": "copy_buffer", "src": "y", "dst": "x",
                 "regions": [[d, s, ln] for s, d, ln in random_regions(r, size, 2)]}])
        c.snapshot([{"name": "x", "buffer": "x"}, {"name": "y", "buffer": "y"}])
        if k % 2 == 1:     # host write through the mapping at an odd offset between steps
            off = int(r.randint(1, size // 2)) | 1
            c.upload("x", offset=off, array=r.randint(0, 256, size=int(r.randint(1, size - off))).astype(np.uint8))
    return c


def v_fill_update(name, p, r):
    size = p["size"]
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    c.buffer("b", size)
    c.buffer("c", size)
    for k in range(p["steps"]):
        cmds = []
        for _ in range(3):
            off = int(r.randint(0, size // 4 - 1)) * 4
            n = int(r.randint(1, (size - off) // 4 + 1)) * 4
            cmds.append({"cmd": "fill_buffer", "buffer": "b", "offset": off, "size": n,
                         "value": int(r.randint(0, 2**32, dtype=np.uint64))})
        off = int(r.randint(0, size // 8)) * 4
        n = int(r.randint(1, min(256, (size - off) // 4) + 1))
        cmds.append({"cmd": "update_buffer", "buffer": "b", "offset": off,
                     "data": {"u32": [int(x) for x in r.randint(0, 2**31, size=n)]}})
        if k == 2:
            cmds.append({"cmd": "fill_buffer", "buffer": "c", "value": 0xDEADBEEF})      # whole size
        off = int(r.randint(0, size // 4 - 64)) * 4
        cmds.append({"cmd": "copy_buffer", "src": "b", "dst": "c", "regions": [[off, size - off - 252, 250]]})
        c.exec(cmds)
        c.snapshot([{"name": "b", "buffer": "b", "elem": "u32"}, {"name": "c", "buffer": "c", "elem": "u32"}])
        c.upload("b", offset=int(r.randint(0, size - 16)), data={"u8": [int(x) for x in r.randint(0, 256, 13)]})
    return c


def v_large(name, p, r):
    size = p["mib"] << 20
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    c.buffer("src", size)
    c.buffer("dst", size)
    c.upload("src", array=r.randint(0, 2**31, size=size // 4).astype(np.uint32))
    chunk = size // p["chunks"]
    c.exec([{"cmd": "fill_buffer", "buffer": "dst", "value": 0x01020304},
            {"cmd": "copy_buffer", "src": "src", "dst": "dst",
             "regions": [[k * chunk + 3 * k, size - (k + 1) * chunk + k, chunk - 7 * k - 1] for k in range(p["chunks"])]}])
    items = []
    for k in range(4):
        off = int(r.randint(0, size // 4 - 4096)) * 4
        items.append({"name": f"dst_{k}", "buffer": "dst", "offset": off, "size": 16384, "elem": "u32"})
    items.append({"name": "dst_tail", "buffer": "dst", "offset": size - 65536, "elem": "u32"})
    c.snapshot(items)
    return c


def v_memory(name, p, r):
    c = Case(name, FAMILY, "procedural")
    c.instance()
    c.query("memory", {"memory": True})
    c.device()
    for k, (size, usage) in enumerate(p["buffers"]):
        c.buffer(f"b{k}", size, usage)
    c.snapshot([{"name": f"b{k}", "buffer": f"b{k}", "size": min(size, 4096)} for k, (size, _) in enumerate(p["buffers"])])
    for k, (size, _) in enumerate(p["buffers"]):
        c.upload(f"b{k}", offset=size // 2, data={"u8": [int(x) for x in r.randint(0, 256, min(size - size // 2, 64))]})
    c.snapshot([{"name": f"b{k}", "buffer": f"b{k}", "offset": size // 2, "size": min(size - size // 2, 64)}
                for k, (size, _) in enumerate(p["buffers"])])
    return c


VARIANTS = {"copies": v_copies, "fill_update": v_fill_update, "large": v_large, "memory": v_memory}


def build(name, p):
    return VARIANTS[p["variant"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
