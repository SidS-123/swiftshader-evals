"""Control: every draw returns the *previous* draw's image (black at first).

Prediction: fidelity low on sequences (about 1/snapshots: only a snapshot that
happens to repeat the previous one can score) and 0 on single-snapshot cases.
Queries are unchanged. See docs/DESIGN.md.
"""
from oracle import Painter as Reference


class Painter(Reference):
    def __init__(self):
        super().__init__()
        self._previous = None

    def draw(self, samples):
        current = super().draw(samples)
        stale = self._previous
        self._previous = current
        if stale is None or len(stale) != len(current) or len(stale[0]) != len(current[0]):
            return [[0.0] * len(current[0]) for _ in current]
        return stale
