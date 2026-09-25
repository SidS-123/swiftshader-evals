"""Starter: replace this with your implementation of SPEC.md."""


class Painter:
    def __init__(self):
        self.width, self.height = 1, 1

    def apply(self, op):
        if op.get("op") == "canvas":
            self.width, self.height = int(op["width"]), int(op["height"])
        return None

    def draw(self, samples):
        return [[0.0] * self.width for _ in range(self.height)]
