"""Static site data bundle for a published results site.

Pictures and heatmaps go
through the instance's `CaseScorer` (`preview_rgb8`, `block_defects`), so an
instance without pictures still gets every JSON file and page.

Reads what the grader already wrote -- `report.json`, the candidate case
directories, the refcache and (for harness attempts) `attempt.json` plus
`checkpoint-curve.json` -- and turns it into the JSON + PNG bundle the blog is
built from. Nothing is recomputed that the grader already decided: scores,
thresholds and per-snapshot defects are copied from the report; only the pictures
(reference, candidate, block-defect heatmap, animated strip) are produced here.

  python3 -m evalbase.grader.export --instance X --run runs/attempts/<name> --site site
  python3 -m evalbase.grader.export --instance X --runs-root runs/attempts --site site --pictures key
  python3 -m evalbase.grader.export --instance X --compare runs/control-stub runs/control-reference \
      --refcache runs/refcache --site site

`--refcache` defaults to the cache each report records, so a hidden-corpus
grade is exported against `runs/refcache-hidden` without being told; a refcache
that holds none of the graded cases is an error, not an empty export.

Layout:

  site/index.html                        leaderboard, static HTML, no script
  site/labels.json                       the writer's interim/record labels
  site/viewer.html                       the JSON explorer (wants a server)
  site/leaderboard.json                  one entry per exported run
  site/results/<candidate>/index.html    one model: scores, validity, curve
  site/results/<candidate>/summary.json  overall, sections, per_testcase
  site/results/<candidate>/metadata.json task version, budget, usage, stop reason
  site/results/<candidate>/bands.json    score-band examples
  site/results/<candidate>/checkpoints/index.json, curve.json
  site/results/<candidate>/cases/<name>/index.html   one case
  site/results/<candidate>/cases/<name>/{ref,cand,heat}_NNN.png, anim.png
  site/compare/<a>__vs__<b>/index.json   baseline-vs-model figure

Only numpy and the standard library are used: the animated strip is an APNG written here, not a call out to ffmpeg, and
the pages are plain HTML with inline SVG charts and no script tag at all.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np

from ..harness.attempt import AUTO_NOTES, FINAL_NOTES
from ..interfaces import Instance, load_instance
from . import jsonio
from .png import write_png
from .sitehtml import write_html_site

# Instances without a heatmap (no `block_defects`) still get a cell size for
# the "missing snapshot" red grid.
DEFAULT_CELL = 16

ANIM_DELAY_MS = 400
GAP_PX = 6
GAP_RGB = (18, 18, 20)

BANDS = (("0.9-1.0", 0.9, 1.01), ("0.7-0.9", 0.7, 0.9),
         ("0.4-0.7", 0.4, 0.7), ("0.0-0.4", -0.01, 0.4))

BAND_NOTES = {
    "0.9-1.0": "indistinguishable from the reference at this replay's threshold",
    "0.7-0.9": "right structure, wrong detail: a systematic bias, a missing term, "
               "or a difference over part of the snapshot",
    "0.4-0.7": "the output is recognisable but a whole feature is absent",
    "0.0-0.4": "structurally different or absent output: empty snapshots, "
               "crash or timeout",
}

PROVIDERS = (("claude", "Anthropic"), ("anthropic", "Anthropic"), ("gpt", "OpenAI"),
             ("o3", "OpenAI"), ("o4", "OpenAI"), ("openai", "OpenAI"),
             ("gemini", "Google"), ("grok", "xAI"), ("llama", "Meta"),
             ("deepseek", "DeepSeek"), ("qwen", "Alibaba"), ("mistral", "Mistral"))

# Which snapshots of a replay get pictures. The JSON timelines always carry every
# snapshot the grader scored; this only bounds how many PNG triples are written,
# because a large hidden corpus is thousands of pictures per model.
PICTURE_MODES = ("all", "key", "first")


# The name of the file the writer edits by hand to say whether a row is a
# number of record. The exporter creates it with every run at "interim" and
# never rewrites a status that is already there: the site must not be able to
# promote its own numbers.
LABELS_FILE = "labels.json"
DEFAULT_STATUS = "interim"
STATUS_NOTES = {
    "interim": "interim grade, not a number of record",
    "record": "number of record",
    "compromised": "compromised measurement, not a number of record",
    "superseded": "superseded by a later attempt",
    "control": "control, not a model attempt",
}


# The per-family means the site shows beside a category score are a different
# rollup from the grader's own category score (unweighted over families vs
# weighted over cases), so they are never printed unlabelled.
SUBSYSTEM_NOTE = ("unweighted mean of the case scores in each family; these do not "
                  "average to the category score, which is the grader's own "
                  "aggregate.categories[*].score")

def checkpoint_trigger(name: str | None, note: str | None) -> str:
    """auto (harness timer), final (the graded source) or tool (the model asked)."""
    note = (note or "").strip()
    if name == "final" or note in FINAL_NOTES:
        return "final"
    if note in AUTO_NOTES:
        return "auto"
    return "tool"


# ------------------------------------------------------------------ small utils

def final_curve_row(curve) -> dict | None:
    """The graded `trigger == "final"` point of a `checkpoint-curve.json`, if any.

    `grade-checkpoints` regrades the attempt's final source on an idle host; the
    row it writes carries the path of the report that grade produced. That report
    -- not the grade taken while the model was still running and competing for
    the machine -- is the number of record. A row that
    was skipped, errored or never graded is not one.
    """
    best = None
    for row in (curve or []):
        if not isinstance(row, dict) or row.get("trigger") != "final":
            continue
        if row.get("skipped") or row.get("error"):
            continue
        if row.get("overall") is None or not row.get("report"):
            continue
        best = row
    return best


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "candidate"


def provider_of(model: str | None) -> str | None:
    m = (model or "").lower()
    for key, name in PROVIDERS:
        if key in m:
            return name
    return None


def _read_json(path) -> dict | list | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def jsonable(obj):
    """Strip what `JSON.parse` cannot read: the shared sanitizer, without notes.

    The site's own JSON is data for charts, so a non-finite number becomes null
    and nothing else is added; the grader's reports get the `ratio_note` that
    grader/jsonio.py writes for a reader who needs to know why it is null.
    """
    return jsonio.jsonable(obj, annotate=False)


def _write_json(path, obj) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = jsonio.dumps(obj, indent=1, annotate=False) + "\n"
    path.write_text(text)
    return len(text)


def snapshots(ledger: dict | None) -> list[dict]:
    if not isinstance(ledger, dict):
        return []
    return [e for e in ledger.get("events", []) if e.get("op") == "snapshot"]


def display(img: np.ndarray) -> np.ndarray:
    """The picture as a human reads it; the scorer's preview already decides orientation."""
    return img


