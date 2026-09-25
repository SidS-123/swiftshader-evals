"""The grader command line. Run from anywhere with an instance:

  python3 -m evalbase.grader.cli --instance <instance.py> [--corpus DIR] [--cache DIR] [--only a,b] \\
      refcache [--force] [--trust-unstamped] [--stamp] [--recalibrate]
      grade --candidate <dir holding the artifact> [--out DIR] [--label L]
      grade-ref [--out DIR] [--label L]              # the oracle as a candidate (control)
      control <name> [--variant v] [--out DIR]       # build a control candidate and grade it
      report <report.json>                           # print a human summary
      regrade --run <dir> | --attempt <dir> [--out DIR] [--with-grade-dev]
      sweep [--owner id | --all --yes] [--older-than s] [--label l]

`--instance` defaults to
$EVALBASE_INSTANCE; `--corpus` and `--cache` default to the instance's public
corpus and its refcache.

Every container this command starts carries `io.evalbase.owner=cli-<pid>`,
and every grading command sweeps its *own* owner before it starts. It never
sweeps by label alone: a label-wide sweep is what killed a parallel attempt's
live solver container and its in-flight grading containers. Cleaning up after
an older run is an explicit operator action, `sweep --all --yes`, which lists
what it will kill before killing it.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

from ..interfaces import Instance, load_instance
from . import containers, jsonio, regrade, runner


def _replays(corpus: str, only: list[str] | None) -> list[str]:
    files = sorted(glob.glob(os.path.join(corpus, "*.json")))
    if only:
        files = [f for f in files if os.path.splitext(os.path.basename(f))[0] in only]
    if not files:
        sys.exit(f"no cases in {corpus}")
    return files


def _missing_perturbations(instance: Instance, entry_dir: str) -> list[str]:
    """Perturbation runs of record that this entry does not have.

    The directory is the evidence, not a version field: an entry written
    before `metric_version` existed is still judged by what it ran. What
    the metric version decides is the *threshold*, which `runner.entry_threshold`
    recomputes from these outputs when the entry was calibrated by an older
    metric -- no rebuild, no driver run.
    """
    return [m for m in instance.metric.perturbations
            if not os.path.isdir(os.path.join(entry_dir, m))]


def _stale_reason(instance: Instance, entry_path: str, replay_path: str, trust_unstamped: bool) -> str | None:
    """Why an existing refcache entry may not be reused, or None if it may be."""
    try:
        with open(entry_path) as f:
            rc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "unreadable refcache.json"
    missing = _missing_perturbations(instance, os.path.dirname(entry_path))
    if missing:
        return f"no {'/'.join(missing)} run (metric v{instance.metric.version})"
    cached = rc.get("replay_sha256")
    if not cached:
        return None if trust_unstamped else "no replay_sha256"
    if cached != runner.replay_identity(replay_path)["replay_sha256"]:
        return "replay changed"
    return None


def cmd_refcache(a):
    instance = a.instance
    os.makedirs(a.cache, exist_ok=True)
    if a.stamp:
        return cmd_refcache_stamp(a)
    if getattr(a, "recalibrate", False):
        return cmd_refcache_recalibrate(a)
    for f in _replays(a.corpus, a.only):
        name = os.path.splitext(os.path.basename(f))[0]
        entry = os.path.join(a.cache, name, "refcache.json")
        if not a.force and os.path.exists(entry):
            reason = _stale_reason(instance, entry, f, a.trust_unstamped)
            if reason is None:
                print(f"[skip] {name} (cached)")
                continue
            print(f"[ref] {name} stale ({reason}), rebuilding")
        t = time.time()
        info = runner.build_refcache(instance, f, a.cache, a.assets, cpus=a.cpus, owner=getattr(a, "owner", None))
        print(f"[ref] {name}: snapshots={info['snapshots']} T={info['threshold']:.4f} motion={info['motion_p90']:.4f} "
              f"noise={info['noise']:.4f} sens={info['sensitivity']:.4f} ref_wall={info['reference_wall_seconds']:.1f}s ({time.time() - t:.0f}s)")


def cmd_refcache_recalibrate(a):
    """Recompute stored thresholds from the outputs already in the cache, no driver run."""
    instance = a.instance
    version = instance.metric.version
    done, skipped, refused = [], [], []
    for f in _replays(a.corpus, a.only):
        name = os.path.splitext(os.path.basename(f))[0]
        cdir = os.path.join(a.cache, name)
        entry = os.path.join(cdir, "refcache.json")
        if not os.path.exists(entry):
            print(f"[skip] {name} (no refcache entry)")
            skipped.append(name)
            continue
        with open(entry) as fh:
            rc = json.load(fh)
        if rc.get("metric_version") == version and not a.force:
            print(f"[skip] {name} (already metric v{version})")
            skipped.append(name)
            continue
        missing = _missing_perturbations(instance, cdir)
        if missing:
            print(f"[refuse] {name} (no {'/'.join(missing)} run; needs a rebuild)")
            refused.append(name)
            continue
        try:
            fields = runner.recalibrate_entry(instance, cdir)
        except (RuntimeError, OSError, ValueError) as e:
            print(f"[refuse] {name} ({e})")
            refused.append(name)
            continue
        was, now = rc.get("threshold"), fields["threshold"]
        rc.update(fields)
        jsonio.write_json(entry, rc, strict=True)
        print(f"[recal] {name} v{rc.get('metric_version')}: T {was:.4f} -> {now:.4f} "
              f"sens={fields['sensitivity']:.4f}")
        done.append(name)
    print(f"recalibrated {len(done)}, skipped {len(skipped)}, refused {len(refused)}"
          + (f" ({', '.join(refused)})" if refused else ""))
    return {"recalibrated": done, "skipped": skipped, "refused": refused}


def cmd_refcache_stamp(a):
    """Write replay_sha256/assets into entries that predate the field, without running the driver.

    Only legitimate when the entry really was produced from the case file that
    is there now, so an entry is refused unless its recorded name, its snapshot
    count and the file's mtime all agree with the cache.
    """
    instance = a.instance
    stamped, skipped, refused = [], [], []
    for f in _replays(a.corpus, a.only):
        name = os.path.splitext(os.path.basename(f))[0]
        entry = os.path.join(a.cache, name, "refcache.json")
        if not os.path.exists(entry):
            print(f"[skip] {name} (no refcache entry)")
            skipped.append(name)
            continue
        try:
            with open(entry) as fh:
                rc = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[refuse] {name} (unreadable refcache.json: {e})")
            refused.append(name)
            continue
        missing = _missing_perturbations(instance, os.path.dirname(entry))
        if missing:
            print(f"[refuse] {name} (no {'/'.join(missing)} run; metric "
                  f"v{instance.metric.version} needs a rebuild, not a stamp)")
            refused.append(name)
            continue
        ident = runner.replay_identity(f)
        cached = rc.get("replay_sha256")
        if cached == ident["replay_sha256"]:
            print(f"[skip] {name} (already stamped)")
            skipped.append(name)
            continue
        if cached:
            print(f"[refuse] {name} (stamped for a different replay: {cached[:12]} != {ident['replay_sha256'][:12]})")
            refused.append(name)
            continue
        if rc.get("replay") != name:
            print(f"[refuse] {name} (entry records replay {rc.get('replay')!r})")
            refused.append(name)
            continue
        snapshots = runner.replay_snapshots(f)
        if rc.get("snapshots") != snapshots:
            print(f"[refuse] {name} (snapshot count {snapshots} in corpus, {rc.get('snapshots')} in cache)")
            refused.append(name)
            continue
        ledger = os.path.join(a.cache, name, "n1", "ledger.json")
        if not os.path.exists(ledger):
            print(f"[refuse] {name} (no n1/ledger.json to date the output)")
            refused.append(name)
            continue
        if os.path.getmtime(f) > os.path.getmtime(ledger):
            print(f"[refuse] {name} (replay file is newer than the cached output)")
            refused.append(name)
            continue
        rc.update(ident)
        jsonio.write_json(entry, rc, strict=True)
        print(f"[stamp] {name} sha256={ident['replay_sha256'][:12]} assets={len(ident['assets'])} snapshots={snapshots}")
        stamped.append(name)
    print(f"stamped {len(stamped)}, skipped {len(skipped)}, refused {len(refused)}"
          + (f" ({', '.join(refused)})" if refused else ""))
    return {"stamped": stamped, "skipped": skipped, "refused": refused}


def _grade(a, lib_dir: str | None, label: str):
    instance = a.instance
    out_root = a.out or os.path.join(str(instance.runs_dir), label)
    os.makedirs(out_root, exist_ok=True)
    grades = []
    for f in _replays(a.corpus, a.only):
        t = time.time()
        g = runner.grade_replay(instance, f, a.cache, a.assets, lib_dir, os.path.join(out_root, "cases"),
                                cpus=a.cpus,
                                extra_mounts=getattr(a, "extra_mounts", ()) or (),
                                extra_env=getattr(a, "extra_env", None) or {},
                                owner=getattr(a, "owner", None))
        grades.append(g)
        extra = ""
        if g.category == instance.metric.performance_category:
            ratio = g.detail.get("ratio")
            extra = f" ratio={ratio:.2f}" if ratio not in (None, float("inf")) else " ratio=inf"
        if g.category == instance.metric.procedural_category:
            failed = [c["check"] for c in g.detail.get("checks", []) if not c["ok"]]
            extra = f" failed={failed}" if failed else " all checks passed"
        print(f"[{g.category}] {g.name}: {g.score:.3f} exit={g.detail.get('exit')}{extra} ({time.time() - t:.0f}s)")
    agg = runner.aggregate(instance.metric, grades)
    report = {"label": label, "lib_dir": lib_dir or "reference", "corpus": a.corpus, "cache": a.cache,
              "image": instance.reference_image, "metric_version": instance.metric.version,
              "instance": instance.name, "aggregate": agg,
              "cases": [{"name": g.name, "category": g.category, "family": g.family, "score": g.score,
                         "detail": g.detail} for g in grades]}
    if getattr(a, "control_info", None):
        report["control"] = a.control_info
    path = os.path.join(out_root, "report.json")
    jsonio.write_json(path, report)
    print_report(report)
    print("report:", path)
    return report


def print_report(rep: dict):
    agg = rep["aggregate"]
    print(f"\n== {rep['label']} ==  overall={agg['overall']:.4f}  full_success={agg['full_success']}")
    for cat, c in agg["categories"].items():
        eff = c.get("effective_weight", c["weight"])
        note = "" if abs(eff - c["weight"]) < 1e-9 else f" -> {eff:.3f} renormalised"
        print(f"  {cat:12s} {c['score']:.4f} (weight {c['weight']}{note}, n={c['n']})")


def cmd_grade(a):
    lib = os.path.abspath(a.candidate)
    if not os.path.isdir(lib):
        sys.exit(f"{lib} is not a directory")
    _grade(a, lib, a.label or "candidate-" + time.strftime("%Y%m%d-%H%M%S"))


def cmd_grade_ref(a):
    _grade(a, None, a.label or "control-reference")


def control_config(root: str, name: str) -> dict:
    """A control's optional <controls>/<name>/control.json, or {}.

    Keys: `mounts` [{source, target, mode}] (source relative to the instance
    root or absolute, mode defaults to ro), `env` {NAME: value},
    `variants` {name: {env, mounts}} selected with --variant, `default_variant`.
    """
    path = os.path.join(root, name, "control.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        sys.exit(f"{path}: expected a JSON object")
    return cfg


def _resolve_mounts(specs: list, where: str, root: str) -> list[tuple[str, str, str]]:
    out = []
    for m in specs:
        try:
            source, target = m["source"], m["target"]
        except (TypeError, KeyError):
            sys.exit(f"{where}: each mount needs 'source' and 'target', got {m!r}")
        mode = m.get("mode", "ro")
        if mode not in ("ro", "rw"):
            sys.exit(f"{where}: mount mode must be 'ro' or 'rw', got {mode!r}")
        if not os.path.isabs(target):
            sys.exit(f"{where}: mount target must be absolute, got {target!r}")
        src = source if os.path.isabs(source) else os.path.join(root, source)
        src = os.path.abspath(src)
        if not os.path.exists(src):
            print(f"[control] warning: mount source {src} does not exist; "
                  f"the container will see an empty {target}")
        out.append((src, target, mode))
    return out


def control_run_spec(controls_root: str, name: str, variant: str | None, instance_root: str) -> dict:
    """What `control <name> [--variant v]` adds to the driver runs."""
    cfg = control_config(controls_root, name)
    variants = cfg.get("variants") or {}
    where = os.path.join(controls_root, name, "control.json")
    if variant and not variants:
        sys.exit(f"control {name} has no variants; --variant {variant} is not valid")
    if variants:
        variant = variant or cfg.get("default_variant") or sorted(variants)[0]
        if variant not in variants:
            sys.exit(f"control {name}: unknown variant {variant!r} (have: {', '.join(sorted(variants))})")
        vcfg = variants[variant] or {}
    else:
        variant, vcfg = None, {}
    env = dict(cfg.get("env") or {})
    env.update(vcfg.get("env") or {})
    mounts = _resolve_mounts(list(cfg.get("mounts") or []) + list(vcfg.get("mounts") or []),
                             where, instance_root)
    suffix = f"-{variant}" if variant else ""
    return {"name": name, "variant": variant, "suffix": suffix, "env": env, "mounts": mounts}


def cmd_control(a):
    """Build a control candidate through the instance's ControlSpec and grade it."""
    instance = a.instance
    if instance.controls is None:
        sys.exit(f"instance {instance.name} declares no controls")
    root = str(instance.controls.root)
    if not os.path.isdir(os.path.join(root, a.name)):
        sys.exit(f"no control named {a.name} in {root}")
    spec = control_run_spec(root, a.name, getattr(a, "variant", None), str(instance.root))
    tag = a.name + spec["suffix"]
    a.out = os.path.abspath(a.out or os.path.join(str(instance.runs_dir), f"control-{tag}"))
    a.extra_mounts = spec["mounts"]
    a.extra_env = spec["env"]
    a.control_info = {"name": a.name, "variant": spec["variant"], "env": spec["env"],
                      "mounts": [f"{s}:{d}:{m}" for s, d, m in spec["mounts"]]}
    os.makedirs(a.out, exist_ok=True)
    lib = instance.controls.build(a.name, os.path.join(a.out, "lib"), owner=getattr(a, "owner", None))
    _grade(a, str(lib), a.label or f"control-{tag}")


