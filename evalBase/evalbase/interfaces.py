"""The extension points an evaluation instance fills in.

evalBase is the machinery of an oracle-graded evaluation: a fixed driver
replays cases against a candidate inside a sandbox, the outputs are scored
against a real oracle, a reference cache holds the oracle's own outputs and the
perturbation runs that define tolerance, and a harness hands a coding agent
five tools. Everything the machinery needs to know about one particular
evaluation -- how the driver is run, how two outputs are compared, how cases are
generated, how a control candidate is built, what the model is told -- comes
through the objects in this module. An instance is one `Instance` value, found
through `load_instance` (the `EVALBASE_INSTANCE` environment variable or
`--instance` on every command line).

The ledger contract (the one thing every instance's driver must honour):

    <outdir>/ledger.json  {"exit": "ok" | anything else,
                           "events": [{"op": "run", "wall_seconds": 0.5, ...},
                                      {"op": "snapshot", "name": "snap_000",
                                       "files": {"<channel>": "snap_000.<ext>"}},
                                      ...],
                           ...anything else the instance's checks read...}

`snapshot` events, in order, are the snapshots the fidelity category scores;
`run` events carry the wall time the performance category measures. A
candidate that crashed leaves `exit` at something other than "ok" (or no ledger
at all). Everything else in the ledger is the instance's own.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ------------------------------------------------------------------- driver


@dataclass
class DriveResult:
    """What one driver run produced. `ledger` is `{}` when none was written."""

    outdir: str
    ledger: dict
    returncode: int
    wall_seconds: float
    timed_out: bool
    stderr_tail: str

    @property
    def exit(self) -> str:
        if self.timed_out:
            return "timeout"
        if self.ledger.get("exit") == "ok":
            return "ok"
        if self.ledger.get("exit") == "driver_error":
            return "driver_error"
        return "crash"


class Driver(ABC):
    """Runs one case against the oracle or a candidate and writes an output directory.

    The instance decides how: a Docker container holding the pinned oracle, a
    local subprocess (test-only), anything that ends with a ledger and output
    files in `outdir`. The generic grader only ever calls `run`, and it passes
    `candidate=None` for the oracle.
    """

    @abstractmethod
    def run(self, case_path: str, outdir: str, assets_dir: str, *,
            candidate: str | None = None, sample_mult: int = 1, perturbation: str = "",
            timeout_s: float = 600.0, cpus: str | None = None, memory: str = "8g",
            extra_mounts=(), extra_env: dict | None = None,
            owner: str | None = None) -> DriveResult:
        """Run `case_path`.

        `candidate` is a directory holding the candidate artifact (None: the
        oracle). `sample_mult` multiplies the oracle's sample count (the
        reference cache runs the oracle at 1x and 2x). `perturbation` names one of the
        instance's `MetricSpec.perturbations` and is only ever set for the
        oracle. `extra_mounts` / `extra_env` come from a control's
        `control.json` and are otherwise empty. `owner` labels any container
        this run starts; see `evalbase.grader.containers`.
        """

    def inspect_case(self, path: Path) -> dict:
        """Parse and bound a case a *model* wrote, for the `oracle` tool.

        Returns `{"name", "snapshots", "assets", "bytes"}`. The default reads the
        generic shape -- a JSON object with an `ops` list, one snapshot per
        `snapshot` op, assets named by `op["asset"]["file"]` -- and raises
        `ValueError` on anything else. Override to enforce instance limits
        (resolution, snapshot counts) on what the oracle will run.
        """
        size = path.stat().st_size
        try:
            data = json.loads(path.read_text())
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"case is not valid JSON: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
            raise ValueError("a case must be an object with an 'ops' array")
        snapshots = 0
        assets: set[str] = set()
        for op in data["ops"]:
            if not isinstance(op, dict):
                raise ValueError("every op must be an object")
            if op.get("op") == "snapshot":
                snapshots += 1
            asset = op.get("asset")
            if isinstance(asset, dict):
                name = asset.get("file")
                if not isinstance(name, str) or not name:
                    raise ValueError("asset.file must be a relative file name")
                if name.startswith("/") or ".." in Path(name).parts:
                    raise ValueError(f"asset paths must be relative and inside the workspace: {name}")
                assets.add(name)
        return {"name": data.get("name"), "snapshots": snapshots, "assets": sorted(assets), "bytes": size}


# ------------------------------------------------------------------- scoring


class CaseScorer(ABC):
    """How two outputs of the same case are compared, per category.

    Fidelity: `load_output` reads one snapshot's primary channel and `distance`
    turns a reference/candidate pair into a defect D in [0, 1] (1 for a missing
    or unusable candidate snapshot). The generic metric derives the per-case
    tolerance from `distance` applied to the oracle's own perturbation runs,
    and scores snapshots with a Hill function; see `evalbase.grader.metrics`.

    Procedural: `procedural_checks` reads both ledgers and both output
    directories and returns `[{"check", "ok", "detail"}, ...]`; the case scores
    the fraction that pass. Every check must be derived from the reference's
    own ledger, never from an expectation written into a generator.

    Performance: `timings` reads the run wall times the ratio is built from.
    """

    #: the channel `load_output` reads by default and the reference cache
    #: copies into the workspace as the public reference output
    primary_channel: str = "color"

    @abstractmethod
    def load_output(self, outdir: str, event: dict, channel: str | None = None) -> Any | None:
        """One snapshot's file as an array (or None when absent or unreadable)."""

    @abstractmethod
    def distance(self, ref: Any, cand: Any | None) -> float:
        """Snapshot defect D in [0, 1]; `cand is None` or a shape mismatch -> 1.0."""

    def preview_rgb8(self, output: Any) -> np.ndarray | None:
        """An HxWx3 uint8 picture of one output for previews and the site; None: no pictures."""
        return None

    def block_defects(self, ref: Any, cand: Any | None) -> np.ndarray | None:
        """A 2-D grid of per-block defects for a heatmap; None: no heatmap."""
        return None

    def block_pixels(self) -> int:
        """Source pixels per heatmap cell along each axis (used with `block_defects`)."""
        return 16

    def floor(self) -> float:
        """The per-block perceptual floor the heatmap greys out (0: none)."""
        return 0.0

    def timings(self, ledger: dict) -> list[float]:
        """Run wall times in ledger order: the performance category's input."""
        return [float(e.get("wall_seconds", 0.0)) for e in ledger.get("events", [])
                if e.get("op") == "run"]

    def procedural_checks(self, case: "CaseMeta", ref_ledger: dict, cand_ledger: dict,
                          ref_dir: str, cand_dir: str, threshold: float | None) -> list[dict]:
        """Ledger checks for a procedural case. The default is the two generic ones.

        `exit_ok` is a gate (a crash fails the case; not crashing earns nothing)
        and `output_matches_reference` scores every snapshot of the case within
        the case's own tolerance, so a candidate cannot pass every ledger check
        while producing nothing. Instances extend this list.
        """
        return generic_checks(self, ref_ledger, cand_ledger, ref_dir, cand_dir, threshold)


