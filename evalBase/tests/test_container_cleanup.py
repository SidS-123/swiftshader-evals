"""The grader must not leave containers behind.

`subprocess.run(..., timeout=...)` kills the local `docker run` client, not
the container; every container is named and labelled, the timeout path kills
it by name, and a sweep can find anything that still got away. No Docker
here: subprocess is replaced and the argv is inspected.
"""
import subprocess

import pytest

from evalbase.grader import cli, containers


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


def _drive(toy, tmp_path, **kw):
    from instance import ToyDriver
    (tmp_path / "r.json").write_text("{}")
    return ToyDriver(toy.root, sandbox="docker").run(str(tmp_path / "r.json"), str(tmp_path / "out"),
                                                     str(tmp_path / "assets"), **kw)


def test_drive_names_and_labels_its_container(monkeypatch, tmp_path, toy):
    calls = _fake_docker(monkeypatch, lambda cmd: Done())
    _drive(toy, tmp_path)
    cmd = calls[0]
    assert cmd[:2] == ["docker", "run"]
    name = cmd[cmd.index("--name") + 1]
    assert name.startswith("evalbase-drive-")
    assert cmd[cmd.index("--label") + 1] == containers.LABEL
    assert cmd.index("--name") < cmd.index("python:3.12-slim")


def test_container_names_are_unique():
    names = {containers.container_name("drive") for _ in range(50)}
    assert len(names) == 50


def _drive_timing_out(monkeypatch, tmp_path, toy):
    def handler(cmd):
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, 1.0, stderr=b"slow\n")
        return Done()
    calls = _fake_docker(monkeypatch, handler)
    result = _drive(toy, tmp_path, timeout_s=1.0)
    return calls, result


def test_a_timed_out_drive_kills_the_container_it_launched(monkeypatch, tmp_path, toy):
    calls, result = _drive_timing_out(monkeypatch, tmp_path, toy)
    launched = calls[0][calls[0].index("--name") + 1]
    assert ["docker", "kill", launched] in calls
    assert ["docker", "rm", "-f", launched] in calls
    assert not [c for c in calls if c[:2] == ["docker", "kill"] and c[2] != launched]


def test_a_timed_out_drive_still_reports_a_timeout(monkeypatch, tmp_path, toy):
    _, result = _drive_timing_out(monkeypatch, tmp_path, toy)
    assert result.timed_out is True and result.exit == "timeout" and result.returncode == -1
    assert "slow" in result.stderr_tail


def test_an_interrupted_drive_kills_the_container_and_re_raises(monkeypatch, tmp_path, toy):
    def handler(cmd):
        if cmd[:2] == ["docker", "run"]:
            raise KeyboardInterrupt
        return Done()
    calls = _fake_docker(monkeypatch, handler)
    with pytest.raises(KeyboardInterrupt):
        _drive(toy, tmp_path)
    launched = calls[0][calls[0].index("--name") + 1]
    assert ["docker", "kill", launched] in calls


def test_a_drive_that_finishes_kills_nothing(monkeypatch, tmp_path, toy):
    calls = _fake_docker(monkeypatch, lambda cmd: Done())
    _drive(toy, tmp_path)
    assert [c for c in calls if c[1] in ("kill", "rm")] == []


PS = ("ab12\tevalbase-drive-1\timg\tUp 11 hours\t2026-09-17 09:47:39 +0200 CEST\tmine\n"
      "cd34\t\timg\tUp 3 minutes\t2026-09-17 20:40:00 +0200 CEST\tmine\n")


def test_the_sweep_filters_by_label_and_kills_what_docker_ps_returned(monkeypatch, capsys):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=PS) if cmd[1] == "ps" else Done())
    killed = containers.kill_stale_containers("mine")
    ps = calls[0]
    assert ps[:3] == ["docker", "ps", "-a"]
    assert [ps[i + 1] for i, x in enumerate(ps) if x == "--filter"] == [f"label={containers.LABEL}", "label=io.evalbase.owner=mine"]
    assert [c for c in calls if c[1] == "kill"] == [["docker", "kill", "ab12"], ["docker", "kill", "cd34"]]
    assert [c["id"] for c in killed] == ["ab12", "cd34"]
    out = capsys.readouterr().out
    assert "evalbase-drive-1" in out and "cd34" in out


def test_the_sweep_kills_nothing_when_no_container_carries_the_label(monkeypatch):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=""))
    assert containers.kill_stale_containers("mine") == []
    assert [c for c in calls if c[1] in ("kill", "rm")] == []


def test_the_sweep_can_spare_young_containers(monkeypatch):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=PS) if cmd[1] == "ps" else Done())
    monkeypatch.setattr(containers, "_age_seconds", lambda created: 11 * 3600 if "09:47" in created else 180)
    killed = containers.kill_stale_containers("mine", older_than_s=3600)
    assert [c["id"] for c in killed] == ["ab12"]
    assert [c for c in calls if c[1] == "kill"] == [["docker", "kill", "ab12"]]


def test_a_label_argument_is_passed_through_verbatim(monkeypatch):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=""))
    containers.kill_stale_containers("mine", label="io.example=2")
    assert calls[0][calls[0].index("--filter") + 1] == "label=io.example=2"


def test_an_unparsable_creation_stamp_is_treated_as_old():
    assert containers._age_seconds("") == float("inf")
    assert containers._age_seconds("not a date") == float("inf")
    assert containers._age_seconds("2026-09-17 09:47:39 +0200 CEST") > 0


def test_a_missing_docker_is_not_an_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(containers.subprocess, "run", fake_run)
    assert containers.list_managed_containers() == []
    assert containers.kill_stale_containers("mine") == []
    assert containers.kill_container("x") is False


def test_the_cli_exposes_a_sweep_command(monkeypatch, capsys):
    calls = _fake_docker(monkeypatch, lambda cmd: Done(stdout=PS) if cmd[1] == "ps" else Done())
    cli.main(["sweep", "--owner", "mine"])
    assert [c for c in calls if c[1] == "kill"] == [["docker", "kill", "ab12"], ["docker", "kill", "cd34"]]
    assert "killed 2 container(s) owned by mine" in capsys.readouterr().out


def test_the_cli_sweeps_before_a_grading_run(monkeypatch, toy):
    swept = []
    monkeypatch.setattr(containers, "kill_stale_containers", lambda *a, **k: swept.append(a) or [])
    monkeypatch.setattr(cli, "cmd_grade", lambda a: swept.append("graded"))
    cli.main(["grade", "--candidate", "/nowhere"], instance=toy)
    assert swept and swept[-1] == "graded"
