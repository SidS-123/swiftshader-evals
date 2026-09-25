"""Restrict the native Codex tool catalog without replacing its agent loop.

Feature switches alone do not disable model-metadata-selected tools in Codex
0.154.x. Keep the exact model and its other metadata, remove the host
capabilities, and record both digests in the attempt.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

BOUNDARY_VERSION = "codex-mcp-v2"
METADATA_OVERRIDES = {
    "apply_patch_tool_type": None,
    "experimental_supported_tools": [],
    "multi_agent_version": None,
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def restricted_catalog(catalog, model):
    matches = [entry for entry in catalog["models"] if entry.get("slug") == model]
    if len(matches) != 1:
        raise RuntimeError("the Codex catalog must contain the exact requested model once: " + model)
    original = matches[0]
    if not set(METADATA_OVERRIDES) <= set(original):
        raise RuntimeError("unsupported Codex model metadata; qualify this CLI before running attempts")
    restricted = copy.deepcopy(original)
    restricted.update(copy.deepcopy(METADATA_OVERRIDES))
    return {"models": [restricted]}, {
        "boundary_version": BOUNDARY_VERSION,
        "model": model,
        "catalog_source": "installed Codex bundled catalog; no model substitution or remote refresh",
        "original_model_sha256": digest(original),
        "restricted_model_sha256": digest(restricted),
        "metadata_overrides": copy.deepcopy(METADATA_OVERRIDES),
    }


def prepare_catalog(executable, model, destination):
    result = subprocess.run([executable, "debug", "models", "--bundled"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Codex must support `debug models --bundled`; the tool boundary was not qualified")
    try:
        catalog, record = restricted_catalog(json.loads(result.stdout), model)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("unsupported Codex catalog; the tool boundary was not qualified") from exc
    destination = Path(destination)
    with destination.open("x", encoding="utf-8") as stream:
        destination.chmod(0o600)
        json.dump(catalog, stream, sort_keys=True)
    record["catalog_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    return record
