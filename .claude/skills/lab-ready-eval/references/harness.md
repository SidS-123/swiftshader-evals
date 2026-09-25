# The agent harness and the tool boundary

## Architecture

- **One sandbox per attempt**: a Docker container (pinned image, compilers,
  no network, fixed CPU and memory limits; 4 cores and 8 GB worked for one
  eval) holding the workspace. The coding agent itself runs on the host; only
  its tool calls enter the sandbox.
- **One MCP bridge**, a stdio server the harness owns, exposing a handful of
  tools: `shell` (in the sandbox), `oracle` (run the reference on any public
  case), `driver` (run the driver against the candidate), `grade_dev`
  (grade against the public corpus only, compact payload), `checkpoint`
  (snapshot the source). Nothing else.
- **Harness kinds** are launch recipes for native coding-agent CLIs plus an
  in-house loop for raw chat APIs. Every native CLI is launched with all
  built-in tools disabled so the bridge is the only tool surface; a boundary
  module asserts the emitted config and stamps a boundary version into the
  manifest.
- Each recipe has a no-key smoke: a stub CLI that accepts the real launch
  command line, discovers the MCP server from it, runs a scripted tool
  sequence through the real sandbox, oracle and grader, and emits the CLI's
  real event format. The smoke proves the pipeline without a model.

## What the model must never see

- The hidden corpus, the reference caches, the grader source, this
  repository, any credential. Mount only the workspace, the public corpus and
  its reference outputs, the driver binary and the task text.
- Strip provider keys from the container environment explicitly; scrub them
  from every recorded launch file (a privacy module with a list of secret
  field names and environment variables, applied before writing).
- Verify the boundary on the wire, not from the docs: point the CLI at a
  local recording endpoint and read the `tools` array it sends. Do this again
  after every CLI upgrade.

## Stop policy

- `submit`: the model marks its submission final with a fixed marker; the
  harness asks once whether it is sure; a second marker ends the attempt.
  Otherwise the wall budget ends it. Record the stop reason (`submitted`,
  `budget_wall`, `infrastructure`, `rate_limited`) in the manifest.
- Rate limits are waited out up to a bounded time (6 h worked) and recorded;
  a 429 is never a model decision. A 401/403 ends the attempt as
  infrastructure immediately rather than relaunching.
- Reasoning effort is a required, recorded launch parameter; served effort
  is recorded where the provider reports it.

## Checkpoints and incidents

- Snapshot the source every N minutes and at the model's request; grade
  checkpoints against the public corpus during the run only if the host has
  headroom. For curves of record, grade at a fixed cadence (120 minutes
  worked) with deduplication of unchanged sources; for finals, grade only
  the final.
- The sandbox can die (a cleanup that swept other attempts' containers did
  it). The bridge detects a dead sandbox, recreates it, and records an
  incident; any incident that cost the model its tools invalidates the
  measurement (`measurement_valid: false`) and the run is repeated.
- Label every container with the attempt that owns it; sweep only your own
  containers, at start, at each relaunch, and at exit. Record return codes
  and signals for every driver run.
- The manifest is saved as the run progresses, not only at the end; a crash
  mid-grade must leave a manifest that says so.

## Serialisation and payloads

- Every JSON writer handles non-finite values; a grade with an infinite
  ratio once crashed the model's own dev-grade tool.
- Keep tool payloads compact: the model reads them into its context. A
  per-family summary beats a per-case dump; offer detail on request.

## Known CLI differences worth recording

- Some CLIs cannot mark an MCP server as required; if the bridge fails to
  start they run with zero tools. Detect it afterwards (`tool_call_total ==
  0`) and invalidate, and say so in the docs.
- Cost figures from a CLI are list-price estimates unless the provider
  returns a billed charge; label the field.
- Not every CLI names the model in its event stream; recover it from the
  session export and leave the field empty if that fails.
