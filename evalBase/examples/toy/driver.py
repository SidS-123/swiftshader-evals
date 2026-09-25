"""The toy driver: replays one case against the oracle or a candidate.

    python3 driver.py <case.json> <outdir> [<assets_dir>] [--candidate DIR]

Without `--candidate` the oracle (`oracle.py` beside this file) is used. With
it, `<DIR>/candidate.py` is loaded by path (with DIR on sys.path so a
candidate may ship helper modules) and must define `Painter`.

Environment: `TOY_SAMPLE_MULT` multiplies the oracle's sample count (the
reference cache runs the oracle at 1 and 2), `TOY_PERTURB` is `halfsamples` (every
draw at half the samples) or `jitter` (the camera jittered by 1/256 of the
image) -- both are reference-only perturbations the grader uses to derive
tolerance. This is the only program that calls the candidate; it never reads
the candidate's stdout, and everything it observes goes into `ledger.json`
under the ledger contract (`evalbase.interfaces`).

The driver is deliberately unforgiving: an exception anywhere in the candidate
ends the case with `exit: crash`, and the snapshots written before it are the
only ones scored.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import traceback

JITTER = 1.0 / 256.0


def load_painter(candidate: str | None):
    here = os.path.dirname(os.path.abspath(__file__))
    if candidate is None:
        path, name = os.path.join(here, "oracle.py"), "toy_oracle"
    else:
        candidate = os.path.abspath(candidate)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
        path, name = os.path.join(candidate, "candidate.py"), "toy_candidate"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Painter()


def write_pgm(path: str, img) -> None:
    h = len(img)
    w = len(img[0]) if h else 0
    data = bytearray()
    for row in img:
        if len(row) != w:
            raise ValueError("ragged image")
        for v in row:
            v = float(v)
            q = int(round(v * 255.0)) if v == v else 0
            data.append(0 if q < 0 else (255 if q > 255 else q))
    with open(path, "wb") as f:
        f.write(f"P5\n{w} {h}\n255\n".encode("ascii"))
        f.write(bytes(data))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    candidate = None
    if "--candidate" in argv:
        i = argv.index("--candidate")
        candidate = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    case_path, outdir = argv[0], argv[1]
    os.makedirs(outdir, exist_ok=True)
    mult = int(os.environ.get("TOY_SAMPLE_MULT", "1") or 1)
    perturb = os.environ.get("TOY_PERTURB", "")
    with open(case_path) as f:
        case = json.load(f)
    ledger = {"replay": case.get("name"), "exit": "driver_error", "events": [], "errors": [],
              "sample_mult": mult, "perturbation": perturb}

    def finish(code: int) -> int:
        with open(os.path.join(outdir, "ledger.json"), "w") as fh:
            json.dump(ledger, fh, indent=1)
        return code

    try:
        painter = load_painter(candidate)
    except Exception:
        ledger["exit"] = "crash"
        ledger["traceback"] = traceback.format_exc()[-4000:]
        return finish(1)
    count = 0
    try:
        for index, op in enumerate(case.get("ops", [])):
            kind = op.get("op")
            if kind == "draw":
                samples = int(op.get("samples", 1)) * mult
                if perturb == "halfsamples":
                    samples = max(1, samples // 2)
                if perturb == "jitter":
                    painter.apply({"op": "set", "id": "camera", "key": "jitter", "value": [JITTER, JITTER]})
                t0 = time.perf_counter()
                image = painter.draw(samples)
                wall = time.perf_counter() - t0
                ledger["events"].append({"op": "run", "index": index, "samples": samples,
                                         "wall_seconds": wall})
                last = image
            elif kind == "snapshot":
                name = op.get("name") or f"snap_{count:03d}"
                fname = f"{name}.pgm"
                write_pgm(os.path.join(outdir, fname), last)
                ledger["events"].append({"op": "snapshot", "index": index, "name": name,
                                         "files": {"gray": fname}})
                count += 1
            elif kind == "query":
                value = painter.apply(op)
                event = {"op": "query", "index": index, "name": op.get("name"), "what": op.get("what")}
                if isinstance(value, dict) and "error" in value:
                    event["error"] = value["error"]
                    ledger["errors"].append({"index": index, "code": value["error"]})
                else:
                    event["value"] = value
                ledger["events"].append(event)
            else:
                result = painter.apply(op)
                if isinstance(result, dict) and "error" in result:
                    ledger["errors"].append({"index": index, "code": result["error"]})
                    ledger["events"].append({"op": "error", "index": index, "code": result["error"]})
        ledger["exit"] = "ok"
        return finish(0)
    except Exception:
        ledger["exit"] = "crash"
        ledger["traceback"] = traceback.format_exc()[-4000:]
        return finish(1)


if __name__ == "__main__":
    raise SystemExit(main())
