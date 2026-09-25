#!/usr/bin/env python3
"""Summarize control and performance-variance runs as Markdown.

The control names, the metric constants and the corpus sizes come from the instance.

Reads:
  <runs>/control-<name>-<split>/report.json   for every control the instance declares
                                              and split in public, hidden
  <runs>/control-reference-*/report.json      and <runs>/control-stub*/report.json, if present
  <runs>/perfvar/run<i>/<split>/report.json   for i in 1..5
  <runs>/controls.log                         for the image id and start/end timestamps

With `--regrade <version>` every control run is read from
`<run>/regrade-v<version>/report.json` when that file exists, and from its
native `report.json` otherwise; which reports came from a regrade is recorded
as a footnote in the generated section. The perfvar runs are performance-only
-- a wall-time ratio is a live measurement and a regrade reuses it -- so they
are always read as they are.

Prints a Markdown report to stdout. With --write, also replaces the content
between the `<!-- controls-summary:begin -->` / `:end -->` markers in
<reports>/CONTROLS.md and between `<!-- perfvar:begin -->` / `:end -->` in
<reports>/PERF_VARIANCE.md. A human edits the prose outside the markers only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
import time

from ..interfaces import Instance, load_instance

# The controls every sweep runs: a missing run is reported as "(not run)".
CONTROL_NAMES: list[str] = []
# Controls whose row is simply absent until the run exists.
EXTRA_CONTROL_NAMES: list[str] = []
CORPORA = ["public", "hidden"]
CATEGORIES = ("replay", "procedural", "performance")
INSTANCE: Instance | None = None


def configure(instance: Instance | None, controls=None, extra=None, corpora=None) -> None:
    """Bind the summary to an instance: control names, categories, corpus sizes."""
    global INSTANCE, CONTROL_NAMES, EXTRA_CONTROL_NAMES, CORPORA, CATEGORIES
    INSTANCE = instance
    if controls is not None:
        CONTROL_NAMES = list(controls)
    elif instance is not None and instance.controls is not None:
        CONTROL_NAMES = [n for n in instance.controls.names() if n not in ("reference", "stub")]
    if extra is not None:
        EXTRA_CONTROL_NAMES = list(extra)
    if corpora is not None:
        CORPORA = list(corpora)
    if instance is not None:
        CATEGORIES = (instance.metric.fidelity_category, instance.metric.procedural_category,
                      instance.metric.performance_category)
# Said in the perfvar section whenever --regrade is on, because the control
# table above it is then reporting regraded numbers and this table is not:
# a regrade reuses a run's stored wall times (grader/regrade.py), so there is
# nothing for it to recompute here.
PERFVAR_NATIVE_NOTE = (
    "- These runs are performance-only and are read from their own `report.json`, "
    "with or without `--regrade`: a wall-time ratio is a live measurement, and a "
    "regrade reuses it rather than recomputing it.")


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


class ReportSource:
    """Where each run's numbers came from: its own grade, or a regrade of it.

    With `regrade=None` (the default) this is exactly the old behaviour --
    `<run>/report.json` and nothing else -- so a summary generated without the
    option is unchanged. With a version, a `<run>/regrade-v<version>/report.json`
    is preferred when it exists; the run that has none is still read natively,
    and both cases are recorded so the generated section can say which is which.
    """

    def __init__(self, regrade: str | None = None):
        self.regrade = regrade
        self.rows: dict[str, dict] = {}

    def _record(self, dirname, path, report, regraded):
        source = (report.get("regraded_from") or {}) if regraded else {}
        self.rows[dirname] = {
            "path": path, "regraded": regraded,
            "metric_version": report.get("metric_version"),
            "source_metric_version": source.get("metric_version"),
            "source_overall": source.get("overall"),
        }

    def load(self, runs_dir: str, dirname: str) -> dict | None:
        """The report for one run directory, preferring its regrade when asked for."""
        if self.regrade:
            path = os.path.join(runs_dir, dirname, f"regrade-v{self.regrade}", "report.json")
            rep = _load_json(path)
            if rep is not None:
                self._record(dirname, path, rep, True)
                return rep
        path = os.path.join(runs_dir, dirname, "report.json")
        rep = _load_json(path)
        if rep is not None:
            self._record(dirname, path, rep, False)
        return rep

    def footnote(self) -> list[str]:
        """The provenance line for the generated section; empty without --regrade."""
        if not self.regrade:
            return []
        regraded = sorted(d for d, r in self.rows.items() if r["regraded"])
        native = sorted(d for d, r in self.rows.items() if not r["regraded"])
        if not regraded:
            return [f"- No `regrade-v{self.regrade}/report.json` was found under any run read "
                    f"here; every row above is that run's own grade."]
        versions = sorted({r["metric_version"] or "unstamped"
                           for d, r in self.rows.items() if r["regraded"]})
        sources = sorted({r["source_metric_version"] or "unstamped"
                          for d, r in self.rows.items() if r["regraded"]})
        lines = [
            f"- **Source: regrade.** {len(regraded)} of {len(self.rows)} control runs above are read "
            f"from `<run>/regrade-v{self.regrade}/report.json` — metric "
            f"{', '.join('v' + v if not v.startswith(('v', 'u')) else v for v in versions)}, "
            f"rescored by `grader.cli regrade` from each run's cached snapshots "
            f"(no driver run) out of a run graded under metric "
            f"{', '.join(sources)}.",
        ]
        if native:
            lines.append(f"- Read natively (no `regrade-v{self.regrade}/` present): "
                         + ", ".join(f"`{d}`" for d in native) + ".")
        lines.append("- The performance-variance table is unaffected: those runs are "
                     "performance-only, a wall-time ratio is a live measurement, and they "
                     "are always read from their own `report.json`.")
        return lines


def load_report(runs_dir: str, dirname: str, source: ReportSource | None = None) -> dict | None:
    return (source or ReportSource()).load(runs_dir, dirname)


def find_extra_reports(runs_dir: str, prefix: str,
                       source: ReportSource | None = None) -> list[tuple[str, dict]]:
    """runs/<prefix>* directories that have a report.json, e.g. control-reference*."""
    source = source or ReportSource()
    out = []
    for path in sorted(glob.glob(os.path.join(runs_dir, prefix + "*"))):
        rep = source.load(runs_dir, os.path.basename(path))
        if rep is not None:
            out.append((os.path.basename(path), rep))
    return out


def agg_row(label: str, report: dict) -> dict:
    agg = report["aggregate"]
    cats = agg["categories"]

    def score(cat):
        c = cats.get(cat)
        return c["score"] if c and c.get("n") else None

    return {
        "label": label,
        "overall": agg["overall"],
        "replay": score(CATEGORIES[0]),
        "procedural": score(CATEGORIES[1]),
        "performance": score(CATEGORIES[2]),
        "full_success": agg["full_success"],
    }


def fmt(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def lowest_replay_cases(report: dict, n: int = 8) -> list[dict]:
    rows = []
    for c in report.get("cases", []):
        if c.get("category") != CATEGORIES[0]:
            continue
        rows.append({
            "name": c["name"],
            "score": c["score"],
            "threshold": c.get("detail", {}).get("threshold"),
        })
    rows.sort(key=lambda r: r["score"])
    return rows[:n]


def perfvar_rows(runs_dir: str, corpus: str, n_runs: int = 5) -> dict[str, dict]:
    """replay -> {ratios: [...], scores: [...]} across run1..run<n_runs>."""
    by_replay: dict[str, dict] = {}
    for i in range(1, n_runs + 1):
        rep = _load_json(os.path.join(runs_dir, "perfvar", f"run{i}", corpus, "report.json"))
        if rep is None:
            continue
        for c in rep.get("cases", []):
            if c.get("category") != CATEGORIES[2]:
                continue
            d = by_replay.setdefault(c["name"], {"ratios": [], "scores": []})
            ratio = c.get("detail", {}).get("ratio")
            if ratio is not None and ratio != float("inf"):
                d["ratios"].append(ratio)
            d["scores"].append(c["score"])
    return by_replay


def metric_constants() -> dict:
    """The instance's MetricSpec, minus the category names."""
    if INSTANCE is None:
        return {}
    spec = INSTANCE.metric
    names = ["k_noise", "k_sens", "t_lo", "t_hi", "hill_n", "perf_half", "perf_gate",
             "case_bar", "procedural_bar", "perturbations", "tolerance_perturbations"]
    return {n: getattr(spec, n) for n in names}