def generic_checks(scorer: CaseScorer, ref: dict, cand: dict, ref_dir: str, cand_dir: str,
                   threshold: float | None) -> list[dict]:
    """The checks every procedural case gets, whatever the instance."""
    checks: list[dict] = []

    def add(check, ok, detail=""):
        checks.append({"check": check, "ok": bool(ok), "detail": detail})

    if cand.get("exit") != "ok":
        add("exit_ok", False, cand.get("exit"))
        return checks
    if threshold is not None:
        rs = {e["name"]: e for e in ref.get("events", []) if e.get("op") == "snapshot"}
        cs = {e["name"]: e for e in cand.get("events", []) if e.get("op") == "snapshot"}
        defects = []
        for name, event in rs.items():
            a = scorer.load_output(ref_dir, event)
            if a is None:
                continue
            b = scorer.load_output(cand_dir, cs[name]) if name in cs else None
            defects.append((name, scorer.distance(a, b)))
        if defects:
            worst = max(defects, key=lambda t: t[1])
            add("output_matches_reference", worst[1] <= threshold,
                {"worst_snapshot": worst[0], "defect": round(worst[1], 4),
                 "threshold": round(threshold, 4)})
    return checks


# ------------------------------------------------------------------- metric


@dataclass
class MetricSpec:
    """The constants of the metric, and its version.

    The tolerance of a case is
    `T = clamp(max(k_noise * noise, k_sens * sensitivity), t_lo, t_hi)` where
    `noise` is the mean snapshot distance between the oracle at N and at 2N
    samples and `sensitivity` the max over `tolerance_perturbations` of the
    mean snapshot distance between the oracle and its perturbed run. Every
    perturbation in `perturbations` is stored in the reference cache and
    its defect recorded; only the ones in `tolerance_perturbations` raise T.
    The snapshot score is `1 / (1 + (D / T) ** hill_n)`.

    The keys of `weights` are the categories; `replay`, `procedural` and
    `performance` are the default set, and `fidelity_category`,
    `procedural_category` and `performance_category` say which key plays which
    role. The fidelity category is averaged per family and gates full success
    per case, the procedural and performance categories have their own bars,
    and any further category is a mean over its cases gated by `case_bar`.
    """

    version: str = "0.1"
    perturbations: tuple[str, ...] = ()
    tolerance_perturbations: tuple[str, ...] = ()
    k_noise: float = 2.0
    k_sens: float = 2.0
    t_lo: float = 0.03
    t_hi: float = 0.30
    hill_n: int = 4
    weights: dict[str, float] = field(default_factory=lambda: {
        "replay": 0.60, "procedural": 0.30, "performance": 0.10})
    fidelity_category: str = "replay"
    procedural_category: str = "procedural"
    performance_category: str = "performance"
    perf_half: float = 16.0
    perf_gate: float = 8.0
    case_bar: float = 0.9
    procedural_bar: float = 0.95

    def validate(self) -> "MetricSpec":
        if not set(self.tolerance_perturbations) <= set(self.perturbations):
            raise ValueError("tolerance_perturbations must be a subset of perturbations")
        if not (0 < self.t_lo <= self.t_hi <= 1):
            raise ValueError("need 0 < t_lo <= t_hi <= 1")
        if abs(sum(self.weights.values()) - 1.0) > 1e-9:
            raise ValueError("category weights must sum to 1")
        for cat in (self.fidelity_category, self.procedural_category, self.performance_category):
            if cat not in self.weights:
                raise ValueError(f"category {cat!r} has no weight")
        if self.hill_n < 1 or not math.isfinite(self.perf_half) or self.perf_half <= 0:
            raise ValueError("hill_n and perf_half must be positive")
        return self


