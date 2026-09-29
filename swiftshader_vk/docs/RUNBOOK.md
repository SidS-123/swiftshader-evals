# Runbook

Exact commands, run from the evalBase root with `EVALBASE_INSTANCE=<this
directory>` exported (or `--instance` on every command).

## 0. Prerequisites

```sh
docker info >/dev/null
<build the trusted image>
python3 -m evalbase.corpus.common public
python3 -m evalbase.grader.cli refcache
python3 -m pytest -q tests
```

The workspace needs `runs/refcache/<name>/n1` for every public case;
`populate` refuses to build a workspace without it.

## 1. Build the solver image

```sh
<build command>; python3 -m evalbase.harness.run versions   # records both image ids
```

## 2. Smoke (no API key, no CLI login)

```sh
for h in openrouter claude-code codex opencode; do
  python3 -m evalbase.harness.run smoke --harness $h --budget-seconds 60
done
```

Each writes `runs/smoke/<harness>/` and must end with `NO-KEY SMOKE PASSED`.

## 3. A real attempt

```sh
claude auth status                  # or codex login status / opencode providers list
python3 -m evalbase.harness.run \
  --harness claude-code --model <id> --reasoning high \
  --stop-policy submit --rate-limit-wait 6 \
  --budget-hours 12 --checkpoint-minutes 15 \
  --cpus 4 --memory 8g \
  --out runs/attempts/<name>
```

Record `--reasoning` on every run meant to be compared. Keep `--cpus` and
`--memory` identical across attempts; never run a control sweep beside one.

## 4. Reading progress

```sh
python3 -m evalbase.harness.run report runs/attempts/<name>
jq '.status,.stop_reason,.solver_seconds,.rate_limit_wait_seconds,.usage' runs/attempts/<name>/attempt.json
jq '.infrastructure_incidents, .measurement_valid' runs/attempts/<name>/attempt.json
docker ps --filter label=io.evalbase.managed=1
```

`infrastructure_incidents` must stay empty; a run with one is evidence, not a
score.

## 5. Grade the checkpoints (the progression curve)

After every attempt on the host has ended, on the idle host:

```sh
python3 -m evalbase.harness.run grade-checkpoints runs/attempts/<name> --every 30 --dedupe --dry-run
python3 -m evalbase.harness.run grade-checkpoints runs/attempts/<name> --every 30 --dedupe
```

## 6. Resume after an interrupt

```sh
python3 -m evalbase.harness.run resume runs/attempts/<name>
```

## 7. Cleanup

```sh
python3 -m evalbase.harness.run cleanup --owner <attempt-name>   # that attempt's containers
python3 -m evalbase.grader.cli sweep                              # this shell's own leftovers
python3 -m evalbase.grader.cli sweep --all                        # lists everything it would kill
python3 -m evalbase.grader.cli sweep --all --yes                  # and kills it (nothing running!)
```

Every container carries an owner label; every automatic sweep is scoped to
one owner. Never run `sweep --all --yes` while an attempt is alive.