# ------------------------------------------------------------------ colormap

# Neutral grey for blocks the scorer's perceptual floor zeroed out, so "below
# the floor" is visibly different from "small but counted" rather than just a
# dark shade of the ramp.
NEUTRAL = (58, 60, 64)

# Cool -> hot ramp ending saturated red: luminance climbs over most of the
# range so ordering is readable in greyscale, and "totally wrong" is red.
_RAMP = (
    (0.00, (12, 30, 86)),
    (0.20, (28, 100, 168)),
    (0.40, (44, 168, 152)),
    (0.60, (206, 202, 78)),
    (0.80, (240, 138, 46)),
    (1.00, (222, 32, 32)),
)


def defect_colormap(d: np.ndarray | float, floor: float = 0.0) -> np.ndarray:
    """Map block defects in [0,1] to RGB8. Below `floor` -> NEUTRAL grey.

    Above the floor the value is renormalised onto [0,1] before the ramp, so a
    block that only just cleared the floor is the cool end and a block the
    metric calls totally wrong is red.
    """
    d = np.clip(np.asarray(d, dtype=np.float64), 0.0, 1.0)
    span = max(1.0 - floor, 1e-9)
    t = np.clip((d - floor) / span, 0.0, 1.0)
    stops = np.array([s[0] for s in _RAMP])
    cols = np.array([s[1] for s in _RAMP], dtype=np.float64)
    out = np.empty(d.shape + (3,), dtype=np.float64)
    for c in range(3):
        out[..., c] = np.interp(t, stops, cols[:, c])
    rgb = np.round(out).astype(np.uint8)
    rgb[d < floor] = NEUTRAL
    return rgb


def defect_grid(scorer, ref, cand, shape: tuple[int, int], cell: int) -> np.ndarray:
    """Per-block defect laid out as a 2-D grid (one cell per `cell` x `cell` pixels).

    A missing or unusable candidate is a grid of ones (every cell fully wrong);
    a scorer without `block_defects` gets the same for any candidate whose
    snapshot defect is 1 and a grid of zeros otherwise.
    """
    hb, wb = shape[0] // cell, shape[1] // cell
    if hb < 1 or wb < 1:
        return np.zeros((1, 1))
    if cand is None:
        return np.ones((hb, wb))
    grid = scorer.block_defects(ref, cand)
    if grid is None:
        return np.ones((hb, wb)) if scorer.distance(ref, cand) >= 1.0 else np.zeros((hb, wb))
    return np.asarray(grid, dtype=np.float64)


def heatmap_rgb(grid: np.ndarray, shape: tuple[int, int], cell: int = DEFAULT_CELL,
                floor: float = 0.0) -> np.ndarray:
    """Upsample a block-defect grid to a full-resolution RGB8 image."""
    cells = defect_colormap(grid, floor)
    big = np.repeat(np.repeat(cells, cell, axis=0), cell, axis=1)
    h, w = shape
    if big.shape[0] < h or big.shape[1] < w:   # ragged edge the metric cropped off
        pad = ((0, max(0, h - big.shape[0])), (0, max(0, w - big.shape[1])), (0, 0))
        big = np.pad(big, pad, mode="edge")
    return big[:h, :w]


# ------------------------------------------------------------------ APNG

def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _scanlines(rgb8: np.ndarray) -> bytes:
    rgb8 = np.ascontiguousarray(rgb8, dtype=np.uint8)
    return b"".join(b"\x00" + rgb8[y].tobytes() for y in range(rgb8.shape[0]))


def write_apng(path, panels: list[np.ndarray], delay_ms: int = ANIM_DELAY_MS,
               loops: int = 0, level: int = 6) -> int:
    """Animated PNG (RGB8, no delta coding). Returns the byte size written.

    A single panel is written as a plain PNG: no acTL, so nothing has to
    understand APNG to look at it.
    """
    if not panels:
        raise ValueError("write_apng needs at least one panel")
    fs = [np.ascontiguousarray(f, dtype=np.uint8) for f in panels]
    h, w = fs[0].shape[:2]
    for f in fs:
        if f.shape != (h, w, 3):
            raise ValueError("every panel must be the same HxWx3 uint8 array")
    parts = [b"\x89PNG\r\n\x1a\n",
             _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))]
    if len(fs) > 1:
        parts.append(_chunk(b"acTL", struct.pack(">II", len(fs), loops)))
    seq = 0
    num, den = int(max(1, round(delay_ms))), 1000
    for i, f in enumerate(fs):
        data = zlib.compress(_scanlines(f), level)
        if len(fs) > 1:
            # fcTL: sequence, w, h, x, y, delay_num, delay_den, dispose, blend
            parts.append(_chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, 0, 0,
                                                     num, den, 0, 0)))
            seq += 1
        if i == 0:
            parts.append(_chunk(b"IDAT", data))
        else:
            parts.append(_chunk(b"fdAT", struct.pack(">I", seq) + data))
            seq += 1
    parts.append(_chunk(b"IEND", b""))
    blob = b"".join(parts)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(blob)
    return len(blob)


def hstrip(panels: list[np.ndarray], gap: int = GAP_PX) -> np.ndarray:
    """Concatenate equal-height RGB8 panels with a gap column between them."""
    h = panels[0].shape[0]
    sep = np.empty((h, gap, 3), np.uint8)
    sep[:] = GAP_RGB
    out = []
    for i, p in enumerate(panels):
        if i:
            out.append(sep)
        out.append(np.ascontiguousarray(p, dtype=np.uint8))
    return np.concatenate(out, axis=1)


# ------------------------------------------------------------------ run inputs

