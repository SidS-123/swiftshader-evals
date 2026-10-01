"""Family `queries_sync`: occlusion and timestamp queries, fences, events, binary and
timeline semaphores, barriers, multiple submissions (replay + procedural).

  occlusion       (procedural) precise occlusion queries around groups of triangles,
                  partly hidden behind a near occluder; results copied to a buffer
                  (snapshotted) and read on the host
  timestamps_events (procedural) timestamps around dispatches (validity only), events
                  set/reset on host and device with vkCmdWaitEvents, fence status and
                  zero-timeout waits
  timeline        (procedural) a timeline-semaphore chain across three submissions with
                  host signal and wait; semaphore values read back
  multi_submit    (replay) one recorded command buffer submitted repeatedly while its
                  inputs change on the host, chained with binary semaphores: a driver
                  that returns stale output fails the later snapshots
"""
from __future__ import annotations

import numpy as np

from common import Case, pub_name, random_triangles, raster_scene, rng, write_all

FAMILY = "queries_sync"
N = 256

PUBLIC = [
    {"tag": "occlusion", "seed": 1701, "variant": "occlusion", "groups": [6, 9, 4, 12],
     "occluder": [[-1.0, -1.0], [1.3, -1.0], [-1.0, 0.9]]},
    {"tag": "timestamps_events", "seed": 1702, "variant": "timestamps_events", "dispatches": 3},
    {"tag": "timeline", "seed": 1703, "variant": "timeline", "steps": [1, 3, 4]},
    {"tag": "multi_submit", "seed": 1704, "variant": "multi_submit", "submits": 4},
]

STEP_CS = f"""#version 450
layout(local_size_x = 64) in;
layout(std430, set = 0, binding = 0) buffer A {{ uint a[{N}]; }};
layout(std430, set = 0, binding = 1) buffer B {{ uint b[{N}]; }};
layout(push_constant) uniform PC {{ uint k; uint dir; }} pc;
void main() {{
  uint i = gl_GlobalInvocationID.x;
  if (pc.dir == 0u) b[i] = a[i] * (2u * pc.k + 1u) + pc.k;
  else a[i] = b[{N - 1}u - i] ^ (pc.k << 24);
}}
"""


def compute_setup(c, r, bufs=("a", "b")):
    for name in bufs:
        c.buffer(name, N * 4, ["storage_buffer"])
        c.upload(name, array=r.randint(0, 2**31, size=N).astype(np.uint32))
    c.shader("cs", STEP_CS, "comp")
    c.desc_layout("dl", [{"binding": 0, "type": "storage_buffer", "stages": ["compute"]},
                         {"binding": 1, "type": "storage_buffer", "stages": ["compute"]}])
    c.pipeline_layout("pl", ["dl"], push_constants=[{"stages": ["compute"], "offset": 0, "size": 8}])
    c.desc_set("ds", "dl", [{"binding": 0, "type": "storage_buffer", "buffers": [{"buffer": bufs[0]}]},
                            {"binding": 1, "type": "storage_buffer", "buffers": [{"buffer": bufs[1]}]}])
    c.compute_pipeline("p", "pl", "cs")