def metric_version() -> str:
    return "v" + INSTANCE.metric.version if INSTANCE is not None else "unknown"


def log_image_id(controls_log: str) -> str | None:
    try:
        text = open(controls_log).read()
    except FileNotFoundError:
        return None
    # runs/controls.log is append-only across runs: the last block is the current one.
    ms = re.findall(r"^image id:\s*(\S+)", text, re.MULTILINE)
    return ms[-1] if ms else None


def log_timestamps(controls_log: str) -> tuple[str | None, str | None]:
    try:
        text = open(controls_log).read()
    except FileNotFoundError:
        return None, None
    starts = re.findall(r"^=== .* start (\S+) ===", text, re.MULTILINE)
    ends = re.findall(r"^=== .* end (\S+) ===", text, re.MULTILINE)
    return (starts[-1] if starts else None), (ends[-1] if ends else None)


def corpus_size(corpus_dir: str) -> int:
    return len(glob.glob(os.path.join(corpus_dir, "*.json")))


def extra_control_rows(runs_dir: str,
                       source: ReportSource | None = None) -> tuple[list[str], dict[tuple[str, str], dict]]:
    """Markdown rows for the EXTRA_CONTROL_NAMES runs that exist, and their reports.

    Returns ([], {}) when none of them has been run, which is what keeps the
    default sweep's output identical.
    """
    source = source or ReportSource()
    lines: list[str] = []
    reports: dict[tuple[str, str], dict] = {}
    for name in EXTRA_CONTROL_NAMES:
        for corpus in CORPORA:
            rep = source.load(runs_dir, f"control-{name}-{corpus}")
            if rep is None:
                continue
            reports[(name, corpus)] = rep
            row = agg_row(f"{name}-{corpus}", rep)
            lines.append(f"| `{name}` | {corpus} | {fmt(row['overall'])} | {fmt(row['replay'])} | "
                         f"{fmt(row['procedural'])} | {fmt(row['performance'])} | {row['full_success']} |")
    return lines, reports


