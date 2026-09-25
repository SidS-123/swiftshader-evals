"""Shared fixtures: the toy instance, a Docker-free sandbox, a minimal workspace.

The suite needs no Docker. Tests that exercise a real container are marked
`docker` and skip when the daemon is absent or `EVALBASE_NO_DOCKER=1` is set.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOY = ROOT / "examples" / "toy"

from evalbase.interfaces import load_instance  # noqa: E402


def docker_available() -> bool:
    if os.environ.get("EVALBASE_NO_DOCKER"):
        return False
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_docker = pytest.mark.skipif(not docker_available(),
                                  reason="Docker is unavailable or EVALBASE_NO_DOCKER is set")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: minutes, not seconds -- runs only when EVALBASE_SLOW=1 is set")
    config.addinivalue_line("markers", "docker: needs a working Docker daemon")


@pytest.fixture(scope="session")
def toy():
    """The toy instance, loaded once. Its driver runs locally (TOY_SANDBOX=none)."""
    os.environ.setdefault("TOY_SANDBOX", "none")
    return load_instance(str(TOY / "instance.py"))


class FakeSandbox:
    """Runs commands in the workspace directory instead of a container.

    Tool boundaries (paths, caps, dispatch, MCP framing) are exercised for
    real; only the container is replaced, so the suite needs no Docker.
    """

    def __init__(self, workspace, **_):
        self.workspace = Path(workspace)
        self.name = "fake-sandbox"
        self.image = "fake"
        self.argv = ["fake"]
        self.commands = []
        self.closed = False

    def shell(self, command, timeout=120, cwd=None):
        if not isinstance(command, str) or not command.strip():
            raise ValueError("invalid shell command")
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("timeout_seconds must be 1..600")
        self.commands.append(command)
        command = command.replace("/task/", str(self.workspace) + "/").replace(" /task", " " + str(self.workspace))
        proc = subprocess.run(["bash", "-lc", command], cwd=self.workspace,
                              capture_output=True, text=True, timeout=timeout + 5)
        output = (proc.stdout or "") + (proc.stderr or "")
        return {"exit_code": proc.returncode, "output": output[:32768],
                "truncated": len(output) > 32768, "output_bytes": len(output),
                "timed_out": False}

    def paused(self):
        class _P:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, *exc):
                return False
        return _P()

    def close(self):
        self.closed = True


def make_workspace(tmp_path, instance, with_reference=False):
    """A minimal workspace: the toy starter + one public case, no reference outputs."""
    from evalbase.harness import workspace as ws
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    shutil.copyfile(TOY / "corpus" / "public" / "rects_static_pub_a.json", corpus / "dev_case.json")
    doc = json.loads((corpus / "dev_case.json").read_text())
    doc["name"] = "dev_case"
    (corpus / "dev_case.json").write_text(json.dumps(doc))
    destination = tmp_path / "task"
    ws.populate(instance, destination, corpus=corpus, assets=tmp_path / "assets",
                refcache=tmp_path / "refcache", require_reference=False)
    return destination, corpus


def make_session(tmp_path, instance, workspace=None, corpus=None):
    """A ToolSession wired to the fake sandbox, for boundary tests."""
    from evalbase.harness.privacy import Redactor
    from evalbase.harness.tools import ToolSession

    class Session(ToolSession):
        def __init__(self, run_dir, workspace):
            self.instance = instance
            self.run_dir = Path(run_dir)
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.workspace = Path(workspace)
            self.options = SimpleNamespace(max_seconds=600, checkpoint_seconds=900)
            self.state = {"checkpoint_index": 0, "oracle_index": 0, "grade_index": 0,
                          "checkpoints": []}
            self.sandbox = FakeSandbox(self.workspace)
            self.redactor = Redactor(root=instance.root)
            self.started = time.monotonic()
            self.last_checkpoint = 0.0
            self.events = []

        def elapsed(self):
            return time.monotonic() - self.started

        def save(self):
            pass

        def log(self, event, **data):
            self.events.append((event, data))

    if workspace is None:
        workspace, corpus = make_workspace(tmp_path, instance)
    session = Session(tmp_path / "run", workspace)
    if corpus is not None:
        session.corpus_public = corpus
    return session
