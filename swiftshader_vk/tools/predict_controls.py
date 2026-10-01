"""Predict every control's scores before any control is run (PLAN_v1.md §12 step 1).

    python swiftshader_vk/tools/predict_controls.py [--json OUT]

No control is run. Each prediction applies the control's documented alteration
(controls/<name>/build.json, controls/common/shim.cpp) to the *structure* of
every case -- its snapshots and their items, image formats, samplers, the
submissions vkreplay makes -- and to the reference's own ledgers in the two
caches (which calls return what, which query events exist, before or after the
device), then aggregates with the grader's rules: replay = mean over families
of mean case scores, procedural and performance = mean over cases, overall =
0.6 / 0.3 / 0.1, a snapshot that fails scores 0, one that is untouched scores 1.

The rules are approximations stated in the code; the predictions in DESIGN.md
are these numbers with a band. A miss is either a wrong rule (restated, dated)
or a metric defect (fixed, new metric version, everything re-measured).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLITS = {"public": (ROOT / "corpus" / "public", ROOT / "runs" / "refcache"),
          "hidden": (ROOT / "corpus" / "hidden", ROOT / "runs" / "refcache-hidden")}
WEIGHTS = {"replay": 0.6, "procedural": 0.3, "performance": 0.1}

GAIN_FORMATS = {"R8_UNORM", "R8_SRGB", "R8G8_UNORM", "R8G8_SRGB", "R8G8B8A8_UNORM", "B8G8R8A8_UNORM",
                "A8B8G8R8_UNORM_PACK32", "R8G8B8A8_SRGB", "B8G8R8A8_SRGB", "A8B8G8R8_SRGB_PACK32",
                "R16_SFLOAT", "R16G16_SFLOAT", "R16G16B16A16_SFLOAT", "R32_SFLOAT", "R32G32_SFLOAT",
                "R32G32B32A32_SFLOAT"}
NONSUCCESS_TURNED = {"VK_ERROR_FORMAT_NOT_SUPPORTED", "VK_TIMEOUT", "VK_NOT_READY", "VK_INCOMPLETE"}
DRAWS = {"draw", "draw_indexed", "draw_indirect", "draw_indexed_indirect"}
GPU_WORK = DRAWS | {"dispatch", "dispatch_base", "dispatch_indirect", "copy_buffer", "copy_image", "blit_image",
                    "resolve_image", "clear_color_image", "clear_depth_stencil_image", "fill_buffer",
                    "update_buffer", "copy_buffer_to_image", "copy_image_to_buffer", "clear_attachments",
                    "begin_rendering", "begin_render_pass"}
#: one-thread / Subzero timed-run ratios to the reference, from the Stage 7 gate (stage7_findings 7.4)
ONE_THREAD_RATIO = {"perf_fill": 2.7, "perf_compute": 3.4, "perf_geometry": 1.5, "perf_texture": 3.0}
SUBZERO_RATIO = {"perf_fill": 1.3, "perf_compute": 4.8, "perf_geometry": 1.0, "perf_texture": 1.3}


def fmt_name(f):
    return str(f).upper().replace("VK_FORMAT_", "")


def hill(x, h, n=4):
    return 1.0 / (1.0 + (x / h) ** n)


class Case:
    def __init__(self, path: Path, cache: Path):
        self.doc = json.loads(path.read_text())
        self.name, self.family, self.category = self.doc["name"], self.doc["family"], self.doc["category"]
        entry = cache / self.name
        self.rc = json.loads((entry / "refcache.json").read_text())
        self.ledger = json.loads((entry / "n1" / "ledger.json").read_text())
        self.images = {}
        self.linear_sampler = False
        self.raster = False
        self.device_op = None
        # snapshots in order: (op index, items, flags)
        self.snaps = []
        submits = 0
        self.crash_op = None
        drawn = False
        work_since = defaultdict(bool)
        reads = defaultdict(int)
        for i, op in enumerate(self.doc["ops"]):
            kind = op["op"]
            if kind == "device" and self.device_op is None:
                self.device_op = i
            if kind == "image":
                self.images[op["name"]] = fmt_name(op["format"])
                submits += 1                                  # layout transition
            if kind == "upload_image":
                submits += 1
            if kind == "sampler" and ("linear" in (op.get("mag"), op.get("min"))):
                self.linear_sampler = True
            if kind == "graphics_pipeline":
                self.raster = True
            cmds = op.get("cmds") or []
            if kind in ("exec", "submit"):
                submits += 1
            if kind == "run":
                submits += int(op.get("warmup", 1)) + int(op["iterations"])
            if any(c.get("cmd") in DRAWS for c in cmds):
                drawn = True
            if kind in ("exec", "record", "run") and any(c.get("cmd") in GPU_WORK for c in cmds) or kind == "upload_image":
                for img in self.images:
                    work_since[img] = True
            if self.crash_op is None and submits >= 2:
                self.crash_op = i
            snap = op if kind == "snapshot" else (op.get("snapshot") if kind == "run" else None)
            if snap:
                items = snap.get("items", [])
                stale = False
                for it in items:
                    if "image" in it:
                        if reads[it["image"]] > 0 and work_since[it["image"]]:
                            stale = True
                        reads[it["image"]] += 1
                        work_since[it["image"]] = False
                        submits += 1                          # one readback copy per image item
                self.snaps.append({"i": i, "items": items, "drawn": drawn, "stale": stale})
                if self.crash_op is None and submits >= 2:
                    self.crash_op = i

    # ----------------------------------------------------------- per-snapshot predicates
    def img_items(self, snap):
        return [(it, self.images.get(it["image"], "")) for it in snap["items"] if "image" in it]

    def fails(self, ctl, snap):
        imgs = self.img_items(snap)
        color = [(it, f) for it, f in imgs if it.get("aspect", "color") == "color"]
        if ctl == "gain_x2":
            return any(f in GAIN_FORMATS for _, f in color)
        if ctl == "srgb_mishandled":
            return any("SRGB" in f for _, f in color)
        if ctl == "half_pixel":
            return self.raster and snap["drawn"]
        if ctl == "nearest_filter":
            return self.linear_sampler and snap["drawn"] or (self.linear_sampler and self.family.startswith("compute"))
        if ctl == "aux_garbage":
            return any(it.get("aspect") in ("depth", "stencil") for it, _ in imgs)
        if ctl == "stale_frame":
            return snap["stale"]
        if ctl in ("init_only", "stub", "malformed", "success_everywhere", "hardcode_hidden"):
            return True
        if ctl == "crash":
            return self.crash_op is not None and snap["i"] >= self.crash_op
        return False

    # ----------------------------------------------------------- procedural
    def query_checks(self):
        """(op index, check name, value) per check the scorer makes from the reference's query events."""
        out = []
        for e in self.ledger.get("events", []):
            if e.get("op") != "query":
                continue
            v = e.get("value")
            if isinstance(v, dict) and len(v) > 1 and e.get("kind") == "query":
                out += [(e.get("i", 0), f"{e['name']}:{k}", sub) for k, sub in v.items()]
            else:
                out.append((e.get("i", 0), e["name"], v))
        return out

    def op_of(self, name):
        for i, op in enumerate(self.doc["ops"]):
            if op.get("name") == name:
                return i
        return 10**9

    def procedural(self, ctl):
        calls = [c for c in self.ledger.get("calls", []) if not str(c[1]).startswith("vkQueueSubmit+")]
        results = {c[2] for c in calls}
        checks = []
        if self.crash_op is not None and ctl == "crash":
            return 0.0
        has_snap = bool(self.snaps)
        if has_snap:
            checks.append(not any(self.fails(ctl, s) for s in self.snaps))
        dev = self.device_op if self.device_op is not None else 10**9
        # the VkResult stream
        if ctl in ("reference", "one_thread", "reference_subzero", "round_trunc", "gain_x2", "srgb_mishandled",
                   "half_pixel", "nearest_filter", "stale_frame", "aux_garbage", "wrong_limits"):
            stream = True
        elif ctl == "wrong_errors":
            stream = not (results & NONSUCCESS_TURNED)
        elif ctl == "success_everywhere":
            stream = not (results & {"VK_TIMEOUT", "VK_NOT_READY"})
        elif ctl == "malformed":
            stream = not any(op["op"] in ("buffer", "image") for op in self.doc["ops"])
        elif ctl == "lavapipe":
            stream = False
        elif ctl in ("init_only", "hardcode_hidden"):
            stream = self.device_op is None                     # no device: the same calls as the reference
        else:                                                  # stub: no physical device at all
            stream = False
        checks.append(stream)
        for i, name, value in self.query_checks():
            base = name.split(":")[0]
            key = name.split(":")[1] if ":" in name else ""
            opi = self.op_of(base)
            ok = True
            if ctl == "wrong_limits":
                ok = key not in ("properties", "formats") and not (base.startswith("formats") and key == "")
            elif ctl == "wrong_errors":
                r = value.get("result") if isinstance(value, dict) else None
                cr = value.get("count_result") if isinstance(value, dict) else None
                ok = r not in NONSUCCESS_TURNED or (r == "VK_INCOMPLETE" and "physical" in base) \
                    or (r == "VK_INCOMPLETE" and "instance" in base)
                ok = ok and cr != "VK_INCOMPLETE"
            elif ctl == "reference_subzero":
                ok = key != "properties"                        # deviceName differs
            elif ctl == "lavapipe":
                ok = key in ("instance_version",) or name.endswith("instance_extensions")
            elif ctl in ("init_only", "hardcode_hidden"):
                ok = opi < dev
            elif ctl == "stub":
                ok = key == "instance_version" or (isinstance(value, dict) and set(value) == {"instance_version"})
            elif ctl == "malformed":
                kind = self.doc["ops"][opi]["op"] if opi < len(self.doc["ops"]) else ""
                ok = kind in ("query", "enumerate", "image_format_props") or not any(
                    op["op"] in ("buffer", "image") for op in self.doc["ops"][:opi])
            elif ctl == "success_everywhere":
                kind = self.doc["ops"][opi]["op"] if opi < len(self.doc["ops"]) else ""
                r = value.get("result") if isinstance(value, dict) else None
                if kind in ("query", "enumerate", "image_format_props"):
                    ok = True
                elif kind in ("fence_status", "wait_fence", "wait_semaphores"):
                    ok = r not in ("VK_TIMEOUT", "VK_NOT_READY")
                elif kind == "event_status":
                    ok = not any(op["op"] in ("exec", "submit") for op in self.doc["ops"][:opi]) or r == "VK_EVENT_RESET"
                else:
                    ok = False                                  # read_queries, semaphore_value after device work
            checks.append(ok)
        return sum(checks) / len(checks) if checks else 0.0

    # ----------------------------------------------------------- per-case score
    def replay(self, ctl):
        if ctl in ("reference", "one_thread", "round_trunc", "wrong_limits", "wrong_errors"):
            return 1.0
        if ctl in ("reference_subzero", "lavapipe"):
            d = self.rc.get("perturbation_defects", {}).get("subzero" if ctl == "reference_subzero" else "lavapipe") or 0.0
            return hill(d, self.rc["threshold"]) if d > 0 else 1.0
        if not self.snaps:
            return 0.0
        return sum(0.0 if self.fails(ctl, s) else 1.0 for s in self.snaps) / len(self.snaps)

    def score(self, ctl):
        if self.category == "procedural":
            if ctl in ("reference", "one_thread", "round_trunc"):
                return 1.0
            return self.procedural(ctl)
        rep = self.replay(ctl)
        if self.category == "performance":
            if rep < 0.5:
                return 0.0
            ratio = {"one_thread": ONE_THREAD_RATIO, "reference_subzero": SUBZERO_RATIO}.get(ctl, {}).get(self.family, 1.0)
            if ctl == "lavapipe":
                return float("nan")                             # not predicted: measured
            return hill(ratio, 16.0)
        return rep


