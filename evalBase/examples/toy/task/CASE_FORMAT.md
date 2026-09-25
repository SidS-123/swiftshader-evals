# Case format

A case is a JSON object:

```json
{"version": 1, "name": "rects_static_pub_a", "family": "rects_static",
 "category": "replay", "assets": {}, "meta": {}, "split": "public",
 "ops": [ {"op": "canvas", "width": 64, "height": 64, "background": 0.1, "seed": 11},
          {"op": "rect", "id": "r0", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3, "value": 0.8},
          {"op": "draw", "samples": 8},
          {"op": "snapshot", "name": "snap_000"},
          {"op": "query", "what": "bounds", "name": "bounds_0"} ]}
```

The driver hands every op except `draw`, `snapshot` and `query` to
`Painter.apply`; `draw` calls `Painter.draw(samples)`, `snapshot`
writes the last drawn image as `<name>.pgm` (8-bit P5), and `query` calls
`apply` and records the value. `category` is `replay` (snapshots are scored),
`procedural` (queries and error codes are scored) or `performance`
(`meta.timed_run_index` names the draw whose wall time is compared).

The driver writes `ledger.json` beside the snapshots: `exit`, the `events`
(`run` with `wall_seconds`, `snapshot` with `files`, `query` with `value`
or `error`, `error` with `code`) and the `errors` stream.