def cmd_report(a):
    with open(a.path) as f:
        print_report(json.load(f))


def cmd_regrade(a):
    """Rescore a graded run, or a whole harness attempt, from its cached outputs."""
    if bool(a.run) == bool(a.attempt):
        sys.exit("regrade needs exactly one of --run <graded run dir> or --attempt <attempt dir>")
    if a.attempt:
        out = a.out or os.path.join(os.path.abspath(a.attempt), f"regrade-v{a.instance.metric.version}")
        return regrade.regrade_attempt(a.instance, a.attempt, a.corpus, a.cache, out,
                                       include_dev=a.with_grade_dev)
    if not os.path.exists(os.path.join(a.run, "report.json")):
        sys.exit(f"{a.run} has no report.json (point --run at a graded run directory)")
    return regrade.regrade_run(a.instance, a.run, a.corpus, a.cache, a.out, only=a.only)


def cmd_sweep(a):
    """Kill leftover containers: one owner's by default, all of them on request."""
    older_than = a.older_than if a.older_than is not None else (3600.0 if a.all_owners else 0.0)
    if not a.all_owners:
        owner = containers.resolve_owner(a.owner)
        killed = containers.kill_stale_containers(owner, label=a.label, older_than_s=older_than)
        if not killed:
            print(f"[sweep] nothing owned by {owner} is left")
        else:
            print(f"[sweep] killed {len(killed)} container(s) owned by {owner}")
        return killed
    doomed = [c for c in containers.list_managed_containers(a.label)
              if not older_than or containers._age_seconds(c["created_at"]) >= older_than]
    if not doomed:
        print(f"[sweep] nothing labelled {a.label} is left"
              + (f" that is older than {older_than:g}s" if older_than else ""))
        return []
    print(f"[sweep] --all would kill {len(doomed)} container(s) labelled {a.label}"
          + (f", older than {older_than:g}s" if older_than else "") + ":")
    for c in doomed:
        print(f"  {c['id']}  {c['name'] or '(no name)'}  {c['image']}  {c['status']}  "
              f"owner={c.get('owner') or 'none'}")
    sys.stdout.flush()
    if not a.yes:
        sys.exit("[sweep] refusing to kill another owner's containers without --yes")
    killed = containers.kill_stale_containers(label=a.label, older_than_s=older_than, all_owners=True)
    print(f"[sweep] killed {len(killed)} container(s)")
    return killed