def aggregate(cases, ctl):
    fam = defaultdict(list)
    proc, perf, rep_cases = [], [], []
    for c in cases:
        s = c.score(ctl)
        if c.category == "replay":
            fam[c.family].append(s)
            rep_cases.append(s)
        elif c.category == "procedural":
            proc.append(s)
        else:
            perf.append(s)
    rep = sum(sum(v) / len(v) for v in fam.values()) / len(fam)
    pr = sum(proc) / len(proc)
    pf = sum(perf) / len(perf)
    overall = WEIGHTS["replay"] * rep + WEIGHTS["procedural"] * pr + WEIGHTS["performance"] * (pf if pf == pf else 0.0)
    ratios = {"one_thread": ONE_THREAD_RATIO, "reference_subzero": SUBZERO_RATIO}.get(ctl, {})
    perf_ok = all(s >= 0.5 for s in perf) and all(r <= 8.0 for r in ratios.values()) and ctl != "lavapipe"
    full = min(rep_cases) >= 0.9 and pr >= 0.95 and perf_ok
    return {"replay": rep, "procedural": pr, "performance": pf, "overall": overall, "full_success": full}


CONTROLS = ["reference", "one_thread", "reference_subzero", "lavapipe", "stub", "init_only", "success_everywhere",
            "hardcode_public", "gain_x2", "srgb_mishandled", "half_pixel", "nearest_filter", "round_trunc",
            "stale_frame", "aux_garbage", "wrong_limits", "wrong_errors", "crash", "malformed"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    loaded = {s: [Case(p, cache) for p in sorted(corpus.glob("*.json"))] for s, (corpus, cache) in SPLITS.items()}
    result = {}
    print("| Control | Split | Replay | Procedural | Performance | Overall | Full success |")
    print("|---|---|---|---|---|---|---|")
    for ctl in CONTROLS:
        for split, cases in loaded.items():
            eff = ctl
            if ctl == "hardcode_public":
                eff = "reference" if split == "public" else "hardcode_hidden"
            r = aggregate(cases, eff)
            result[f"{ctl}/{split}"] = r
            pf = "measured" if r["performance"] != r["performance"] else f"{r['performance']:.3f}"
            fs = "measured" if ctl == "lavapipe" else ("yes" if r["full_success"] else "no")
            print(f"| `{ctl}` | {split} | {r['replay']:.3f} | {r['procedural']:.3f} | {pf} | {r['overall']:.3f} | {fs} |")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
