"""Control: every op succeeds, every snapshot is black, every query is empty.

Prediction: the null band. Fidelity ~0 (a uniform snapshot against a structured
reference is D = 1 outright), performance 0 (the image gate), procedural only
the checks a silent candidate satisfies for free. See docs/DESIGN.md.
"""


class Painter:
    def __init__(self):
        self.width, self.height = 1, 1

    def apply(self, op):
        if op.get("op") == "canvas":
            self.width, self.height = int(op.get("width", 1)), int(op.get("height", 1))
        if op.get("op") == "query":
            return None
        return None

    def draw(self, samples):
        return [[0.0] * self.width for _ in range(self.height)]
