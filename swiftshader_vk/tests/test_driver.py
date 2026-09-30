"""vkreplay through the instance's driver: replay, determinism, the trust boundary.

Needs Docker and the ssvk-ref:1 image (and ssvk-ref-stage:vkreplay-deps for the
hostile-ICD build); skipped with EVALBASE_NO_DOCKER=1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "corpus" / "gen"))
sys.path.insert(0, str(ROOT.parent / "evalBase"))

import snapshot as ss                                     # noqa: E402
from common import Case                                    # noqa: E402
from evalbase.corpus.common import CorpusWriter            # noqa: E402
from evalbase.interfaces import CorpusSpec, load_instance  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("EVALBASE_NO_DOCKER") == "1", reason="needs Docker")

CS = """#version 450
layout(local_size_x = 64) in;
layout(set = 0, binding = 0) buffer B { uint v[]; };
void main() { uint i = gl_GlobalInvocationID.x; v[i] = i * i + 7u; }
"""


@pytest.fixture(scope="module")
def inst():
    return load_instance(str(ROOT))


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """One compute case written through the real case builder."""
    d = tmp_path_factory.mktemp("corpus")
    spec = CorpusSpec(root=d, gen_dir=d, public_dir=d / "cases", hidden_dir=d / "h", assets_dir=d / "assets")
    w = CorpusWriter(spec, verbose=False)
    c = Case("t_compute", "compute_arith")
    c.instance()
    c.device()
    c.buffer("out", 1024, ["storage_buffer"])
    c.shader("cs", CS, "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": "out"}]}])
    c.compute_pipeline("p", "pl", "cs")
    c.exec([{"cmd": "bind_pipeline", "pipeline": "p"}, {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "dispatch", "groups": [4]}])
    c.snapshot([{"name": "out", "buffer": "out", "elem": "u32"}])
    c.write(w, "public")
    return d


def _ledger(out: Path) -> dict:
    return json.loads((out / "ledger.json").read_text())


def _values(out: Path) -> np.ndarray:
    snap = ss.read_ssnap(str(out / "snap_000.ssnap"))
    it = snap.item("out")
    return ss.decode(it, snap.raw(it))[0].codes


def test_oracle_replays_and_decodes(inst, corpus, tmp_path):
    r = inst.driver.run(str(corpus / "cases" / "t_compute.json"), str(tmp_path / "o"), str(corpus / "assets"))
    assert r.exit == "ok", r.stderr_tail
    v = _values(tmp_path / "o")
    assert list(v) == [i * i + 7 for i in range(256)]
    calls = _ledger(tmp_path / "o")["calls"]
    assert calls[0][1:] == ["vkCreateInstance", "VK_SUCCESS"]


def test_oracle_is_identical_twice_and_across_thread_counts(inst, corpus, tmp_path):
    outs = []
    for k, mult in enumerate((1, 1, 2)):
        o = tmp_path / f"o{k}"
        assert inst.driver.run(str(corpus / "cases" / "t_compute.json"), str(o), str(corpus / "assets"),
                               sample_mult=mult).exit == "ok"
        outs.append((o / "snap_000.ssnap").read_bytes())
    assert outs[0] == outs[1] == outs[2]


def test_missing_candidate_library_is_not_a_crash(inst, corpus, tmp_path):
    cand = tmp_path / "cand"
    cand.mkdir()
    r = inst.driver.run(str(corpus / "cases" / "t_compute.json"), str(tmp_path / "o"), str(corpus / "assets"),
                        candidate=str(cand))
    L = _ledger(tmp_path / "o")
    assert L["exit"] == "ok"
    assert L["calls"][0][1:] == ["vkCreateInstance", "VK_ERROR_INCOMPATIBLE_DRIVER"]
    snaps = [e for e in L["events"] if e["op"] == "snapshot"]
    assert snaps and all(i["missing"] for e in snaps for i in e["items"])
    assert r.exit == "ok"


def test_malformed_case_is_a_driver_error(inst, tmp_path):
    case = tmp_path / "bad.json"
    case.write_text(json.dumps({"ops": [{"op": "instance"}, {"op": "no_such_op"}]}))
    (tmp_path / "assets").mkdir()
    r = inst.driver.run(str(case), str(tmp_path / "o"), str(tmp_path / "assets"))
    assert r.exit == "driver_error"
    assert "no_such_op" in _ledger(tmp_path / "o")["error"]


def _deps_image() -> bool:
    return subprocess.run(["docker", "image", "inspect", "ssvk-ref-stage:vkreplay-deps"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _deps_image(), reason="needs ssvk-ref-stage:vkreplay-deps (images/build_ref.sh vkreplay-deps)")
def test_hostile_candidate_cannot_touch_the_outputs(inst, corpus, tmp_path):
    cand = tmp_path / "cand"
    cand.mkdir()
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{ROOT / 'driver' / 'tests'}:/src:ro", "-v", f"{cand}:/c", "ssvk-ref-stage:vkreplay-deps",
                    "gcc", "-shared", "-fPIC", "-O1", "-I/opt/vk/include", "/src/hostile_icd.c",
                    "-o", "/c/libvk_candidate.so"], check=True, capture_output=True)
    out = tmp_path / "o"
    out.mkdir()
    t0 = time.time()
    r = inst.driver.run(str(corpus / "cases" / "t_compute.json"), str(out), str(corpus / "assets"),
                        candidate=str(cand))
    assert time.time() - t0 < 30
    L = _ledger(out)
    assert L["exit"] == "crash" and L["child"].get("signal") == 11
    assert "hostile" not in L
    time.sleep(4)                      # the hostile background writer would act after 3 s
    assert sorted(p.name for p in out.iterdir()) == ["ledger.json"]
    assert "Permission denied" in r.stderr_tail
