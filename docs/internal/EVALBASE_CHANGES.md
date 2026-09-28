# Changes to the vendored evalBase

Every local change to `evalBase/` is listed here with the reason. Policy: PLAN_v1.md §3.2.

| Date | File | Change | Why | Verified by |
|---|---|---|---|---|
| 2026-09-28 | `tests/test_toy_end_to_end.py` (`test_a_crashing_candidate_is_recorded_not_raised`) | `next(corpus.glob("rects_static*.json"))` → `sorted(corpus.glob("rects_static*.json"))[0]` | Test-only bug: the case picked depended on directory listing order. On WSL ext4 it picked `rects_static_pub_b`, whose wider tolerance (T≈0.089) gives a crash a score of 6.2e-5 (still near zero, crash correctly recorded), above the test's 1e-5 limit; `pub_a` scores 8.1e-7. No grader code changed. | Full suite in WSL, Python 3.12.14 |
