"""One short real-Docker check: the toy driver in python:3.12-slim.

Skipped when Docker is unavailable or EVALBASE_NO_DOCKER=1 is set. Nothing
bigger than python:3.12-slim is pulled.
"""
import json

import pytest

from conftest import needs_docker

pytestmark = [pytest.mark.docker, needs_docker]


def test_the_toy_driver_runs_the_oracle_and_a_candidate_in_a_container(tmp_path, toy):
    from instance import ToyDriver
    driver = ToyDriver(toy.root, sandbox="docker")
    case = toy.corpus.public_dir / "rects_static_pub_a.json"
    ref = driver.run(str(case), str(tmp_path / "ref"), str(tmp_path / "assets"), timeout_s=300)
    assert ref.exit == "ok", ref.stderr_tail
    assert (tmp_path / "ref" / "snap_000.pgm").is_file()
    assert json.loads((tmp_path / "ref" / "ledger.json").read_text())["exit"] == "ok"
    lib = toy.controls.build("stub", tmp_path / "lib")
    cand = driver.run(str(case), str(tmp_path / "cand"), str(tmp_path / "assets"), candidate=str(lib), timeout_s=300)
    assert cand.exit == "ok"
    local = toy.driver.run(str(case), str(tmp_path / "local"), str(tmp_path / "assets"))
    assert (tmp_path / "local" / "snap_000.pgm").read_bytes() == (tmp_path / "ref" / "snap_000.pgm").read_bytes()
