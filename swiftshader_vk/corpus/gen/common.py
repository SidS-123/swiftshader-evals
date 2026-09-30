"""Helpers every family generator shares: a case builder and GLSL -> SPIR-V.

evalBase never runs this module as a family (the name `common` is skipped);
the family modules and the private hidden modules import it.

    from common import Case, glsl
    c = Case("compute_arith_pub_a", "compute_arith", "replay")
    c.instance(); c.device()
    c.buffer("out", 1024, ["storage_buffer"])
    ...
    c.write(corpus, split)

Shaders are compiled with the pinned glslangValidator from the reference
image (images/pins.lock, VULKAN_SDK_TAG) and cached by content under
~/.cache/ssvk/spv, so regenerating a corpus does not start a container per
shader. The cache key includes the SDK tag: a new glslang recompiles.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]            # swiftshader_vk/
PINS = ROOT / "images" / "pins.lock"
REFERENCE_IMAGE = os.environ.get("EVALBASE_IMAGE") or "ssvk-ref:1"
CACHE = Path(os.environ.get("SSVK_SPV_CACHE") or Path.home() / ".cache" / "ssvk" / "spv")

#: Device features every raster case enables (dynamic rendering is how vkreplay renders).
# glslang compiles `discard` to OpDemoteToHelperInvocation for Vulkan 1.3, which needs its feature.
RASTER_FEATURES = {"VkPhysicalDeviceVulkan13Features": {"dynamicRendering": True, "synchronization2": True,
                                                        "shaderDemoteToHelperInvocation": True}}

STAGE_EXT = {"vert": "vert", "frag": "frag", "comp": "comp"}


def _sdk_tag() -> str:
    for line in PINS.read_text().splitlines():
        if line.startswith("VULKAN_SDK_TAG="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("VULKAN_SDK_TAG missing from pins.lock")


def glsl(source: str, stage: str) -> bytes:
    """SPIR-V for a GLSL shader (`stage` is vert, frag or comp), compiled for Vulkan 1.3."""
    if stage not in STAGE_EXT:
        raise ValueError(f"unknown stage {stage}")
    key = hashlib.sha256(f"{_sdk_tag()}\n{stage}\n{source}".encode()).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    hit = CACHE / f"{key}.spv"
    if hit.exists():
        return hit.read_bytes()
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / f"s.{STAGE_EXT[stage]}"
        src.write_text(source)
        os.chmod(d, 0o777)
        r = subprocess.run(["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                            "-v", f"{d}:/w", "--entrypoint", "glslangValidator", REFERENCE_IMAGE,
                            "-V", "--target-env", "vulkan1.3", f"/w/{src.name}", "-o", "/w/s.spv"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"glslang failed for a {stage} shader:\n{r.stdout}{r.stderr}\n--- source:\n{source}")
        spv = (Path(d) / "s.spv").read_bytes()
    tmp = hit.with_suffix(".tmp")
    tmp.write_bytes(spv)
    tmp.replace(hit)
    return spv


class Case:
    """Builds one case document op by op (see task/CASE_FORMAT.md for every op)."""

    def __init__(self, name: str, family: str, category: str = "replay", meta: dict | None = None):
        self.name, self.family, self.category = name, family, category
        self.meta = dict(meta or {})
        self.ops: list[dict] = []
        # The op dicts themselves (not indices): generators may insert ops later.
        self._shaders: list[tuple[dict, bytes]] = []      # (op, spv) resolved at write time
        self.assets: dict[str, dict] = {}
        self._arrays: list[tuple[dict, str, np.ndarray]] = []  # (op, key, array) -> asset
        self.snapshots = 0

    # ------------------------------------------------------------ generic
    def op(self, op: str, **kw) -> dict:
        d = {"op": op, **{k: v for k, v in kw.items() if v is not None}}
        self.ops.append(d)
        return d

    # ------------------------------------------------------------ setup
    def instance(self, **kw):
        return self.op("instance", **kw)

    def device(self, features: dict | None = None, extensions: list | None = None, raster: bool = False):
        f = {k: dict(v) for k, v in (features or {}).items()}
        if raster:
            for k, v in RASTER_FEATURES.items():
                f.setdefault(k, {}).update(v)
        return self.op("device", features=f or None, extensions=extensions)

    # ------------------------------------------------------------ resources
    def buffer(self, name, size, usage=(), **kw):
        return self.op("buffer", name=name, size=int(size), usage=list(usage), **kw)

    def upload(self, buffer, data=None, array: np.ndarray | None = None, **kw):
        d = self.op("upload", buffer=buffer, data=data, **kw)
        if array is not None:
            self._arrays.append((d, "asset", array))
        return d

    def image(self, name, fmt, extent, usage=(), **kw):
        return self.op("image", name=name, format=fmt, extent=list(extent), usage=list(usage), **kw)

    def upload_image(self, image, data=None, array: np.ndarray | None = None, **kw):
        d = self.op("upload_image", image=image, data=data, **kw)
        if array is not None:
            self._arrays.append((d, "asset", array))
        return d

    def view(self, name, image, **kw):
        return self.op("view", name=name, image=image, **kw)

    def sampler(self, name, **kw):
        return self.op("sampler", name=name, **kw)

    def shader(self, name, source: str, stage: str):
        spv = glsl(source, stage)
        d = self.op("shader", name=name)
        self._shaders.append((d, spv))
        return d

    def desc_layout(self, name, bindings, **kw):
        return self.op("desc_layout", name=name, bindings=bindings, **kw)

    def pipeline_layout(self, name, set_layouts=(), push_constants=None):
        return self.op("pipeline_layout", name=name, set_layouts=list(set_layouts) or None, push_constants=push_constants)

    def desc_set(self, name, layout, writes=None):
        return self.op("desc_set", name=name, layout=layout, writes=writes)

    def compute_pipeline(self, name, layout, shader, **kw):
        return self.op("compute_pipeline", name=name, layout=layout, shader=shader, **kw)

    def graphics_pipeline(self, name, layout, vs, fs=None, **kw):
        return self.op("graphics_pipeline", name=name, layout=layout, vs=vs, fs=fs, **kw)

    # ------------------------------------------------------------ execution and output
    def exec(self, cmds, **kw):
        return self.op("exec", cmds=cmds, **kw)

    def snapshot(self, items, name: str | None = None):
        name = name or f"snap_{self.snapshots:03d}"
        self.snapshots += 1
        return self.op("snapshot", name=name, items=items)

    def query(self, name, what: dict):
        return self.op("query", name=name, what=what)

    def run(self, name, iterations, cmds, snapshot_items, warmup=1):
        snap = f"snap_{self.snapshots:03d}"
        self.snapshots += 1
        return self.op("run", name=name, iterations=int(iterations), warmup=int(warmup), cmds=cmds,
                       snapshot={"name": snap, "items": snapshot_items})

    # ------------------------------------------------------------ write
    def write(self, corpus, split: str):
        """Resolve shaders and arrays into content-addressed assets and write through a CorpusWriter."""
        for op, spv in self._shaders:
            fname = corpus.asset(np.frombuffer(spv, dtype="<u4"))
            op["asset"] = {"file": fname}
            self.assets[fname] = {"kind": "spirv"}
        for op, key, arr in self._arrays:
            fname = corpus.asset(arr)
            op[key] = {"file": fname}
            self.assets[fname] = {"kind": "data", "dtype": str(arr.dtype), "count": int(arr.size)}
        return corpus.write(self.name, self.family, self.category, self.ops, split=split,
                            meta=self.meta or None, assets=self.assets)


# ---------------------------------------------------------------- common scene pieces

FULLSCREEN_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 col;
layout(location = 0) out vec4 v_col;
void main() { gl_Position = pos; v_col = col; }
"""

