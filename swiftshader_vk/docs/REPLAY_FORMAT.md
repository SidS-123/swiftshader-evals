# Replay format (operator view)

The case format, the `.ssnap` snapshot format and the `vkreplay` ledger as
the grader and operators see them. `task/CASE_FORMAT.md` is the model-facing
version of the same format; the two must not disagree.

Skeleton until Stage 4 (PLAN_v1.md §8), which defines:

- **Case JSON:** `name`, `family`, `category`, `meta` (incl.
  `timed_run_index`), `assets` (sha256 names), `version`, `ops`.
- **Op vocabulary:** setup, memory, shaders and state, command blocks,
  execution, output (`snapshot`, `query`, `run`) and negative
  (`expect_error`) ops. Every call's `VkResult` is logged.
- **`.ssnap`:** a JSON header (attachments: name, kind, VkFormat, size,
  layers, offsets) followed by raw little-endian payloads. There is one file
  per `snapshot` event, which is the scorer's primary channel.
- **Ledger:** `ledger.json` under the evalBase ledger contract, written by
  the trusted parent process and flushed after each event.
- **Trust boundary:** the parent parses, owns `/out` and keeps the clock. The
  untrusted child loads the candidate ICD, runs unprivileged and cannot write
  `/out`.
