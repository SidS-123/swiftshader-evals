"""Per-case tolerance and scoring, generic over the instance's distance.

The distance itself -- how far a candidate output is from the reference
output of the same snapshot, as a defect D in [0, 1] -- is an instance's
`CaseScorer.distance`: a block metric over images, a normalised edit distance
over text, a relative norm over numeric arrays, a spectral distance over
audio. Everything the distance feeds is here:

    noise       = mean over snapshots of D(ref_N, ref_2N)
    sensitivity = max over the *tolerated* perturbations of mean over snapshots of
                  D(ref, ref_perturbed)
    T           = clamp(max(k_noise * noise, k_sens * sensitivity), t_lo, t_hi)
    s_snapshot  = 1 / (1 + (D / T) ** hill_n)          # 0.5 at D = T
    s_case      = mean over snapshots of s_snapshot

`motion_p90`, the 90th percentile of the snapshot-to-snapshot reference change,
is recorded for diagnostics and deliberately does not raise T: every snapshot
is taken at a deterministic point of a case, so a large change between
snapshots is not a reason for leniency.

The tolerance set is part of the metric. Every member of
`MetricSpec.tolerance_perturbations` is an output a human calls "the same
output", and the perturbation that defines T scores 1/(1 + 0.5**n) = 0.94 per
snapshot by construction. Adding a member widens T for every candidate, so
measure what each one is worth to the leading candidates before adding it: a
perturbation that lifts existing scores without a line of their code changing
is a metric change, not a fairness fix. Perturbations in
`MetricSpec.perturbations` that are not tolerated are still run into the
reference cache and their defect recorded: they are the cheapest fairness
measurement there is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from ..interfaces import MetricSpec

Distance = Callable[[object, object], float]


def hill(d: float, t: float, n: int = 4) -> float:
    if t <= 0:
        return 1.0 if d <= 0 else 0.0
    x = d / t
    return 1.0 / (1.0 + x ** n)


def mean_snapshot_defect(distance: Distance, ref_snaps: Sequence, snapshots: Sequence | None) -> float | None:
    """Mean D of `snapshots` against `ref_snaps`, or None if they do not pair up."""
    if not snapshots or len(snapshots) != len(ref_snaps) or any(f is None for f in snapshots):
        return None
    return float(np.mean([distance(a, b) for a, b in zip(ref_snaps, snapshots)]))


@dataclass
class ReplayThreshold:
    motion_p90: float
    noise: float
    sensitivity: float
    threshold: float


def replay_threshold(distance: Distance, ref_snaps: Sequence, ref_snaps_2n: Sequence | None,
                     perturbed: Sequence[Sequence] | None = None,
                     spec: MetricSpec | None = None) -> ReplayThreshold:
    """T = clamp(max(k_noise*noise, k_sens*sensitivity), t_lo, t_hi).

    `perturbed` holds snapshot lists from reference runs that a human would
    call identical. Their defect against the unperturbed reference defines how
    sensitive this case's content is; the threshold scales with it so static
    content is not stuck at t_lo. The sensitivity term is the max over what it
    is handed -- the caller chooses the set, and `build_refcache` hands it the
    tolerated perturbations only.
    """
    spec = spec or MetricSpec()
    motion = [distance(a, b) for a, b in zip(ref_snaps, ref_snaps[1:])]
    motion_p90 = float(np.percentile(motion, 90)) if motion else 0.0
    if ref_snaps_2n:
        noise = float(np.mean([distance(a, b) for a, b in zip(ref_snaps, ref_snaps_2n)]))
    else:
        noise = 0.0
    sens = 0.0
    for snapshots in perturbed or []:
        d = mean_snapshot_defect(distance, ref_snaps, snapshots)
        if d is not None:
            sens = max(sens, d)
    t = min(max(max(spec.k_noise * noise, spec.k_sens * sens), spec.t_lo), spec.t_hi)
    return ReplayThreshold(motion_p90, noise, sens, t)


def score_replay(distance: Distance, ref_snaps: Sequence, cand_snaps: Sequence,
                 threshold: float, hill_n: int = 4) -> dict:
    """Mean Hill score over snapshots; missing candidate snapshots score 0."""
    defects, scores = [], []
    for i, ref in enumerate(ref_snaps):
        cand = cand_snaps[i] if i < len(cand_snaps) else None
        d = distance(ref, cand)
        defects.append(d)
        scores.append(hill(d, threshold, hill_n))
    first_bad = next((i for i, s in enumerate(scores) if s < 0.5), None)
    return {
        "score": float(np.mean(scores)) if scores else 0.0,
        "threshold": threshold,
        "snapshot_defects": defects,
        "snapshot_scores": scores,
        "first_diverge_snapshot": first_bad,
        "max_defect_snapshot": int(np.argmax(defects)) if defects else None,
    }


def perf_score(ratio: float, half: float = 16.0, n: int = 4) -> float:
    """Performance score of a candidate/reference wall-time ratio: 0.5 at `half`."""
    if not math.isfinite(ratio) or ratio <= 0:
        return 0.0
    return hill(ratio, half, n)
