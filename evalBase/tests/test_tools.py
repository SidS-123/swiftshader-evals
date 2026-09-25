"""Tool caps and boundaries: paths, case limits, dispatch, corpus reach."""
import json
from pathlib import Path

import pytest

from conftest import make_session, make_workspace
from evalbase.harness import tools


# --------------------------------------------------------------- path rules

def test_paths_outside_the_workspace_are_rejected(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    for bad in ("/etc/passwd", "../../corpus/hidden/case.json", "dev/../../secret", "/task/../grader/metrics.py"):
        with pytest.raises(ValueError):
            tools.resolve_in_workspace(task, bad)


def test_symlink_escapes_are_rejected_even_when_the_target_exists(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    secret = tmp_path / "hidden.json"
    secret.write_text("{}")
    (task / "link.json").symlink_to(secret)
    with pytest.raises(ValueError, match="symlink"):
        tools.resolve_in_workspace(task, "link.json")
    (task / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        tools.resolve_in_workspace(task, "escape/hidden.json")


def test_task_prefixed_and_relative_paths_both_resolve(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    (task / "mine.json").write_text("{}")
    assert tools.resolve_in_workspace(task, "/task/mine.json") == task / "mine.json"
    assert tools.resolve_in_workspace(task, "mine.json") == task / "mine.json"
    with pytest.raises(ValueError, match="no such file"):
        tools.resolve_in_workspace(task, "missing.json")


def test_outdir_may_not_be_the_supplied_spec_or_dev_tree(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    for bad in ("dev/cases", "spec", "/task/dev/reference/x"):
        with pytest.raises(ValueError, match="spec/ or dev/"):
            tools._writable_outdir(task, bad)
    out = tools._writable_outdir(task, "out/run1")
    assert out.is_dir() and out == task / "out" / "run1"


# ------------------------------------------------------------ case limits

def _case(tmp_path, **over):
    data = {"version": 1, "name": "case", "ops": [
        {"op": "canvas", "width": 8, "height": 8, "background": 0.0, "seed": 1}]}
    for i in range(over.get("snapshots", 4)):
        data["ops"] += [{"op": "draw", "samples": 1}, {"op": "snapshot", "name": f"f{i}"}]
    if over.get("asset"):
        data["ops"].append({"op": "data", "id": "d", "asset": over["asset"]})
    path = tmp_path / "case.json"
    path.write_text(json.dumps(data))
    return path


def test_case_snapshot_cap(tmp_path, toy):
    assert tools.inspect_case(toy, _case(tmp_path))["snapshots"] == 4
    with pytest.raises(ValueError, match="allows 64"):
        tools.inspect_case(toy, _case(tmp_path, snapshots=65))


def test_case_size_cap_and_json_validation(tmp_path, toy):
    path = tmp_path / "big.json"
    path.write_text('{"version":1,"ops":[],"pad":"' + "x" * (tools.ORACLE_CASE_BYTES + 10) + '"}')
    with pytest.raises(ValueError, match="MiB"):
        tools.inspect_case(toy, path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        tools.inspect_case(toy, bad)
    noops = tmp_path / "noops.json"
    noops.write_text('{"version":1}')
    with pytest.raises(ValueError, match="'ops' array"):
        tools.inspect_case(toy, noops)


def test_case_assets_must_be_relative_and_inside_task(tmp_path, toy):
    ok = tools.inspect_case(toy, _case(tmp_path, asset={"file": "mesh.bin", "offset": 0}))
    assert ok["assets"] == ["mesh.bin"]
    for bad in ({"file": "/etc/passwd"}, {"file": "../../corpus/assets/x.bin"}):
        with pytest.raises(ValueError, match="relative"):
            tools.inspect_case(toy, _case(tmp_path, asset=bad))


# ------------------------------------------------------------- tool surface

def test_exactly_five_tools_with_documented_caps(toy):
    schemas = tools.tool_schemas(toy.task.tool_descriptions)
    names = [t["function"]["name"] for t in schemas]
    assert names == ["shell", "oracle", "driver", "grade_dev", "checkpoint"] == list(tools.TOOL_NAMES)
    shell = schemas[0]["function"]["parameters"]
    assert shell["properties"]["timeout_seconds"]["maximum"] == 600
    assert tools.SHELL_OUTPUT_LIMIT == 32 * 1024
    for tool in schemas:
        assert tool["function"]["parameters"]["additionalProperties"] is False


def test_argument_validation_rejects_unknown_and_missing():
    tools.validate_arguments("shell", {"command": "ls"})
    with pytest.raises(ValueError, match="unexpected"):
        tools.validate_arguments("shell", {"command": "ls", "sudo": True})
    with pytest.raises(ValueError, match="missing"):
        tools.validate_arguments("oracle", {"case_path": "a.json"})
    with pytest.raises(ValueError, match="unknown tool"):
        tools.validate_arguments("docker", {})


def test_shell_runs_in_the_workspace_and_caps_timeout(tmp_path, toy):
    session = make_session(tmp_path, toy)
    result = session.execute({"function": {"name": "shell", "arguments":
                              json.dumps({"command": "pwd && ls TASK.md", "timeout_seconds": 10})}})
    assert result["exit_code"] == 0 and "TASK.md" in result["output"]
    with pytest.raises(ValueError, match="1..600"):
        session.execute({"function": {"name": "shell", "arguments":
                        json.dumps({"command": "true", "timeout_seconds": 5000})}})


def test_grade_dev_cannot_name_a_case_outside_the_public_corpus(tmp_path, toy):
    session = make_session(tmp_path, toy)
    (session.workspace / "candidate.py").write_text("class Painter: pass\n")
    with pytest.raises(ValueError, match="unknown public case"):
        session.grade_dev(["hidden_case_017"])
    with pytest.raises(ValueError, match="unknown public case"):
        session.grade_dev(["../hidden/case"])
    assert Path(session.corpus_public).name == "corpus"


def test_driver_and_grade_dev_require_the_artifact(tmp_path, toy):
    session = make_session(tmp_path, toy)
    (session.workspace / "mine.json").write_text(json.dumps({"version": 1, "name": "m", "ops": []}))
    (session.workspace / "candidate.py").unlink()
    with pytest.raises(ValueError, match="candidate.py does not exist"):
        session.driver("mine.json", "out")
    with pytest.raises(ValueError, match="candidate.py does not exist"):
        session.grade_dev([])


def test_the_driver_tool_runs_the_instance_command(tmp_path, toy):
    session = make_session(tmp_path, toy)
    result = session.driver("dev/cases/dev_case.json", "out/mine")
    assert result["exit_code"] == 0 and result["exit"] == "ok"
    assert (session.workspace / "out" / "mine" / "ledger.json").is_file()
    assert (session.workspace / "out" / "mine" / "snap_000.pgm").is_file()


def test_the_oracle_tool_runs_a_case_the_model_wrote(tmp_path, toy):
    session = make_session(tmp_path, toy)
    (session.workspace / "mine").mkdir()
    (session.workspace / "mine" / "tiny.json").write_text(json.dumps(toy.task.smoke.case))
    result = session.oracle("mine/tiny.json", "out/oracle")
    assert result["exit"] == "ok" and "snap_000.pgm" in result["files"]
    assert (session.workspace / "out" / "oracle" / "ledger.json").is_file()
    assert (session.run_dir / "oracle" / "000001" / "case.json").is_file()


def test_checkpoint_is_hashed_versioned_and_outside_the_workspace(tmp_path, toy):
    session = make_session(tmp_path, toy)
    (session.workspace / "candidate.py").write_text("x = 1\n")
    first = session.checkpoint("one")
    (session.workspace / "candidate.py").write_text("x = 2\n")
    second = session.checkpoint("two")
    assert first["checkpoint"] == 1 and second["checkpoint"] == 2
    assert first["source_sha256"] != second["source_sha256"]
    for index in (1, 2):
        base = session.run_dir / "checkpoints" / f"{index:06d}"
        assert (base / "checkpoint.json").is_file() and (base / "source.tar.gz").is_file()
    stored = json.loads((session.run_dir / "checkpoints" / "000001" / "checkpoint.json").read_text())
    assert stored["source_sha256"] == first["source_sha256"]


def test_shell_command_and_workspace_never_expose_the_host(tmp_path, monkeypatch, toy):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40)
    from evalbase.harness.privacy import Redactor
    redactor = Redactor(root=toy.root)
    text = redactor.text(f"key sk-or-v1-{'a' * 40} at {tmp_path}")
    assert "sk-or-v1" not in text and "[REDACTED]" in text


def test_checks_summary_and_local_sandbox(tmp_path):
    checks = [{"check": "a", "ok": True, "detail": ""}, {"check": "b", "ok": False, "detail": {"x": 1}}]
    summary = tools.checks_summary(checks)
    assert summary == {"checks_total": 2, "checks_passed": 1,
                       "checks_failed": [{"name": "b", "detail": '{"x": 1}'}]}
    assert tools.checks_summary(None) == {"checks_total": 0, "checks_passed": 0, "checks_failed": []}
    sandbox = tools.LocalSandbox(tmp_path)
    assert sandbox.shell("echo hi", timeout=5)["output"].strip() == "hi"
    assert sandbox.shell("exit 3", timeout=5)["exit_code"] == 3
    with pytest.raises(ValueError):
        tools.make_sandbox("vm", tmp_path, image="x")
