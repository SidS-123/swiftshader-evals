"""The ssvk distance and procedural checks on synthetic snapshots (no Docker).

What the metric must do (PLAN_v1.md §10.5): identical -> 0; the free 1 LSB of
rounding costs nothing; a uniform gain, a half-pixel shift, a wrong block, an
integer off-by-one, a NaN, a missing item are all clearly visible; scattered
single-texel edge flips stay small.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "evalBase"))

import scorer as sc                     # noqa: E402
import snapshot as ss                   # noqa: E402
from evalbase.grader import metrics     # noqa: E402

RGBA8 = [{"name": c, "bits": 8, "numeric": "UNORM"} for c in "RGBA"]
RGBA32F = [{"name": c, "bits": 32, "numeric": "SFLOAT"} for c in "RGBA"]
RGBA16UI = [{"name": c, "bits": 16, "numeric": "UINT"} for c in "RGBA"]


def image_item(name, arr, comps, fmt, kind="color", texel_bytes=None, allow=None):
    h, w = arr.shape[:2]
    it = {"name": name, "kind": kind, "format": fmt, "width": w, "height": h, "depth": 1, "layers": 1, "mip": 0,
          "texel_bytes": texel_bytes or arr.dtype.itemsize * (arr.shape[2] if arr.ndim == 3 else 1),
          "packed": 0, "components": comps, "missing": False}
    if allow:
        it["allow"] = allow
    return it, arr.tobytes()


def buffer_item(name, arr, elem, allow=None):
    it = {"name": name, "kind": "buffer", "elem": elem, "count": arr.size, "missing": False}
    if allow:
        it["allow"] = allow
    return it, arr.tobytes()


def snap(*items) -> ss.Snapshot:
    meta, payload = [], b""
    for it, raw in items:
        it = dict(it, offset=len(payload), size=len(raw))
        meta.append(it)
        payload += raw
    return ss.Snapshot({"version": 1, "items": meta}, payload)


def rgba8(seed=1, h=64, w=64):
    rng = np.random.RandomState(seed)
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 3] = 255
    # smooth gradients plus a few hard-edged rectangles: like rendered content
    yy, xx = np.mgrid[0:h, 0:w]
    img[..., 0] = (xx * 255 // (w - 1)).astype(np.uint8)
    img[..., 1] = (yy * 255 // (h - 1)).astype(np.uint8)
    for _ in range(6):
        x0, y0 = rng.randint(0, w - 16), rng.randint(0, h - 16)
        img[y0:y0 + rng.randint(4, 16), x0:x0 + rng.randint(4, 16), 2] = rng.randint(50, 255)
    return img


def D(ref_arr, cand_arr, comps=RGBA8, fmt="VK_FORMAT_R8G8B8A8_UNORM"):
    r = snap(image_item("c", ref_arr, comps, fmt))
    c = snap(image_item("c", cand_arr, comps, fmt))
    return sc.snapshot_distance(r, c)


def score(d, t=0.03):
    return metrics.hill(d, t, 4)


def test_identical_is_zero():
    a = rgba8()
    assert D(a, a.copy()) == 0.0


def test_one_lsb_rounding_everywhere_is_free():
    a = rgba8()
    b = a.astype(np.int16)
    b[..., :3] -= (np.arange(b[..., :3].size).reshape(b[..., :3].shape) % 2)   # truncation-like, <= 1 LSB
    b = np.clip(b, 0, 255).astype(np.uint8)
    assert D(a, b) == 0.0


def test_two_lsb_uniform_bias_is_visible():
    a = rgba8()
    b = np.clip(a.astype(np.int16) + 2, 0, 255).astype(np.uint8)
    b[..., 3] = 255
    assert D(a, b) > 0.2


def test_gain_x2_scores_near_zero():
    a = rgba8()
    b = np.clip(a.astype(np.int32) * 2, 0, 255).astype(np.uint8)
    d = D(a, b)
    assert d > 0.5 and score(d) < 0.01


def test_half_pixel_shift_is_visible():
    a = rgba8()
    b = np.roll(a, 1, axis=1)
    assert score(D(a, b)) < 0.1


def test_one_wrong_block_of_64_fails():
    a = rgba8()
    b = a.copy()
    b[8:16, 24:32, :3] = 255 - b[8:16, 24:32, :3]
    d = D(a, b)
    assert d == pytest.approx(0.125, abs=1e-6)
    assert score(d) < 0.01


def test_scattered_edge_flips_stay_small():
    a = rgba8()
    b = a.copy()
    for y, x in ((5, 7), (30, 41), (50, 12)):          # three texels in three blocks take another colour
        b[y, x, :3] = 255 - b[y, x, :3]
    d = D(a, b)
    assert d < 0.02


def test_uniform_candidate_scores_one():
    a = rgba8()
    b = np.zeros_like(a)
    assert D(a, b) == 1.0


def test_missing_and_mismatched_items_score_one():
    a = rgba8()
    r = snap(image_item("c", a, RGBA8, "VK_FORMAT_R8G8B8A8_UNORM"))
    assert sc.snapshot_distance(r, None) == 1.0
    c = snap(image_item("other", a, RGBA8, "VK_FORMAT_R8G8B8A8_UNORM"))
    assert sc.snapshot_distance(r, c) == 1.0
    small = snap(image_item("c", a[:32], RGBA8, "VK_FORMAT_R8G8B8A8_UNORM"))
    assert sc.snapshot_distance(r, small) == 1.0


def test_integer_formats_are_exact():
    a = (rgba8().astype(np.uint16) * 257)
    b = a + 1                                          # off by one everywhere: free for UNORM, not for UINT
    assert D(a, b, RGBA16UI, "VK_FORMAT_R16G16B16A16_UINT") == 1.0
    c = a.copy()
    c[10, 10, 0] += 1                                  # one texel off (an edge sample): small, like colour
    assert 0 < D(a, c, RGBA16UI, "VK_FORMAT_R16G16B16A16_UINT") < 0.02


def test_float_ulps_and_nan():
    a = np.random.RandomState(3).uniform(0, 1, (64, 64, 4)).astype(np.float32)
    b = a.copy()
    b.view(np.uint32)[...] += 1                        # 1 ULP everywhere: free
    assert D(a, b, RGBA32F, "VK_FORMAT_R32G32B32A32_SFLOAT") == 0.0
    c = a.copy()
    c[::3, :, 1] = np.nan                              # a third of the texels NaN where the reference is not
    assert D(a, c, RGBA32F, "VK_FORMAT_R32G32B32A32_SFLOAT") > 0.5
    n = a.copy()
    n[:, :, 0] = np.nan                                # NaN matching NaN costs nothing
    assert D(n, n.copy(), RGBA32F, "VK_FORMAT_R32G32B32A32_SFLOAT") == 0.0


def test_float_buffer_with_allowance():
    rng = np.random.RandomState(4)
    a = rng.uniform(-3, 3, 1024).astype(np.float32)
    b = (a.astype(np.float64) * (1 + 3e-6)).astype(np.float32)       # ~ tens of ULP relative error
    r = snap(buffer_item("out", a, "f32"))
    assert sc.snapshot_distance(r, snap(buffer_item("out", b, "f32"))) > 0.2
    r2 = snap(buffer_item("out", a, "f32", allow={"ulp": 4096}))
    assert sc.snapshot_distance(r2, snap(buffer_item("out", b, "f32", allow={"ulp": 4096}))) == 0.0


def test_integer_buffer_one_wrong_element():
    a = np.arange(1024, dtype=np.uint32)
    b = a.copy()
    b[500] ^= 1
    d = sc.snapshot_distance(snap(buffer_item("out", a, "u32")), snap(buffer_item("out", b, "u32")))
    assert d == pytest.approx(1 / 4, abs=1e-6)        # one of 16 runs fully wrong -> sqrt(1/16)


def test_depth_and_stencil():
    rng = np.random.RandomState(5)
    dep = rng.uniform(0, 1, (32, 32)).astype(np.float32)
    ref = snap(image_item("d", dep, [{"name": "D", "bits": 32, "numeric": "SFLOAT"}], "VK_FORMAT_D32_SFLOAT",
                          kind="depth", texel_bytes=4))
    one_ulp = dep.copy()
    one_ulp.view(np.uint32)[0, 0] += 1
    c = snap(image_item("d", one_ulp, [{"name": "D", "bits": 32, "numeric": "SFLOAT"}], "VK_FORMAT_D32_SFLOAT",
                        kind="depth", texel_bytes=4))
    assert sc.snapshot_distance(ref, c) == 0.0
    st = rng.randint(0, 255, (32, 32)).astype(np.uint8)
    s_ref = snap(image_item("s", st, [{"name": "S", "bits": 8, "numeric": "UINT"}], "VK_FORMAT_S8_UINT",
                            kind="stencil", texel_bytes=1))
    st2 = st.copy()
    st2[:, ::2] += 1                                   # half the stencil values wrong
    s_c = snap(image_item("s", st2, [{"name": "S", "bits": 8, "numeric": "UINT"}], "VK_FORMAT_S8_UINT",
                          kind="stencil", texel_bytes=1))
    assert sc.snapshot_distance(s_ref, s_c) == 1.0


def test_items_weigh_equally():
    a = rgba8()
    good = image_item("c0", a, RGBA8, "VK_FORMAT_R8G8B8A8_UNORM")
    bad = image_item("c1", a, RGBA8, "VK_FORMAT_R8G8B8A8_UNORM")
    ref = snap(good, bad)
    cand = snap(good, image_item("c1", np.zeros_like(a), RGBA8, "VK_FORMAT_R8G8B8A8_UNORM"))
    assert sc.snapshot_distance(ref, cand) == pytest.approx(np.sqrt(0.5), abs=1e-6)


def test_preview_is_rgb8():
    a = rgba8()
    img = sc.SsvkScorer().preview_rgb8(snap(image_item("c", a, RGBA8, "VK_FORMAT_R8G8B8A8_UNORM")))
    assert img.shape == (64, 64, 3) and img.dtype == np.uint8
    assert (img == a[..., :3]).all()


# ------------------------------------------------------------------ procedural checks

def ledger(calls, queries, exit="ok"):
    return {"exit": exit, "calls": calls,
            "events": [{"op": "query", "kind": k, "name": n, "value": v} for n, k, v in queries]}


def test_procedural_checks():
    s = sc.SsvkScorer()
    ref = ledger([[0, "vkCreateInstance", "VK_SUCCESS"], [3, "vkWaitForFences", "VK_TIMEOUT"]],
                 [("props", "query", {"properties": {"VkPhysicalDeviceProperties": {"deviceName": "X",
                                                                                   "pipelineCacheUUID": [1, 2]}},
                                      "device_extensions": {"VK_KHR_a": 1}}),
                  ("st", "fence_status", {"result": "VK_NOT_READY"})])
    same = json.loads(json.dumps(ref))
    same["events"][0]["value"]["properties"]["VkPhysicalDeviceProperties"]["pipelineCacheUUID"] = [9, 9]
    checks = s.procedural_checks(None, ref, same, "", "", None)
    assert all(c["ok"] for c in checks) and {c["check"] for c in checks} == {
        "vkresult_stream", "query:props:properties", "query:props:device_extensions", "query:st"}
    wrong = json.loads(json.dumps(ref))
    wrong["calls"][1][2] = "VK_SUCCESS"
    wrong["events"][1]["value"]["result"] = "VK_SUCCESS"
    bad = {c["check"]: c for c in s.procedural_checks(None, ref, wrong, "", "", None)}
    assert not bad["vkresult_stream"]["ok"] and "VK_TIMEOUT" in bad["vkresult_stream"]["detail"]
    assert not bad["query:st"]["ok"] and bad["query:props:properties"]["ok"]
    crashed = s.procedural_checks(None, ref, {"exit": "crash"}, "", "", None)
    assert [c["check"] for c in crashed] == ["exit_ok"] and not crashed[0]["ok"]