def build_summary_table(runs_dir: str,
                       source: ReportSource | None = None) -> tuple[list[str], dict[tuple[str, str], dict]]:
    source = source or ReportSource()
    lines = ["| Control | Corpus | Overall | Replay | Procedural | Performance | Full success |",
              "|---|---|---|---|---|---|---|"]
    reports: dict[tuple[str, str], dict] = {}
    for name in CONTROL_NAMES:
        for corpus in CORPORA:
            rep = source.load(runs_dir, f"control-{name}-{corpus}")
            if rep is None:
                lines.append(f"| `{name}` | {corpus} | (not run) | | | | |")
                continue
            reports[(name, corpus)] = rep
            row = agg_row(f"{name}-{corpus}", rep)
            lines.append(f"| `{name}` | {corpus} | {fmt(row['overall'])} | {fmt(row['replay'])} | "
                          f"{fmt(row['procedural'])} | {fmt(row['performance'])} | {row['full_success']} |")
    extra_lines, extra_reports = extra_control_rows(runs_dir, source)
    lines += extra_lines
    reports.update(extra_reports)
    for prefix in ("control-reference", "control-stub"):
        for dirname, rep in find_extra_reports(runs_dir, prefix, source):
            row = agg_row(dirname, rep)
            lines.append(f"| `{dirname}` | (label) | {fmt(row['overall'])} | {fmt(row['replay'])} | "
                          f"{fmt(row['procedural'])} | {fmt(row['performance'])} | {row['full_success']} |")
    return lines, reports


def build_lowest_cases_section(reports: dict[tuple[str, str], dict]) -> list[str]:
    lines = ["### 8 lowest-scoring replay cases per control"]
    for name in CONTROL_NAMES + EXTRA_CONTROL_NAMES:
        for corpus in CORPORA:
            rep = reports.get((name, corpus))
            if rep is None:
                continue
            lines.append(f"\n**{name} ({corpus})**\n")
            lines.append("| case | score | threshold |")
            lines.append("|---|---|---|")
            for row in lowest_replay_cases(rep):
                thr = "-" if row["threshold"] is None else f"{row['threshold']:.3f}"
                lines.append(f"| {row['name']} | {row['score']:.3f} | {thr} |")
    return lines


def build_perfvar_table(runs_dir: str) -> list[str]:
    lines = ["| corpus | replay | min ratio | median ratio | max ratio | min score | max score |",
              "|---|---|---|---|---|---|---|"]
    any_rows = False
    for corpus in CORPORA:
        by_replay = perfvar_rows(runs_dir, corpus)
        for replay in sorted(by_replay):
            d = by_replay[replay]
            if not d["ratios"]:
                continue
            any_rows = True
            lines.append(
                f"| {corpus} | {replay} | {min(d['ratios']):.3f} | {statistics.median(d['ratios']):.3f} | "
                f"{max(d['ratios']):.3f} | {min(d['scores']):.4f} | {max(d['scores']):.4f} |")
    if not any_rows:
        lines.append("| (no perfvar runs found) | | | | | | |")
    return lines


