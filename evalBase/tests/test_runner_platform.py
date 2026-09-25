"""EVALBASE_PLATFORM threads through to `docker run`, so a host can grade
under emulation against a cross-built image without anything else changing.
"""
from evalbase.grader import containers


def test_docker_base_omits_platform_by_default(monkeypatch):
    monkeypatch.delenv("EVALBASE_PLATFORM", raising=False)
    cmd = containers.docker_base(None, "8g")
    assert "--platform" not in cmd and "--network" in cmd


def test_docker_base_passes_platform_when_set(monkeypatch):
    monkeypatch.setenv("EVALBASE_PLATFORM", "linux/amd64")
    cmd = containers.docker_base(None, "8g")
    assert cmd[cmd.index("--platform") + 1] == "linux/amd64"


def test_the_toy_driver_puts_platform_before_the_image(monkeypatch, tmp_path, toy):
    from instance import ToyDriver
    monkeypatch.setenv("EVALBASE_PLATFORM", "linux/amd64")
    captured = {}

    def fake_run(cmd, name, timeout_s):
        captured["cmd"] = cmd
        return 0, "", False

    monkeypatch.setattr(containers, "run_named", fake_run)
    (tmp_path / "r.json").write_text("{}")
    ToyDriver(toy.root, sandbox="docker").run(str(tmp_path / "r.json"), str(tmp_path / "out"), str(tmp_path / "assets"),
                                              cpus="2", sample_mult=2, perturbation="jitter")
    cmd = captured["cmd"]
    assert "--platform" in cmd and cmd.index("--platform") < cmd.index("python:3.12-slim")
    assert cmd[cmd.index("--cpus") + 1] == "2"
    envs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-e"]
    assert "TOY_SAMPLE_MULT=2" in envs and "TOY_PERTURB=jitter" in envs
    assert "--candidate" not in cmd
