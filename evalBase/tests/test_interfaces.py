"""The extension points: loading an instance, validating specs, the defaults."""
import json
from pathlib import Path

import pytest

from evalbase import interfaces as ifc


def test_the_toy_instance_loads_by_path_directory_and_attribute(toy):
    root = toy.root
    assert ifc.load_instance(str(root)).name == "toy"
    assert ifc.load_instance(str(root / "instance.py")).name == "toy"
    assert ifc.load_instance(str(root / "instance.py") + ":INSTANCE").name == "toy"
    with pytest.raises(ValueError, match="not an evalbase"):
        ifc.load_instance(str(root / "instance.py") + ":METRIC")
    with pytest.raises(ValueError, match="not found"):
        ifc.load_instance(str(root / "nope.py"))


def test_no_instance_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(ifc.INSTANCE_ENV, raising=False)
    with pytest.raises(ValueError, match="EVALBASE_INSTANCE"):
        ifc.load_instance(None)


def test_the_environment_names_the_instance(monkeypatch, toy):
    monkeypatch.setenv(ifc.INSTANCE_ENV, str(toy.root))
    assert ifc.load_instance().name == "toy"


def test_metric_spec_validation():
    ifc.MetricSpec(perturbations=("a", "b"), tolerance_perturbations=("a",)).validate()
    with pytest.raises(ValueError, match="subset"):
        ifc.MetricSpec(perturbations=("a",), tolerance_perturbations=("b",)).validate()
    with pytest.raises(ValueError, match="t_lo"):
        ifc.MetricSpec(t_lo=0.5, t_hi=0.3).validate()
    with pytest.raises(ValueError, match="sum to 1"):
        ifc.MetricSpec(weights={"replay": 0.5, "procedural": 0.3, "performance": 0.1}).validate()
    with pytest.raises(ValueError, match="no weight"):
        ifc.MetricSpec(weights={"fidelity": 1.0}).validate()


def test_corpus_spec_defaults(tmp_path):
    spec = ifc.CorpusSpec(root=tmp_path, gen_dir=tmp_path / "gen", public_dir=tmp_path / "p",
                          hidden_dir=tmp_path / "h", assets_dir=tmp_path / "a")
    assert spec.hidden_default == tmp_path.parent / (tmp_path.name + "-hidden")
    assert spec.forbidden_hidden_roots == (tmp_path.resolve(),)
    assert spec.hidden_env == "EVALBASE_HIDDEN_GEN"


def test_task_spec_requires_every_tool_description(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        ifc.TaskSpec(task_dir=tmp_path, instructions="x", tool_descriptions={"shell": "s"})


def test_the_default_case_inspector_reads_the_generic_shape(tmp_path):
    class D(ifc.Driver):
        def run(self, *a, **k):
            raise NotImplementedError

    path = tmp_path / "c.json"
    path.write_text(json.dumps({"ops": [{"op": "snapshot"}, {"op": "x", "asset": {"file": "a.bin"}}]}))
    info = D().inspect_case(path)
    assert info["snapshots"] == 1 and info["assets"] == ["a.bin"] and info["bytes"] > 0
    path.write_text(json.dumps({"ops": [{"op": "x", "asset": {"file": "../a.bin"}}]}))
    with pytest.raises(ValueError, match="relative"):
        D().inspect_case(path)


def test_drive_result_exit_classification():
    r = lambda ledger, timed_out=False: ifc.DriveResult("o", ledger, 0, 1.0, timed_out, "")
    assert r({"exit": "ok"}).exit == "ok"
    assert r({"exit": "driver_error"}).exit == "driver_error"
    assert r({}).exit == "crash"
    assert r({"exit": "ok"}, timed_out=True).exit == "timeout"


def test_generic_checks_gate_and_output_match(tmp_path, toy):
    from examples_helpers import picture, write_output
    ref = [picture(1), picture(2)]
    ref_ledger = write_output(tmp_path / "ref", ref)
    ok_ledger = write_output(tmp_path / "ok", ref)
    bad_ledger = write_output(tmp_path / "bad", [ref[0], ref[0] * 0.0])
    scorer = toy.scorer
    crashed = ifc.generic_checks(scorer, ref_ledger, {"exit": "crash"}, str(tmp_path / "ref"), str(tmp_path / "bad"), 0.1)
    assert crashed == [{"check": "exit_ok", "ok": False, "detail": "crash"}]
    good = ifc.generic_checks(scorer, ref_ledger, ok_ledger, str(tmp_path / "ref"), str(tmp_path / "ok"), 0.1)
    assert [c["check"] for c in good] == ["output_matches_reference"] and good[0]["ok"]
    bad = ifc.generic_checks(scorer, ref_ledger, bad_ledger, str(tmp_path / "ref"), str(tmp_path / "bad"), 0.1)
    assert not bad[0]["ok"] and bad[0]["detail"]["worst_snapshot"] == "snap_001"
    # no threshold: nothing image-based is checked, and nothing is invented
    assert ifc.generic_checks(scorer, ref_ledger, ok_ledger, str(tmp_path / "ref"), str(tmp_path / "ok"), None) == []


def test_instance_paths_follow_the_environment(monkeypatch, toy):
    monkeypatch.setenv("EVALBASE_REFCACHE", "/elsewhere/refcache")
    monkeypatch.delenv("EVALBASE_REFCACHE_HIDDEN", raising=False)
    assert str(toy.refcache) == "/elsewhere/refcache"
    assert str(toy.refcache_hidden) == "/elsewhere/refcache-hidden"
    monkeypatch.setenv("EVALBASE_IMAGE", "img:9")
    assert toy.reference_image == "img:9"