class Run:
    """A graded run directory: a control (`report.json`) or an attempt (`grade/report.json`)."""

    def __init__(self, instance: Instance, run_dir, refcache=None, label: str | None = None,
                 model: str | None = None, harness: str | None = None,
                 provider: str | None = None):
        self.instance = instance
        self.dir = Path(run_dir).resolve()
        report_path = None
        for rel in ("report.json", "grade/report.json"):
            if (self.dir / rel).is_file():
                report_path = self.dir / rel
                break
        if report_path is None:
            raise FileNotFoundError(f"no report.json under {self.dir}")
        self.report_path = report_path
        self.report = _read_json(report_path) or {}
        self.cases_dir = report_path.parent / "cases"
        self.attempt = _read_json(self.dir / "attempt.json") or {}
        self.curve = _read_json(self.dir / "checkpoint-curve.json")
        # The grade of record. `<run>/grade/report.json` (and the copy of its
        # aggregate in `attempt.json["grade"]`) was produced while the attempt
        # was still running, on a host that was also running the solver -- so
        # its performance category is timing-contaminated. `grade-checkpoints`
        # regrades the same final source on an idle host and writes the result
        # as the `trigger == "final"` row of `checkpoint-curve.json`. When that
        # row exists, it is what the leaderboard, the run page and every case
        # page show; the during-run number stays visible beside it, labelled,
        # with the delta. With no curve there is nothing else to show and the
        # manifest grade is used, and said to be the manifest grade.
        self.during_run_report_path = report_path
        self.during_run_report = self.report
        self.final_row = final_curve_row(self.curve)
        self.grade_source = "during_run"
        if self.final_row:
            path = Path(self.final_row["report"])
            if not path.is_absolute():
                path = self.dir / path
            final = _read_json(path)
            if isinstance(final, dict) and final.get("aggregate"):
                self.report, self.report_path = final, path
                self.cases_dir = path.parent / "cases"
                self.grade_source = "final_checkpoint"
        # A hidden-corpus grade is scored against runs/refcache-hidden, not the
        # dev refcache the CLI defaults to. The report records which cache it
        # used, so use that unless the operator names one; an unrelated cache
        # used to export silently -- every ref snapshot missing, every picture a
        # black candidate next to nothing -- so it is now an error.
        self.refcache_source = "explicit" if refcache else "report"
        self.refcache = Path(refcache).resolve() if refcache else self.default_refcache()
        self.check_refcache()
        # The final checkpoint's report calls itself `checkpoint-000081`; the
        # run is still named by the during-run report, so `results/<candidate>`
        # does not move when a curve appears.
        self.label = label or self.during_run_report.get("label") or self.dir.name
        self.candidate = slug(self.label)
        self.model = model or self.attempt.get("model_requested")
        self.harness = harness or self.attempt.get("harness")
        self.provider = provider or provider_of(self.model)
        self.baseline = self.label.startswith("control-")

    def default_refcache(self) -> Path:
        """The refcache the report says it was graded against.

        `cache` is usually relative (`runs/refcache-hidden`), and relative to
        the repository the grader ran in -- which is not necessarily the
        checkout the exporter is running from. Try the working directory, this
        checkout and the run directory's own repository, in that order, and
        fall back to the working directory so the error message names a path.
        """
        cache = self.report.get("cache")
        if not cache:
            raise FileNotFoundError(
                f"{self.report_path} records no `cache`: pass --refcache explicitly")
        path = Path(cache)
        if path.is_absolute():
            return path.resolve()
        bases = [Path.cwd(), self.instance.root] + list(self.dir.parents)
        for base in bases:
            if (base / path).is_dir():
                return (base / path).resolve()
        return (Path.cwd() / path).resolve()

    def check_refcache(self) -> None:
        """Fail loudly when the refcache does not hold the cases the report graded."""
        self.missing_reference_cases: list[str] = []
        names = [c["name"] for c in (self.report.get("cases") or []) if c.get("name")]
        if not names:
            return
        missing = [n for n in names if not (self.refcache / n / "n1" / "ledger.json").is_file()]
        if len(missing) == len(names):
            raise FileNotFoundError(
                f"refcache {self.refcache} has no entry for any of the {len(names)} cases "
                f"in {self.report_path} (first: {names[0]}). The report was graded against "
                f"`{self.report.get('cache')}`; pass --refcache that directory.")
        self.missing_reference_cases = missing

    # -------------------------------------------------- per-case snapshot sources
    def ref_dir(self, name: str) -> Path:
        return self.refcache / name / "n1"

    def ref_snapshots(self, name: str) -> list[dict]:
        return snapshots(_read_json(self.ref_dir(name) / "ledger.json"))

    def cand_snapshots(self, name: str) -> list[dict]:
        return snapshots(_read_json(self.cases_dir / name / "ledger.json"))

    def refcache_info(self, name: str) -> dict:
        return _read_json(self.refcache / name / "refcache.json") or {}

    def during_run_overall(self):
        """The overall the attempt recorded for itself while it was running."""
        agg = (self.during_run_report.get("aggregate") or {})
        value = agg.get("overall")
        if value is None:
            value = (self.attempt.get("grade") or {}).get("overall")
        return value

    def grade_of_record(self) -> dict:
        """Which report the site's numbers come from, and what the other one said.

        `source` is `final_checkpoint` when the attempt has a graded final curve
        point (the idle-host regrade of the same source) and `during_run` when
        it does not. The during-run number and the delta are always carried, so
        a page can print the headline and, beside it, what the attempt thought
        of itself at the time.
        """
        during = self.during_run_overall()
        record = (self.report.get("aggregate") or {}).get("overall")
        final = self.final_row or {}
        try:
            delta = float(record) - float(during)
        except (TypeError, ValueError):
            delta = None
        return {
            "source": self.grade_source,
            "report": str(self.report_path),
            "overall": record,
            "checkpoint_id": final.get("index"),
            "checkpoint": final.get("id"),
            "trigger": final.get("trigger"),
            "elapsed_seconds": final.get("elapsed_seconds"),
            "source_sha256": final.get("source_sha256"),
            "during_run": {
                "overall": during,
                "report": str(self.during_run_report_path),
                "label": "during-run (may be timing-contaminated)",
                "note": "graded while the attempt was still running, on a host that "
                        "was also running the solver: the performance category is "
                        "timing-contaminated",
            },
            "delta_vs_during_run": delta,
            "note": ("idle-host regrade of the final source "
                     "(checkpoint-curve.json, trigger \"final\")"
                     if self.grade_source == "final_checkpoint" else
                     "no graded final curve point for this run: the number is the "
                     "attempt manifest's own during-run grade, which may be "
                     "timing-contaminated (python3 -m evalbase.harness.run grade-checkpoints "
                     "<attempt> --only final produces the idle-host final)"),
        }

    def graded_at(self) -> str:
        stamp = self.attempt.get("ended")
        if stamp:
            return stamp
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(self.report_path.stat().st_mtime))

    def wall_clock_hours(self):
        seconds = self.attempt.get("solver_seconds")
        return round(seconds / 3600.0, 6) if seconds else None

    def tokens_used(self):
        usage = self.attempt.get("usage") or {}
        tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        return tokens or None


def _load(run: "Run", directory: Path, event: dict | None):
    if not event:
        return None
    try:
        return run.instance.scorer.load_output(str(directory), event)
    except Exception:
        return None


def _preview(run: "Run", output):
    if output is None:
        return None
    try:
        return run.instance.scorer.preview_rgb8(output)
    except Exception:
        return None


# ------------------------------------------------------------------ case export

def picture_selection(n: int, defects: list, mode: str = "all") -> list[int]:
    """Which snapshot indices get pictures. `first` is snapshot 0; `key` adds the worst.

    The requirement the site has to meet is a reference-vs-candidate picture of
    the first snapshot; `key` also keeps the snapshot the metric liked least, which is
    the one worth looking at when a replay diverges partway through.
    """
    if n <= 0:
        return []
    if mode == "all":
        return list(range(n))
    picks = {0}
    if mode == "key" and defects:
        picks.add(min(int(np.argmax(defects)), n - 1))
    return sorted(picks)