COLOR_FS = """#version 450
layout(location = 0) in vec4 v_col;
layout(location = 0) out vec4 o;
void main() { o = v_col; }
"""


def pos_col_vertices(verts):
    """Interleave [(x, y, z, w), (r, g, b, a)] pairs into one f32 list (stride 32)."""
    out = []
    for p, c in verts:
        out += [float(x) for x in p] + [float(x) for x in c]
    return out


POS_COL_INPUT = {"bindings": [{"binding": 0, "stride": 32}],
                 "attributes": [{"location": 0, "format": "R32G32B32A32_SFLOAT", "offset": 0},
                                {"location": 1, "format": "R32G32B32A32_SFLOAT", "offset": 16}]}
POSITION_PERTURB = {"class": "position", "stride": 32, "offset": 0, "components": 2}


def render_pass_cmds(color_views, area, clear=(0.0, 0.0, 0.0, 1.0), depth_view=None, depth_clear=1.0,
                     stencil_view=None, stencil_clear=0, load="clear", body=()):
    """begin_rendering ... body ... end_rendering, with clears."""
    begin = {"cmd": "begin_rendering", "area": list(area),
             "color": [{"view": v, "load": load, "clear": {"f32": list(clear)}} for v in color_views]}
    if depth_view:
        begin["depth"] = {"view": depth_view, "load": load, "clear": {"depth": depth_clear, "stencil": stencil_clear}}
    if stencil_view:
        begin["stencil"] = {"view": stencil_view, "load": load, "clear": {"depth": depth_clear, "stencil": stencil_clear}}
    return [begin, *body, {"cmd": "end_rendering"}]
