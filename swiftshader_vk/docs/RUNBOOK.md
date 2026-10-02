# Runbook

Exact commands. Run from the repository root inside WSL Ubuntu after
`source .envrc` (sets `EVALBASE_INSTANCE`, `SSVK_HIDDEN_GEN`, activates the
venv).

## 0. Prerequisites

```sh
docker info >/dev/null
bash swiftshader_vk/images/build_ref.sh ref      # ssvk-ref:1, the oracle
bash swiftshader_vk/images/build_ref.sh cand     # ssvk-cand:1, candidate replays
bash swiftshader_vk/images/build_ref.sh solver   # ssvk-solver:1, the model's sandbox
(cd swiftshader_vk && python -m pytest -q tests)
(cd evalBase && env -u EVALBASE_INSTANCE EVALBASE_NO_DOCKER=1 python -m pytest -q)
```

The evalBase suite runs with `EVALBASE_INSTANCE` unset: one of its tests
loads the toy example, whose `scorer` module would otherwise clash with this
instance's.

The corpus and both reference caches are rebuilt as in
`docs/REPRODUCIBILITY.md`; `populate` refuses to build a workspace without
`runs/refcache/<case>/n1` for every public case.

## 1. Before any paid attempt

```sh
bash swiftshader_vk/tools/preflight_host.sh       # must end PREFLIGHT PASSED
bash swiftshader_vk/tools/isolation_selftest.sh
bash swiftshader_vk/tools/wire_check.sh           # no usage: a local recorder answers
bash swiftshader_vk/tools/credential_check.sh
python -m evalbase.harness.run smoke --harness claude-code
python -m evalbase.harness.run smoke --harness openrouter
```

`preflight_host.sh` checks the Claude Code version (the API refuses
`claude-opus-5-5` from versions before 2.1.280), the Max login and the
absence of any `ANTHROPIC_*` variable, AC power, sleep / hibernate / lid
close disabled on AC, the Best performance power mode, the three images and
an idle Docker host. The host stays idle for the whole attempt: no control
sweep, no cache rebuild, and no other Claude use on the same account, which
draws from the same usage limits.

## 2. A real attempt

```sh
python -m evalbase.harness.run attempt \
  --harness claude-code --model claude-opus-5-5 --reasoning high \
  --stop-policy submit --rate-limit-wait 24 \
  --budget-hours 12 --checkpoint-minutes 15 \
  --cpus 4 --memory 8g \
  --out swiftshader_vk/runs/attempts/<name>
```

Long attempts are launched detached so the shell can close:

```sh
mkdir -p swiftshader_vk/runs/attempts
nohup setsid python -m evalbase.harness.run attempt <same flags> \
  > swiftshader_vk/runs/attempts/<name>.log 2>&1 < /dev/null &
echo $! > swiftshader_vk/runs/attempts/<name>.pid
```

Keep `--reasoning`, `--cpus` and `--memory` identical across attempts that
are compared. `--rate-limit-wait 24` lets the harness wait out up to 24 h of
subscription usage limits; waits are recorded in `rate_limit_wait_seconds`
and never count against the budget. Each wait ends with a relaunch of the
CLI: the model keeps its files, `NOTES.md` and checkpoints but not its
conversation.

## 3. Reading progress

```sh
python -m evalbase.harness.run report swiftshader_vk/runs/attempts/<name>
jq '.status,.stop_reason,.solver_seconds,.rate_limit_wait_seconds,.usage' swiftshader_vk/runs/attempts/<name>/attempt.json
jq '.infrastructure_incidents, .measurement_valid, (.rate_limit_waits|length)' swiftshader_vk/runs/attempts/<name>/attempt.json
docker ps --filter label=io.evalbase.managed=1
```

`infrastructure_incidents` must stay empty; a run with one is evidence, not
a score.

After the attempt, check the host's power history for its window (event 105
is an AC change, 506/507 standby entry and exit):

```sh
powershell.exe -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=105,506,507; StartTime='<start>'} | Format-Table TimeCreated,Id -AutoSize"
```

## 4. Grade the checkpoints (the progression curve)

After every attempt on the host has ended, on the idle host:

```sh
python -m evalbase.harness.run grade-checkpoints swiftshader_vk/runs/attempts/<name> --every 30 --dedupe --dry-run
python -m evalbase.harness.run grade-checkpoints swiftshader_vk/runs/attempts/<name> --every 30 --dedupe
```

The curve's last point must equal the final of record.

## 5. Pause and resume

```sh
bash swiftshader_vk/tools/pause_attempt.sh <name>     # SIGINT: stops the CLI, records the time used, no export
bash swiftshader_vk/tools/resume_attempt.sh <name>    # preflight, then resume detached with the remaining budget
```

A paused attempt keeps its workspace, `NOTES.md` and checkpoints; the resumed
CLI session starts without the previous conversation, and `resume_count`
records it. `resume_attempt.sh` launches the harness with SIGINT at its
default. A plain `nohup ... &` from a non-interactive shell starts it with
SIGINT ignored, so `pause_attempt.sh` refuses such a process rather than
forcing it.

## 6. Cleanup

```sh
python -m evalbase.harness.run cleanup --owner <attempt-name>   # that attempt's containers
python -m evalbase.grader.cli sweep                              # this shell's own leftovers
python -m evalbase.grader.cli sweep --all                        # lists everything it would kill
python -m evalbase.grader.cli sweep --all --yes                  # and kills it (nothing running!)
```

Every container carries an owner label; every automatic sweep is scoped to
one owner. Never run `sweep --all --yes` while an attempt is alive.
