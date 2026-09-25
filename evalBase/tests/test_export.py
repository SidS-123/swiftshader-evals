"""The site exporter: heatmap mapping, the APNG encoder, the summary shape,
the leaderboard, labels, validity and the static pages.
"""
import json
import struct
import zlib

import numpy as np
import pytest

from evalbase.grader import export as ex
from evalbase.grader import metrics as m
from examples_helpers import picture, write_output


def decode_png(blob: bytes):
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    i, tags, idat, fdat, w, h = 8, [], b"", [], 0, 0
    num_images, seqs = 1, []
    while i < len(blob):
        length = struct.unpack(">I", blob[i:i + 4])[0]
        tag = blob[i + 4:i + 8]
        data = blob[i + 8:i + 8 + length]
        crc = struct.unpack(">I", blob[i + 8 + length:i + 12 + length])[0]
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF
        tags.append(tag.decode())
        if tag == b"IHDR":
            w, h, depth, color = struct.unpack(">IIBB", data[:10])
        elif tag == b"acTL":
            num_images, _ = struct.unpack(">II", data)
        elif tag == b"fcTL":
            seqs.append(struct.unpack(">I", data[:4])[0])
        elif tag == b"fdAT":
            seqs.append(struct.unpack(">I", data[:4])[0])
            fdat.append(data[4:])
        elif tag == b"IDAT":
            idat += data
        i += 12 + length
    assert seqs == list(range(len(seqs)))
    snapshots = []
    for payload in [idat] + fdat:
        raw = zlib.decompress(payload)
        stride = w * 3 + 1
        snapshots.append(np.stack([np.frombuffer(raw[y * stride + 1:(y + 1) * stride], np.uint8) for y in range(h)]).reshape(h, w, 3))
    return w, h, num_images, snapshots, tags


@pytest.fixture
def fake_run(tmp_path, toy):
    """A refcache + a graded run directory with one good case and one black case."""
    refcache = tmp_path / "refcache"
    run = tmp_path / "runs" / "fake"
    cases = []
    for case, family, score in (("good_case", "rects_static", 0.97), ("black_case", "rects_anim", 0.0)):
        ref = [picture(i) for i in range(3)]
        write_output(refcache / case / "n1", ref)
        write_output(run / "cases" / case, ref if case == "good_case" else [np.zeros_like(f) for f in ref])
        (refcache / case / "refcache.json").write_text(json.dumps(
            {"replay": case, "snapshots": 3, "threshold": 0.05, "noise": 0.004, "sensitivity": 0.02,
             "motion_p90": 0.2, "reference_wall_seconds": 1.0}))
        defects = [0.0] * 3 if case == "good_case" else [1.0] * 3
        scores = [m.hill(d, 0.05) for d in defects]
        cases.append({"name": case, "category": "replay", "family": family, "score": score,
                      "detail": {"exit": "ok", "threshold": 0.05, "snapshot_defects": defects, "snapshot_scores": scores,
                                 "first_diverge_snapshot": None if case == "good_case" else 0,
                                 "candidate_wall_seconds": 1.0, "reference_wall_seconds": 1.0}})
    report = {"label": "fake-run", "lib_dir": "/lib", "corpus": "corpus/public", "cache": str(refcache),
              "image": "img", "aggregate": {"categories": {
                  "replay": {"score": 0.485, "weight": 0.6, "effective_weight": 1.0, "n": 2,
                             "cases": {c["name"]: c["score"] for c in cases}}},
                  "overall": 0.485, "n": 2, "categories_present": ["replay"], "full_success": False},
              "cases": cases}
    (run / "report.json").write_text(json.dumps(report))
    return run, refcache


# ---------------------------------------------------------------- colormap

def test_below_the_floor_is_neutral_and_the_top_is_red():
    below = ex.defect_colormap(np.array([0.0, 0.15 - 1e-6]), floor=0.15)
    assert (below == np.array(ex.NEUTRAL, np.uint8)).all()
    top = ex.defect_colormap(1.0)
    assert top[0] > 2 * int(top[1]) and top[0] > 2 * int(top[2])
    assert not np.array_equal(ex.defect_colormap(0.15 + 1e-6, floor=0.15), np.array(ex.NEUTRAL, np.uint8))