# ------------------------------------------------------------------- corpus


@dataclass
class CorpusSpec:
    """Where the corpus lives and how the hidden split is found.

    `gen_dir` holds one module per feature family, each exposing
    `generate(split, corpus)` (see `evalbase.corpus.common`). The public split
    is generated from `gen_dir` alone; the hidden split's functions live in a
    private tree found through `hidden_env` (default `hidden_default`), one
    `gen/<family>.py` per public module. The loader refuses a private tree that
    lies inside any of `forbidden_hidden_roots` (default: the instance root),
    which is what keeps it out of a public repository.
    """

    root: Path
    gen_dir: Path
    public_dir: Path
    hidden_dir: Path
    assets_dir: Path
    hidden_env: str = "EVALBASE_HIDDEN_GEN"
    hidden_default: Path | None = None
    hidden_package: str = "evalbase_hidden_gen"
    forbidden_hidden_roots: tuple[Path, ...] | None = None
    #: JSON keys whose subtree holds genuinely double-precision data (written at
    #: 15 significant digits instead of being narrowed to float32)
    double_keys: tuple[str, ...] = ()
    #: set by generators through CorpusWriter.write; a format version stamp
    format_version: int = 1

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        for name in ("gen_dir", "public_dir", "hidden_dir", "assets_dir"):
            setattr(self, name, Path(getattr(self, name)))
        if self.hidden_default is None:
            self.hidden_default = self.root.parent / (self.root.name + "-hidden")
        if self.forbidden_hidden_roots is None:
            self.forbidden_hidden_roots = (self.root,)


# ------------------------------------------------------------------- controls


