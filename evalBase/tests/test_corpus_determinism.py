"""The corpus must be the same bytes on every host.

The fast
tests check the two mechanisms (float32 narrowing, the committed corpus is a
fixed point of the writer); the slow one regenerates the toy corpus twice
with numpy's SIMD kernels disabled and compares every file.
"""
import json
import os

import numpy as np
import pytest

from evalbase.corpus import common, determinism


def test_a_one_ulp_libm_difference_serialises_identically():
    glibc, apple = 0.4383711467890774, 0.43837114678907746
    assert glibc != apple
    assert common.round_f32(glibc) == common.round_f32(apple) == 0.43837115


def test_json_floats_are_narrowed_but_doubles_are_kept():
    doc = {"ops": [{"op": "set", "value": 0.4383711467890774},
                   {"op": "data", "double_values": [0.4383711467890774]},
                   {"op": "data", "values": [0.4383711467890774]}]}
    out = common.narrow(doc, double_keys=("double_values",))
    assert out["ops"][0]["value"] == 0.43837115
    assert out["ops"][1]["double_values"] == [0.438371146789077]
    assert out["ops"][2]["values"] == [0.43837115]
    assert out["ops"][0]["op"] == "set"
    assert common.round_f32(float("nan")) != common.round_f32(float("nan"))     # nan stays nan
    assert common.round_f64(float("inf")) == float("inf")


def test_every_float_in_the_committed_public_corpus_is_already_a_float32(toy):
    def floats(node):
        if isinstance(node, float):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from floats(value)
        elif isinstance(node, list):
            for value in node:
                yield from floats(value)

    files = sorted(toy.corpus.public_dir.glob("*.json"))
    assert files
    for path in files:
        doc = json.loads(path.read_text())
        for value in floats(doc):
            assert value == common.round_f32(value), (path.name, value)


def test_the_committed_public_corpus_is_what_the_generators_produce(tmp_path, toy):
    from evalbase.interfaces import CorpusSpec
    spec = CorpusSpec(root=toy.root, gen_dir=toy.corpus.gen_dir, public_dir=tmp_path / "public",
                      hidden_dir=tmp_path / "hidden", assets_dir=tmp_path / "assets",
                      hidden_env=toy.corpus.hidden_env, hidden_default=toy.corpus.hidden_default,
                      hidden_package=toy.corpus.hidden_package, forbidden_hidden_roots=())
    common.generate_all(spec, ["public"], verbose=False)
    made = {p.name: p.read_bytes() for p in (tmp_path / "public").glob("*.json")}
    committed = {p.name: p.read_bytes() for p in toy.corpus.public_dir.glob("*.json")}
    assert made == committed


def test_assets_are_content_addressed(tmp_path, toy):
    from evalbase.interfaces import CorpusSpec
    spec = CorpusSpec(root=tmp_path, gen_dir=tmp_path, public_dir=tmp_path / "p", hidden_dir=tmp_path / "h",
                      assets_dir=tmp_path / "assets")
    writer = common.CorpusWriter(spec, verbose=False)
    a = writer.asset(np.arange(6, dtype=np.float32))
    b = writer.asset(np.arange(6, dtype=np.float32))
    c = writer.asset(np.arange(7, dtype=np.float32))
    assert a == b != c and (tmp_path / "assets" / a).is_file()
    path = writer.write("x", "fam", "replay", [{"op": "set", "v": 0.1 + 0.2}], split="public",
                        assets={a: a[:-4]})
    doc = json.loads(path.read_text())
    assert doc["ops"][0]["v"] == common.round_f32(0.1 + 0.2) and doc["split"] == "public"
    with pytest.raises(ValueError):
        writer.out_dir("secret")


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("EVALBASE_SLOW") != "1", reason="set EVALBASE_SLOW=1 to run it")
def test_both_toy_corpora_are_byte_identical_across_simd_dispatch(toy):
    result = determinism.check(toy.corpus, "both", quiet=True)
    assert result["same"] and not result["bad"]
