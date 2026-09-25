"""Shared corpus machinery: the hidden-generator loader, host-independent float
text, content-addressed assets and the case writer.

Conventions for an instance's generators (`CorpusSpec.gen_dir`):

- One module per feature family, exposing `generate(split, corpus)` where
  `split` is "public" or "hidden" and `corpus` is a `CorpusWriter`. Public
  cases go to `CorpusSpec.public_dir` (tracked); hidden cases go to
  `CorpusSpec.hidden_dir` (ignored, never shown to the solver). Public and
  hidden must be different draws: different seeds AND different parameter
  values and compositions, not just seeds.
- **The public tree holds the public cases only.** A hidden case function, its
  seeds and its parameter draws belong in the private tree, in
  `gen/<same module name>.py`; `generate("hidden", corpus)` in a public module
  is one line, `return corpus.hidden_generate(__file__, split)`. A helper both
  splits use stays public; one only the hidden split uses moves out.
- Case names must be unique across all modules and are file names; prefix
  them with the family.
- Category names are the instance's `MetricSpec` categories.
- Large arrays go through `CorpusWriter.asset()` (sha256-named files); small
  ones inline.

Host-independent float text: every float that reaches a case JSON is written
as the float32 value the consumer will read, spelled as the shortest decimal
that names that float32. Both steps are pure functions of the float32 bit
pattern, so a value produced by glibc's libm and the same value produced by
another libm -- which differ only in the last bit of a double -- serialise to
identical text. Data that is genuinely double precision (under a key named in
`CorpusSpec.double_keys`) is written at 15 significant digits, one short of
the 17 a double needs, which absorbs a 1-ULP libm difference. Generators
compute in float64 and narrow to float32 exactly once, so numpy's per-ISA
float32 SIMD kernels cannot move a byte either.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import struct
import sys
import types
from pathlib import Path

import numpy as np

from ..interfaces import CorpusSpec


class HiddenGeneratorsUnavailable(SystemExit):
    """The private hidden-generator tree is missing, misplaced or incomplete.

    A SystemExit so that a generator run prints one clear line and exits
    non-zero instead of a traceback.
    """


# ------------------------------------------------------- the hidden generators

def hidden_gen_root(spec: CorpusSpec) -> str:
    """The private tree: $<hidden_env>, else the spec's default sibling directory."""
    return os.path.abspath(os.environ.get(spec.hidden_env) or str(spec.hidden_default))


def _inside(path: str, parent: str) -> bool:
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


def require_hidden_gen(spec: CorpusSpec) -> str:
    """The private tree's `gen` directory, or one clear error naming the env var."""
    root = hidden_gen_root(spec)
    how = (f"set {spec.hidden_env} to the private hidden-generator tree "
           f"(default: {spec.hidden_default})")
    for forbidden in spec.forbidden_hidden_roots or ():
        if _inside(root, str(forbidden)):
            raise HiddenGeneratorsUnavailable(
                f"the hidden generators must live outside the public tree, but "
                f"{spec.hidden_env} resolves to {root}, which is inside {forbidden}: {how}")
    gen = os.path.join(root, "gen")
    if not os.path.isdir(gen):
        raise HiddenGeneratorsUnavailable(
            f"the hidden split cannot be generated here: no generator tree at {gen} -- "
            f"{how}. The public split needs none of it.")
    return gen


def hidden_module(spec: CorpusSpec, name: str):
    """Import `<private>/gen/<name>.py` under a private package name.

    The private modules are loaded by path, never through sys.path, so a
    private module cannot shadow the public one of the same name it imports.
    """
    gen = require_hidden_gen(spec)
    public = str(spec.gen_dir)
    if public not in sys.path:                 # the private modules import the public helpers
        sys.path.insert(0, public)
    if spec.hidden_package not in sys.modules:
        package = types.ModuleType(spec.hidden_package)
        package.__path__ = [gen]
        sys.modules[spec.hidden_package] = package
    full = f"{spec.hidden_package}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = os.path.join(gen, name + ".py")
    if not os.path.isfile(path):
        raise HiddenGeneratorsUnavailable(
            f"the hidden generators are incomplete: {path} is missing "
            f"({spec.hidden_env} resolves to {hidden_gen_root(spec)})")
    module_spec = importlib.util.spec_from_file_location(full, path)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[full] = module
    try:
        module_spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[full]
        raise
    return module


# ------------------------------------------------- host-independent float text

def round_f32(x: float) -> float:
    """The float32 the consumer will read, as the shortest decimal naming it."""
    try:
        v = struct.unpack("<f", struct.pack("<f", x))[0]
    except (OverflowError, ValueError):
        return float(x)                      # out of float32 range: leave it
    if v != v or v in (float("inf"), float("-inf")):
        return v
    for digits in range(1, 10):              # 9 always round-trips a float32
        text = "%.*g" % (digits, v)
        if struct.unpack("<f", struct.pack("<f", float(text)))[0] == v:
            return float(text)
    return v


