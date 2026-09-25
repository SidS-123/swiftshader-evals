"""Workspace population, immutable export and checkpoint manifests."""
import json
import os
import tarfile
from pathlib import Path

import numpy as np
import pytest

from conftest import make_workspace
from evalbase.harness import workspace as ws
from examples_helpers import picture, write_output


def test_population_contains_public_material_only(tmp_path, toy):
    task, corpus = make_workspace(tmp_path, toy)
    assert (task / "candidate.py").is_file() and (task / "NOTES.md").is_file()
    assert (task / "TASK.md").is_file() and (task / "SPEC.md").is_file()
    assert (task / "dev" / "CASE_FORMAT.md").is_file()
    assert (task / "dev" / "driver.py").is_file()          # TaskSpec.dev_extra
    assert (task / "dev" / "cases" / "dev_case.json").is_file()
    present = {p.name for p in task.rglob("*")}
    assert "hidden" not in present and "refcache" not in present
    assert not (task / "grader").exists() and not (task / "corpus").exists()
    for forbidden in ("oracle.py", "scorer.py", "instance.py", "runner.py"):
        assert forbidden not in present
    assert not (task / "dev" / "reference").exists()
    assert not os.access(task / "dev" / "cases" / "dev_case.json", os.W_OK)


def test_population_copies_reference_outputs_and_writes_previews(tmp_path, toy):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "case.json").write_text(json.dumps({"version": 1, "name": "case", "ops": []}))
    n1 = tmp_path / "refcache" / "case" / "n1"
    write_output(n1, [picture(1)])
    task = tmp_path / "task"
    ws.populate(toy, task, corpus=corpus, assets=tmp_path / "assets", refcache=tmp_path / "refcache")
    out = task / "dev" / "reference" / "case"
    assert (out / "snap_000.pgm").is_file()
    assert (out / "snap_000.png").read_bytes().startswith(b"\x89PNG")
    import scorer as toy_scorer
    assert np.abs(toy_scorer.read_pgm(str(out / "snap_000.pgm")) - picture(1)).max() <= 1 / 255 + 1e-6


def test_population_refuses_to_overwrite_and_requires_reference(tmp_path, toy):
    task, corpus = make_workspace(tmp_path, toy)
    with pytest.raises(ValueError, match="already exists"):
        ws.populate(toy, task, corpus=corpus, refcache=tmp_path / "refcache", require_reference=False)
    with pytest.raises(ValueError, match="no reference output"):
        ws.populate(toy, tmp_path / "other", corpus=corpus, refcache=tmp_path / "refcache")


def test_the_task_text_states_the_policy_it_is_run_under(tmp_path, toy):
    budget = ws.stopping_paragraph("budget", 24 * 3600, 900)
    submit = ws.stopping_paragraph("submit", 12 * 3600, 900)
    assert "budget" in budget and "24 hours" in budget and "15 minutes" in budget
    assert ws.SUBMIT_MARKER in submit and "12 hours" in submit and "hard cap" in submit
    workspace, _ = make_workspace(tmp_path, toy)
    text = (workspace / "TASK.md").read_text()
    assert ws.STOP_POLICY_MARKER not in text
    assert "Stopping policy for this attempt: **budget**" in text


def test_populate_refuses_a_task_file_that_lost_its_policy_marker(tmp_path, toy):
    import dataclasses
    root = tmp_path / "root"
    (root / "starter").mkdir(parents=True)
    (root / "TASK.md").write_text("no marker here\n")
    task = dataclasses.replace(toy.task, task_dir=root, format_doc=None, dev_extra={})
    instance = dataclasses.replace(toy, task=task)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.json").write_text("{}")
    with pytest.raises(ValueError, match="policy"):
        ws.populate(instance, tmp_path / "out", corpus=corpus, assets=tmp_path / "assets",
                    refcache=tmp_path / "cache", require_reference=False)


