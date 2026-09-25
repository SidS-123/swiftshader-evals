"""Control: every pixel twice as bright (clamped).

Prediction: fidelity low (<= 0.2). A uniform gain is invisible to a purely
structural distance; the luminance term in the scorer is what sees it, and
this control is the evidence that it does. Queries are unchanged, so the
procedural category keeps everything but `output_matches_reference`.
See docs/DESIGN.md.
"""
from oracle import Painter as Reference


class Painter(Reference):
    def draw(self, samples):
        return [[min(1.0, 2.0 * v) for v in row] for row in super().draw(samples)]
