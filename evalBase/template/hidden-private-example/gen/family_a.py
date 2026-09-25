"""Hidden `family_a` cases. This tree lives in a PRIVATE repository, outside
the public one; `CorpusSpec.hidden_env` names it. Never commit it publicly."""
from family_a import build_case


def generate(split, corpus):
    build_case(corpus, "family_a_hid_a", split, seed=1101)
    build_case(corpus, "family_a_hid_b", split, seed=1102)
    build_case(corpus, "family_a_hid_c", split, seed=1103)