def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m evalbase.grader.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance", default=None, help="path to the instance file (default $EVALBASE_INSTANCE)")
    p.add_argument("--corpus", default=None, help="case directory (default: the instance's public corpus)")
    p.add_argument("--assets", default=None, help="asset directory (default: the instance's)")
    p.add_argument("--cache", default=None, help="refcache directory (default: the instance's)")
    p.add_argument("--cpus", default=None, help="cpu limit handed to the driver")
    p.add_argument("--only", default=None, help="comma-separated case names to include")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("refcache")
    s.add_argument("--force", action="store_true", help="rebuild every entry, even a matching one")
    s.add_argument("--trust-unstamped", action="store_true",
                   help="reuse entries with no replay_sha256 instead of treating them as stale")
    s.add_argument("--recalibrate", action="store_true",
                   help="recompute stored thresholds from the outputs already in the cache "
                        "under the metric of record, without running the driver")
    s.add_argument("--stamp", action="store_true",
                   help="write replay_sha256/assets into existing entries without running the driver")
    s.set_defaults(fn=cmd_refcache)
    s = sub.add_parser("grade"); s.add_argument("--candidate", required=True, help="directory holding the candidate artifact")
    s.add_argument("--out"); s.add_argument("--label"); s.set_defaults(fn=cmd_grade)
    s = sub.add_parser("grade-ref"); s.add_argument("--out"); s.add_argument("--label"); s.set_defaults(fn=cmd_grade_ref)
    s = sub.add_parser("control"); s.add_argument("name"); s.add_argument("--out"); s.add_argument("--label")
    s.add_argument("--variant", default=None, help="for a control whose control.json declares variants")
    s.set_defaults(fn=cmd_control)
    s = sub.add_parser("report"); s.add_argument("path"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("regrade")
    s.add_argument("--run", default=None, help="a graded run directory (has report.json and cases/)")
    s.add_argument("--attempt", default=None, help="a harness attempt directory")
    s.add_argument("--with-grade-dev", action="store_true", dest="with_grade_dev")
    s.add_argument("--out", default=None, help="where to write (default <run>/regrade-v<version>/)")
    s.set_defaults(fn=cmd_regrade)
    s = sub.add_parser("sweep")
    s.add_argument("--label", default=containers.LABEL)
    s.add_argument("--owner", default=None, help="sweep this owner's containers (default: this process)")
    s.add_argument("--all", action="store_true", dest="all_owners", help="every managed container; needs --yes")
    s.add_argument("--yes", action="store_true", help="confirm an --all sweep")
    s.add_argument("--older-than", type=float, default=None, dest="older_than",
                   help="spare containers younger than this many seconds (default: 3600 with --all, 0 otherwise)")
    s.set_defaults(fn=cmd_sweep)
    return p


def main(argv=None, instance: Instance | None = None):
    p = build_parser()
    a = p.parse_args(argv)
    a.only = [x for x in a.only.split(",") if x] if a.only else None
    a.owner = containers.resolve_owner(getattr(a, "owner", None))
    if a.cmd != "sweep":
        a.instance = instance or load_instance(a.instance)
        a.corpus = a.corpus or str(a.instance.corpus_public)
        a.assets = a.assets or str(a.instance.assets)
        a.cache = a.cache or str(a.instance.refcache)
    if a.cmd in ("refcache", "grade", "grade-ref", "control"):
        # Nothing *this process* left behind may still be burning CPU while we
        # measure. Another run's containers are not ours to kill.
        containers.kill_stale_containers(a.owner)
    return a.fn(a)


if __name__ == "__main__":
    main()