def export_case(run: Run, case: dict, out_root: Path, animate: bool = True,
                pictures: str = "all") -> dict:
    """Export the selected snapshots of one case and return its per_testcase entry."""
    name = case["name"]
    detail = case.get("detail") or {}
    defects = list(detail.get("snapshot_defects") or [])
    scores = list(detail.get("snapshot_scores") or [])
    ref_snaps = run.ref_snapshots(name)
    cand_snaps = run.cand_snapshots(name)
    n = len(ref_snaps) or len(defects)
    out_dir = out_root / "cases" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    images, panels, missing = [], [], 0
    panel_sizes: list[tuple[int, int]] = []
    scorer = run.instance.scorer
    cell, floor = scorer.block_pixels(), scorer.floor()
    for i in picture_selection(n, defects, pictures):
        ref = _load(run, run.ref_dir(name), ref_snaps[i] if i < len(ref_snaps) else None)
        ref8 = _preview(run, ref)
        if ref is None or ref8 is None:
            continue
        cand = _load(run, run.cases_dir / name, cand_snaps[i] if i < len(cand_snaps) else None)
        cand8 = _preview(run, cand)
        if cand8 is not None and cand8.shape[:2] != ref8.shape[:2]:
            cand, cand8 = None, None
        if cand8 is None:
            missing += 1
            shown_cand = np.zeros_like(ref8)
        else:
            shown_cand = cand8
        # A candidate snapshot the metric scored D = 1 outright (a near-uniform
        # snapshot, say) is flagged, so a heatmap of neutral cells does not look
        # inconsistent with the snapshot defect printed next to it.
        d_i = defects[i] if i < len(defects) else None
        override = bool(cand8 is not None and d_i is not None and d_i >= 1.0)
        heat = heatmap_rgb(defect_grid(scorer, ref, cand, ref8.shape[:2], cell), ref8.shape[:2], cell, floor)
        ref_img, cand_img = display(ref8), display(shown_cand)
        heat_img = display(heat)
        stem = ref_snaps[i].get("name") if i < len(ref_snaps) else f"snap_{i:03d}"
        write_png(str(out_dir / f"ref_{i:03d}.png"), ref_img)
        write_png(str(out_dir / f"cand_{i:03d}.png"), cand_img)
        write_png(str(out_dir / f"heat_{i:03d}.png"), heat_img)
        images.append({
            "index": i, "snapshot": stem,
            "ref": f"cases/{name}/ref_{i:03d}.png",
            "cand": f"cases/{name}/cand_{i:03d}.png",
            "heat": f"cases/{name}/heat_{i:03d}.png",
            "defect": defects[i] if i < len(defects) else None,
            "score": scores[i] if i < len(scores) else None,
            "candidate_missing": cand8 is None,
            "monochrome_override": override,
        })
        panels.append(hstrip([ref_img, cand_img, heat_img]))
        panel_sizes.append(tuple(ref_img.shape[:2]))

    # An APNG has one IHDR for the whole file, so every snapshot has to be the same
    # size. A replay that changes its output size mid-sequence produces panels
    # that are not, and write_apng rejects them -- which used to abort the whole
    # export, so `--pictures key` only worked with `--no-anim`. Such a replay now
    # loses its animation and keeps its still, with a note saying why, and every
    # other case still exports.
    anim, anim_note = None, None
    if animate and len(panels) > 1:
        sizes = sorted(set(panel_sizes))
        if len(sizes) > 1:
            anim_note = ("snapshots differ in size ("
                         + ", ".join(f"{w}x{h}" for h, w in sizes)
                         + "): an APNG needs one size for every snapshot, so this replay "
                           "has a still of the first exported snapshot instead of a strip")
        else:
            try:
                write_apng(out_dir / "anim.png", panels)
                anim = f"cases/{name}/anim.png"
            except ValueError as exc:
                anim_note = (f"no animated strip for this replay: {exc}; "
                             f"the still is the first exported snapshot")
        if anim is None:
            write_png(str(out_dir / "anim.png"), panels[0])
            anim = f"cases/{name}/anim.png"
    elif panels:
        write_png(str(out_dir / "anim.png"), panels[0])
        anim = f"cases/{name}/anim.png"

    rc = run.refcache_info(name)
    # `fidelity_score` is the snapshot-distance score. For a performance or
    # procedural case the headline score is the timing ratio / the ledger
    # checks, so the fidelity score is kept separately in `replay_score`.
    replay_score = detail.get("replay_score")
    entry = {
        "section": case.get("category"),
        "subsystem": case.get("family"),
        "family": case.get("family"),
        "fidelity_score": float(replay_score if replay_score is not None else case.get("score", 0.0)),
        "score": float(case.get("score", 0.0)),
        "threshold": detail.get("threshold"),
        "snapshot_defects": defects,
        "snapshot_scores": scores,
        "first_diverge_snapshot": detail.get("first_diverge_snapshot"),
        "max_defect_snapshot": int(np.argmax(defects)) if defects else None,
        "n_snapshots": len(defects) or len(images),
        "pictures_exported": pictures,
        "exit": detail.get("exit"),
        "returncode": detail.get("returncode"),
        "signal": detail.get("signal"),
        "candidate_wall_seconds": detail.get("candidate_wall_seconds"),
        "reference_wall_seconds": detail.get("reference_wall_seconds"),
        "missing_snapshots": missing,
        "images": images,
        "anim": anim,
        "anim_animated": bool(anim) and anim_note is None and len(panels) > 1,
        "anim_note": anim_note,
        # The refcache entry as it stands, plus the threshold the run was
        # actually scored at: the two differ when the entry was calibrated by an
        # older metric and recalibrated at grading or regrading time
        # (grader/runner.py:entry_threshold).
        "reference": dict({k: rc.get(k) for k in
                           ("threshold", "noise", "sensitivity", "motion_p90", "snapshots")},
                          scored_threshold=detail.get("threshold"),
                          threshold_recalibrated=detail.get("threshold_recalibrated")),
    }
    if "ratio" in detail:
        # runner.grade_replay sets ratio = inf when the case crashed, timed out
        # or failed the fidelity gate; JSON cannot carry that, so the report has
        # already turned it into null + `ratio_note` (grader/jsonio.py). Both
        # spellings arrive here, so both are recognised.
        ratio = detail["ratio"]
        finite = isinstance(ratio, (int, float)) and not isinstance(ratio, bool) \
            and math.isfinite(ratio)
        entry["ratio"] = float(ratio) if finite else None
        entry["ratio_unmeasurable"] = not finite
        entry["ratio_note"] = detail.get("ratio_note") or (
            None if finite else "infinite (timeout or correctness gate)")
        # The full-success bar for a performance case: within perf_gate x the
        # reference, or it is a miss.
        gate, half = run.instance.metric.perf_gate, run.instance.metric.perf_half
        entry["perf_gate"] = gate
        entry["perf_half"] = half
        entry["within_gate"] = bool(finite and ratio <= gate)
    if "checks" in detail:
        checks = detail["checks"] or []
        failed = [c["check"] for c in checks if not c.get("ok")]
        entry["checks_total"] = len(checks)
        entry["checks_passed"] = len(checks) - len(failed)
        entry["checks_failed"] = failed
    if detail.get("device_errors"):
        entry["device_errors"] = detail["device_errors"][:8]
    if detail.get("stderr_tail"):
        entry["stderr_tail"] = str(detail["stderr_tail"])[-600:]
    # The picture a thumbnail should use: the worst snapshot that was exported.
    entry["worst_image_index"] = (
        max(range(len(images)), key=lambda k: images[k]["defect"] if
            isinstance(images[k]["defect"], (int, float)) else -1.0) if images else None)
    return entry


