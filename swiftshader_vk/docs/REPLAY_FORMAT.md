# Replay format (operator view)

The case format, the op vocabulary and the output files are specified in
[`task/CASE_FORMAT.md`](../task/CASE_FORMAT.md), the model-facing document;
this file adds what operators and graders need beyond it. The two must not
disagree.

## Components

| Piece | Where | Role |
|---|---|---|
| `vkreplay` | `driver/src/` (C++17), built into `ssvk-ref` (and copied into `ssvk-solver` in Stage 9) | executes a case against one ICD |
| Vulkan tables | `driver/codegen/gen_vk_tables.py` over the pinned `vk.xml` | enum/flag names, format layouts, feature/property struct serializers |
| `ssvk` entry | `images/ssvk-entry.sh` | `drive` (oracle and its variants), `drive-candidate`, `drive-lavapipe` |
| Python driver | `instance.py` `VkReplayDriver` | runs the container for evalBase |
| Snapshot reader | `snapshot.py` | reads `.ssnap`, decodes every format into per-component codes |
| Case builder | `corpus/gen/common.py` | `Case` builder, GLSL -> SPIR-V with the pinned glslang (cached) |

Errors are observed, not asserted: every `VkResult` is in `ledger.calls`, and
ops like `enumerate`, `image_format_props`, `wait_fence` and `fence_status`
record results as query events. There is no `expect_error` op.

Two case features exist for the grader (Stage 5):

- `"data": {"address": [{"buffer", "offset"}]}` writes 64-bit buffer device
  addresses into uploads and push constants (the `compute_types` family's
  buffer-device-address cases).
- A snapshot item's `"allow": {"ulp" | "lsb" | "abs": n}` is copied by the
  trusted parent, from its own plan, into the `.ssnap` header; the scorer
  ignores per-element differences up to it. Generators set it only where the
  Vulkan precision appendix bounds a result instead of defining it.

The candidate library is loaded from a private copy the parent makes in its
work directory, so the mount's permissions and later changes to the file do
not matter.

## Trust boundary (driver/src/parent.cpp)

- The process started in the container is the **trusted parent**. It parses
  the case, builds the *plan* (which op produces which output, every snapshot
  item's size and format), starts the **untrusted child** (the same binary
  with `--child`), and is the only writer of the output directory.
- In the grading container the parent runs as root; the child runs as uid
  2000 (`cand`), which has no write access to the output directory. The
  parent is not dumpable (no ptrace from the child's uid).
- The child reports over a pipe: call results, notes, query values, snapshot
  bytes. The parent checks every message against the plan: an event for an op
  that produces none, an output out of order, or a snapshot whose items do not
  match the plan is a protocol error. Item bytes whose length disagrees with
  the plan are recorded as missing.
- **Timing.** For a `run` op the child signals ready after the warm-up, the
  parent notes the time and sends "go", and the measurement ends when the
  snapshot bytes arrive. A candidate cannot report a time; it can only
  deliver correct output sooner or later.
- When the child exits, the parent reaps it immediately (a process it left
  behind cannot hold the pipe open), kills every process of uid 2000, and
  only then writes the ledger. Snapshot files are written as they arrive, the
  ledger once at the end. Files and a directory it creates are handed to the
  owner of the output directory (or the directory above it).
- In the model's sandbox (`driver` tool) the parent is not root: the child
  runs under the same uid. Nothing there needs protecting; the grading
  container is the boundary.

Tested by `driver/tests/hostile_icd.c` through `tests/test_driver.py`: an ICD
that writes into the output directory, leaves a background writer, and
crashes in `vkCreateInstance`. The ledger says `crash`, no hostile file
survives, and the crash is recorded in about 0.2 s.

## Oracle variants (`ssvk drive`, set by `VkReplayDriver`)

| Variable | Values | Effect |
|---|---|---|
| `DRIVER_PERTURB` | `""` | SwiftShader, LLVM backend (oracle of record) |
| | `subzero` | SwiftShader, Subzero backend |
| | `lavapipe` | Mesa lavapipe (fairness comparator, never tolerated) |
| | `vtxjitter` | uploads tagged `perturb: {class: position}` get +/-2^-17 on x, y |
| | `texcoord_ulp` | uploads tagged `perturb: {class: texcoord}` move 1 ULP |
| `DRIVER_SAMPLE_MULT` | `2` | `ThreadCount=1` instead of 4 (the reference cache's n2 run) |
| `DRIVER_VALIDATE` | `1` | Khronos validation layer on; messages in `ledger.validation` |
| `DRIVER_TIMEOUT` | seconds | vkreplay stops the case (set 10 s under the container timeout) |

A candidate never sees a perturbation: `VkReplayDriver` clears both for it.

## Exit statuses

| `exit` | Meaning |
|---|---|
| `ok` | the child ran every op and exited normally |
| `crash` | the child died, exited early, or hung at exit |
| `timeout` | the parent's timeout expired |
| `driver_error` | the case is malformed (never the ICD's fault) |
| `protocol_error` | the child sent something the plan does not allow |

## The validity gate

A case is valid only if it replays on the oracle with the validation layer on
and **no message citing the spec** (`VUID-` or "The Vulkan spec states").
Loader messages about an unsupported extension or feature that a case asks
for on purpose (to observe `VK_ERROR_EXTENSION_NOT_PRESENT` /
`VK_ERROR_FEATURE_NOT_PRESENT`) are not violations. `tools/determinism.py`
applies the gate; Stage 7's generators apply it to every generated case.
