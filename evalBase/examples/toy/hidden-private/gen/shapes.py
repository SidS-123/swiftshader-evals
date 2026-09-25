"""Hidden `rects` cases. This tree is private in a real instance (see
NEW_EVAL.md, "The hidden split"); the toy ships it because the toy is a toy."""
from shapes import anim_case, static_case


def generate(split, corpus):
    static_case(corpus, "rects_static_hid_a", split, seed=1101, size=56, n=5, samples=6, background=0.25)
    static_case(corpus, "rects_static_hid_b", split, seed=1102, size=72, n=3, samples=4, background=0.02)
    static_case(corpus, "rects_static_hid_c", split, seed=1103, size=64, n=7, samples=12, background=0.4)
    anim_case(corpus, "rects_anim_hid_move", split, seed=1201, size=64, n=6, samples=4, steps=6,
              background=0.1, pan=False)
    anim_case(corpus, "rects_anim_hid_pan", split, seed=1202, size=64, n=5, samples=8, steps=5,
              background=0.2, pan=True)
    anim_case(corpus, "rects_anim_hid_mixed", split, seed=1203, size=40, n=4, samples=6, steps=8,
              background=0.0, pan=False)
