"""One JSON encoder for every report the evaluation writes or hands the model.

Plain `json.dumps` is wrong here in two different directions, and both of them
have bitten:

* A performance case that timed out, or that failed the correctness gate,
  carries `ratio = inf` (evalbase/grader/runner.py:grade_case). `json.dumps`
  writes that as a bare `Infinity`, which is not JSON -- `JSON.parse` refuses it
  and so does every strict reader -- and `allow_nan=False` refuses to write it
  at all. A `grade_dev` tool that used `allow_nan=False` once handed the model
  `Out of range float values are not JSON compliant: inf` instead of its dev
  score for the rest of an attempt. That is a fairness defect, not a cosmetic
  one.
* numpy scalars (`np.float32` out of the metrics) are not JSON either.

So every writer goes through here. A non-finite float becomes `null` -- JSON's
only way of saying "no number" -- and for `ratio`, where a bare null would leave
the reader guessing, a sibling `ratio_note` says why it is missing.

Scores are never touched. The sanitizer runs at serialization time on a copy,
long after `runner.aggregate()` has read the real `inf` (its full-success bar
compares `ratio <= 8`, which needs the float). `score` stays the 0.0 that
`metrics.perf_score(inf)` already returns.

`strict=True` turns the substitution off and lets `allow_nan=False` raise. The
refcache is written that way: a threshold that is not a number must stop a
refcache build, never be cached as `null` for later runs to score against.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

# Keys whose null needs an explanation, and the sibling key that carries it.
NOTE_KEYS = {"ratio": "ratio_note"}
RATIO_NOTE = "infinite (timeout or correctness gate)"
NAN_NOTE = "not a number (no usable measurement)"


def _scalar(value):
    """numpy scalar -> Python scalar; everything else unchanged."""
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return value


def _note_for(value: float) -> str:
    return NAN_NOTE if math.isnan(value) else RATIO_NOTE


def jsonable(obj, *, annotate: bool = True):
    """A copy of `obj` that `json.dumps(..., allow_nan=False)` can write.

    Non-finite floats become None; numpy scalars become Python ones. With
    `annotate`, a non-finite `ratio` also gets a `ratio_note` beside it (unless
    the caller already wrote one).
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            key = str(key)
            out[key] = jsonable(value, annotate=annotate)
            note_key = NOTE_KEYS.get(key)
            raw = _scalar(value)
            if (annotate and note_key and out[key] is None and note_key not in obj
                    and isinstance(raw, float) and not math.isfinite(raw)):
                out[note_key] = _note_for(raw)
        return out
    if isinstance(obj, (list, tuple)):
        return [jsonable(v, annotate=annotate) for v in obj]
    obj = _scalar(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def dumps(obj, *, indent=None, strict: bool = False, annotate: bool = True, **kwargs) -> str:
    """Serialize a report. Never emits `Infinity`/`NaN`; never raises on one.

    `strict=True` skips the substitution, so a non-finite number raises the
    usual `ValueError` instead of being written as null.
    """
    payload = _numpy_only(obj) if strict else jsonable(obj, annotate=annotate)
    kwargs.setdefault("default", str)
    return json.dumps(payload, indent=indent, allow_nan=False, **kwargs)


def _numpy_only(obj):
    """Like `jsonable`, but keeps non-finite floats so `allow_nan=False` can refuse them."""
    if isinstance(obj, dict):
        return {str(k): _numpy_only(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_numpy_only(v) for v in obj]
    return _scalar(obj)


def write_json(path, obj, *, indent=1, strict: bool = False, mode: int | None = None) -> Path:
    """Write one report. The trailing newline keeps the files diffable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj, indent=indent, strict=strict) + "\n")
    if mode is not None:
        path.chmod(mode)
    return path


def atomic_write_json(path, obj, *, indent=None, mode=0o600, ensure_ascii=False) -> Path:
    """Write via a temporary file and rename, so a reader never sees half a report."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps(obj, indent=indent, ensure_ascii=ensure_ascii) + "\n")
    temporary.chmod(mode)
    os.replace(temporary, path)
    return path
