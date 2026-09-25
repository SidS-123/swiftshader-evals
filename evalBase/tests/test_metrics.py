"""The generic metric arithmetic, and the toy scorer's distance against it.

The image-specific assertions run through the toy's `CaseScorer`.
"""
import numpy as np
import pytest

from evalbase.grader import metrics as m
from evalbase.interfaces import MetricSpec

pytest.importorskip("numpy")


def _picture(seed=0, size=64):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    img = np.sin(6 * x) * 0.3 + np.cos(4 * y) * 0.2 + 0.4
    for _ in range(8):
        cx, cy, r = rng.uniform(0, 1, 3)
        img[(x - cx) ** 2 + (y - cy) ** 2 < (0.05 + 0.1 * r) ** 2] = rng.uniform(0.2, 1)
    return np.clip(img, 0, 1).astype(np.float32)


@pytest.fixture
def scorer(toy):
    return toy.scorer


def _spec(**kw):
    base = dict(perturbations=("halfsamples", "jitter"), tolerance_perturbations=("halfsamples", "jitter"))
    base.update(kw)
    return MetricSpec(**base)


# ------------------------------------------------------------- the toy distance

def test_identical_is_zero_defect(scorer):
    a = _picture()
    assert scorer.distance(a, a) == 0.0
    assert m.hill(0.0, 0.05) == 1.0


def test_missing_or_wrong_size_is_one(scorer):
    a = _picture()
    assert scorer.distance(a, None) == 1.0
    assert scorer.distance(a, a[:32, :32]) == 1.0


def test_uniform_candidate_against_structured_ref_is_one(scorer):
    a = _picture()
    assert scorer.distance(a, np.zeros_like(a)) == 1.0
    assert scorer.distance(a, np.full_like(a, 0.5)) == 1.0


def test_sparse_noise_below_floor_scores_near_perfect(scorer):
    a = _picture()
    rng = np.random.default_rng(1)
    b = np.clip(a + rng.normal(0, 0.01, a.shape), 0, 1).astype(np.float32)
    assert scorer.distance(a, b) < 0.02


def test_concentrated_defect_worse_than_sparse_noise(scorer):
    a = _picture()
    rng = np.random.default_rng(2)
    sparse = np.clip(a + rng.normal(0, 0.02, a.shape), 0, 1).astype(np.float32)
    concentrated = a.copy()
    concentrated[16:48, 16:48] = 1.0 - concentrated[16:48, 16:48]
    assert scorer.distance(a, concentrated) > scorer.distance(a, sparse)


def test_uniform_gain_is_a_defect(scorer):
    a = _picture() * 0.45
    assert scorer.distance(a, np.clip(a * 2.0, 0, 1)) > 0.5      # x2 fails the snapshot at any T
    assert scorer.distance(a, np.clip(a * 1.3, 0, 1)) > 0.03     # a 30% exposure error registers
    assert scorer.distance(a, np.clip(a * 1.05, 0, 1)) == 0.0    # a 5% error stays under the floor


# ------------------------------------------------------------- the arithmetic

def test_hill_shape():
    assert abs(m.hill(0.1, 0.1) - 0.5) < 1e-9
    assert m.hill(0.05, 0.1) > 0.9
    assert m.hill(0.2, 0.1) < 0.1
    assert m.hill(0.0, 0.0) == 1.0 and m.hill(0.1, 0.0) == 0.0


def test_threshold_clamps_and_noise_floor(scorer):
    spec = _spec()
    snapshots = [_picture(s) for s in range(3)]
    t = m.replay_threshold(scorer.distance, snapshots, None, spec=spec)
    assert spec.t_lo <= t.threshold <= spec.t_hi
    noisy = [np.clip(f + np.random.default_rng(9).normal(0, 0.12, f.shape), 0, 1).astype(np.float32) for f in snapshots]
    t2 = m.replay_threshold(scorer.distance, snapshots, noisy, spec=spec)
    assert t2.noise > 0 and t2.threshold >= t.threshold


def test_threshold_uses_the_spec_constants():
    d = lambda a, b: 0.0 if b is None else abs(float(a) - float(b))
    t = m.replay_threshold(d, [1.0], [1.1], [[1.05]], spec=MetricSpec(k_noise=3.0, k_sens=1.0, t_lo=0.01, t_hi=0.9))
    assert t.noise == pytest.approx(0.1) and t.sensitivity == pytest.approx(0.05)
    assert t.threshold == pytest.approx(0.3)
    capped = m.replay_threshold(d, [1.0], [2.0], None, spec=MetricSpec(t_hi=0.5))
    assert capped.threshold == 0.5


def test_score_replay_missing_snapshots(scorer):
    snapshots = [_picture(s) for s in range(4)]
    r = m.score_replay(scorer.distance, snapshots, snapshots[:2], threshold=0.05)
    assert r["snapshot_scores"][:2] == [1.0, 1.0]
    assert all(s < 1e-4 for s in r["snapshot_scores"][2:])
    assert abs(r["score"] - 0.5) < 1e-4
    assert r["first_diverge_snapshot"] == 2


def test_perf_score():
    assert m.perf_score(1.0) > 0.99
    assert abs(m.perf_score(16.0) - 0.5) < 1e-9
    assert abs(m.perf_score(4.0, half=4.0) - 0.5) < 1e-9
    assert m.perf_score(float("inf")) == 0.0
    assert m.perf_score(-1.0) == 0.0


def test_threshold_sensitivity_raises_floor(scorer):
    spec = _spec()
    snapshots = [_picture(s, 96) for s in range(2)]
    shifted = [np.roll(f, 1, axis=1) for f in snapshots]     # a small shift a human ignores
    t0 = m.replay_threshold(scorer.distance, snapshots, None, spec=spec)
    t1 = m.replay_threshold(scorer.distance, snapshots, None, [shifted], spec=spec)
    assert t1.sensitivity > 0 and t1.threshold >= t0.threshold
    # the perturbed reference itself must score well under the derived threshold
    r = m.score_replay(scorer.distance, snapshots, shifted, t1.threshold)
    assert r["score"] > 0.85


def test_mean_snapshot_defect_needs_pairs():
    d = lambda a, b: abs(a - b)
    assert m.mean_snapshot_defect(d, [1, 2], [1, 3]) == 0.5
    assert m.mean_snapshot_defect(d, [1, 2], [1]) is None
    assert m.mean_snapshot_defect(d, [1, 2], [1, None]) is None
    assert m.mean_snapshot_defect(d, [1, 2], None) is None


def test_pgm_roundtrip(tmp_path, toy):
    import scorer as toy_scorer
    from examples_helpers import write_pgm
    a = _picture(3, 32)
    p = tmp_path / "x.pgm"
    write_pgm(str(p), a)
    b = toy_scorer.read_pgm(str(p))
    assert b.shape == a.shape and np.abs(a - b).max() <= 1 / 255 + 1e-6