def build_provenance(runs_dir: str, corpus_dev: str, corpus_hidden: str, controls_log: str) -> list[str]:
    lines = ["### Provenance"]
    image_id = log_image_id(controls_log)
    lines.append(f"- image id: {image_id or '(unknown; runs/controls.log not found or missing the inspect line)'}")
    consts = metric_constants()
    const_str = ", ".join(f"{k}={v}" for k, v in consts.items())
    lines.append(f"- metric constants (MetricSpec): {const_str}")
    lines.append(f"- corpus sizes: public={corpus_size(corpus_dev)}, hidden={corpus_size(corpus_hidden)}")
    start, end = log_timestamps(controls_log)
    lines.append(f"- run_controls.sh: start={start or '(unknown)'} end={end or '(unknown)'}")
    return lines


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--instance", default=None)
    p.add_argument("--runs-dir", default=None, help="default: the instance's runs directory")
    p.add_argument("--corpus-dev", "--corpus-public", dest="corpus_dev", default=None)
    p.add_argument("--corpus-hidden", default=None)
    p.add_argument("--controls", default=None, help="comma-separated control names (default: the instance's)")
    p.add_argument("--extra-controls", default=None, help="comma-separated names whose rows appear only when run")
    p.add_argument("--controls-log", default=None, help="default: <runs-dir>/controls.log")
    p.add_argument("--reports-dir", default=None, help="default: <instance root>/docs")
    p.add_argument("--write", action="store_true", help="replace the marked sections in reports/CONTROLS.md and reports/PERF_VARIANCE.md")
    p.add_argument("--regrade", default=None, metavar="VERSION",
                   help="read each control run from <run>/regrade-v<VERSION>/report.json when "
                        "that exists (e.g. --regrade v0.5 or --regrade 0.5), and from its own "
                        "report.json otherwise; off by default. The perfvar runs are always "
                        "read as they are.")
    a = p.parse_args(argv)
    instance = None
    try:
        instance = load_instance(a.instance)
    except ValueError:
        if not (a.runs_dir and a.corpus_dev and a.corpus_hidden and a.reports_dir and a.controls is not None):
            raise
    configure(instance, controls=[x for x in a.controls.split(",") if x] if a.controls is not None else None,
              extra=[x for x in a.extra_controls.split(",") if x] if a.extra_controls is not None else None)
    if instance is not None:
        a.runs_dir = a.runs_dir or str(instance.runs_dir)
        a.corpus_dev = a.corpus_dev or str(instance.corpus_public)
        a.corpus_hidden = a.corpus_hidden or str(instance.corpus_hidden)
        a.reports_dir = a.reports_dir or str(instance.root / "docs")
    source = ReportSource(regrade=(a.regrade or "").lstrip("v") or None)
    controls_log = a.controls_log or os.path.join(a.runs_dir, "controls.log")

    version = metric_version()
    image_id = log_image_id(controls_log) or "(unknown)"
    header = f"Metric {version}, image {image_id}. Generated by evalbase.reports.controls_summary at " \
             f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}."

    summary_lines, reports = build_summary_table(a.runs_dir, source)
    lowest_lines = build_lowest_cases_section(reports)
    perfvar_lines = build_perfvar_table(a.runs_dir)
    prov_lines = build_provenance(a.runs_dir, a.corpus_dev, a.corpus_hidden, controls_log)
    footnote = source.footnote()

    out = []
    out.append(header)
    out.append("")
    out.append("## (a) Summary")
    out += summary_lines
    if footnote:
        out.append("")
        out += footnote
    out.append("")
    out.append("## (b) Lowest-scoring replay cases")
    out += lowest_lines
    out.append("")
    out.append("## (c) Performance-ratio variance")
    out += perfvar_lines
    if source.regrade:
        out.append("")
        out.append(PERFVAR_NATIVE_NOTE)
    out.append("")
    out += prov_lines
    text = "\n".join(out)
    print(text)

    if a.write:
        controls_section = "\n".join([header, ""] + summary_lines
                                      + ([""] + footnote if footnote else []) + [""] + lowest_lines)
        perfvar_note = ([""] + [PERFVAR_NATIVE_NOTE]) if source.regrade else []
        perfvar_section = "\n".join([header, ""] + perfvar_lines + perfvar_note)
        _replace_marked(os.path.join(a.reports_dir, "CONTROLS.md"),
                         "controls-summary", controls_section)
        _replace_marked(os.path.join(a.reports_dir, "PERF_VARIANCE.md"),
                         "perfvar", perfvar_section)


def _replace_marked(path: str, marker: str, new_content: str) -> None:
    begin = f"<!-- {marker}:begin -->"
    end = f"<!-- {marker}:end -->"
    with open(path) as f:
        text = f.read()
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        raise SystemExit(f"markers {begin!r}/{end!r} not found in {path}")
    replacement = f"{begin}\n{new_content}\n{end}"
    text = pattern.sub(lambda _m: replacement, text, count=1)
    with open(path, "w") as f:
        f.write(text)
    print(f"wrote: {path}")


if __name__ == "__main__":
    main()