# ------------------------------------------------------------------ run export

def sections_of(run: Run, per_testcase: dict) -> dict:
    """`sections`: the grader's own category score, plus family means.

    `score` is copied straight out of `report.aggregate.categories[*].score` and
    is the only category number the site prints. `subsystems` is a second,
    different rollup -- the unweighted mean of the case scores in each family --
    and a mean of those means does **not** reproduce `score` when the families
    hold different numbers of cases (procedural is off by ~0.02 on every
    attempt). It is kept because it says which family is dragging a category
    down, and it is labelled everywhere it appears so that nobody reads it as a
    second opinion about the category score.
    """
    agg = run.report.get("aggregate") or {}
    cats = agg.get("categories") or {}
    out = {}
    for cat in run.instance.metric.weights:
        info = cats.get(cat)
        if info is None:
            continue
        subs: dict[str, list[float]] = {}
        for name, entry in per_testcase.items():
            if entry.get("section") == cat:
                subs.setdefault(entry.get("subsystem") or "unknown", []).append(entry["score"])
        out[cat] = {
            "score": info.get("score", 0.0),
            "score_source": "report.aggregate.categories",
            "weight": info.get("weight"),
            "effective_weight": info.get("effective_weight", info.get("weight")),
            "n": info.get("n", 0),
            "family_means": {k: float(np.mean(v)) for k, v in sorted(subs.items())},
            "family_means_note": SUBSYSTEM_NOTE,
        }
    return out


def bands_of(per_testcase: dict, per_band: int = 3) -> dict:
    """Up to `per_band` examples per score band, the ones with the most snapshots."""
    out = {"bands": []}
    for label, lo, hi in BANDS:
        picks = [(name, e) for name, e in per_testcase.items() if lo <= e["fidelity_score"] < hi]
        picks.sort(key=lambda t: (-(t[1]["n_snapshots"] or 0), t[0]))
        out["bands"].append({
            "band": label, "lo": max(lo, 0.0), "hi": min(hi, 1.0),
            "note": BAND_NOTES[label],
            "examples": [{
                "name": name,
                "fidelity_score": e["fidelity_score"],
                "score": e["score"],
                "section": e["section"],
                "subsystem": e["subsystem"],
                "n_snapshots": e["n_snapshots"],
                "threshold": e["threshold"],
                "first_diverge_snapshot": e["first_diverge_snapshot"],
                "anim": e["anim"],
                "images": (e["images"][e["max_defect_snapshot"]]
                           if e["images"] and e["max_defect_snapshot"] is not None
                           and e["max_defect_snapshot"] < len(e["images"])
                           else (e["images"][0] if e["images"] else None)),
            } for name, e in picks[:per_band]],
        })
    return out


def checkpoints_of(run: Run) -> list[dict]:
    """`checkpoints/index.json`, from checkpoint-curve.json plus attempt bookkeeping.

    The curve schema (evalbase.harness.attempt.grade_checkpoints)
    is a list of rows with `index`, `id`, `elapsed_seconds`, `trigger` and
    `source_sha256`, and then either a grade (`overall`, `full_success`,
    `build_failure`, `report`, `grade_reused`/`reused_from`) or `skipped` with
    the reason the selection dropped it. Every one of those is carried through,
    because "0.31 at 4 h" means something different when the point was reused
    from an identical earlier source than when it was freshly graded.

    When there is no `checkpoint-curve.json` at all -- an attempt that has only
    been finally graded -- the checkpoints the attempt recorded are still
    listed, with their trigger and wall time and `status: not_graded`, so the
    site can say "81 checkpoints, none graded yet" instead of showing nothing.
    """
    curve = [row for row in (run.curve or []) if isinstance(row, dict)]
    noted = {c.get("index"): c for c in (run.attempt.get("checkpoints") or [])
             if isinstance(c, dict)}
    by_index = {row.get("index"): row for row in curve}
    order = [row.get("index") for row in curve]
    order += [i for i in sorted(noted, key=lambda x: (x is None, x)) if i not in by_index]

    out = []
    for index in order:
        row = by_index.get(index) or {}
        note = noted.get(index) or {}
        text = note.get("note")
        entry = {
            "checkpoint_id": index,
            "id": row.get("id") or note.get("name"),
            "trigger": row.get("trigger") or checkpoint_trigger(note.get("name"), text),
            "source_sha256": row.get("source_sha256") or note.get("source_sha256"),
            # The curve is the authority on wall time when it exists; the
            # attempt's own checkpoint list is the fallback.
            "elapsed_seconds": row.get("elapsed_seconds", note.get("elapsed_seconds")),
            "note": text,
        }
        if row.get("error"):
            entry.update(status="grading_failed", error=row["error"])
        elif row.get("skipped"):
            entry.update(status="skipped", skipped=row["skipped"])
        elif "overall" in row:
            entry.update(status="build_failure" if row.get("build_failure") else "ok",
                         overall=row.get("overall"),
                         full_success=row.get("full_success", False),
                         build_failure=bool(row.get("build_failure", False)),
                         report=row.get("report"),
                         grade_reused=bool(row.get("grade_reused", False)),
                         reused_from=row.get("reused_from"))
        else:
            entry["status"] = "not_graded"
        out.append(entry)
    return out


