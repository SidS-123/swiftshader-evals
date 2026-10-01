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

#: The instance directory (swiftshader_vk/). EVALBASE_INSTANCE wins: evalBase's corpus
#: determinism check runs a copy of gen/ from a temporary directory.
ROOT = Path(os.environ.get("EVALBASE_INSTANCE") or Path(__file__).resolve().parents[2])
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
                     stencil_view=None, stencil_clear=0, load="clear", body=(), resolve_views=None):
    """begin_rendering ... body ... end_rendering, with clears (and MSAA resolves)."""
    colors = []
    for k, v in enumerate(color_views):
        a = {"view": v, "load": load, "clear": clear_value(clear)}
        if resolve_views and resolve_views[k]:
            a["resolve_view"] = resolve_views[k]
        colors.append(a)
    begin = {"cmd": "begin_rendering", "area": list(area), "color": colors}
    if depth_view:
        begin["depth"] = {"view": depth_view, "load": load, "clear": {"depth": depth_clear, "stencil": stencil_clear}}
    if stencil_view:
        begin["stencil"] = {"view": stencil_view, "load": load, "clear": {"depth": depth_clear, "stencil": stencil_clear}}
    return [begin, *body, {"cmd": "end_rendering"}]


def clear_value(clear):
    """A colour clear value: a 4-tuple of floats, or {"u32"/"i32": [...]} for integer targets."""
    if isinstance(clear, dict):
        return clear
    return {"f32": [float(x) for x in clear]}


def rng(seed: int) -> np.random.RandomState:
    return np.random.RandomState(int(seed) & 0x7FFFFFFF)


# ---------------------------------------------------------------- spec-bounded results
# Allowances from the Vulkan spec's "Precision of Individual Operations" (Vulkan-Docs
# v1.4.357, appendices/spirvenv.adoc), in the scorer's units: ULPs of max(|ref|, 1/16)
# for 32-bit floats. Absolute bounds are converted at the 1/16 floor (ulp = 2^-27),
# the worst case. Only snapshot items holding these results carry an allowance
# (docs/DESIGN.md, 2026-09-30 "allow policy").

def spec_allow(op: str, x_max: float = 1.0) -> dict:
    """Twice the spec's bound: the reference and a candidate may each err by the bound,
    in opposite directions."""
    floor_ulp = 2.0 ** -27
    table = {
        "div": 3,                                   # 2.5 ULP
        "inversesqrt": 2,
        "sqrt": 3,                                  # inherited from 1/inversesqrt
        "exp": int(3 + 2 * abs(x_max)) + 1,         # 3 + 2|x| ULP
        "exp2": int(3 + 2 * abs(x_max)) + 1,
        "log": int(2.0 ** -21 / floor_ulp) + 3,     # 3 ULP outside [0.5, 2], 2^-21 absolute inside
        "log2": int(2.0 ** -21 / floor_ulp) + 3,
        "sin": int(2.0 ** -11 / floor_ulp),         # 2^-11 absolute in [-pi, pi]
        "cos": int(2.0 ** -11 / floor_ulp),
        "atan": 4096,                               # 4096 ULP
        "pow": 8192,                                # inherited: exp2(y * log2(x)), moderate ranges
        "sinh": 8192, "cosh": 8192, "tanh": 8192,   # inherited from exp
    }
    if op not in table:
        raise KeyError(f"no spec allowance recorded for {op}")
    return {"ulp": 2 * table[op]}


# ---------------------------------------------------------------- compute builder