class ControlSpec(ABC):
    """How a control candidate is built from the reference.

    A control is a candidate whose score can be predicted before it runs
    (the reference itself, a stub that does nothing, the reference with every
    light doubled, ...). `build` produces the candidate directory the driver
    takes; `control.json` beside a control's sources may add read-only mounts,
    environment and variants to the *driver* runs (`evalbase.grader.cli`).
    """

    #: directory holding one subdirectory per control
    root: Path

    def names(self) -> list[str]:
        return sorted(d.name for d in Path(self.root).iterdir()
                      if d.is_dir() and d.name not in ("common", "__pycache__"))

    @abstractmethod
    def build(self, name: str, out_dir: Path, *, owner: str | None = None) -> Path:
        """Build control `name` into `out_dir`; return the candidate directory."""


# ------------------------------------------------------------------- task


@dataclass
class SmokeSpec:
    """What the no-key smoke's fake model does: enough to prove the tool path.

    `sources` are written into the workspace (relative path -> text) before
    `build_command` runs; `case` is a case document the fake model writes and
    runs through the oracle; `public_case` names the public case the fake model
    runs the driver and `grade_dev` on; `max_overall` is the score the
    deliberately incomplete `sources` must stay under.
    """

    sources: dict[str, str]
    case: dict
    public_case: str
    max_overall: float = 0.05
    #: whether the driver tool is expected to fail on `sources` (a stub that
    #: cannot load) -- the smoke asserts the outcome either way
    driver_fails: bool = True


@dataclass
class TaskSpec:
    """What the model sees and how its artifact is built and run."""

    #: holds TASK.md, SPEC.md and starter/ (the initial workspace)
    task_dir: Path
    #: the text the harness hands the model as its system prompt
    instructions: str
    #: the tool descriptions the model sees, keyed shell/oracle/driver/grade_dev/checkpoint
    tool_descriptions: dict[str, str]
    #: workspace-relative path of the candidate artifact the driver loads
    artifact: str = "build/candidate"
    #: what the evaluator runs in a clean sandbox to produce `artifact`
    build_command: str = "make -C /task"
    #: the driver command inside the sandbox; {case} {outdir} {assets} are filled in
    driver_command: str = "driver {case} {outdir} {assets}"
    #: a frozen tree copied read-only into the workspace as spec/ (None: none)
    spec_dir: Path | None = None
    #: the case-format document copied into dev/ (None: none)
    format_doc: Path | None = None
    #: extra files copied read-only into the workspace's dev/ tree: relpath -> source
    dev_extra: dict = field(default_factory=dict)
    #: the workspace image the model's shell runs in, and the trusted oracle image
    solver_image: str = "evalbase-solver:1"
    reference_image: str = "evalbase-ref:1"
    #: the MCP server name the CLI harnesses see (tools are mcp__<name>__<tool>)
    mcp_server: str = "evalbase"
    #: file names / suffixes that make up a submission (the source export)
    source_suffixes: frozenset = frozenset({
        ".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".inc", ".inl",
        ".ipp", ".s", ".asm", ".md", ".txt", ".mk", ".cmake", ".json", ".py", ".sh", ".def",
        ".ld", ".map", ".toml", ".yaml", ".yml", ".rs", ".go", ".java", ".kt", ".ts", ".js"})
    source_names: frozenset = frozenset({"Makefile", "makefile", "GNUmakefile", "CMakeLists.txt",
                                         ".clang-format", ".clang-tidy"})
    #: at least one of these must be in an export, or the export is refused
    required_names: frozenset = frozenset({"Makefile", "makefile", "GNUmakefile"})
    #: suffixes counted as compilation units in the export manifest
    unit_suffixes: frozenset = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".s", ".asm"})
    #: the no-key smoke script (None: the smoke cannot run for this instance)
    smoke: SmokeSpec | None = None
    #: extra sanity for the solver image: shell fragment run by the smoke's
    #: isolation check (must exit 0 inside the sandbox)
    isolation_check: str = ""

    def __post_init__(self):
        self.task_dir = Path(self.task_dir)
        if self.spec_dir is not None:
            self.spec_dir = Path(self.spec_dir)
        if self.format_doc is not None:
            self.format_doc = Path(self.format_doc)
        missing = [k for k in ("shell", "oracle", "driver", "grade_dev", "checkpoint")
                   if k not in self.tool_descriptions]
        if missing:
            raise ValueError(f"tool_descriptions is missing {missing}")