def test_export_selects_source_and_hashes_it(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    (task / "helper.py").write_text("x = 1\n")
    (task / "build").mkdir(exist_ok=True)
    (task / "build" / "junk.py").write_text("no\n")
    (task / "snapshot.pgm").write_bytes(b"binary")
    digest = ws.source_snapshot(toy, task, tmp_path / "export")
    manifest = json.loads((tmp_path / "export" / "source-manifest.json").read_text())
    names = set(manifest["files"])
    assert {"candidate.py", "helper.py", "NOTES.md"} <= names
    assert not any(n.startswith("build/") for n in names)
    assert not any(n.startswith("dev/") for n in names)
    assert "TASK.md" not in names and "snapshot.pgm" not in names
    assert manifest["sha256"] == digest and manifest["compilation_units"] == 2
    assert ws.verify_snapshot(tmp_path / "export") == digest
    target = tmp_path / "export" / "candidate.py"
    assert not os.access(target, os.W_OK)
    target.chmod(0o644)
    target.write_text("class Painter: ...\n")
    with pytest.raises(ValueError, match="changed on disk"):
        ws.verify_snapshot(tmp_path / "export")


def test_export_requires_the_instances_required_file(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    (task / "candidate.py").unlink()
    with pytest.raises(ValueError, match="candidate.py"):
        ws.source_snapshot(toy, task, tmp_path / "export")


def test_export_rejects_symlinks_and_special_files(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    (task / "escape.py").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        ws.source_snapshot(toy, task, tmp_path / "export")
    (task / "escape.py").unlink()
    os.mkfifo(task / "pipe.py")
    with pytest.raises(ValueError, match="special file"):
        ws.source_snapshot(toy, task, tmp_path / "export2")


def test_export_enforces_size_and_name_limits(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    (task / "big.py").write_bytes(b"x" * (ws.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds 8 MiB"):
        ws.source_snapshot(toy, task, tmp_path / "e1")
    (task / "big.py").unlink()
    (task / "we ird.py").write_text("x = 1\n")
    with pytest.raises(ValueError, match="simple source filenames"):
        ws.source_snapshot(toy, task, tmp_path / "e2")


def test_export_must_be_outside_the_workspace(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    with pytest.raises(ValueError, match="outside the workspace"):
        ws.source_snapshot(toy, task, task / "inner")


def test_checkpoint_writes_archive_notes_and_manifest(tmp_path, toy):
    task, _ = make_workspace(tmp_path, toy)
    (task / "NOTES.md").write_text("# plan\n")
    manifest = ws.checkpoint(toy, task, tmp_path / "cp" / "000001", index=1, note="first", elapsed=12.5)
    assert manifest["index"] == 1 and manifest["note"] == "first"
    assert manifest["notes_present"] and manifest["elapsed_seconds"] == 12.5
    archive = tmp_path / "cp" / "000001" / "source.tar.gz"
    assert archive.is_file() and manifest["archive_sha256"] == ws.sha256_file(archive)
    with tarfile.open(archive) as tar:
        members = set(tar.getnames())
    assert "source/candidate.py" in members and "source/NOTES.md" in members
    stored = json.loads((tmp_path / "cp" / "000001" / "checkpoint.json").read_text())
    assert stored["source_sha256"] == manifest["source_sha256"]


def test_version_record_covers_the_instance_trees_and_the_corpus(toy, monkeypatch):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    record = ws.version_record(toy)
    for key in ("task_sha256", "evalbase_sha256", "oracle_sha256", "corpus", "reference_image",
                "solver_image", "git_commit", "instance"):
        assert key in record
    assert record["corpus"]["sha256"] and record["instance"] == "toy"
    assert any(name.endswith("oracle.py") for name in record["files"]["oracle"])
    assert record["reference_image"]["id"] is None      # no docker call under EVALBASE_NO_DOCKER


def test_container_remove_command_and_remove_tree(tmp_path, monkeypatch):
    target = tmp_path / "grade" / "build"
    target.mkdir(parents=True)
    argv = ws.container_remove_command(target, image="img:1", owner="att-1")
    assert argv[:2] == ["docker", "run"] and "img:1" in argv
    assert argv[argv.index("--user") + 1] == "0:0"
    assert argv[argv.index("--mount") + 1] == f"type=bind,src={target.parent},dst=/parent"
    assert argv[-3:] == ['rm -rf -- "/parent/$1"', "sh", "build"]
    with pytest.raises(ValueError):
        ws.container_remove_command("/", image="img:1")
    (target / "x").write_text("x")
    ws.remove_tree(target)
    assert not target.exists()
