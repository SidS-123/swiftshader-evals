"""The attempt runner.

  python3 -m evalbase.harness.run --instance <instance.py> \\
      --harness claude-code|codex|opencode|openrouter --model <id> \\
      --budget-hours 12 --checkpoint-minutes 15 [--max-cost-usd N] [--max-tokens N] \\
      [--reasoning low|medium|high|xhigh] [--stop-policy budget|submit] \\
      [--rate-limit-wait HOURS] [--corpus-hidden DIR] [--refcache-hidden DIR] \\
      [--sandbox docker|none] --out runs/attempts/<name>

  python3 -m evalbase.harness.run --instance X resume runs/attempts/<name>
  python3 -m evalbase.harness.run --instance X grade-checkpoints runs/attempts/<name> \\
      [--every MINUTES] [--auto-only] [--dedupe] [--max N] [--only 3,7|final] [--dry-run]
  python3 -m evalbase.harness.run --instance X smoke --harness <h> [--sandbox docker|none]
  python3 -m evalbase.harness.run --instance X report runs/attempts/<name>
  python3 -m evalbase.harness.run --instance X versions
  python3 -m evalbase.harness.run cleanup --owner <attempt-name>

`--instance` defaults to
$EVALBASE_INSTANCE; `--sandbox none` is test-only and never a measurement.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

from ..interfaces import load_instance
from . import attempt as att
from . import tools, workspace as ws

COMMANDS = ("attempt", "resume", "grade-checkpoints", "smoke", "report", "cleanup", "versions")


def brief(manifest: dict) -> dict:
    keys = ("attempt", "instance", "harness", "model_requested", "models_served", "simulated_model",
            "stop_policy", "sandbox", "status", "stop_reason", "solver_seconds", "wall_seconds",
            "rate_limit_wait_seconds", "requests", "usage", "tool_calls",
            "final_source_sha256", "grade_report_path", "grade", "graded_corpus",
            "graded_refcache", "measurement_valid", "measurement_invalid_reasons",
            "infrastructure_incidents", "model_final_events", "submission")
    return {k: manifest[k] for k in keys if k in manifest}


def _options_from_args(a) -> att.Options:
    return att.Options(harness=a.harness, model=a.model, budget_hours=a.budget_hours,
                       checkpoint_minutes=a.checkpoint_minutes, max_cost_usd=a.max_cost_usd,
                       max_tokens=a.max_tokens, reasoning=a.reasoning,
                       stop_policy=a.stop_policy, rate_limit_wait_hours=a.rate_limit_wait,
                       corpus_hidden=a.corpus_hidden, refcache_hidden=a.refcache_hidden,
                       seed=a.seed, cpus=a.cpus, memory=a.memory, sandbox=a.sandbox,
                       context_chars=a.context_chars,
                       max_output_tokens=a.max_output_tokens).validate()


def _stored_options(run_dir: Path) -> att.Options:
    config = json.loads((run_dir / "agent-config.json").read_text())
    stored = config.get("options") or config
    return att.Options(**{f.name: stored[f.name] for f in fields(att.Options) if f.name in stored})


def cmd_attempt(a):
    instance = load_instance(a.instance)
    options = _options_from_args(a)
    out = Path(a.out).resolve()
    if options.harness == "openrouter":
        from .openrouter import Runner
        manifest = Runner(instance, options, out).run()
    else:
        from .cli_runner import CLIAttempt
        manifest = CLIAttempt(instance, options, out).run()
    print(json.dumps(brief(manifest), indent=1, default=str))


def cmd_resume(a):
    instance = load_instance(a.instance)
    run_dir = Path(a.attempt).resolve()
    options = _stored_options(run_dir).validate()
    if options.harness == "openrouter":
        from .openrouter import Runner
        manifest = Runner(instance, options, run_dir, resume=True).run()
    else:
        from .cli_runner import CLIAttempt
        print("note: the CLI harnesses do not resume a conversation; the workspace, checkpoints "
              "and remaining budget are preserved and a fresh CLI session continues the attempt.",
              file=sys.stderr)
        manifest = CLIAttempt(instance, options, run_dir, resume=True).run()
    print(json.dumps(brief(manifest), indent=1, default=str))


def _clock(seconds) -> str:
    if seconds is None:
        return "   ?   "
    seconds = int(seconds)
    return f"{seconds // 3600:>3}h{seconds % 3600 // 60:02d}m"


def cmd_grade_checkpoints(a):
    run_dir = Path(a.attempt).resolve()
    options = None
    config_path = run_dir / "agent-config.json"
    if config_path.is_file():
        options = _stored_options(run_dir)
        if a.corpus_hidden:
            options.corpus_hidden = a.corpus_hidden
        if a.refcache_hidden:
            options.refcache_hidden = a.refcache_hidden
    elif not a.dry_run:
        raise ValueError(f"no agent-config.json in {run_dir}")
    instance = None if a.dry_run and not a.instance and not config_path.is_file() else load_instance(
        a.instance or (json.loads(config_path.read_text()).get("instance") if config_path.is_file() else None))
    indices = att.parse_checkpoint_selection(a.only)
    results = att.grade_checkpoints(instance, run_dir, options, indices=indices, every=a.every,
                                    auto_only=a.auto_only, dedupe=a.dedupe,
                                    max_points=a.max_points, dry_run=a.dry_run)
    graded = [r for r in results if "skipped" not in r]
    rebuilt = [r for r in graded if not r.get("grade_reused")]
    for row in results:
        head = (f"[{row['id']:>6} {_clock(row['elapsed_seconds'])} {row['trigger']:<5} "
                f"{(row['source_sha256'] or '')[:12]}]")
        if "skipped" in row:
            if a.dry_run:
                print(f"{head} skipped: {row['skipped']}")
        elif row.get("grade_reused"):
            print(f"{head} same source as {row['reused_from']}: grade reused")
        elif a.dry_run:
            print(f"{head} would grade")
        elif "error" in row:
            print(f"{head} {row['error']}")
        else:
            print(f"{head} overall={row['overall']:.4f} {'BUILD FAILURE' if row['build_failure'] else ''}")
    print(f"{len(results)} checkpoints on disk: {len(graded)} curve points "
          f"({len(rebuilt)} to rebuild and grade, {len(graded) - len(rebuilt)} reused), "
          f"{len(results) - len(graded)} skipped")
    if a.dry_run:
        print("dry run: nothing was built, graded or written")
    else:
        print("curve:", run_dir / "checkpoint-curve.json")


def cmd_smoke(a):
    from .smoke import smoke
    instance = load_instance(a.instance)
    out = Path(a.out).resolve() if a.out else instance.runs_dir / "smoke" / a.harness
    smoke(instance, a.harness, out, budget_seconds=a.budget_seconds, sandbox=a.sandbox)


def cmd_report(a):
    run_dir = Path(a.attempt).resolve()
    manifest = json.loads((run_dir / "attempt.json").read_text())
    print(json.dumps(brief(manifest), indent=1, default=str))
    for row in manifest.get("checkpoints", []):
        print(f"  checkpoint {row.get('index')}: {row.get('source_sha256', '')[:12]} "
              f"@{row.get('elapsed_seconds')}s {row.get('note', '')[:60]}")
    curve = run_dir / "checkpoint-curve.json"
    if curve.is_file():
        print("checkpoint curve:", json.dumps(json.loads(curve.read_text())))


def cmd_cleanup(a):
    """Remove one owner's leftover containers. There is no label-wide option here."""
    if not a.owner:
        sys.exit("cleanup needs --owner <attempt-name> (or cli-<pid>). To remove every "
                 "managed container regardless of owner, run "
                 "`python3 -m evalbase.grader.cli sweep --all --yes`.")
    print(json.dumps(tools.cleanup_containers(a.owner)))