# ------------------------------------------------------------------- instance


@dataclass
class CaseMeta:
    """What the grader knows about one case from its file."""

    name: str
    category: str
    family: str
    meta: dict
    path: str
    doc: dict


def read_case_meta(path: str) -> CaseMeta:
    with open(path) as f:
        d = json.load(f)
    name = d.get("name") or Path(path).stem
    return CaseMeta(name, d.get("category", "replay"), d.get("family", "unknown"),
                    d.get("meta", {}) or {}, str(path), d)


@dataclass
class Instance:
    """One evaluation: the five extension points, plus where its files live."""

    name: str
    root: Path
    driver: Driver
    scorer: CaseScorer
    metric: MetricSpec
    corpus: CorpusSpec
    task: TaskSpec
    controls: ControlSpec | None = None
    runs_dir: Path | None = None
    #: trees hashed into an attempt's version record: label -> (paths, patterns)
    hashed_trees: dict[str, tuple[list[Path], tuple[str, ...]]] = field(default_factory=dict)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.runs_dir = Path(self.runs_dir) if self.runs_dir else self.root / "runs"
        self.metric.validate()

    # Paths every command needs; each can be overridden from the environment so
    # a host that keeps its caches elsewhere does not have to edit the instance.
    @property
    def refcache(self) -> Path:
        return Path(os.environ.get("EVALBASE_REFCACHE") or self.runs_dir / "refcache")

    @property
    def refcache_hidden(self) -> Path:
        return Path(os.environ.get("EVALBASE_REFCACHE_HIDDEN") or self.refcache.parent / "refcache-hidden")

    @property
    def corpus_public(self) -> Path:
        return Path(os.environ.get("EVALBASE_CORPUS_PUBLIC") or self.corpus.public_dir)

    @property
    def corpus_hidden(self) -> Path:
        return Path(os.environ.get("EVALBASE_CORPUS_HIDDEN") or self.corpus.hidden_dir)

    @property
    def assets(self) -> Path:
        return Path(os.environ.get("EVALBASE_ASSETS") or self.corpus.assets_dir)

    @property
    def solver_image(self) -> str:
        return os.environ.get("EVALBASE_SOLVER_IMAGE") or self.task.solver_image

    @property
    def reference_image(self) -> str:
        return os.environ.get("EVALBASE_IMAGE") or self.task.reference_image


INSTANCE_ENV = "EVALBASE_INSTANCE"
INSTANCE_ATTR = "INSTANCE"


def load_instance(spec: str | None = None) -> Instance:
    """Load an instance from `path/to/instance.py[:ATTR]` or `EVALBASE_INSTANCE`.

    The module is loaded by file path under a synthetic name, so an instance
    file needs no package and cannot shadow anything on sys.path. It must
    define `INSTANCE` (or the named attribute) as an `Instance`.
    """
    spec = spec or os.environ.get(INSTANCE_ENV)
    if not spec:
        raise ValueError(f"no evaluation instance: pass --instance <path/to/instance.py> "
                         f"or set {INSTANCE_ENV}")
    path, _, attr = str(spec).partition(":")
    attr = attr or INSTANCE_ATTR
    file = Path(path).expanduser()
    if file.is_dir():
        file = file / "instance.py"
    file = file.resolve()
    if not file.is_file():
        raise ValueError(f"instance file not found: {file}")
    key = "evalbase_instance_" + str(file).replace("/", "_").replace(".", "_")
    module = sys.modules.get(key)
    if module is None:
        module_spec = importlib.util.spec_from_file_location(key, file)
        module = importlib.util.module_from_spec(module_spec)
        # the instance file may import its neighbours (driver.py, scorer.py)
        parent = str(file.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        sys.modules[key] = module
        try:
            module_spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[key]
            raise
    instance = getattr(module, attr, None)
    if not isinstance(instance, Instance):
        raise ValueError(f"{file}:{attr} is not an evalbase.interfaces.Instance")
    return instance