def step(k, direction):
    return [{"cmd": "bind_pipeline", "pipeline": "p"},
            {"cmd": "bind_sets", "layout": "pl", "sets": ["ds"]},
            {"cmd": "push_constants", "layout": "pl", "stages": ["compute"], "data": {"u32": [k, direction]}},
            {"cmd": "dispatch", "groups": [N // 64]}]


def v_occlusion(name, p, r):
    groups = p["groups"]
    # a near occluder over part of the target (`occluder`: its three corners)
    near = [((x, y, 0.1, 1.0), (0.3, 0.3, 0.3, 1.0)) for x, y in p["occluder"]]
    verts, draw = [], []
    q = 0
    for g, n in enumerate(groups):
        tris = random_triangles(r, n, spread=0.6, z_range=(0.3, 0.9))
        draw += [{"cmd": "begin_query", "pool": "occ", "query": q, "precise": True},
                 {"cmd": "draw", "vertices": len(tris), "first_vertex": len(verts)},
                 {"cmd": "end_query", "pool": "occ", "query": q}]
        verts += tris
        q += 1
    nq_first = q
    passes = [{"verts": verts, "draw": draw,
               "before": [{"cmd": "reset_query_pool", "pool": "occ"}],
               "pipeline": {"depth_stencil": {"test": True, "write": True, "compare": "less"}}}]
    # second pass: the occluder, then the same groups again (now partly hidden)
    draw2 = [{"cmd": "draw", "vertices": 3, "first_vertex": len(verts)}]
    for g, n in enumerate(groups):
        first = sum(3 * m for m in groups[:g])
        draw2 += [{"cmd": "begin_query", "pool": "occ", "query": q, "precise": True},
                  {"cmd": "draw", "vertices": 3 * n, "first_vertex": first},
                  {"cmd": "end_query", "pool": "occ", "query": q}]
        q += 1
    passes.append({"verts": verts + near, "draw": draw2, "load": "clear",
                   "pipeline": {"depth_stencil": {"test": True, "write": True, "compare": "less"}}})
    total = q

    def setup(c):
        c.op("query_pool", name="occ", type="occlusion", count=total)
        c.buffer("qres", total * 8, ["transfer_dst"])

    c = raster_scene(name, FAMILY, passes=passes, depth="D32_SFLOAT", category="procedural", extra_setup=setup,
                     features={"VkPhysicalDeviceFeatures": {"occlusionQueryPrecise": True}},
                     meta={"queries": {"first_pass": nq_first, "total": total}})
    c.exec([{"cmd": "copy_query_results", "pool": "occ", "buffer": "qres", "stride": 8, "flags": ["64", "wait"]}])
    c.snapshot([{"name": "qres", "buffer": "qres", "elem": "u64"}])
    c.op("read_queries", name="occ_host", pool="occ")
    return c


def v_timestamps_events(name, p, r):
    n = p["dispatches"]
    c = Case(name, FAMILY, "procedural")
    c.instance()
    c.device()
    compute_setup(c, r)
    c.op("query_pool", name="ts", type="timestamp", count=n + 1)
    cmds = [{"cmd": "reset_query_pool", "pool": "ts"}, {"cmd": "write_timestamp", "pool": "ts", "query": 0}]
    for k in range(n):
        cmds += step(k + 1, k % 2)
        cmds.append({"cmd": "write_timestamp", "pool": "ts", "query": k + 1})
    c.exec(cmds)
    c.op("read_queries", name="ts_host", pool="ts")
    c.snapshot([{"name": "a", "buffer": "a", "elem": "u32"}, {"name": "b", "buffer": "b", "elem": "u32"}])
    # events: host and device set/reset, device wait
    c.op("event", name="e0")
    c.op("event", name="e1")
    c.op("event_status", name="e0_initial", event="e0")
    c.op("set_event", event="e0")
    c.op("event_status", name="e0_host_set", event="e0")
    c.exec([{"cmd": "wait_events", "events": ["e0"], "src_stage": "host"}, *step(9, 0),
            {"cmd": "set_event", "event": "e1"}, {"cmd": "reset_event", "event": "e0"}])
    c.op("event_status", name="e0_after", event="e0")
    c.op("event_status", name="e1_after", event="e1")
    c.op("reset_event", event="e1")
    c.op("event_status", name="e1_host_reset", event="e1")
    c.snapshot([{"name": "b", "buffer": "b", "elem": "u32"}])
    # fences
    c.op("fence", name="f_sig", signaled=True)
    c.op("fence", name="f_un")
    c.op("fence_status", name="f_sig_status", fence="f_sig")
    c.op("fence_status", name="f_un_status", fence="f_un")
    c.op("wait_fence", name="wait_zero", fences=["f_un"], timeout_ns=0)
    c.op("wait_fence", name="wait_any", fences=["f_sig", "f_un"], all=False, timeout_ns=0)
    c.op("reset_fence", fences=["f_sig"])
    c.op("fence_status", name="f_sig_reset", fence="f_sig")
    c.op("record", name="cb", cmds=step(11, 1))
    c.op("submit", cbs=["cb"], fence="f_un")
    c.op("wait_fence", name="wait_done", fences=["f_un"])
    c.op("fence_status", name="f_un_done", fence="f_un")
    c.snapshot([{"name": "a", "buffer": "a", "elem": "u32"}])
    return c


def v_timeline(name, p, r):
    s = p["steps"]
    c = Case(name, FAMILY, "procedural")
    c.instance()
    c.device(features={"VkPhysicalDeviceVulkan12Features": {"timelineSemaphore": True}})
    compute_setup(c, r)
    c.op("semaphore", name="tl", timeline=True, initial=s[0])
    c.op("semaphore_value", name="tl_initial", semaphore="tl")
    c.op("record", name="cb0", cmds=step(5, 0))
    c.op("record", name="cb1", cmds=step(6, 1))
    c.op("record", name="cb2", cmds=step(7, 0))
    v1, v2, v3 = s[0] + s[1], s[0] + s[1] + s[2], s[0] + 2 * s[1] + s[2]
    # cb1 waits for a host signal, cb0 runs first, cb2 waits for cb1
    c.op("submit", cbs=["cb0"], wait=[{"semaphore": "tl", "value": s[0]}], signal=[{"semaphore": "tl", "value": v1}])
    c.op("wait_semaphores", name="wait_v1", semaphores=[["tl", v1]])
    c.op("semaphore_value", name="tl_v1", semaphore="tl")
    c.op("wait_semaphores", name="wait_v2_zero", semaphores=[["tl", v2]], timeout_ns=0)
    c.op("signal_semaphore", semaphore="tl", value=v2)
    c.op("submit", cbs=["cb1"], wait=[{"semaphore": "tl", "value": v2}], signal=[{"semaphore": "tl", "value": v3}])
    c.op("submit", cbs=["cb2"], wait=[{"semaphore": "tl", "value": v3}], signal=[{"semaphore": "tl", "value": v3 + 1}])
    c.op("wait_semaphores", name="wait_any", semaphores=[["tl", v3 + 1], ["tl", v3 + 100]], any=True)
    c.op("semaphore_value", name="tl_final", semaphore="tl")
    c.snapshot([{"name": "a", "buffer": "a", "elem": "u32"}, {"name": "b", "buffer": "b", "elem": "u32"}])
    return c


def v_multi_submit(name, p, r):
    c = Case(name, FAMILY)
    c.instance()
    c.device()
    compute_setup(c, r)
    c.op("semaphore", name="s0")
    c.op("record", name="fwd", cmds=step(3, 0))
    c.op("record", name="back", cmds=step(4, 1))
    for k in range(p["submits"]):
        c.upload("a", array=r.randint(0, 2**31, size=N).astype(np.uint32))
        c.op("fence", name=f"f{k}")
        c.op("submit", cbs=["fwd"], signal=[{"semaphore": "s0"}])
        c.op("submit", cbs=["back"], wait=[{"semaphore": "s0", "stage": "compute_shader"}], fence=f"f{k}")
        c.op("wait_fence", name=f"w{k}", fences=[f"f{k}"])
        c.snapshot([{"name": "a", "buffer": "a", "elem": "u32"}, {"name": "b", "buffer": "b", "elem": "u32"}])
    return c


VARIANTS = {"occlusion": v_occlusion, "timestamps_events": v_timestamps_events, "timeline": v_timeline,
            "multi_submit": v_multi_submit}


def build(name, p):
    return VARIANTS[p["variant"]](name, p, rng(p["seed"]))


def generate(split, corpus):
    if split == "hidden":
        return corpus.hidden_generate(__file__, split)
    write_all([build(pub_name(FAMILY, p["tag"]), p) for p in PUBLIC], corpus, split)
