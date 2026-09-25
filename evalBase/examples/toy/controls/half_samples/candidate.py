"""Control: the oracle at half the requested samples.

Prediction: fidelity >= 0.9 on every case -- this run is one of the
tolerance perturbations, so the threshold accepts it by construction (a snapshot
at D = sensitivity scores 1/(1 + 0.5**4) = 0.94, and most snapshots sit below
the floor). Procedural and performance unchanged. See docs/DESIGN.md.
"""
from oracle import Painter as Reference


class Painter(Reference):
    def draw(self, samples):
        return super().draw(max(1, samples // 2))
