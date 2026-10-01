"""Oracle determinism and validity over a case directory (PLAN_v1.md §7 step 4, §8.4).

    python3 swiftshader_vk/tools/determinism.py [CASE_DIR] [--jobs N] [--repeats N] [--out DIR]

Runs every case through the instance's driver (the real grading path) in these
variants, and compares snapshots byte for byte, query events, and the stream
of VkResults:

    base    LLVM backend, ThreadCount=4        x repeats (default 3)
    thr1    LLVM backend, ThreadCount=1        x 2   (sample_mult=2, the refcache n2 run)
    subzero Subzero backend, ThreadCount=4     x 1
    valid   LLVM backend with the Khronos validation layer x 1

and reports per case: exit status, validation messages, whether repeats are
identical, whether thread count changes anything, whether the backend does,
and the largest difference in the format's own units (LSB / ULP) where they
differ. Writes <out>/determinism.json and prints a Markdown summary.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "evalBase"))

import snapshot as ss                              # noqa: E402
from evalbase.interfaces import load_instance      # noqa: E402


def drive(inst, case: Path, out: Path, assets: Path, *, sample_mult=1, perturb="", env=None):
    r = inst.driver.run(str(case), str(out), str(assets), sample_mult=sample_mult, perturbation=perturb,
                        timeout_s=300, cpus="4", extra_env=env or {})
    return r


def outputs(outdir: Path) -> dict:
    """Snapshot payloads, query values and the call stream of one run."""
    lp = outdir / "ledger.json"
    if not lp.exists():
        return {"exit": "no_ledger"}
    L = json.loads(lp.read_text())
    snaps, queries = {}, {}
    for e in L.get("events", []):
        if e["op"] == "snapshot":
            snaps[e["name"]] = ss.read_ssnap(str(outdir / e["files"]["snap"]))
        elif e["op"] == "query":
            queries[e["name"]] = e["value"]
    return {"exit": L.get("exit"), "snaps": snaps, "queries": queries,
            "calls": [tuple(c) for c in L.get("calls", [])], "validation": L.get("validation", []),
            "notes": L.get("notes", []), "error": L.get("error")}


def compare(a: dict, b: dict) -> dict:
    """{'identical': bool, 'diffs': [...]} between two runs of one case."""
    diffs = []
    if a.get("exit") != b.get("exit"):
        diffs.append({"what": "exit", "a": a.get("exit"), "b": b.get("exit")})
        return {"identical": False, "diffs": diffs}
    for name, sa in a.get("snaps", {}).items():
        sb = b.get("snaps", {}).get(name)
        if sb is None:
            diffs.append({"what": "snapshot_missing", "snapshot": name})
            continue
        for it in sa.items:
            ib = sb.item(it["name"])
            ra, rb = sa.raw(it), sb.raw(ib) if ib else None
            if ra == rb:
                continue
            d = {"what": "snapshot", "snapshot": name, "item": it["name"], "kind": it.get("kind"),
                 "format": it.get("format") or it.get("elem")}
            if ra is not None and rb is not None and len(ra) == len(rb):
                try:
                    d["units"] = ss.max_unit_diff(ss.decode(it, ra), ss.decode(ib, rb))
                except Exception as exc:          # a format the decoder does not handle yet
                    d["units"] = f"undecoded: {exc}"
            diffs.append(d)
    for name, qa in a.get("queries", {}).items():
        if b.get("queries", {}).get(name) != qa:
            diffs.append({"what": "query", "name": name})
    ca = [c for c in a.get("calls", []) if not c[1].startswith("vkQueueSubmit+")]
    cb = [c for c in b.get("calls", []) if not c[1].startswith("vkQueueSubmit+")]
    if ca != cb:
        diffs.append({"what": "calls", "a": len(ca), "b": len(cb)})
    return {"identical": not diffs, "diffs": diffs}


#: Extra oracle variants compared against the base run (set by --perturb).
PERTURB: list[str] = []
#: The CONTROL_POISON shim (built in main unless --no-poison): the oracle with every new
#: allocation filled with 0xA5. A case whose outputs differ under it reads memory it never
#: wrote -- stable inside one process, so repeats and thread counts cannot reveal it.
POISON_LIB: list[str] = []


def is_violation(msg: str) -> bool:
    """A validation-layer message that cites the spec (a VUID): invalid usage, which makes a
    case's output meaningless. Loader and informational messages (e.g. an unsupported
    extension a case requests on purpose, to observe VK_ERROR_EXTENSION_NOT_PRESENT) are not."""
    return "VUID-" in msg or "The Vulkan spec states" in msg


def run_case(inst, case: Path, assets: Path, work: Path, repeats: int) -> dict:
    name = case.stem
    runs = {}
    for k in range(repeats):
        o = work / name / f"base{k}"
        drive(inst, case, o, assets)
        runs[f"base{k}"] = outputs(o)
    for k in range(2):
        o = work / name / f"thr1_{k}"
        drive(inst, case, o, assets, sample_mult=2)
        runs[f"thr1_{k}"] = outputs(o)
    o = work / name / "subzero"
    drive(inst, case, o, assets, perturb="subzero")
    runs["subzero"] = outputs(o)
    o = work / name / "valid"
    drive(inst, case, o, assets, env={"DRIVER_VALIDATE": "1"})
    runs["valid"] = outputs(o)
    for p in PERTURB:
        o = work / name / p
        drive(inst, case, o, assets, perturb=p)
        runs[p] = outputs(o)
    if POISON_LIB:
        o = work / name / "poison"
        os.makedirs(o, exist_ok=True)
        inst.driver.run(str(case), str(o), str(assets), candidate=POISON_LIB[0], timeout_s=300, cpus="4")
        runs["poison"] = outputs(o)
    base = runs["base0"]
    msgs = runs["valid"].get("validation", [])
    violations = [v for v in msgs if is_violation(v["msg"])]
    rep = {"case": name, "family": json.loads(case.read_text()).get("family"),
           "exit": base.get("exit"), "error": base.get("error"),
           "skips": [n["msg"] for n in base.get("notes", []) if n.get("level") in ("skip", "error")][:5],
           "validation": [v["msg"][:400] for v in violations][:10],
           "validation_count": len(violations),
           "validation_other": [v["msg"][:200] for v in msgs if not is_violation(v["msg"])][:5]}
    rep["repeat"] = [compare(base, runs[f"base{k}"]) for k in range(1, repeats)]
    rep["threads"] = [compare(base, runs["thr1_0"]), compare(runs["thr1_0"], runs["thr1_1"])]
    rep["backend"] = compare(base, runs["subzero"])
    rep["perturbations"] = {p: compare(base, runs[p]) for p in PERTURB}
    rep["repeat_identical"] = all(r["identical"] for r in rep["repeat"])
    rep["threads_identical"] = all(r["identical"] for r in rep["threads"])
    rep["backend_identical"] = rep["backend"]["identical"]
    if POISON_LIB:
        rep["poison"] = compare(base, runs["poison"])
        rep["poison_identical"] = rep["poison"]["identical"]
    return rep


def summarize(reports: list[dict]) -> str:
    lines = ["| Case | Family | Exit | Validation | Repeats | Threads | LLVM vs Subzero | Largest difference |",
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(reports, key=lambda r: (r["family"] or "", r["case"])):
        worst = []
        for block in (r["repeat"] + r["threads"] + [r["backend"]]):
            for d in block["diffs"]:
                if d["what"] == "snapshot":
                    worst.append(f'{d["item"]}: {json.dumps(d.get("units"))[:120]}')
                else:
                    worst.append(d["what"] + (f' {d.get("name", "")}' if d.get("name") else ""))
        mark = lambda b: "same" if b else "**differs**"   # noqa: E731
        lines.append(f'| {r["case"]} | {r["family"]} | {r["exit"]} | {r["validation_count"]} | {mark(r["repeat_identical"])} '
                     f'| {mark(r["threads_identical"])} | {mark(r["backend_identical"])} | {"; ".join(sorted(set(worst)))[:300]} |')
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", nargs="?", default=str(HERE.parent / "runs" / "pilot" / "cases"))
    ap.add_argument("--assets", default=None)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-poison", action="store_true", help="skip the poisoned-memory run")
    ap.add_argument("--perturb", nargs="*", default=[],
                    help="also compare these oracle variants to the base run (vtxjitter texcoord_ulp lavapipe)")
    a = ap.parse_args()
    PERTURB[:] = a.perturb
    inst = load_instance(os.environ.get("EVALBASE_INSTANCE") or str(HERE.parent))
    if not a.no_poison:
        lib = Path(a.out or HERE.parent / "runs" / "determinism") / "poison_lib"
        POISON_LIB[:] = [str(inst.controls.build_from({"defines": {"CONTROL_POISON": 1}}, lib, "poison"))]
    cases = sorted(Path(a.case_dir).glob("*.json"))
    if a.only:
        cases = [c for c in cases if c.stem in a.only]
    assets = Path(a.assets) if a.assets else Path(a.case_dir).parent / "assets"
    out = Path(a.out) if a.out else Path(a.case_dir).parent / "determinism"
    work = out / "runs"
    reports = []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(run_case, inst, c, assets, work, a.repeats): c for c in cases}
        for f in cf.as_completed(futs):
            r = f.result()
            reports.append(r)
            print(f'[{len(reports)}/{len(cases)}] {r["case"]}: exit={r["exit"]} valid={r["validation_count"]} '
                  f'repeat={r["repeat_identical"]} threads={r["threads_identical"]} backend={r["backend_identical"]}',
                  flush=True)
    (out / "determinism.json").write_text(json.dumps(reports, indent=1))
    print(summarize(reports))


if __name__ == "__main__":
    main()
