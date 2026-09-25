"""Control building through the instance's ControlSpec, and control.json.

No driver runs: the grade is captured.
"""
import json
from pathlib import Path

import pytest

from evalbase.grader import cli, containers

TOY_CONTROLS = ("reference", "stub", "half_samples", "brightness_x2", "stale_output")


def _run_control(monkeypatch, tmp_path, toy, name, extra=()):
    graded = {}
    monkeypatch.setattr(containers, "kill_stale_containers", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_grade", lambda a, lib, label: graded.update(a=a, lib=lib, label=label))
    cli.main(["--corpus", str(tmp_path / "corpus"), "--assets", str(tmp_path / "assets"),
              "--cache", str(tmp_path / "cache"), "control", name, "--out", str(tmp_path / "out"), *extra],
             instance=toy)
    return graded


def test_control_honours_corpus_assets_cache_and_out(monkeypatch, tmp_path, toy):
    graded = _run_control(monkeypatch, tmp_path, toy, "brightness_x2")
    assert graded["a"].corpus == str(tmp_path / "corpus")
    assert graded["a"].assets == str(tmp_path / "assets")
    assert graded["a"].cache == str(tmp_path / "cache")
    assert graded["a"].out == str(tmp_path / "out")
    assert graded["lib"] == str(tmp_path / "out" / "lib")
    assert (tmp_path / "out" / "lib" / "candidate.py").is_file()
    assert (tmp_path / "out" / "lib" / "oracle.py").is_file()
    assert graded["label"] == "control-brightness_x2"


def test_control_label_can_be_overridden(monkeypatch, tmp_path, toy):
    graded = _run_control(monkeypatch, tmp_path, toy, "brightness_x2", ["--label", "brightness_x2-hidden"])
    assert graded["label"] == "brightness_x2-hidden"


def test_the_reference_control_is_the_oracle_itself(monkeypatch, tmp_path, toy):
    graded = _run_control(monkeypatch, tmp_path, toy, "reference")
    built = (tmp_path / "out" / "lib" / "candidate.py").read_text()
    assert built == (toy.root / "oracle.py").read_text()


def test_unknown_control_is_rejected(monkeypatch, tmp_path, toy):
    with pytest.raises(SystemExit):
        _run_control(monkeypatch, tmp_path, toy, "no_such_control")


def test_every_toy_control_directory_is_known(toy):
    assert set(toy.controls.names()) == set(TOY_CONTROLS)


@pytest.mark.parametrize("name", [n for n in TOY_CONTROLS if n != "reference"])
def test_each_toy_control_has_a_candidate_and_a_prediction(name, toy):
    source = toy.controls.root / name / "candidate.py"
    assert source.is_file()
    assert "Prediction" in source.read_text()
    assert f"`{name}`" in (toy.controls.root / "README.md").read_text()


def test_a_control_without_a_control_json_gets_no_mounts_and_no_env(monkeypatch, tmp_path, toy):
    for name in TOY_CONTROLS:
        graded = _run_control(monkeypatch, tmp_path, toy, name)
        assert graded["a"].extra_mounts == [] and graded["a"].extra_env == {}, name


# ------------------------------------------------------- mounts, env, variants

@pytest.fixture
def controls_root(tmp_path):
    root = tmp_path / "controls"
    (root / "plain").mkdir(parents=True)
    (root / "mounted").mkdir()
    (root / "mounted" / "control.json").write_text(json.dumps({
        "mounts": [{"source": "runs/refcache", "target": "/controls-data", "mode": "ro"}],
        "env": {"DATA_DIR": "/controls-data"}}))
    (root / "variants").mkdir()
    (root / "variants" / "control.json").write_text(json.dumps({
        "variants": {"pfm": {"env": {"MODE": "pfm"}}, "huge": {"env": {"MODE": "huge"}},
                     "crash": {"env": {"MODE": "crash"}}},
        "default_variant": "pfm"}))
    return root


def test_control_run_spec_reads_mounts_env_and_variants(controls_root, tmp_path):
    plain = cli.control_run_spec(str(controls_root), "plain", None, str(tmp_path))
    assert plain == {"name": "plain", "variant": None, "suffix": "", "env": {}, "mounts": []}
    mounted = cli.control_run_spec(str(controls_root), "mounted", None, str(tmp_path))
    assert mounted["mounts"] == [(str(tmp_path / "runs" / "refcache"), "/controls-data", "ro")]
    assert mounted["env"] == {"DATA_DIR": "/controls-data"}
    crash = cli.control_run_spec(str(controls_root), "variants", "crash", str(tmp_path))
    assert crash["env"] == {"MODE": "crash"} and crash["suffix"] == "-crash"
    default = cli.control_run_spec(str(controls_root), "variants", None, str(tmp_path))
    assert default["variant"] == "pfm" and default["suffix"] == "-pfm"


def test_control_run_spec_rejects_bad_variants_and_bad_mounts(controls_root, tmp_path):
    with pytest.raises(SystemExit):
        cli.control_run_spec(str(controls_root), "variants", "nope", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.control_run_spec(str(controls_root), "plain", "pfm", str(tmp_path))
    (controls_root / "bad").mkdir()
    (controls_root / "bad" / "control.json").write_text(json.dumps({"mounts": [{"source": "x", "target": "rel"}]}))
    with pytest.raises(SystemExit):
        cli.control_run_spec(str(controls_root), "bad", None, str(tmp_path))
    (controls_root / "bad" / "control.json").write_text(json.dumps({"mounts": [{"source": "x", "target": "/y", "mode": "rwx"}]}))
    with pytest.raises(SystemExit):
        cli.control_run_spec(str(controls_root), "bad", None, str(tmp_path))


def test_a_control_run_records_its_variant_mounts_and_env(controls_root, tmp_path, toy, monkeypatch):
    import dataclasses
    from evalbase.interfaces import ControlSpec

    class Spec(ControlSpec):
        def __init__(self, root):
            self.root = root

        def build(self, name, out_dir, *, owner=None):
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            return Path(out_dir)

    instance = dataclasses.replace(toy, controls=Spec(controls_root))
    graded = _run_control(monkeypatch, tmp_path, instance, "variants", ["--variant", "huge"])
    assert graded["a"].extra_env == {"MODE": "huge"} and graded["a"].extra_mounts == []
    assert graded["label"] == "control-variants-huge"
    assert graded["a"].control_info == {"name": "variants", "variant": "huge", "env": {"MODE": "huge"}, "mounts": []}
    graded = _run_control(monkeypatch, tmp_path, instance, "mounted")
    assert graded["a"].extra_env == {"DATA_DIR": "/controls-data"}
    assert graded["a"].extra_mounts == [(str(toy.root / "runs" / "refcache"), "/controls-data", "ro")]


def test_the_toy_driver_passes_a_controls_mount_and_env_through(monkeypatch, tmp_path, toy):
    from instance import ToyDriver
    seen = {}
    monkeypatch.setattr(containers, "run_named", lambda cmd, name, timeout_s: seen.update(cmd=cmd) or (0, "", False))
    src = tmp_path / "refcache"
    src.mkdir()
    (tmp_path / "r.json").write_text("{}")
    ToyDriver(toy.root, sandbox="docker").run(str(tmp_path / "r.json"), str(tmp_path / "out"), str(tmp_path / "assets"),
                                              candidate=str(tmp_path / "lib"),
                                              extra_mounts=[(str(src), "/controls-data", "ro")],
                                              extra_env={"MODE": "crash", "DATA_DIR": "/controls-data"})
    cmd = seen["cmd"]
    assert f"{src}:/controls-data:ro" in cmd
    envs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-e"]
    assert "MODE=crash" in envs and "DATA_DIR=/controls-data" in envs
    image = cmd.index("python:3.12-slim")
    assert cmd.index(f"{src}:/controls-data:ro") < image and all(cmd.index(e) < image for e in envs)
    assert cmd[-2:] == ["--candidate", "/candidate"]
