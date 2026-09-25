"""Hidden `proc_queries` cases (private in a real instance)."""
from queries import query_case


def generate(split, corpus):
    query_case(corpus, "proc_queries_hid_a", split, seed=1301, size=40, n=5, samples=4,
               background=0.1, bad_ops=True)
    query_case(corpus, "proc_queries_hid_b", split, seed=1302, size=56, n=2, samples=8,
               background=0.35, bad_ops=True)
    query_case(corpus, "proc_queries_hid_c", split, seed=1303, size=48, n=6, samples=6,
               background=0.0, bad_ops=False)