def cmd_versions(a):
    instance = load_instance(a.instance)
    record = ws.version_record(instance)
    print(json.dumps({k: v for k, v in record.items() if k != "files"}, indent=1))


def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m evalbase.harness.run", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance", default=None, help="path to the instance file (default $EVALBASE_INSTANCE)")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("attempt", help="run one attempt (the default command)")
    a.add_argument("--harness", required=True, choices=list(att.HARNESSES))
    a.add_argument("--model", required=True, help="exact provider model id; passed through verbatim")
    a.add_argument("--out", required=True)
    a.add_argument("--budget-hours", type=float, default=12.0)
    a.add_argument("--checkpoint-minutes", type=float, default=15.0)
    a.add_argument("--max-cost-usd", type=float, default=0.0, help="openrouter only")
    a.add_argument("--max-tokens", type=int, default=0, help="openrouter only")
    a.add_argument("--reasoning", default=None, choices=list(att.REASONING))
    a.add_argument("--stop-policy", default="budget", choices=list(att.STOP_POLICIES),
                   help="budget: a final answer never ends the attempt (default). "
                        "submit: a confirmed final answer ends it; the budget is a hard cap. "
                        "CLI harnesses only.")
    a.add_argument("--rate-limit-wait", type=float, default=6.0, metavar="HOURS",
                   help="CLI harnesses: how long to wait out a provider usage limit (default 6)")
    a.add_argument("--corpus-hidden", default=None)
    a.add_argument("--refcache-hidden", default=None)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--cpus", default="4")
    a.add_argument("--memory", default="8g")
    a.add_argument("--sandbox", default="docker", choices=list(att.SANDBOXES),
                   help="docker (the measurement configuration) or none (test-only: no isolation)")
    a.add_argument("--context-chars", type=int, default=400000, help="openrouter compaction threshold")
    a.add_argument("--max-output-tokens", type=int, default=32000, help="openrouter only")
    a.set_defaults(fn=cmd_attempt)

    r = sub.add_parser("resume", help="continue an interrupted attempt")
    r.add_argument("attempt")
    r.set_defaults(fn=cmd_resume)

    g = sub.add_parser("grade-checkpoints", help="rebuild and grade an attempt's checkpoints")
    g.add_argument("attempt")
    g.add_argument("--corpus-hidden", default=None)
    g.add_argument("--refcache-hidden", default=None)
    g.add_argument("--only", default=None, help="comma-separated checkpoints: index (3), id (000012) or final")
    g.add_argument("--every", type=float, default=None, metavar="MINUTES",
                   help="keep the last checkpoint of each wall-time bucket this wide")
    g.add_argument("--auto-only", action="store_true", help="grade only the harness's automatic checkpoints")
    g.add_argument("--dedupe", action="store_true",
                   help="do not rebuild a checkpoint whose source sha256 equals the previously selected one")
    g.add_argument("--max", type=int, default=None, dest="max_points", metavar="N",
                   help="thin the selection evenly to at most N checkpoints")
    g.add_argument("--dry-run", action="store_true", help="print the selection; build, grade and write nothing")
    g.set_defaults(fn=cmd_grade_checkpoints)

    s = sub.add_parser("smoke", help="no-key end-to-end check with a simulated model")
    s.add_argument("--harness", required=True, choices=list(att.HARNESSES))
    s.add_argument("--out", default=None)
    s.add_argument("--budget-seconds", type=int, default=120)
    s.add_argument("--sandbox", default="docker", choices=list(att.SANDBOXES))
    s.set_defaults(fn=cmd_smoke)

    rep = sub.add_parser("report", help="print an attempt summary")
    rep.add_argument("attempt")
    rep.set_defaults(fn=cmd_report)

    c = sub.add_parser("cleanup", help="remove one owner's leftover harness containers")
    c.add_argument("--owner", default=None, help="the attempt name (or cli-<pid>) whose containers to remove")
    c.set_defaults(fn=cmd_cleanup)

    v = sub.add_parser("versions", help="print the task/harness version record")
    v.set_defaults(fn=cmd_versions)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `--instance X --harness ...` with no subcommand means `attempt`
    head = argv[:]
    if head[:1] == ["--instance"] and len(head) > 2:
        head = head[2:]
    if not head or (head[0].startswith("-") and head[0] not in ("-h", "--help")):
        insert_at = 2 if argv[:1] == ["--instance"] else 0
        argv = argv[:insert_at] + ["attempt"] + argv[insert_at:]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 2
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("interrupted; the attempt state is preserved. Use `resume` when the recorded "
              "request outcome is known.", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError, OSError, AssertionError, subprocess.SubprocessError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