def curve_of(run: Run, checkpoints: list[dict]) -> dict:
    """What the site needs to draw (or to explain the absence of) a curve."""
    points = [{"index": c["checkpoint_id"], "elapsed_seconds": c["elapsed_seconds"],
               "overall": c["overall"], "trigger": c["trigger"],
               "grade_reused": c.get("grade_reused", False),
               "full_success": c.get("full_success", False)}
              for c in checkpoints
              if c["status"] in ("ok", "build_failure") and c.get("overall") is not None]
    counts: dict[str, int] = {}
    for c in checkpoints:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "present": run.curve is not None,
        "source": "checkpoint-curve.json" if run.curve is not None else None,
        "n_checkpoints": len(checkpoints),
        "n_graded": len(points),
        "status_counts": counts,
        "reason_absent": None if run.curve is not None else (
            "no checkpoint-curve.json in the attempt directory: the checkpoints "
            "were recorded but never rebuilt and graded "
            "(python3 -m evalbase.harness.run grade-checkpoints <attempt>)"),
        "points": points,
    }


def cost_of(run: Run) -> dict:
    """The cost line, always as an estimate.

    Claude Code's `total_cost_usd` is a list-price
    estimate (`modelUsage[*].costBasis == "list"`), not money anyone was
    billed, and on a subscription login nobody was billed anything. The site
    therefore never gets a bare dollar amount to print: it gets a number, a
    basis, a flag saying whether it is a real charge, and a sentence.
    """
    usage = run.attempt.get("usage") or {}
    amount = usage.get("cost_usd")
    basis = usage.get("cost_basis")
    billed = bool(usage.get("cost_is_billed_charge", False))
    if amount is None:
        label = "not reported"
    elif billed:
        label = f"${amount:,.2f} billed"
    else:
        label = f"${amount:,.2f} {basis or 'list'}-price estimate (not spend)"
    return {
        "amount_usd": amount,
        "basis": basis,
        "is_billed_charge": billed,
        "reported_by_cli": bool(usage.get("cost_reported_by_cli", False)),
        "label": label,
        "note": usage.get("cost_note") or (
            "list-price estimate of what these tokens would cost on the API; "
            "not an amount anyone was billed"),
    }


def validity_of(run: Run) -> dict:
    """`measurement_valid` with the reasons and the incidents behind it."""
    attempt = run.attempt
    reasons = attempt.get("measurement_invalid_reasons") or []
    incidents = attempt.get("infrastructure_incidents") or []
    return {
        "measurement_valid": attempt.get("measurement_valid"),
        "measurement_invalid_reasons": list(reasons),
        "infrastructure_incidents": incidents,
        "n_infrastructure_incidents": len(incidents),
        "incident_kinds": sorted({i.get("kind") for i in incidents
                                  if isinstance(i, dict) and i.get("kind")}),
    }


def read_labels(site: Path) -> dict:
    """`site/labels.json`: the writer's own words about what each row means."""
    data = _read_json(Path(site) / LABELS_FILE)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("runs", {})
    if not isinstance(data["runs"], dict):
        data["runs"] = {}
    return data


def sync_labels(site: Path, runs: list[Run]) -> dict:
    """Add a row for every exported run at `interim`, never touching an edit.

    The exporter can create a label and can only ever create it as `interim`.
    Promoting one to `record` is a hand edit of `site/labels.json`, which is why
    the site can never claim a record number on its own.
    """
    labels = read_labels(site)
    labels["_comment"] = (
        "Edited by hand. `status` is what the site prints next to a run: "
        + ", ".join(f"{k} ({v})" for k, v in STATUS_NOTES.items())
        + ". grader.export only ever ADDS a row at \"" + DEFAULT_STATUS
        + "\" and never rewrites one, so the site cannot promote its own numbers.")
    labels.setdefault("headline", {"text": None, "note":
                                   "optional one-line banner for the leaderboard page"})
    for run in runs:
        row = labels["runs"].get(run.candidate)
        if not isinstance(row, dict):
            labels["runs"][run.candidate] = {
                "status": "control" if run.baseline else DEFAULT_STATUS,
                "note": None,
            }
    labels["runs"] = dict(sorted(labels["runs"].items()))
    _write_json(Path(site) / LABELS_FILE, labels)
    return labels


def label_of(labels: dict, candidate: str) -> dict:
    row = (labels.get("runs") or {}).get(candidate)
    if not isinstance(row, dict):
        row = {}
    status = row.get("status") or DEFAULT_STATUS
    return {"status": status,
            "note": row.get("note") or STATUS_NOTES.get(status),
            "from_labels_file": candidate in (labels.get("runs") or {})}


def metadata_of(run: Run) -> dict:
    attempt, report = run.attempt, run.report
    version = attempt.get("task_version") or {}
    corpus = version.get("corpus") or {}
    return {
        "candidate": run.candidate,
        "label": run.label,
        "model": run.model,
        "model_served": (attempt.get("models_served") or [None])[0],
        "harness": run.harness,
        "harness_version": attempt.get("harness_version"),
        "provider": run.provider,
        "simulated_model": attempt.get("simulated_model"),
        "reasoning": attempt.get("reasoning_served") or attempt.get("reasoning_requested"),
        "task_version": {
            "git_commit": version.get("git_commit"),
            "task_sha256": version.get("task_sha256"),
            "spec_sha256": version.get("spec_sha256"),
            "grader_sha256": version.get("grader_sha256"),
            "harness_sha256": version.get("harness_sha256"),
            "corpus_sha256": corpus.get("sha256"),
            "corpus_replays": len(corpus.get("replays") or {}) or None,
            "reference_image": version.get("reference_image"),
            "solver_image": version.get("solver_image"),
        },
        "budget": attempt.get("budget"),
        "stop_reason": attempt.get("stop_reason"),
        "stop_policy": attempt.get("stop_policy"),
        "usage": attempt.get("usage"),
        # Never a bare dollar amount: see cost_of.
        "cost": cost_of(run),
        "tool_calls": attempt.get("tool_calls"),
        "status": attempt.get("status"),
        "measurement_valid": attempt.get("measurement_valid"),
        # Why it is not valid, and the infrastructure incidents behind a
        # `sandbox_lost` reason: a reader of the export must not have to open
        # attempt.json to find out that the solver container was rebuilt
        # mid-run.
        "measurement_invalid_reasons": attempt.get("measurement_invalid_reasons") or [],
        "infrastructure_incidents": attempt.get("infrastructure_incidents") or [],
        "rate_limit_wait_seconds": attempt.get("rate_limit_wait_seconds"),
        "started": attempt.get("started"),
        "ended": attempt.get("ended"),
        "solver_seconds": attempt.get("solver_seconds"),
        "grading_seconds": attempt.get("grading_seconds"),
        "graded_corpus": attempt.get("graded_corpus"),
        "graded_refcache": attempt.get("graded_refcache"),
        "refcache_used": str(run.refcache),
        "refcache_source": run.refcache_source,
        "missing_reference_cases": list(getattr(run, "missing_reference_cases", [])),
        "final_source_sha256": attempt.get("final_source_sha256"),
        "compaction_events": attempt.get("compaction_events"),
        "checkpoints_count": len(attempt.get("checkpoints") or []),
        # Which report the numbers on this page came from (export.Run.grade_of_record).
        "grade_of_record": run.grade_of_record(),
        "grading": {
            "report": str(run.report_path),
            "during_run_report": str(run.during_run_report_path),
            "grade_source": run.grade_source,
            "corpus": report.get("corpus"),
            "refcache": report.get("cache"),
            "reference_image": report.get("image"),
            "lib_dir": report.get("lib_dir"),
            "build": report.get("build"),
            "build_failure": report.get("build_failure", False),
            "full_success": (report.get("aggregate") or {}).get("full_success"),
            "weights_renormalised": (report.get("aggregate") or {}).get("weights_renormalised"),
            "categories_present": (report.get("aggregate") or {}).get("categories_present"),
        },
        "weights": {"note": "nominal weights from the instance MetricSpec; a partial run "
                            "renormalises over the categories present"},
    }