def test_colormap_is_ordered_and_bounded():
    d = np.linspace(0.0, 1.0, 101)
    rgb = ex.defect_colormap(d, floor=0.15)
    assert rgb.shape == (101, 3) and rgb.dtype == np.uint8
    hot = rgb[:, 0].astype(int) - rgb[:, 2].astype(int)
    above = hot[d >= 0.15]
    assert above[-1] > above[0]


def test_identical_images_give_a_neutral_heatmap_and_black_gives_red(toy):
    scorer = toy.scorer
    ref = picture(1, 64)
    cell, floor = scorer.block_pixels(), scorer.floor()
    same = ex.heatmap_rgb(ex.defect_grid(scorer, ref, ref, ref.shape, cell), ref.shape, cell, floor)
    assert (same == np.array(ex.NEUTRAL, np.uint8)).all()
    black = ex.heatmap_rgb(ex.defect_grid(scorer, ref, np.zeros_like(ref), ref.shape, cell), ref.shape, cell, floor)
    assert black[..., 0].mean() > 150 and black[..., 2].mean() < 90


def test_missing_candidate_snapshot_is_entirely_red(toy):
    scorer = toy.scorer
    ref = picture(2, 64)
    cell = scorer.block_pixels()
    grid = ex.defect_grid(scorer, ref, None, ref.shape, cell)
    assert grid.shape == (64 // cell, 64 // cell) and (grid == 1.0).all()
    heat = ex.heatmap_rgb(grid, ref.shape, cell, scorer.floor())
    assert heat.shape == (64, 64, 3) and (heat == ex.defect_colormap(1.0)).all()


def test_a_scorer_without_block_defects_still_gets_a_grid(toy):
    class NoHeat:
        def block_defects(self, ref, cand):
            return None

        def distance(self, ref, cand):
            return 1.0 if cand is None or (cand == 0).all() else 0.0

    ref = picture(3, 64)
    assert (ex.defect_grid(NoHeat(), ref, ref, ref.shape, 16) == 0.0).all()
    assert (ex.defect_grid(NoHeat(), ref, np.zeros_like(ref), ref.shape, 16) == 1.0).all()


def test_ragged_image_size_is_padded_to_the_full_image(toy):
    scorer = toy.scorer
    ref = picture(4, 40)
    heat = ex.heatmap_rgb(ex.defect_grid(scorer, ref, ref, ref.shape, scorer.block_pixels()), ref.shape,
                          scorer.block_pixels(), scorer.floor())
    assert heat.shape == (40, 40, 3)


# ---------------------------------------------------------------- APNG

def _sequence(n=4, h=5, w=7):
    return [np.full((h, w, 3), (10 * i, 20 * i, 30 * i), np.uint8) for i in range(n)]


def test_apng_round_trips_a_tiny_sequence(tmp_path):
    snapshots = _sequence()
    path = tmp_path / "anim.png"
    size = ex.write_apng(path, snapshots, delay_ms=120)
    assert size == path.stat().st_size
    w, h, num, decoded, tags = decode_png(path.read_bytes())
    assert (w, h) == (7, 5) and num == len(snapshots) == len(decoded)
    assert tags[:3] == ["IHDR", "acTL", "fcTL"] and tags[-1] == "IEND"
    assert tags.count("fcTL") == len(snapshots) and tags.count("fdAT") == len(snapshots) - 1
    for want, got in zip(snapshots, decoded):
        assert np.array_equal(want, got)


def test_single_image_is_a_plain_png(tmp_path):
    path = tmp_path / "one.png"
    ex.write_apng(path, _sequence(1))
    _, _, num, decoded, tags = decode_png(path.read_bytes())
    assert num == 1 and "acTL" not in tags and len(decoded) == 1


def test_apng_rejects_empty_and_ragged_input(tmp_path):
    with pytest.raises(ValueError):
        ex.write_apng(tmp_path / "x.png", [])
    with pytest.raises(ValueError):
        ex.write_apng(tmp_path / "y.png", [np.zeros((4, 4, 3), np.uint8), np.zeros((5, 4, 3), np.uint8)])


def test_hstrip_places_panels_side_by_side():
    a = np.full((4, 3, 3), 10, np.uint8)
    b = np.full((4, 2, 3), 20, np.uint8)
    strip = ex.hstrip([a, b], gap=1)
    assert strip.shape == (4, 6, 3)
    assert (strip[:, :3] == 10).all() and (strip[:, 3] == np.array(ex.GAP_RGB, np.uint8)).all() and (strip[:, 4:] == 20).all()


def test_jsonable_drops_non_finite_numbers():
    assert ex.jsonable({"a": float("inf"), "b": [float("nan"), 1.0], "ratio": float("inf")}) == {"a": None, "b": [None, 1.0], "ratio": None}


def test_slug_and_provider():
    assert ex.slug("Control: Stub (dev)") == "control-stub-dev"
    assert ex.provider_of("claude-opus-5") == "Anthropic" and ex.provider_of("gpt-5.6-sol") == "OpenAI"
    assert ex.provider_of(None) is None


# ---------------------------------------------------------------- export

def test_export_run_writes_the_summary_shape(tmp_path, fake_run, toy):
    run, refcache = fake_run
    site = tmp_path / "site"
    entry = ex.export_run(ex.Run(toy, run), site, animate=True, quiet=True)
    summary = json.loads((site / "results" / "fake-run" / "summary.json").read_text())
    assert summary["candidate"] == "fake-run" and summary["overall"] == 0.485
    assert set(summary["per_testcase"]) == {"good_case", "black_case"}
    good = summary["per_testcase"]["good_case"]
    assert good["section"] == "replay" and good["n_snapshots"] == 3 and len(good["images"]) == 3
    assert (site / "results" / "fake-run" / good["images"][0]["ref"]).is_file()
    assert (site / "results" / "fake-run" / good["images"][0]["heat"]).is_file()
    assert good["anim"] and (site / "results" / "fake-run" / good["anim"]).is_file()
    black = summary["per_testcase"]["black_case"]
    assert black["images"][0]["monochrome_override"] is True
    assert summary["sections"]["replay"]["score"] == 0.485
    assert entry["candidate"] == "fake-run" and entry["baseline"] is False


def test_export_writes_metadata_bands_checkpoints_leaderboard_and_viewer(tmp_path, fake_run, toy):
    run, refcache = fake_run
    site = tmp_path / "site"
    argv = ["--instance", str(toy.root), "--run", str(run), "--site", str(site), "--quiet", "--pictures", "key"]
    assert ex.main(argv) == 0
    out = site / "results" / "fake-run"
    for name in ("summary.json", "metadata.json", "bands.json", "checkpoints/index.json", "checkpoints/curve.json", "index.html"):
        assert (out / name).is_file(), name
    board = json.loads((site / "leaderboard.json").read_text())
    assert [r["candidate"] for r in board] == ["fake-run"]
    assert (site / "viewer.html").read_text().count("toy") >= 1
    assert '["replay", "procedural", "performance"]' in (site / "viewer.html").read_text()
    labels = json.loads((site / "labels.json").read_text())
    assert labels["runs"]["fake-run"]["status"] == "interim"
    html = (site / "index.html").read_text()
    assert "<script" not in html and "http://" not in html and "https://" not in html
    assert "toy" in html


def test_re_export_replaces_rather_than_duplicates_a_leaderboard_row(tmp_path, fake_run, toy):
    run, _ = fake_run
    site = tmp_path / "site"
    for _ in range(2):
        ex.merge_leaderboard(site, [ex.export_run(ex.Run(toy, run), site, animate=False, quiet=True)])
    assert len(json.loads((site / "leaderboard.json").read_text())) == 1


def test_labels_are_created_at_interim_and_never_promoted_by_the_exporter(tmp_path, fake_run, toy):
    run, _ = fake_run
    site = tmp_path / "site"
    runs = [ex.Run(toy, run)]
    ex.export_run(runs[0], site, animate=False, quiet=True)
    labels = ex.sync_labels(site, runs)
    assert labels["runs"]["fake-run"]["status"] == "interim"
    labels["runs"]["fake-run"]["status"] = "record"
    (site / "labels.json").write_text(json.dumps(labels))
    again = ex.sync_labels(site, runs)
    assert again["runs"]["fake-run"]["status"] == "record"


def test_a_control_run_is_labelled_a_control(tmp_path, fake_run, toy):
    run, _ = fake_run
    report = json.loads((run / "report.json").read_text())
    report["label"] = "control-stub-public"
    (run / "report.json").write_text(json.dumps(report))
    site = tmp_path / "site"
    runs = [ex.Run(toy, run)]
    assert runs[0].baseline
    ex.export_run(runs[0], site, animate=False, quiet=True)
    assert ex.sync_labels(site, runs)["runs"]["control-stub-public"]["status"] == "control"


def test_measurement_validity_and_its_reasons_reach_the_site(tmp_path, fake_run, toy):
    run, _ = fake_run
    (run / "attempt.json").write_text(json.dumps({
        "model_requested": "claude-x", "harness": "claude-code", "measurement_valid": False,
        "measurement_invalid_reasons": ["sandbox_lost"],
        "infrastructure_incidents": [{"kind": "sandbox_lost", "time": "t"}],
        "usage": {"cost_usd": 1.5, "cost_basis": "list", "cost_is_billed_charge": False}}))
    site = tmp_path / "site"
    entry = ex.export_run(ex.Run(toy, run), site, animate=False, quiet=True)
    assert entry["measurement_valid"] is False and entry["measurement_invalid_reasons"] == ["sandbox_lost"]
    assert entry["cost"]["is_billed_charge"] is False and "estimate" in entry["cost"]["label"]
    meta = json.loads((site / "results" / "fake-run" / "metadata.json").read_text())
    assert meta["infrastructure_incidents"][0]["kind"] == "sandbox_lost"


def test_a_refcache_without_the_graded_cases_is_an_error_not_an_empty_export(tmp_path, fake_run, toy):
    run, _ = fake_run
    (tmp_path / "other").mkdir()
    with pytest.raises(FileNotFoundError, match="no entry for any"):
        ex.Run(toy, run, refcache=tmp_path / "other")
    report = json.loads((run / "report.json").read_text())
    report.pop("cache")
    (run / "report.json").write_text(json.dumps(report))
    with pytest.raises(FileNotFoundError, match="--refcache"):
        ex.Run(toy, run)


def test_picture_selection_is_stable_for_odd_inputs():
    assert ex.picture_selection(0, []) == []
    assert ex.picture_selection(3, [0.1, 0.9, 0.2], "key") == [0, 1]
    assert ex.picture_selection(3, [], "key") == [0]
    assert ex.picture_selection(3, [0.5, 0.1, 0.1], "first") == [0]
    assert ex.picture_selection(2, [0.0, 0.0], "all") == [0, 1]


def test_the_graded_final_curve_point_is_the_grade_of_record(tmp_path, fake_run, toy):
    run, _ = fake_run
    final = run / "checkpoints" / "final" / "grade"
    final.mkdir(parents=True)
    report = json.loads((run / "report.json").read_text())
    report["aggregate"]["overall"] = 0.5
    (final / "report.json").write_text(json.dumps(report))
    (run / "checkpoint-curve.json").write_text(json.dumps([
        {"index": 1, "id": "final", "elapsed_seconds": 60.0, "trigger": "final", "source_sha256": "x",
         "overall": 0.5, "full_success": False, "build_failure": False, "report": "checkpoints/final/grade/report.json"}]))
    r = ex.Run(toy, run)
    record = r.grade_of_record()
    assert record["source"] == "final_checkpoint" and record["overall"] == 0.5
    assert record["during_run"]["overall"] == 0.485 and record["delta_vs_during_run"] == pytest.approx(0.015)


def test_compare_writes_a_three_panel_figure_per_shared_case(tmp_path, fake_run, toy):
    run, _ = fake_run
    site = tmp_path / "site"
    a, b = ex.Run(toy, run, label="a"), ex.Run(toy, run, label="b")
    index = ex.export_compare(a, b, site, quiet=True)
    assert {row["name"] for row in index["cases"]} == {"good_case", "black_case"}
    assert (site / "compare" / "a__vs__b" / "good_case.png").is_file()