def round_f64(x: float) -> float:
    """A double kept as a double, trimmed to 15 significant digits."""
    if x != x or x in (float("inf"), float("-inf")):
        return x
    return float("%.15g" % x)


def narrow(node, double_keys=(), f64: bool = False):
    """Recursively rewrite every float in a case document.

    Floats are narrowed to float32 text, except inside the subtree of a dict
    key named in `double_keys`, which is written at 15 significant digits.
    """
    if isinstance(node, float):
        return round_f64(node) if f64 else round_f32(node)
    if isinstance(node, dict):
        return {key: narrow(value, double_keys, True if key in double_keys else f64)
                for key, value in node.items()}
    if isinstance(node, list):
        return [narrow(v, double_keys, f64) for v in node]
    return node


# ------------------------------------------------------------------- writer

class CorpusWriter:
    """Writes cases and assets for one split of one instance's corpus."""

    def __init__(self, spec: CorpusSpec, verbose: bool = True):
        self.spec = spec
        self.verbose = verbose
        self.written: list[str] = []

    def out_dir(self, split: str) -> Path:
        if split == "public":
            return Path(self.spec.public_dir)
        if split == "hidden":
            return Path(self.spec.hidden_dir)
        raise ValueError(f"split must be public or hidden, got {split!r}")

    def asset(self, array: np.ndarray) -> str:
        """Store a raw little-endian array as a content-addressed asset; return its file name."""
        raw = np.ascontiguousarray(array).astype(array.dtype.newbyteorder("<")).tobytes()
        sha = hashlib.sha256(raw).hexdigest()
        fname = f"{sha}.bin"
        assets = Path(self.spec.assets_dir)
        assets.mkdir(parents=True, exist_ok=True)
        path = assets / fname
        if not path.exists():
            path.write_bytes(raw)
        return fname

    def write(self, name: str, family: str, category: str, ops: list, *, split: str,
              meta: dict | None = None, assets: dict | None = None, extra: dict | None = None) -> Path:
        """Write one case document. Every float is narrowed; the file is `<name>.json`."""
        out = self.out_dir(split)
        out.mkdir(parents=True, exist_ok=True)
        doc = {"version": self.spec.format_version, "name": name, "family": family,
               "category": category, "assets": assets or {}, "meta": meta or {},
               "split": split, "ops": ops}
        if extra:
            doc.update(extra)
        path = out / f"{name}.json"
        path.write_text(json.dumps(narrow(doc, self.spec.double_keys), indent=1) + "\n")
        self.written.append(str(path))
        if self.verbose:
            print("wrote", os.path.relpath(path, self.spec.root), len(ops), "ops")
        return path

    def hidden_generate(self, module_file: str, split: str = "hidden"):
        """Run the private hidden generator matching a public family module.

        `module_file` is the public module's `__file__`.
        """
        name = os.path.splitext(os.path.basename(str(module_file)))[0]
        return hidden_module(self.spec, name).generate(split, self)


SKIP_MODULES = {"__init__", "all", "common"}


def family_modules(spec: CorpusSpec) -> list[str]:
    return sorted(p.stem for p in Path(spec.gen_dir).glob("*.py") if p.stem not in SKIP_MODULES)


def generate_all(spec: CorpusSpec, splits=("public", "hidden"), verbose: bool = True) -> CorpusWriter:
    """Run every family module of the instance for the given splits.

    The public split is generated from the public tree alone. The hidden split
    needs the private tree; without it, generation stops here with one message
    naming the environment variable, before anything is written.
    """
    splits = list(splits)
    if "both" in splits:
        splits = ["public", "hidden"]
    if "hidden" in splits:
        require_hidden_gen(spec)
    gen = str(spec.gen_dir)
    if gen not in sys.path:
        sys.path.insert(0, gen)
    writer = CorpusWriter(spec, verbose=verbose)
    for name in family_modules(spec):
        mod = importlib.import_module(name)
        if not hasattr(mod, "generate"):
            if verbose:
                print(f"[skip] {name}: no generate(split, corpus)")
            continue
        for split in splits:
            if verbose:
                print(f"[gen] {name} {split}")
            mod.generate(split, writer)
    return writer


def main(argv=None):
    """python3 -m evalbase.corpus.common --instance X [public|hidden|both]"""
    import argparse
    from ..interfaces import load_instance
    p = argparse.ArgumentParser(description="generate an instance's corpus")
    p.add_argument("--instance", default=None)
    p.add_argument("splits", nargs="*", default=["both"])
    a = p.parse_args(argv)
    instance = load_instance(a.instance)
    generate_all(instance.corpus, a.splits)


if __name__ == "__main__":
    main()
