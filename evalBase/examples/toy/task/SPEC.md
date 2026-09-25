# Painter specification

`candidate.py` defines `class Painter` with no-argument construction and:

- `apply(op: dict) -> None | value | {"error": code}`: apply one op (below).
- `draw(samples: int) -> list[list[float]]`: the current canvas at
  `samples` accumulated samples, H rows of W floats in [0, 1].
- (queries go through `apply({"op": "query", "what": ...})`)

## Ops

| op | fields | effect |
|---|---|---|
| `canvas` | `width`, `height` (ints, 1..1024), `background` (float in [0,1]), `seed` (int) | resets everything: size, background, seed, no rectangles, camera offset and jitter at (0, 0) |
| `rect` | `id`, `x`, `y`, `w`, `h`, `value` | adds a rectangle (or replaces the one with that `id` in place); coordinates in normalised [0,1] units of the image, `value` in [0,1] |
| `set` | `id`, `key`, `value` | for a rectangle id: one of `x y w h value`; for `id: "camera"`: `offset` or `jitter`, a 2-vector added to every rectangle's position |
| `remove` | `id` | removes that rectangle |
| `query` | `what` in `bounds`, `coverage`, `mean` | see below |

Validity: `w > 0`, `h > 0`, `0 <= value <= 1`, `-1 <= x, y <= 2`,
`w, h <= 3`, camera vectors with both components in [-1, 1]. An invalid op
returns `{"error": "INVALID_ARGUMENT"}` and changes nothing; an unknown
rectangle id returns `{"error": "UNKNOWN_ID"}`; an unknown op or query
returns `{"error": "INVALID_ARGUMENT"}`.

## Compositing

The noiseless image is the background, composited with every rectangle in
insertion order (a replaced rectangle keeps its position in the order).
Rectangle `r` covers the pixel `[px, px+1) x [py, py+1)` with the exact
overlap area `c` of the rectangle `[(x+dx)W, (x+dx+w)W) x [(y+dy)H, (y+dy+h)H)`,
where `(dx, dy) = offset + jitter`; the pixel becomes `(1-c) * current + c * value`.

## Noise

`draw(N)` averages `N` noisy copies of the noiseless image. Copy `i`
(0-based) uses `random.Random(seed * 1000003 + i)` and draws, for every pixel
in row-major order, `gauss(0, 0.25 * sqrt(v + 0.02))` where `v` is the
noiseless value; the noisy sample `v + noise` is clipped to [0, 1] before
being added to the average. The driver quantises images to 8 bits.

## Queries

- `bounds`: `[min x, min y, max x, max y]` over the rectangles with the
  camera offset and jitter applied, or `None` with no rectangles.
- `coverage`: the fraction of pixels whose noiseless value is above 0.5.
- `mean`: the mean noiseless pixel value.

## Performance

A case's timed draw is compared with the reference's wall time on the same
host: within 8x is the full-success bar, 16x scores 0.5.