def export_run(run: Run, site: Path, animate: bool = True, quiet: bool = False,
               pictures: str = "all") -> dict:
    """Write results/<candidate>/* and return the leaderboard entry."""
    out_root = site / "results" / run.candidate
    # Re-exporting a run that now grades fewer replays must not leave the old
    # ones behind, or the viewer shows pictures no summary.json refers to.
    shutil.rmtree(out_root / "cases", ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)
    per_testcase = {}
    for case in sorted(run.report.get("cases") or [], key=lambda c: c["name"]):
        entry = export_case(run, case, out_root, animate=animate, pictures=pictures)
        per_testcase[case["name"]] = entry
        if not quiet:
            print(f"  [{entry['section']:11s}] {case['name']:28s} "
                  f"score={entry['score']:.4f} fidelity={entry['fidelity_score']:.4f} "
                  f"snapshots={len(entry['images'])}"
                  + (f"  [anim skipped: {entry['anim_note']}]"
                     if entry.get("anim_note") else ""))
    agg = run.report.get("aggregate") or {}
    sections = sections_of(run, per_testcase)
    checkpoints = checkpoints_of(run)
    curve = curve_of(run, checkpoints)
    validity = validity_of(run)
    cost = cost_of(run)
    record = run.grade_of_record()
    summary = {
        "candidate": run.candidate,
        "candidate_sha256": run.attempt.get("final_source_sha256"),
        "label": run.label,
        "model": run.model,
        "harness": run.harness,
        "provider": run.provider,
        "baseline": run.baseline,
        "graded_at": run.graded_at(),
        # The headline number, and where it came from: the idle-host final
        # regrade when the attempt has one, the during-run manifest grade when
        # it does not. Both numbers are carried either way.
        "overall": agg.get("overall", 0.0),
        "grade_of_record": record,
        "grade_source": record["source"],
        "during_run_overall": record["during_run"]["overall"],
        "full_success": agg.get("full_success", False),
        "n": agg.get("n", len(per_testcase)),
        "sections": sections,
        "per_testcase": per_testcase,
    }
    _write_json(out_root / "summary.json", summary)
    _write_json(out_root / "metadata.json", metadata_of(run))
    _write_json(out_root / "bands.json", bands_of(per_testcase))
    _write_json(out_root / "checkpoints" / "index.json", checkpoints)
    _write_json(out_root / "checkpoints" / "curve.json", curve)
    return {
        "candidate": run.candidate,
        "candidate_sha256": summary["candidate_sha256"],
        "label": run.label,
        "model": run.model,
        "harness": run.harness,
        "provider": run.provider,
        "baseline": run.baseline,
        "graded_at": summary["graded_at"],
        "overall": summary["overall"],
        "grade_of_record": record,
        "grade_source": record["source"],
        "during_run_overall": record["during_run"]["overall"],
        "full_success": summary["full_success"],
        "n_testcases": len(per_testcase),
        "checkpoints_count": len(run.attempt.get("checkpoints") or []) or len(checkpoints),
        "curve": {k: curve[k] for k in ("present", "n_checkpoints", "n_graded",
                                        "reason_absent")},
        "tokens_used": run.tokens_used(),
        "wall_clock_hours": run.wall_clock_hours(),
        "solver_seconds": run.attempt.get("solver_seconds"),
        "stop_reason": run.attempt.get("stop_reason"),
        "status": run.attempt.get("status"),
        "graded_corpus": run.attempt.get("graded_corpus") or run.report.get("corpus"),
        # A reader must be able to see from the leaderboard alone that a row is
        # not a valid measurement, and why.
        "measurement_valid": validity["measurement_valid"],
        "measurement_invalid_reasons": validity["measurement_invalid_reasons"],
        "n_infrastructure_incidents": validity["n_infrastructure_incidents"],
        "incident_kinds": validity["incident_kinds"],
        "cost": cost,
        # The grader's own category score and nothing that could be mistaken
        # for a second opinion on it: the per-family means live in summary.json
        # under `family_means`, labelled.
        "sections": {k: {"score": v["score"], "score_source": v["score_source"],
                         "weight": v["weight"],
                         "effective_weight": v["effective_weight"], "n": v["n"]}
                     for k, v in sections.items()},
        "summary": f"results/{run.candidate}/summary.json",
        "page": f"results/{run.candidate}/index.html",
    }


def merge_leaderboard(site: Path, entries: list[dict]) -> list[dict]:
    """Replace-by-candidate, then sort by overall so re-exporting one run is safe."""
    existing = _read_json(site / "leaderboard.json")
    rows = {row["candidate"]: row for row in existing} if isinstance(existing, list) else {}
    for entry in entries:
        rows[entry["candidate"]] = entry
    out = sorted(rows.values(), key=lambda r: (-(r.get("overall") or 0.0), r["candidate"]))
    _write_json(site / "leaderboard.json", out)
    return out


# ------------------------------------------------------------------ compare

