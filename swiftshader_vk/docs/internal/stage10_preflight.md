# Stage 10 — pre-flight review for gate G-PAID (2026-10-01)

PLAN_v1.md §14. Everything the paid runs (Stage 11 pilot, Stage 12 attempt of
record) depend on, measured today on this host. Nothing paid has run.

## 10.1 Status table

| Item | State | Evidence |
|---|---|---|
| Oracle image | `ssvk-ref:1` `sha256:82a05e1f…a52a11fd` | `docker image inspect`; `images/pins.lock` |
| Candidate / solver images | `ssvk-cand:1` `sha256:a1bf8d9d…27852165`; `ssvk-solver:1` `sha256:eff44590…2f256a6` | same |
| Corpus | 91 public / 187 hidden; every case gated (validation layer clean, repeats + ThreadCount 1/4 identical, no skipped op, poisoned-memory identical); generation 776/776 identical | `runs/gate-*`; `evalbase.corpus.determinism` |
| Reference cache | T min 0.030 / median 0.030 / max 0.300; public 89 at t_lo, 2 at t_hi; hidden 180 at t_lo, 4 at t_hi | `tools/refcache_summary.py` |
| Reference as candidate | 1.000, full success, both splits (re-graded in `ssvk-cand:1`, 2026-10-01 22:22) | `runs/control-reference-{public,hidden}/report.json` |
| Null band (stub) | 0.013 public / 0.007 hidden | `runs/control-stub-*` |
| Controls | 32/38 inside the committed bands; 6 restated as predictor errors | `docs/CONTROLS.md`, DESIGN.md "Controls" |
| Metric | `ssvk-1.0`, unchanged since Stage 6; D5: perf_half 16, perf_gate 8, weight 0.10 | DESIGN.md log |
| Tests | instance 26 passed; evalBase 383 passed / 4 skipped | pytest (evalBase with `EVALBASE_INSTANCE` unset) |
| No-key smokes | claude-code, openrouter PASSED (Stage 9) | `runs/smoke-*` |
| Wire check (request body) | **PASSED**: main request has exactly the five `mcp__ssvk__*` tools, `claude-opus-5-5`, effort high, adaptive thinking | `runs/wire/request.json` |
| Model id | **BLOCKED**: API refuses `claude-opus-5-5` from Claude Code 2.1.274; needs ≥ 2.1.280 | 10.2 |
| Host preflight | **FAILED** on 4 items: CLI version, sleep on AC (300 s), lid close on AC (sleep), power mode (Balanced) | `tools/preflight_host.sh` |
| VALIDATION.md | draft, sections 1–7 complete; 8–10 after the runs | `reports/VALIDATION.md` |
| RUNBOOK.md | real commands (was the template) | `docs/RUNBOOK.md` |

## 10.2 Findings

1. **Wire recorder stopped at a side request.** Claude Code sends a
   session-title request (no tools, JSON-schema answer) before the main turn;
   the recorder recorded that and stopped, so the check "failed" with zero
   tools. Fixed in `tools/wire_recorder.py`; `wire_check.sh` tolerates the
   missing transcript of the stopped attempt.
2. **The CLI is too old for the model.** `claude -p --model claude-opus-5-5`
   from 2.1.274 got `400 ... version 2.1.280 or newer is required`. The paid
   run would have died on its first request. The CLI is a global npm install
   (`/usr/lib/node_modules`), so updating needs sudo.
3. **The audit's event list is pinned to 2.1.274.** `evalbase/harness/audit.py`
   records any stream event type outside its list as `unsupported_cli_event`.
   A newer CLI may add one. The no-key smokes use a stub CLI and cannot show
   this; the pilot is the first real stream and is checked for it.

## 10.3 Usage estimate (Claude Max)

There is no measurement to base this on before the pilot, so it is a method
plus a range, replaced by the pilot's numbers.

- Max has a rolling 5-hour usage window and a weekly cap. One attempt hour is
  continuous Opus use at effort high with large tool outputs (`grade_dev`,
  build logs), the heaviest kind of use.
- Let *f* be the fraction of a 5-hour window one attempt hour uses (the
  pilot measures it from its `rate_limit_event`s and any wait). The 12-hour
  run then hits about ⌈12·f⌉ − 1 window limits, each a wait of up to 5 h.
  - *f* ≈ 0.25 → ~2 waits, real time ~12–22 h
  - *f* ≈ 0.5 → ~5 waits, ~20–36 h (the 24 h wait cap may bind)
  - *f* ≥ 1 → the pilot itself pauses; the 12-hour run is impractical on
    this plan without a longer wait cap
- **The weekly cap is the bigger risk.** A seven-day limit cannot be waited
  out within 24 h; the attempt would end `rate_limited`. Check the weekly
  allowance (claude.ai → Settings → Usage) before the pilot and again before
  the 12-hour run, and use no other Claude on the account in between.
- Each wait relaunches the CLI and costs Opus its conversation context; the
  relaunch count is reported with the result.

## 10.4 Launch commands

Pilot (Stage 11), from the repo root in WSL after `source .envrc`:

```sh
bash swiftshader_vk/tools/preflight_host.sh && \
python -m evalbase.harness.run attempt --harness claude-code --model claude-opus-5-5 --reasoning high \
  --stop-policy submit --rate-limit-wait 24 --budget-hours 1 --checkpoint-minutes 15 \
  --cpus 4 --memory 8g --out swiftshader_vk/runs/attempts/opus55-pilot
```

Attempt of record (Stage 12), only after the pilot is reviewed, at the start
of a fresh usage window, launched detached:

```sh
bash swiftshader_vk/tools/preflight_host.sh && mkdir -p swiftshader_vk/runs/attempts && \
nohup setsid python -m evalbase.harness.run attempt --harness claude-code --model claude-opus-5-5 --reasoning high \
  --stop-policy submit --rate-limit-wait 24 --budget-hours 12 --checkpoint-minutes 15 \
  --cpus 4 --memory 8g --out swiftshader_vk/runs/attempts/opus55-a1 \
  > swiftshader_vk/runs/attempts/opus55-a1.log 2>&1 < /dev/null &
echo $! > swiftshader_vk/runs/attempts/opus55-a1.pid
```

## 10.5 Before G-PAID can be approved

1. Update Claude Code to ≥ 2.1.280 (needs sudo).
2. Host power: sleep never on AC, lid close on AC "Do nothing" (or keep the
   lid open), power mode Best performance.
3. Re-run with the new CLI: `preflight_host.sh` (PASSED), the model-id
   check (one tiny request), `wire_check.sh`, both no-key smokes,
   `credential_check.sh`.
4. Check the weekly Max allowance.
5. Approve the pilot.
