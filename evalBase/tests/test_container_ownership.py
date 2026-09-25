"""Containers belong to somebody, and only that somebody may sweep them.

No
Docker: subprocess and the docker helper are replaced and the argv is
inspected. The toy driver's Docker path stands in for an instance's driver.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from evalbase.grader import cli, containers, runner
from evalbase.harness import attempt as att, tools
from evalbase.harness import workspace as ws
from evalbase.interfaces import DriveResult
from test_refcache_identity import args, stub_driver, write_replay


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_docker(monkeypatch, handler):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return handler(list(cmd))

    monkeypatch.setattr(containers.subprocess, "run", fake_run)
    return calls


def _labels(argv) -> list[str]:
    return [argv[i + 1] for i, x in enumerate(argv) if x == "--label"]


def _toy_docker_driver(toy):
    from instance import ToyDriver
    return ToyDriver(toy.root, sandbox="docker")


# ------------------------------------------------------- the owner id itself

def test_an_owner_defaults_to_this_process_and_never_to_a_wildcard():
    assert containers.process_owner() == f"cli-{os.getpid()}"
    assert containers.resolve_owner(None) == containers.process_owner()
    assert containers.resolve_owner("   ") == containers.process_owner()
    assert containers.resolve_owner("opus5-claude-code-01") == "opus5-claude-code-01"
    assert containers.owner_label("att-1") == "io.evalbase.owner=att-1"
    assert runner.owner_label("att-1") == containers.owner_label("att-1")


@pytest.mark.parametrize("bad", ["a b", "x,y", "-leading", "a" * 200, "sh$(id)", ""])
def test_an_owner_that_could_forge_a_label_or_a_filter_is_refused(bad):
    if bad == "":
        assert containers.resolve_owner(bad) == containers.process_owner()
        return
    with pytest.raises(ValueError):
        containers.resolve_owner(bad)


# --------------------------------------------------- the owner label on argv

def test_every_driver_container_carries_the_managed_label_and_its_owner(monkeypatch, tmp_path, toy):
    calls = _fake_docker(monkeypatch, lambda cmd: Done())
    driver = _toy_docker_driver(toy)
    (tmp_path / "r.json").write_text("{}")
    driver.run(str(tmp_path / "r.json"), str(tmp_path / "out"), str(tmp_path / "assets"), owner="opus5-claude-code-01")
    cmd = calls[0]
    assert cmd[:2] == ["docker", "run"]
    assert _labels(cmd) == [containers.LABEL, "io.evalbase.owner=opus5-claude-code-01"]
    assert max(i for i, x in enumerate(cmd) if x == "--label") < cmd.index(driver.image)
    assert cmd[cmd.index("--name") + 1].startswith("evalbase-drive-")


def test_a_drive_with_no_owner_is_owned_by_this_process_not_by_everybody(monkeypatch, tmp_path, toy):
    calls = _fake_docker(monkeypatch, lambda cmd: Done())
    (tmp_path / "r.json").write_text("{}")
    _toy_docker_driver(toy).run(str(tmp_path / "r.json"), str(tmp_path / "out"), str(tmp_path / "assets"))
    assert _labels(calls[0])[1] == f"io.evalbase.owner=cli-{os.getpid()}"


def test_the_refcache_runs_carry_the_owner(monkeypatch, tmp_path, toy):
    stub_driver(monkeypatch)
    stubbed, seen = runner.drive, []

    def spy(*a, **kw):
        seen.append(kw.get("owner"))
        return stubbed(*a, **kw)

    monkeypatch.setattr(runner, "drive", spy)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    runner.build_refcache(toy, str(write_replay(corpus, "case")), str(cache), str(corpus), owner="att-9")
    assert len(seen) == 2 + len(toy.metric.perturbations) and set(seen) == {"att-9"}


def test_every_harness_container_carries_the_attempt_as_its_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "docker", lambda *a, **k: calls.append(list(a)) or Done(stdout=""))
    tools.docker_run("oracle", "--rm", "img", "drive", owner="att-1")
    argv = calls[0]
    assert _labels(argv) == [tools.LABEL, "io.evalbase.owner=att-1"]
    assert argv[0] == "run" and "--name" in argv


def test_the_solver_sandbox_carries_the_attempt_as_its_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "docker", lambda *a, **k: Done(stdout=""))
    sandbox = tools.SolverSandbox(tmp_path, image="img", owner="att-1")
    assert _labels(sandbox.argv) == [tools.LABEL, "io.evalbase.owner=att-1"]
    assert sandbox.owner == "att-1" and sandbox.name.startswith("evalbase-solver-")
    assert sandbox.argv[sandbox.argv.index("--user") + 1] == "1000:1000"
    assert sandbox.argv[sandbox.argv.index("--network") + 1] == "none"


def test_even_the_throwaway_tree_removal_container_has_an_owner(tmp_path):
    target = tmp_path / "grade" / "build"
    target.mkdir(parents=True)
    argv = ws.container_remove_command(target, image="img:1", owner="att-1")
    assert _labels(argv) == ["io.evalbase.managed=1", "io.evalbase.owner=att-1"]
    assert _labels(ws.container_remove_command(target, image="img:1"))[1] == f"io.evalbase.owner=cli-{os.getpid()}"


def test_liveness_is_read_from_docker_inspect(monkeypatch, tmp_path):
    answers = {"v": "true\n"}
    monkeypatch.setattr(tools, "docker", lambda *a, **k: Done(stdout=answers["v"] if a[0] == "inspect" else ""))
    sandbox = tools.SolverSandbox(tmp_path, image="img", owner="att-1")
    assert sandbox.alive() is True
    answers["v"] = "false\n"
    assert sandbox.alive() is False
    answers["v"] = "Error: No such object: evalbase-solver-x\n"
    assert sandbox.alive() is False


def test_the_grading_of_an_attempt_passes_its_owner_all_the_way_down(monkeypatch, tmp_path, toy):
    seen = {}

    def note(key, owner, result):
        seen[key] = owner
        return result

    monkeypatch.setattr(tools, "build_export", lambda instance, source, out, **k: note(
        "build", k.get("owner"), {"build_success": True, "lib_dir": str(tmp_path / "lib")}))
    monkeypatch.setattr(runner, "grade_replay", lambda *a, **k: note(
        "grade", k.get("owner"), runner.ReplayGrade("case", "replay", "fam", 1.0, {})))
    monkeypatch.setattr(runner, "aggregate", lambda spec, grades: {"overall": 1.0, "categories": {}, "full_success": True})
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "case.json").write_text("{}")
    tools.build_and_grade(toy, tmp_path / "src", tmp_path / "out", corpus=corpus, cache=tmp_path / "cache",
                          assets=tmp_path / "assets", label="att-1", owner="att-1")
    assert seen == {"build": "att-1", "grade": "att-1"}


# ---------------------------------------------- a sweep reaches only its own

PS_MINE = ("ab12\tevalbase-drive-1\timg\tUp 2 hours\t2026-09-17 09:47:39 +0200 CEST\tmine\n")
PS_ALL = (PS_MINE
          + "cd34\tevalbase-solver-9\tsolver\tUp 3 minutes\t2026-09-17 20:40:00 +0200 CEST\tother\n"
          + "ef56\tevalbase-drive-2\timg\tUp 11 hours\t2026-09-17 09:00:00 +0200 CEST\t\n")


def _ps_honouring_filters(monkeypatch):
    def handler(cmd):
        if cmd[1] != "ps":
            return Done()
        wanted = {cmd[i + 1] for i, x in enumerate(cmd) if x == "--filter"}
        owner = next((w.split("=", 2)[2] for w in wanted if w.startswith("label=" + containers.OWNER_KEY + "=")), None)
        rows = PS_ALL.splitlines(keepends=True)
        if owner is not None:
            rows = [r for r in rows if r.rstrip("\n").split("\t")[5] == owner]
        return Done(stdout="".join(rows))
    return _fake_docker(monkeypatch, handler)


def test_a_sweep_asks_docker_for_its_own_owner_only(monkeypatch):
    calls = _ps_honouring_filters(monkeypatch)
    killed = containers.kill_stale_containers("mine")
    ps = calls[0]
    filters = [ps[i + 1] for i, x in enumerate(ps) if x == "--filter"]
    assert filters == [f"label={containers.LABEL}", "label=io.evalbase.owner=mine"]
    assert [c["id"] for c in killed] == ["ab12"]
    assert [c for c in calls if c[1] == "kill"] == [["docker", "kill", "ab12"]]


def test_a_sweep_never_touches_another_attempts_container(monkeypatch):
    calls = _ps_honouring_filters(monkeypatch)
    containers.kill_stale_containers("mine")
    assert not [c for c in calls if c[1] in ("kill", "rm") and "cd34" in c]


def test_a_sweep_leaves_a_container_from_before_owners_alone(monkeypatch):
    calls = _ps_honouring_filters(monkeypatch)
    containers.kill_stale_containers("mine")
    assert not [c for c in calls if c[1] in ("kill", "rm") and "ef56" in c]


def test_the_harness_cleanup_filters_on_both_labels_and_removes_only_those(monkeypatch):
    calls = []

    def fake_docker(*a, **k):
        calls.append(list(a))
        return Done(stdout="ab12\n" if a[0] == "ps" else "")

    monkeypatch.setattr(tools, "docker", fake_docker)
    result = tools.cleanup_containers("mine")
    ps = calls[0]
    assert [ps[i + 1] for i, x in enumerate(ps) if x == "--filter"] == ["label=" + tools.LABEL, "label=io.evalbase.owner=mine"]
    assert calls[1] == ["rm", "-f", "ab12"]
    assert result == {"owner": "mine", "containers_removed": 1}


def test_the_attempt_sweeps_are_scoped_and_cannot_be_called_without_an_owner(monkeypatch):
    seen = []
    monkeypatch.setattr(containers, "kill_stale_containers", lambda owner=None, **k: seen.append(owner) or [])
    att.sweep_containers("attempt-start", "att-1")
    assert seen == ["att-1"]
    with pytest.raises(TypeError):
        att.sweep_containers("attempt-start")


def test_a_new_attempts_manifest_declares_its_owner_and_sweeps_only_that(monkeypatch, toy):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    seen = []
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: seen.append((stage, owner)))
    options = att.Options(harness="claude-code", model="m").validate()
    manifest = att.base_manifest(toy, options, simulated=True, name="att-1")
    assert manifest["owner"] == "att-1" and manifest["instance"] == "toy"
    assert seen == [("attempt-start", "att-1")]
    assert manifest["infrastructure_incidents"] == []


# ------------------------------------------------------- the explicit sweep

def _old_ps(monkeypatch):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=PS_ALL) if cmd[1] == "ps" else Done())
    monkeypatch.setattr(containers, "_age_seconds", lambda created: 11 * 3600)
    return calls


def test_the_explicit_all_sweep_refuses_without_yes_and_says_what_it_would_kill(monkeypatch, capsys):
    calls = _old_ps(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["sweep", "--all"])
    assert "--yes" in str(exit_info.value)
    out = capsys.readouterr().out
    assert "would kill 3 container(s)" in out
    for name in ("ab12", "cd34", "ef56", "owner=other", "owner=none"):
        assert name in out
    assert not [c for c in calls if c[1] in ("kill", "rm")]


def test_the_explicit_all_sweep_with_yes_kills_every_owner(monkeypatch, capsys):
    calls = _old_ps(monkeypatch)
    cli.main(["sweep", "--all", "--yes"])
    assert [c for c in calls if c[1] == "kill"] == [["docker", "kill", "ab12"], ["docker", "kill", "cd34"], ["docker", "kill", "ef56"]]
    assert "killed 3 container(s)" in capsys.readouterr().out


def test_the_explicit_all_sweep_spares_anything_younger_than_an_hour(monkeypatch, capsys):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=PS_ALL) if cmd[1] == "ps" else Done())
    monkeypatch.setattr(containers, "_age_seconds", lambda created: 60 if "20:40" in created else 11 * 3600)
    cli.main(["sweep", "--all", "--yes"])
    killed = [c[2] for c in calls if c[1] == "kill"]
    assert killed == ["ab12", "ef56"] and "cd34" not in killed
    assert "older than 3600s" in capsys.readouterr().out


def test_a_plain_sweep_is_scoped_to_this_process(monkeypatch, capsys):
    calls = _ps_honouring_filters(monkeypatch)
    cli.main(["sweep"])
    assert f"label=io.evalbase.owner=cli-{os.getpid()}" in calls[0]
    assert not [c for c in calls if c[1] == "kill"]
    assert "nothing owned by" in capsys.readouterr().out


def test_a_grading_command_sweeps_its_own_owner_before_it_starts(monkeypatch, toy):
    swept = []
    monkeypatch.setattr(containers, "kill_stale_containers", lambda owner=None, **k: swept.append(owner) or [])
    monkeypatch.setattr(cli, "cmd_grade", lambda a: swept.append("graded"))
    cli.main(["grade", "--candidate", "/nowhere"], instance=toy)
    assert swept == [f"cli-{os.getpid()}", "graded"]


# ------------------------------------------- the returncode in the record

def _graded(monkeypatch, tmp_path, toy, returncode, *, ledger=None, timed_out=False):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir(exist_ok=True)
    replay = write_replay(corpus, "case")
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    stubbed = runner.drive

    def drive_with(instance, replay_path, outdir, assets_dir, lib_dir=None, sample_mult=1, **kw):
        res = stubbed(instance, replay_path, outdir, assets_dir, lib_dir, sample_mult, **kw)
        return DriveResult(res.outdir, {**res.ledger, **(ledger or {})}, returncode, res.wall_seconds, timed_out, "")

    monkeypatch.setattr(runner, "drive", drive_with)
    return runner.grade_replay(toy, str(replay), str(cache), str(corpus), None, str(tmp_path / "out")).detail


def test_a_container_killed_by_a_sweep_is_recorded_as_a_sigkill(monkeypatch, tmp_path, toy):
    detail = _graded(monkeypatch, tmp_path, toy, 137, ledger={"exit": "died"})
    assert detail["exit"] == "crash" and detail["returncode"] == 137 and detail["signal"] == "SIGKILL"


def test_a_segfault_is_distinguishable_from_that_kill(monkeypatch, tmp_path, toy):
    detail = _graded(monkeypatch, tmp_path, toy, 139, ledger={"exit": "died"})
    assert (detail["returncode"], detail["signal"]) == (139, "SIGSEGV")
    abort = _graded(monkeypatch, tmp_path, toy, 134, ledger={"exit": "died"})
    assert (abort["returncode"], abort["signal"]) == (134, "SIGABRT")


def test_an_ordinary_failure_and_a_timeout_name_no_signal(monkeypatch, tmp_path, toy):
    ok = _graded(monkeypatch, tmp_path, toy, 0)
    assert (ok["exit"], ok["returncode"], ok["signal"]) == ("ok", 0, None)
    err = _graded(monkeypatch, tmp_path, toy, 1, ledger={"exit": "driver_error"})
    assert (err["returncode"], err["signal"]) == (1, None)
    late = _graded(monkeypatch, tmp_path, toy, -1, timed_out=True)
    assert (late["exit"], late["returncode"], late["signal"]) == ("timeout", -1, None)


def test_signal_name_covers_the_two_encodings():
    assert containers.signal_name(-9) == "SIGKILL"
    assert containers.signal_name(128 + 11) == "SIGSEGV"
    assert containers.signal_name(0) is None and containers.signal_name(2) is None
    assert containers.signal_name(None) is None


# ----------------------------------------------------- losing the sandbox

class _FakeDocker:
    def __init__(self, running=True):
        self.running = running
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append(list(a))
        if a[0] == "inspect":
            return Done(stdout=("true" if self.running else "false") + "\n")
        if a[0] == "run":
            self.running = True
        return Done(stdout="")


def _sandbox(monkeypatch, tmp_path, running=True):
    fake = _FakeDocker(running=True)
    monkeypatch.setattr(tools, "docker", fake)
    incidents = []
    sandbox = tools.SolverSandbox(tmp_path, image="img", owner="att-1", on_incident=incidents.append)
    fake.running = running
    fake.calls.clear()
    return sandbox, fake, incidents


def test_a_shell_call_on_a_container_that_is_gone_restarts_it_and_says_so(monkeypatch, tmp_path):
    sandbox, fake, incidents = _sandbox(monkeypatch, tmp_path, running=False)
    monkeypatch.setattr(tools.SolverSandbox, "_exec", lambda self, *a: pytest.fail("no exec against a dead container"))
    result = sandbox.shell("make -C /task")
    assert result["sandbox_restarted"] is True and result["exit_code"] != 0
    assert "restarted it" in result["output"] and "/task" in result["output"]
    assert ["rm", "-f", sandbox.name] in fake.calls
    run = [c for c in fake.calls if c[0] == "run"][0]
    assert run == sandbox.argv[1:] and sandbox.restarts == 1


def test_the_loss_is_recorded_as_an_incident_with_when_and_what_was_seen(monkeypatch, tmp_path):
    sandbox, fake, incidents = _sandbox(monkeypatch, tmp_path, running=False)
    monkeypatch.setattr(tools.SolverSandbox, "_exec", lambda self, *a: None)
    sandbox.shell("true")
    assert len(incidents) == 1
    entry = incidents[0]
    assert entry["kind"] == "sandbox_lost" and entry["container"] == sandbox.name and entry["owner"] == "att-1"
    assert entry["restarted"] is True and entry["restart_error"] is None
    assert entry["time"].endswith("Z") and "not running" in entry["observed"]


def _removed_during(fake):
    def _exec(self, *a):
        fake.running = False
        return {"exit_code": 1, "output": "Error response from daemon: No such container: x",
                "truncated": False, "output_bytes": 46, "timed_out": False}
    return _exec


def test_a_container_removed_mid_call_is_caught_from_dockers_own_error(monkeypatch, tmp_path):
    sandbox, fake, incidents = _sandbox(monkeypatch, tmp_path, running=True)
    monkeypatch.setattr(tools.SolverSandbox, "_exec", _removed_during(fake))
    result = sandbox.shell("gcc -c main.c")
    assert result["sandbox_restarted"] is True
    assert "No such container" in result["output"] and "restarted it" in result["output"]
    assert [i["kind"] for i in incidents] == ["sandbox_lost"]


def test_a_models_own_output_saying_is_not_running_is_not_a_lost_container(monkeypatch, tmp_path):
    sandbox, fake, incidents = _sandbox(monkeypatch, tmp_path, running=True)
    output = "checking the daemon... the service is not running\n"
    monkeypatch.setattr(tools.SolverSandbox, "_exec", lambda self, *a: {
        "exit_code": 1, "output": output, "truncated": False, "output_bytes": len(output), "timed_out": False})
    result = sandbox.shell("./run-tests.sh")
    assert result["output"] == output and "sandbox_restarted" not in result
    assert incidents == [] and sandbox.restarts == 0


def test_an_ordinary_command_failure_is_not_an_incident(monkeypatch, tmp_path):
    sandbox, fake, incidents = _sandbox(monkeypatch, tmp_path, running=True)
    monkeypatch.setattr(tools.SolverSandbox, "_exec", lambda self, *a: {
        "exit_code": 2, "output": "main.c:1: error: expected ';'", "truncated": False, "output_bytes": 28, "timed_out": False})
    result = sandbox.shell("make")
    assert result["exit_code"] == 2 and incidents == [] and sandbox.restarts == 0


def test_a_restart_that_fails_is_still_recorded_and_still_reported(monkeypatch, tmp_path):
    sandbox, fake, incidents = _sandbox(monkeypatch, tmp_path, running=False)

    def refuse(*a, **k):
        fake.calls.append(list(a))
        if a[0] == "inspect":
            return Done(stdout="false\n")
        if a[0] == "run":
            raise RuntimeError("docker command failed: no space left on device")
        return Done(stdout="")

    monkeypatch.setattr(tools, "docker", refuse)
    result = sandbox.shell("true")
    assert result["sandbox_restarted"] is False and "could not restart it" in result["output"]
    assert incidents[0]["restarted"] is False and "no space left" in incidents[0]["restart_error"]


def test_a_session_writes_the_incident_into_its_manifest(tmp_path, toy):
    from conftest import make_session
    session = make_session(tmp_path, toy)
    session.state["manifest"] = {"status": "running"}
    entry = session.record_incident({"kind": "sandbox_lost", "time": "2026-09-18T00:00:00Z",
                                     "container": "c", "observed": "gone", "restarted": True})
    assert session.state["manifest"]["infrastructure_incidents"] == [entry]
    assert entry["elapsed_seconds"] >= 0.0
    assert ("infrastructure_incident", entry) in session.events


# --------------------------------------------- an incident invalidates a run

def test_apply_validity_flips_a_clean_looking_attempt_that_lost_its_sandbox():
    manifest = {"infrastructure_incidents": [{"kind": "sandbox_lost", "restarted": True}]}
    assert att.apply_validity(manifest, True) is False
    assert manifest["measurement_valid"] is False
    assert manifest["measurement_invalid_reasons"] == ["sandbox_lost"]


def test_apply_validity_keeps_an_untouched_attempt_valid():
    manifest = {"infrastructure_incidents": []}
    assert att.apply_validity(manifest, True) is True
    assert manifest["measurement_invalid_reasons"] == []


def test_apply_validity_records_both_reasons_when_both_apply():
    manifest = {"infrastructure_incidents": [{"kind": "sandbox_lost"}]}
    att.apply_validity(manifest, False)
    assert manifest["measurement_invalid_reasons"] == ["harness_criteria", "sandbox_lost"]


def _scripted_attempt(tmp_path, monkeypatch, toy, *, incidents):
    from evalbase.harness import cli_runner
    from evalbase.harness.cli_runner import CLIAttempt
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    clock = {"t": 0.0}

    def segment(self, index, remaining, info, manifest, raw, errlog, started, *, kind="continuation"):
        clock["t"] += 4000.0
        state = {"checkpoint_index": 0, "checkpoints": [], "manifest": {
            "status": "stopped", "calls": 12, "tool_calls": {"shell": 12},
            "solver_seconds": 4000.0, "infrastructure_incidents": incidents}}
        (self.run_dir / "tools-state.json").write_text(json.dumps(state))
        return {"segment": index, "seconds": 4000.0, "prompt_kind": kind, "exit_code": 0,
                "tool_calls": 12, "stop_requested": False, "rate_limit": None,
                "final_text": None, "submission_final": False}

    monkeypatch.setattr(CLIAttempt, "_segment", segment, raising=True)
    monkeypatch.setattr(CLIAttempt, "_elapsed", lambda self, started: clock["t"])
    monkeypatch.setattr(cli_runner, "preflight", lambda h, model=None: {"executable": "/bin/true", "version": "x", "authentication": "y"})
    monkeypatch.setattr(cli_runner.ws, "populate", lambda *a, **k: None)
    monkeypatch.setattr(cli_runner.ws, "checkpoint", lambda *a, **k: (_ for _ in ()).throw(ValueError("no workspace")))
    cleaned = []
    monkeypatch.setattr(cli_runner.tools, "cleanup_containers", lambda owner=None: cleaned.append(owner) or {})
    monkeypatch.setattr(cli_runner, "audit_events", lambda *a, **k: {
        "valid_tool_boundary": True, "violations": [], "boundary_evidence": [], "errors": [],
        "events": 1, "mcp_tool_calls": 12, "rate_limit_events": [], "usage": None,
        "usage_entries": [], "usage_totals": {}, "cost": {}, "usage_source": "test", "served_models": None})
    monkeypatch.setattr(cli_runner.att, "export_and_grade", lambda *a, **k: a[4].update(status="complete"))
    monkeypatch.setattr(cli_runner.att, "sweep_containers", lambda stage, owner: [])
    options = att.Options(harness="claude-code", model="m", budget_hours=1.0).validate()
    manifest = CLIAttempt(toy, options, tmp_path / "att-1").run()
    return manifest, cleaned


def test_an_attempt_that_never_lost_its_sandbox_is_valid(tmp_path, monkeypatch, toy):
    manifest, cleaned = _scripted_attempt(tmp_path, monkeypatch, toy, incidents=[])
    assert manifest["stop_reason"] == "budget_wall" and manifest["measurement_valid"] is True
    assert manifest["measurement_invalid_reasons"] == []
    assert cleaned == ["att-1"]
    on_disk = json.loads((tmp_path / "att-1" / "attempt.json").read_text())
    assert on_disk["owner"] == "att-1" and on_disk["measurement_valid"] is True


def test_an_attempt_whose_sandbox_was_lost_can_never_be_reported_as_valid(tmp_path, monkeypatch, toy):
    incident = {"kind": "sandbox_lost", "time": "2026-09-18T00:00:44Z", "elapsed_seconds": 44.0,
                "container": "evalbase-solver-abc", "observed": "container is not running", "restarted": True}
    manifest, _ = _scripted_attempt(tmp_path, monkeypatch, toy, incidents=[incident])
    assert manifest["stop_reason"] == "budget_wall"
    assert manifest["measurement_valid"] is False
    assert manifest["measurement_invalid_reasons"] == ["sandbox_lost"]
    assert manifest["infrastructure_incidents"] == [incident]
    assert manifest["status"] == "invalid_measurement"


def test_the_mcp_bridge_owns_its_containers_by_attempt_name(tmp_path, toy):
    from conftest import FakeSandbox
    from evalbase.harness import mcp_server
    run_dir = tmp_path / "att-1"
    run_dir.mkdir()
    (run_dir / "agent-config.json").write_text(json.dumps({"max_seconds": 600, "checkpoint_seconds": 900, "owner": "att-1"}))
    session = mcp_server.CLIToolSession(run_dir, sandbox=FakeSandbox(run_dir / "workspace"), instance=toy)
    assert session.owner == "att-1" and session.manifest["owner"] == "att-1"
    assert session.manifest["infrastructure_incidents"] == []


def test_the_mcp_bridge_falls_back_to_the_run_directory_name(tmp_path, toy):
    from conftest import FakeSandbox
    from evalbase.harness import mcp_server
    run_dir = tmp_path / "att-2"
    run_dir.mkdir()
    (run_dir / "agent-config.json").write_text(json.dumps({"max_seconds": 600, "checkpoint_seconds": 900}))
    session = mcp_server.CLIToolSession(run_dir, sandbox=FakeSandbox(run_dir / "workspace"), instance=toy)
    assert session.owner == "att-2"