def compute_case(name: str, family: str, *, source: str, buffers: list, dispatches: list,
                 push_bytes: int = 0, spec: list | None = None, features: dict | None = None,
                 extensions: list | None = None, category: str = "replay", meta: dict | None = None,
                 extra_setup=None, extra_bindings: list | None = None, extra_writes: list | None = None) -> Case:
    """One compute pipeline over named buffers, run by one or more dispatches.

    buffers: [{"name", "size", "usage" (["storage_buffer"]), "array" (np array uploaded) or None,
               "binding" (int; omit for buffers the shader does not bind), "type" ("storage_buffer")}]
    dispatches: [{"groups": [x, y, z], "push": {data} or None, "snapshot": [items] or None}]
    A dispatch's snapshot is taken after it completes, so a case is a sequence.
    """
    c = Case(name, family, category, meta)
    c.instance()
    c.device(features=features, extensions=extensions)
    bindings, writes = [], []
    for b in buffers:
        c.buffer(b["name"], b["size"], b.get("usage", ["storage_buffer"]))
        if b.get("array") is not None:
            c.upload(b["name"], array=b["array"])
        if b.get("binding") is not None:
            t = b.get("type", "storage_buffer")
            bindings.append({"binding": b["binding"], "type": t, "stages": ["compute"]})
            writes.append({"binding": b["binding"], "type": t, "buffers": [{"buffer": b["name"]}]})
    if extra_setup:
        extra_setup(c)
    bindings += extra_bindings or []
    writes += extra_writes or []
    c.shader("cs", source, "comp")
    if bindings:
        c.desc_layout("dl", bindings)
    pcs = [{"stages": ["compute"], "offset": 0, "size": push_bytes}] if push_bytes else None
    c.pipeline_layout("pl", ["dl"] if bindings else [], push_constants=pcs)
    if bindings:
        c.desc_set("ds", "dl", writes)
    c.compute_pipeline("p", "pl", "cs", spec=spec)
    for d in dispatches:
        cmds = [{"cmd": "bind_pipeline", "pipeline": "p"}]
        if bindings:
            cmds.append({"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]})
        if d.get("push") is not None:
            cmds.append({"cmd": "push_constants", "layout": "pl", "stages": ["compute"], "data": d["push"]})
        cmds += d.get("pre", [])
        cmds.append({"cmd": "dispatch", "groups": list(d["groups"])})
        cmds += d.get("post", [])
        c.exec(cmds)
        if d.get("snapshot"):
            c.snapshot(d["snapshot"])
    return c


# ---------------------------------------------------------------- raster builder

def raster_scene(name: str, family: str, *, passes: list, size=(64, 64), targets=("R8G8B8A8_UNORM",),
                 depth: str | None = None, stencil: bool = False, samples: int = 1,
                 features: dict | None = None, extensions: list | None = None, category: str = "replay",
                 meta: dict | None = None, vs: str = FULLSCREEN_VS, fs: str = COLOR_FS,
                 vertex_input: dict | None = None, clear=(0.0, 0.0, 0.0, 1.0), clear_depth: float = 1.0,
                 clear_stencil: int = 0, snapshot_depth: bool = True, extra_setup=None,
                 pipeline_layout: dict | None = None, perturb: bool = True) -> Case:
    """Draw passes into colour targets (and depth/stencil), snapshotting after each pass.

    pass: {"verts": [((x, y, z, w), (r, g, b, a)), ...] or "data": [f32...],
           "pipeline": {graphics_pipeline overrides}, "vs", "fs", "vertex_input",
           "draw": [cmds] (default: draw all vertices), "pre": [cmds before the draw, inside rendering],
           "load": "clear" | "load" (default: clear for the first pass, load after),
           "snapshot": True}
    With samples > 1 the targets are multisampled and resolved (average) into
    single-sample images at the end of every pass; the resolved images are snapshotted.
    """
    W, H = size
    c = Case(name, family, category, meta)
    c.instance()
    c.device(features=features, extensions=extensions, raster=True)
    views, resolves, snap_imgs = [], [], []
    for k, f in enumerate(targets):
        if samples > 1:
            c.image(f"ms{k}", f, [W, H], ["color_attachment"], samples=samples, auto_usage=False)
            c.view(f"ms{k}_v", f"ms{k}")
            c.image(f"color{k}", f, [W, H], ["color_attachment"])
            c.view(f"color{k}_v", f"color{k}")
            views.append(f"ms{k}_v")
            resolves.append(f"color{k}_v")
        else:
            c.image(f"color{k}", f, [W, H], ["color_attachment"])
            c.view(f"color{k}_v", f"color{k}")
            views.append(f"color{k}_v")
        snap_imgs.append(f"color{k}")
    if depth:
        if samples > 1:
            c.image("depth", depth, [W, H], ["depth_stencil_attachment"], samples=samples, auto_usage=False)
        else:
            c.image("depth", depth, [W, H], ["depth_stencil_attachment"])
        c.view("depth_v", "depth")
    if extra_setup:
        extra_setup(c)
    c.pipeline_layout("pl", **(pipeline_layout or {}))
    shader_names = {}

    def shader(src, stage):
        key = (src, stage)
        if key not in shader_names:
            n = f"{stage[0]}s{len(shader_names)}"
            c.shader(n, src, "vert" if stage == "vs" else "frag")
            shader_names[key] = n
        return shader_names[key]

    stencil_only = depth == "S8_UINT"
    rendering = {"color_formats": list(targets)}
    if depth and not stencil_only:
        rendering["depth_format"] = depth
    if depth and (stencil or stencil_only):
        rendering["stencil_format"] = depth
    for i, p in enumerate(passes):
        nverts = p.get("nverts") or (len(p["verts"]) if p.get("verts") is not None else None)
        vb_names = []
        if p.get("arrays") is not None:
            # raw vertex bytes, one array per binding (formats chosen by the case's vertex_input)
            for j, arr in enumerate(p["arrays"]):
                c.buffer(f"vb{i}_{j}", max(16, arr.nbytes), ["vertex_buffer"])
                c.upload(f"vb{i}_{j}", array=arr)
                vb_names.append(f"vb{i}_{j}")
        else:
            data = p.get("data") if p.get("data") is not None else pos_col_vertices(p["verts"])
            c.buffer(f"vb{i}", 4 * max(1, len(data)), ["vertex_buffer"])
            c.upload(f"vb{i}", data={"f32": data},
                     perturb=POSITION_PERTURB if perturb and p.get("verts") is not None else None)
            vb_names.append(f"vb{i}")
        index_cmds = []
        if p.get("index") is not None:
            arr, itype = p["index"]
            c.buffer(f"ib{i}", max(16, arr.nbytes), ["index_buffer"])
            c.upload(f"ib{i}", array=arr)
            index_cmds = [{"cmd": "bind_index_buffer", "buffer": f"ib{i}", "type": itype}]
        for name_, arr in (p.get("extra_buffers") or {}).items():
            c.buffer(name_, max(16, arr.nbytes), ["indirect_buffer", "storage_buffer"])
            c.upload(name_, array=arr)
        kw = dict(vertex_input=p.get("vertex_input") or vertex_input or POS_COL_INPUT,
                  viewport=[0, 0, W, H], rendering=rendering)
        if samples > 1:
            kw["multisample"] = {"samples": samples}
        kw.update(p.get("pipeline", {}))
        if samples > 1 and "multisample" in p.get("pipeline", {}):
            kw["multisample"] = {"samples": samples, **p["pipeline"]["multisample"]}
        c.graphics_pipeline(f"gp{i}", "pl", shader(p.get("vs") or vs, "vs"), shader(p.get("fs") or fs, "fs"), **kw)
        body = [{"cmd": "bind_pipeline", "pipeline": f"gp{i}"},
                {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": b} for b in vb_names]},
                *index_cmds,
                *p.get("pre", []),
                *(p.get("draw") or [{"cmd": "draw", "vertices": nverts}])]
        load = p.get("load") or ("clear" if i == 0 else "load")
        c.exec([*p.get("before", []),
                *render_pass_cmds(views, [0, 0, W, H], clear=clear,
                                  depth_view="depth_v" if (depth and not stencil_only) else None,
                                  depth_clear=clear_depth,
                                  stencil_view="depth_v" if (depth and (stencil or stencil_only)) else None,
                                  stencil_clear=clear_stencil, load=load, body=body,
                                  resolve_views=resolves if samples > 1 else None)])
        if p.get("snapshot", True):
            items = [{"name": f"color{k}", "image": img} for k, img in enumerate(snap_imgs)]
            if depth and samples == 1 and snapshot_depth:
                if not stencil_only:
                    items.append({"name": "depth", "image": "depth", "aspect": "depth",
                                  **({"allow": depth_allow} if depth_allow else {})})
                if stencil or stencil_only:
                    items.append({"name": "stencil", "image": "depth", "aspect": "stencil"})
            c.snapshot(items)
    return c


#: Allowance on D32 depth snapshots. None until Stage 8 decides it from the lavapipe
#: run (depth interpolation precision is not exactly specified; DESIGN.md 2026-09-30).
depth_allow = None


def random_triangles(r: np.random.RandomState, n: int, *, z_range=(0.05, 0.95), alpha=1.0, spread=0.5,
                     extent=1.0):
    """n triangles with random centres in [-extent, extent]^2, one random colour each."""
    vs = []
    for _ in range(n):
        cx, cy = r.uniform(-extent, extent, 2)
        col = (*r.uniform(0, 1, 3), alpha)
        z = r.uniform(*z_range)
        for _ in range(3):
            vs.append(((cx + r.uniform(-spread, spread), cy + r.uniform(-spread, spread), z, 1.0), col))
    return vs


# ---------------------------------------------------------------- texture builder

TEX_VS = """#version 450
layout(location = 0) in vec4 pos;
layout(location = 1) in vec4 uv;
layout(location = 0) out vec4 v_uv;
void main() { gl_Position = pos; v_uv = uv; }
"""

TEXCOORD_PERTURB = {"class": "texcoord", "stride": 32, "offset": 16, "components": 2}


def quad(u0=0.0, v0=0.0, u1=1.0, v1=1.0, x0=-1.0, y0=-1.0, x1=1.0, y1=1.0, w=0.0, q=0.0):
    """Two triangles over [x0, x1] x [y0, y1]; texcoords (u0, v0)..(u1, v1) in xy, w and q in zw."""
    p = [(x0, y0), (x1, y0), (x0, y1), (x1, y0), (x1, y1), (x0, y1)]
    t = [(u0, v0), (u1, v0), (u0, v1), (u1, v0), (u1, v1), (u0, v1)]
    return [((x, y, 0.5, 1.0), (s, tt, w, q)) for (x, y), (s, tt) in zip(p, t)]


def texture_scene(name: str, family: str, *, tex_setup, fs: str, passes: list, sampler: dict | None = None,
                  view: dict | None = None, size=(64, 64), target: str = "R8G8B8A8_UNORM",
                  features: dict | None = None, binding_type: str = "combined_image_sampler",
                  push_bytes: int = 0, category: str = "replay", meta: dict | None = None) -> Case:
    """Draw textured quads; `tex_setup(c)` creates image "tex" (and uploads it).

    pass: {"quads": [quad(...), ...], "push": {data} or None, "load": ..., "snapshot": True}
    The sampled view is "tex_v" (options in `view`), the sampler "s" (`sampler`).
    """
    W, H = size
    c = Case(name, family, category, meta)
    c.instance()
    c.device(features=features, raster=True)
    c.image("color0", target, [W, H], ["color_attachment"])
    c.view("color0_v", "color0")
    tex_setup(c)
    c.view("tex_v", "tex", **(view or {}))
    if binding_type in ("combined_image_sampler", "sampler"):
        c.sampler("s", **(sampler or {}))
    image_info = {"view": "tex_v", "layout": "general"}
    if binding_type == "combined_image_sampler":
        image_info["sampler"] = "s"
    c.desc_layout("dl", [{"binding": 0, "type": binding_type, "stages": ["fragment"]}])
    pcs = [{"stages": ["fragment"], "offset": 0, "size": push_bytes}] if push_bytes else None
    c.pipeline_layout("pl", ["dl"], push_constants=pcs)
    c.desc_set("ds", "dl", [{"binding": 0, "type": binding_type, "images": [image_info]}])
    c.shader("vs", TEX_VS, "vert")
    c.shader("fs", fs, "frag")
    c.graphics_pipeline("gp", "pl", "vs", "fs", vertex_input=POS_COL_INPUT, viewport=[0, 0, W, H],
                        rendering={"color_formats": [target]})
    for i, p in enumerate(passes):
        verts = [v for q in p["quads"] for v in q]
        data = pos_col_vertices(verts)
        c.buffer(f"vb{i}", 4 * len(data), ["vertex_buffer"])
        c.upload(f"vb{i}", data={"f32": data}, perturb=TEXCOORD_PERTURB)
        body = [{"cmd": "bind_pipeline", "pipeline": "gp"},
                {"cmd": "bind_sets", "layout": "pl", "bind_point": "graphics", "sets": ["ds"]},
                {"cmd": "bind_vertex_buffers", "buffers": [{"buffer": f"vb{i}"}]}]
        if p.get("push") is not None:
            body.append({"cmd": "push_constants", "layout": "pl", "stages": ["fragment"], "data": p["push"]})
        body.append({"cmd": "draw", "vertices": len(verts)})
        c.exec(render_pass_cmds(["color0_v"], [0, 0, W, H], load=p.get("load") or ("clear" if i == 0 else "load"),
                                body=body))
        if p.get("snapshot", True):
            c.snapshot([{"name": "color0", "image": "color0"}])
    return c


def upload_mips(c: Case, r: np.random.RandomState, *, image="tex", w=16, h=16, mips=1, layers=1, channels=4,
                dtype=np.uint8, smooth=False, alpha_opaque=True):
    """Random (or smooth) texel data for every mip of every layer of an RGBA8-like image."""
    for m in range(mips):
        mw, mh = max(1, w >> m), max(1, h >> m)
        if smooth:
            yy, xx = np.mgrid[0:mh, 0:mw]
            base = np.stack([xx * 255 // max(1, mw - 1), yy * 255 // max(1, mh - 1),
                             ((xx + yy) * 37 + m * 60) % 256, np.full_like(xx, 255)], axis=-1)
            arr = np.broadcast_to(base, (layers, mh, mw, 4)).astype(dtype)
            arr = (arr + r.randint(0, 8, size=arr.shape)).astype(dtype)
        else:
            arr = r.randint(0, 256, size=(layers, mh, mw, channels)).astype(dtype)
        if alpha_opaque and channels == 4:
            arr[..., 3] = 255
        c.upload_image(image, array=arr, mip=m, layers=layers)


# ---------------------------------------------------------------- shared parameter helpers

def pub_name(family: str, tag: str) -> str:
    return f"{family}_pub_{tag}"


def hid_name(family: str, k: int) -> str:
    """Hidden case names carry no parameters (PLAN_v1.md §11.2)."""
    return f"{family}_hid_{k:02d}"


def write_all(cases, corpus, split):
    for c in cases:
        c.write(corpus, split)
