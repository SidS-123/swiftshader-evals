"""The refcache records which case file it was built from, and that is enforced.

No
driver runs: `runner.drive` and `runner.load_snapshot` are stubbed.
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from evalbase.grader import cli, runner
from evalbase.interfaces import DriveResult


def write_replay(corpus: Path, name: str, *, snapshots: int = 2, assets: dict | None = None,
                 tweak: float = 0.0) -> Path:
    ops = [{"op": "canvas", "width": 16, "height": 16, "background": 0.1, "seed": 1, "jitter": tweak}]
    for i in range(snapshots):
        ops.append({"op": "draw", "samples": 1})
        ops.append({"op": "snapshot", "name": f"snap_{i:03d}"})
    doc = {"version": 1, "name": name, "family": "fam", "category": "replay",
           "assets": assets if assets is not None else {}, "meta": {}, "split": "public", "ops": ops}
    path = corpus / f"{name}.json"
    path.write_text(json.dumps(doc, indent=1))
    return path


def stub_driver(monkeypatch, snapshots: int = 2):
    """Replace the driver with a ledger writer and a constant snapshot."""
    def fake_drive(instance, replay_path, outdir, assets_dir, lib_dir=None, sample_mult=1, **kw):
        os.makedirs(outdir, exist_ok=True)
        events = []
        for i in range(snapshots):
            events.append({"op": "run", "wall_seconds": 0.01 * (i + 1)})
            events.append({"op": "snapshot", "name": f"snap_{i:03d}", "files": {"gray": f"snap_{i:03d}.pgm"}})
        ledger = {"exit": "ok", "events": events, "errors": [], "replay": "x"}
        with open(os.path.join(outdir, "ledger.json"), "w") as f:
            json.dump(ledger, f)
        return DriveResult(outdir, ledger, 0, 0.5, False, "")

    monkeypatch.setattr(runner, "drive", fake_drive)
    monkeypatch.setattr(runner, "load_snapshot",
                        lambda instance, outdir, event, channel=None: np.zeros((16, 16), np.float32))


def args(instance, corpus, cache, **kw):
    base = dict(instance=instance, corpus=str(corpus), cache=str(cache), assets=str(corpus), cpus=None,
                only=None, force=False, trust_unstamped=False, stamp=False, owner=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_build_refcache_records_the_replay_hash_and_asset_names(tmp_path, monkeypatch, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case", assets={"bb.bin": "bb", "aa.bin": "aa"})
    info = runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    written = json.loads((cache / "case" / "refcache.json").read_text())
    assert written == info
    assert info["replay_sha256"] == hashlib.sha256(replay.read_bytes()).hexdigest()
    assert info["assets"] == ["aa.bin", "bb.bin"]
    assert "generator_stamp" not in info
    assert info["replay"] == "case" and info["snapshots"] == 2
    assert info["image"] == toy.reference_image and info["metric_version"] == toy.metric.version


def test_a_generator_stamp_is_recorded_when_the_corpus_grows_one(tmp_path):
    replay = tmp_path / "r.json"
    replay.write_text(json.dumps({"name": "r", "generator": "gen-v7", "assets": {}, "ops": []}))
    assert runner.replay_identity(str(replay))["generator_stamp"] == "gen-v7"


def test_refcache_reuses_a_matching_entry_and_rebuilds_a_changed_one(tmp_path, monkeypatch, capsys, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case")
    cli.cmd_refcache(args(toy, corpus, cache))
    assert "[ref] case:" in capsys.readouterr().out
    cli.cmd_refcache(args(toy, corpus, cache))
    assert "[skip] case (cached)" in capsys.readouterr().out
    write_replay(corpus, "case", tweak=1e-7)      # same name, same snapshots, other bytes
    calls = []
    real = runner.build_refcache
    monkeypatch.setattr(runner, "build_refcache", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    cli.cmd_refcache(args(toy, corpus, cache))
    out = capsys.readouterr().out
    assert "[ref] case stale (replay changed), rebuilding" in out
    assert calls == [str(replay)]
    entry = json.loads((cache / "case" / "refcache.json").read_text())
    assert entry["replay_sha256"] == hashlib.sha256(replay.read_bytes()).hexdigest()


def test_an_unstamped_entry_is_stale_unless_it_is_trusted(tmp_path, monkeypatch, capsys, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    write_replay(corpus, "case")
    cli.cmd_refcache(args(toy, corpus, cache))
    entry = cache / "case" / "refcache.json"
    rc = json.loads(entry.read_text())
    rc.pop("replay_sha256")
    entry.write_text(json.dumps(rc))
    capsys.readouterr()
    cli.cmd_refcache(args(toy, corpus, cache, trust_unstamped=True))
    assert "[skip] case (cached)" in capsys.readouterr().out
    assert "replay_sha256" not in json.loads(entry.read_text())
    cli.cmd_refcache(args(toy, corpus, cache))
    assert "[ref] case stale (no replay_sha256), rebuilding" in capsys.readouterr().out
    assert "replay_sha256" in json.loads(entry.read_text())


def test_grade_refuses_a_refcache_built_from_a_different_replay(tmp_path, monkeypatch, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case")
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    other = write_replay(corpus, "case", tweak=2e-7)
    with pytest.raises(RuntimeError) as e:
        runner.grade_replay(toy, str(other), str(cache), str(corpus), None, str(tmp_path / "out"))
    message = str(e.value)
    assert "case" in message and str(other) in message
    assert hashlib.sha256(other.read_bytes()).hexdigest() in message
    assert json.loads((cache / "case" / "refcache.json").read_text())["replay_sha256"] in message


def test_grading_an_unstamped_entry_is_allowed_but_marked(tmp_path, monkeypatch, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case")
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    grade = runner.grade_replay(toy, str(replay), str(cache), str(corpus), None, str(tmp_path / "out"))
    assert "refcache_unstamped" not in grade.detail
    entry = cache / "case" / "refcache.json"
    rc = json.loads(entry.read_text())
    rc.pop("replay_sha256")
    entry.write_text(json.dumps(rc))
    grade = runner.grade_replay(toy, str(replay), str(cache), str(corpus), None, str(tmp_path / "out"))
    assert grade.detail["refcache_unstamped"] is True


def unstamp(cache: Path, name: str) -> Path:
    entry = cache / name / "refcache.json"
    rc = json.loads(entry.read_text())
    rc.pop("replay_sha256", None)
    rc.pop("assets", None)
    entry.write_text(json.dumps(rc))
    return entry


def test_stamp_fills_an_unstamped_entry_without_running_the_driver(tmp_path, monkeypatch, capsys, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case", assets={"aa.bin": "aa"})
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    entry = unstamp(cache, "case")
    capsys.readouterr()
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("stamp must not run the driver"))
    result = cli.cmd_refcache(args(toy, corpus, cache, stamp=True))
    assert result["stamped"] == ["case"] and not result["refused"]
    rc = json.loads(entry.read_text())
    assert rc["replay_sha256"] == hashlib.sha256(replay.read_bytes()).hexdigest()
    assert rc["assets"] == ["aa.bin"]
    assert "[stamp] case" in capsys.readouterr().out
    assert cli.cmd_refcache(args(toy, corpus, cache, stamp=True))["skipped"] == ["case"]


def test_stamp_refuses_a_snapshot_count_or_name_that_disagrees(tmp_path, monkeypatch, capsys, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case")
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    entry = unstamp(cache, "case")
    write_replay(corpus, "case", snapshots=3)
    result = cli.cmd_refcache(args(toy, corpus, cache, stamp=True))
    assert result["refused"] == ["case"]
    assert "snapshot count 3 in corpus, 2 in cache" in capsys.readouterr().out
    assert "replay_sha256" not in json.loads(entry.read_text())
    rc = json.loads(entry.read_text())
    rc["replay"] = "someone_else"
    rc["snapshots"] = 3
    entry.write_text(json.dumps(rc))
    assert cli.cmd_refcache(args(toy, corpus, cache, stamp=True))["refused"] == ["case"]
    assert "entry records replay 'someone_else'" in capsys.readouterr().out


def test_stamp_refuses_a_replay_regenerated_after_the_cached_output(tmp_path, monkeypatch, capsys, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_replay(corpus, "case")
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    unstamp(cache, "case")
    ledger = cache / "case" / "n1" / "ledger.json"
    os.utime(ledger, (1_600_000_000, 1_600_000_000))
    write_replay(corpus, "case", tweak=3e-7)
    assert cli.cmd_refcache(args(toy, corpus, cache, stamp=True))["refused"] == ["case"]
    assert "newer than the cached output" in capsys.readouterr().out


def test_stamp_skips_a_missing_entry_and_refuses_a_foreign_stamp(tmp_path, monkeypatch, capsys, toy):
    stub_driver(monkeypatch)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    cache.mkdir()
    write_replay(corpus, "gone")
    assert cli.cmd_refcache(args(toy, corpus, cache, stamp=True))["skipped"] == ["gone"]
    assert "no refcache entry" in capsys.readouterr().out
    replay = write_replay(corpus, "case")
    runner.build_refcache(toy, str(replay), str(cache), str(corpus))
    write_replay(corpus, "case", tweak=4e-7)
    result = cli.cmd_refcache(args(toy, corpus, cache, only=["case"], stamp=True))
    assert result["refused"] == ["case"]
    assert "stamped for a different replay" in capsys.readouterr().out
