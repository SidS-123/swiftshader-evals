"""Prove the corpus is byte-identical across numpy's SIMD dispatch.

A corpus can depend on the host without anyone noticing: numpy 1.26's float32
transcendental kernels are dispatched per ISA and are not correctly rounded,
so AVX2/FMA3, AVX512 and NEON each produce a slightly different field -- which
reaches the assets, and therefore the sha256 *filenames* of the assets. The
cure is in the generators (compute in float64, narrow to float32 once, write
the float32's shortest decimal); this is the proof.

The generator suite runs twice into two throwaway trees -- once normally, once
with the float32 SIMD kernels switched off through `NPY_DISABLE_CPU_FEATURES`
-- and every case file and asset is compared byte for byte.

    python3 -m evalbase.corpus.determinism --instance X [--split both|public|hidden] [--quiet]

Exit status is 0 only when every file matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

from ..interfaces import load_instance
from . import common

# The switch that changed the float32 kernels' results in the original diagnosis.
DEFAULT_FEATURES = ["AVX512F", "AVX512_SKX", "AVX2", "FMA3", "F16C", "AVX"]
SUBDIRS = {"public": ["public"], "hidden": ["hidden"], "both": ["public", "hidden"]}

# Written into each throwaway tree and run there: it rebuilds a CorpusSpec that
# points at the copy, so `common.generate_all` writes under the copy.
RUNNER = """
import json, sys
from pathlib import Path
from evalbase.interfaces import CorpusSpec
from evalbase.corpus import common
cfg = json.loads(Path(sys.argv[1]).read_text())
root = Path(cfg["root"])
spec = CorpusSpec(root=root, gen_dir=root / "gen", public_dir=root / "public",
                  hidden_dir=root / "hidden", assets_dir=root / "assets",
                  hidden_env=cfg["hidden_env"], hidden_default=cfg["hidden_default"],
                  hidden_package=cfg["hidden_package"], forbidden_hidden_roots=(),
                  double_keys=tuple(cfg["double_keys"]), format_version=cfg["format_version"])
common.generate_all(spec, [cfg["split"]], verbose=False)
"""


def dispatchable() -> list[str]:
    """SIMD features numpy can dispatch on here (baseline ones cannot be off)."""
    import numpy as np
    return list(getattr(np.core._multiarray_umath, "__cpu_dispatch__", []))


def generate(spec, dest: str, split: str, features: list[str]) -> None:
    """Run every generator into `dest`, with `features` disabled in numpy."""
    shutil.copytree(spec.gen_dir, os.path.join(dest, "gen"), ignore=shutil.ignore_patterns("__pycache__"))
    cfg = {"root": dest, "split": split, "hidden_env": spec.hidden_env,
           "hidden_default": common.hidden_gen_root(spec), "hidden_package": spec.hidden_package,
           "double_keys": list(spec.double_keys), "format_version": spec.format_version}
    with open(os.path.join(dest, "config.json"), "w") as f:
        json.dump(cfg, f)
    with open(os.path.join(dest, "run.py"), "w") as f:
        f.write(RUNNER)
    env = dict(os.environ)
    env[spec.hidden_env] = common.hidden_gen_root(spec)
    env.pop("NPY_DISABLE_CPU_FEATURES", None)
    if features:
        env["NPY_DISABLE_CPU_FEATURES"] = ",".join(features)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env["PYTHONPATH"] = here + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, os.path.join(dest, "run.py"), os.path.join(dest, "config.json")],
                          cwd=dest, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise SystemExit(f"generation failed (features disabled: {features or 'none'})")


def digests(root: str, subdirs: list[str]) -> dict[str, str]:
    out = {}
    for sub in list(subdirs) + ["assets"]:
        base = os.path.join(root, sub)
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[f"{sub}/{name}"] = hashlib.sha256(fh.read()).hexdigest()
    return out


def check(spec, split: str = "both", features: list[str] | None = None, quiet: bool = False) -> dict:
    """Generate twice and compare. Returns {"same": [...], "bad": [...], "features": [...]}."""
    wanted = DEFAULT_FEATURES if features is None else features
    usable = set(dispatchable())
    used = [f for f in wanted if f in usable]
    skipped = [f for f in wanted if f not in usable]
    if not quiet:
        print("run A: numpy default dispatch")
        print(f"run B: NPY_DISABLE_CPU_FEATURES={','.join(used) or '(none)'}")
        if skipped:
            print(f"       (not dispatchable on this host, ignored: {','.join(skipped)})")
        if not used:
            print("       WARNING: nothing to disable here -- run B repeats run A, so this "
                  "checks repeatability only, not SIMD independence.")
    tmp = tempfile.mkdtemp(prefix="corpus-determinism-")
    try:
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        os.makedirs(a)
        os.makedirs(b)
        generate(spec, a, split, [])
        generate(spec, b, split, used)
        da, db = digests(a, SUBDIRS[split]), digests(b, SUBDIRS[split])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    names = sorted(set(da) | set(db))
    same = [n for n in names if da.get(n) and da.get(n) == db.get(n)]
    bad = [n for n in names if n not in same]
    if not quiet:
        for n in names:
            print(("  ok   " if n in same else "  DIFF ") + n)
    kinds = {}
    for n in names:
        kind = n.split("/")[0]
        s, t = kinds.get(kind, (0, 0))
        kinds[kind] = (s + (n in same), t + 1)
    for kind in sorted(kinds):
        s, t = kinds[kind]
        print(f"{kind:>8}: {s}/{t} identical")
    print(f"{'total':>8}: {len(same)}/{len(names)} identical, {len(bad)} differing")
    for n in bad[:20]:
        print("  differs:", n)
    return {"same": same, "bad": bad, "features": used}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default=None)
    ap.add_argument("--split", choices=sorted(SUBDIRS), default="both")
    ap.add_argument("--features", default=",".join(DEFAULT_FEATURES),
                    help="comma-separated NPY_DISABLE_CPU_FEATURES list for run B")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    args = ap.parse_args(argv)
    spec = load_instance(args.instance).corpus
    private = common.hidden_gen_root(spec)
    have_private = os.path.isdir(os.path.join(private, "gen"))
    if args.split == "public":
        print("hidden generators: not needed for --split public")
    elif have_private:
        print(f"hidden generators: {private} ({spec.hidden_env})")
    else:
        print(f"hidden generators: MISSING -- no {os.path.join(private, 'gen')}.")
        print(f"       The hidden split cannot be checked here: set {spec.hidden_env} to the "
              f"private generator tree, or run --split public, which needs none of it.")
        return 2
    result = check(spec, args.split, [f for f in args.features.split(",") if f], args.quiet)
    return 0 if not result["bad"] and result["same"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
