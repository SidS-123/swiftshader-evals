"""Hidden `perf_many` cases (private in a real instance)."""
from perf import perf_case


def generate(split, corpus):
    perf_case(corpus, "perf_many_hid_a", split, seed=1401, size=160, n=40, samples=32)
    perf_case(corpus, "perf_many_hid_b", split, seed=1402, size=128, n=80, samples=48)