def export_compare(a: Run, b: Run, site: Path, quiet: bool = False) -> dict:
    """reference | A | B on the worst snapshot of every replay both runs graded."""
    key = f"{a.candidate}__vs__{b.candidate}"
    out_dir = site / "compare" / key
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_a = {c["name"]: c for c in a.report.get("cases") or []}
    cases_b = {c["name"]: c for c in b.report.get("cases") or []}
    rows = []
    for name in sorted(set(cases_a) & set(cases_b)):
        ca, cb = cases_a[name], cases_b[name]
        da, db = ca.get("detail") or {}, cb.get("detail") or {}
        ref_snaps = a.ref_snapshots(name)
        if not ref_snaps:
            continue
        # the snapshot where the two candidates disagree most with the reference
        spread = [max(x, y) for x, y in zip(da.get("snapshot_defects") or [],
                                            db.get("snapshot_defects") or [])]
        idx = int(np.argmax(spread)) if spread else 0
        idx = min(idx, len(ref_snaps) - 1)
        ref = _load(a, a.ref_dir(name), ref_snaps[idx])
        ref8 = _preview(a, ref)
        if ref8 is None:
            continue
        panels = [display(ref8)]
        for run in (a, b):
            snaps = run.cand_snapshots(name)
            img = _load(run, run.cases_dir / name, snaps[idx] if idx < len(snaps) else None)
            c8 = _preview(run, img)
            if c8 is None or c8.shape[:2] != ref8.shape[:2]:
                c8 = np.zeros_like(ref8)
            panels.append(display(c8))
        fig = f"{name}.png"
        write_png(str(out_dir / fig), hstrip(panels))
        rows.append({
            "name": name, "snapshot": idx,
            "section": ca.get("category"), "subsystem": ca.get("family"),
            "figure": f"compare/{key}/{fig}",
            "panels": ["reference", a.label, b.label],
            "a": {"score": ca.get("score"), "defect": (da.get("snapshot_defects") or [None])[idx]
                  if idx < len(da.get("snapshot_defects") or []) else None},
            "b": {"score": cb.get("score"), "defect": (db.get("snapshot_defects") or [None])[idx]
                  if idx < len(db.get("snapshot_defects") or []) else None},
            "threshold": da.get("threshold"),
        })
        if not quiet:
            print(f"  [compare] {name:28s} snapshot={idx} "
                  f"{a.label}={ca.get('score'):.4f} {b.label}={cb.get('score'):.4f}")
    index = {
        "key": key,
        "a": {"candidate": a.candidate, "label": a.label, "model": a.model,
              "overall": (a.report.get("aggregate") or {}).get("overall", 0.0)},
        "b": {"candidate": b.candidate, "label": b.label, "model": b.model,
              "overall": (b.report.get("aggregate") or {}).get("overall", 0.0)},
        "cases": rows,
    }
    _write_json(out_dir / "index.json", index)
    listing = _read_json(site / "compare" / "index.json")
    keys = {row["key"]: row for row in listing} if isinstance(listing, list) else {}
    keys[key] = {"key": key, "a": index["a"], "b": index["b"],
                 "n_cases": len(rows), "index": f"compare/{key}/index.json"}
    _write_json(site / "compare" / "index.json", sorted(keys.values(), key=lambda r: r["key"]))
    return index


# ------------------------------------------------------------------ viewer

VIEWER = Path(__file__).resolve().parent / "viewer.html"


def write_viewer(site: Path, title: str = "evalbase", categories=("replay", "procedural", "performance")) -> int:
    """The JSON explorer, beside the generated pages.

    `index.html` and the per-run and per-replay pages are written by
    grader/sitehtml.py and need nothing but a file:// open. This one is the
    interactive inspector: it fetches the same JSON, so it wants
    `python3 -m http.server` in the site directory.
    """
    site.mkdir(parents=True, exist_ok=True)
    html = VIEWER.read_text().replace("__SITE_TITLE__", title).replace(
        "__CATEGORIES__", json.dumps(list(categories)))
    (site / "viewer.html").write_text(html)
    return len(html)


# ------------------------------------------------------------------ CLI

def _run_dirs(root: Path) -> list[Path]:
    out = []
    for child in sorted(Path(root).iterdir()):
        if child.is_dir() and ((child / "report.json").is_file()
                               or (child / "grade" / "report.json").is_file()):
            out.append(child)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evalbase.grader.export",
                                description="static site data bundle for a results site")
    p.add_argument("--instance", default=None, help="path to the instance file (default $EVALBASE_INSTANCE)")
    p.add_argument("--run", action="append", default=[], help="a graded run directory")
    p.add_argument("--runs-root", default=None, help="export every graded run under this directory")
    p.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), default=None)
    p.add_argument("--refcache", default=None,
                   help="refcache directory; default: the `cache` each report records")
    p.add_argument("--site", required=True)
    p.add_argument("--label", default=None, help="label for a single --run")
    p.add_argument("--model", default=None)
    p.add_argument("--harness", default=None)
    p.add_argument("--provider", default=None)
    p.add_argument("--pictures", choices=PICTURE_MODES, default="all",
                   help="which snapshots get pictures: all (default), key (first + the "
                        "worst snapshot) or first. The JSON timelines always carry every "
                        "snapshot the grader scored.")
    p.add_argument("--no-anim", action="store_true", help="skip the animated strips")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)
    instance = load_instance(a.instance)

    site = Path(a.site).resolve()
    site.mkdir(parents=True, exist_ok=True)
    refcache = Path(a.refcache).resolve() if a.refcache else None

    targets: list[Path] = [Path(r) for r in a.run]
    if a.runs_root:
        targets += _run_dirs(Path(a.runs_root))
    if a.compare:
        targets += [Path(a.compare[0]), Path(a.compare[1])]
    if not targets:
        p.error("nothing to export: pass --run, --runs-root or --compare")

    seen, runs = set(), []
    for target in targets:
        resolved = Path(target).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        # --label names one run; with several targets each keeps its report label
        label = a.label if len(targets) == 1 else None
        runs.append(Run(instance, resolved, refcache, label=label, model=a.model,
                        harness=a.harness, provider=a.provider))

    entries = []
    for run in runs:
        if not a.quiet:
            print(f"[export] {run.label} -> results/{run.candidate} "
                  f"(refcache {run.refcache}, from the {run.refcache_source})")
            if run.missing_reference_cases:
                print(f"[export] WARNING: {len(run.missing_reference_cases)} case(s) have "
                      f"no refcache entry, e.g. {run.missing_reference_cases[0]}")
        entries.append(export_run(run, site, animate=not a.no_anim, quiet=a.quiet,
                                  pictures=a.pictures))
    board = merge_leaderboard(site, entries)

    if a.compare:
        by_dir = {r.dir: r for r in runs}
        export_compare(by_dir[Path(a.compare[0]).resolve()],
                       by_dir[Path(a.compare[1]).resolve()], site, quiet=a.quiet)

    write_viewer(site, instance.name, list(instance.metric.weights))
    labels = sync_labels(site, runs)
    pages = write_html_site(site, board, labels, title=instance.name,
                            categories=list(instance.metric.weights))
    if not a.quiet:
        print(f"[export] leaderboard: {len(board)} run(s) -> {site / 'leaderboard.json'}")
        print(f"[export] labels: {site / LABELS_FILE} "
              f"({sum(1 for r in labels['runs'].values() if r.get('status') == 'record')} "
              f"row(s) marked as a record by hand)")
        print(f"[export] pages: {pages} static HTML -> {site / 'index.html'}")
        print(f"[export] json explorer (needs a server): {site / 'viewer.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
