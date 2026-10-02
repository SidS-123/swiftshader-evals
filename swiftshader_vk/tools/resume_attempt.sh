#!/usr/bin/env bash
# resume_attempt.sh NAME -- continue a paused attempt (runs/attempts/NAME), detached.
# Checks the host (preflight_host.sh) and the attempt (interrupted, not exported), then runs
# `evalbase.harness.run resume` in its own session with SIGINT at its default, so
# tools/pause_attempt.sh can pause it again cleanly. A plain `nohup ... &` from a non-interactive
# shell starts the harness with SIGINT ignored, and its pause path never runs.
set -euo pipefail
name=${1:?usage: resume_attempt.sh NAME}
cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source .envrc
A=swiftshader_vk/runs/attempts; R=$A/$name
[ -f "$R/attempt.json" ] || { echo "no attempt at $R"; exit 1; }
if [ -f "$A/$name.pid" ] && kill -0 "$(cat "$A/$name.pid")" 2>/dev/null; then
    echo "$name is already running (pid $(cat "$A/$name.pid"))"; exit 1
fi
python3 - "$R" <<'PY'
import json, sys
from pathlib import Path
run = Path(sys.argv[1]); d = json.loads((run / "attempt.json").read_text())
problems = [f"status is {d.get('status')!r}, not 'interrupted'"] if d.get("status") != "interrupted" else []
problems += [f"{n}/ exists (already exported)" for n in ("submission", "grade") if (run / n).exists()]
left = d["budget"]["seconds"] - float(d.get("solver_seconds") or 0)
print(f"{run.name}: used {d.get('solver_seconds', 0) / 3600:.3f} h, {left / 3600:.3f} h left, "
      f"resumes so far {d.get('resume_count', 0)}")
if problems:
    sys.exit("cannot resume: " + "; ".join(problems))
PY
bash swiftshader_vk/tools/preflight_host.sh
n=$(ls "$A/$name".resume*.log 2>/dev/null | wc -l)
log=$A/$name.resume$((n + 1)).log
date -Is > "$A/$name.resume$((n + 1)).start"
nohup python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
    python -m evalbase.harness.run resume "$R" > "$log" 2>&1 < /dev/null &
p=$!
echo "$p" > "$A/$name.pid"
echo "$(date -Is) resumed, pid $p, log $log" >> "$A/$name.pauses"
sleep 45
kill -0 "$p" 2>/dev/null || { echo "resume exited early; see $log"; tail -20 "$log"; exit 1; }
ign=$(awk '/SigIgn/{print $2}' "/proc/$p/status")
(( (16#$ign & 2) == 0 )) || { echo "WARNING: SIGINT is ignored by pid $p; pause_attempt.sh will not work"; }
docker ps --filter "label=io.evalbase.owner=$name" --format '{{.Names}} {{.Status}}'
echo "RESUMED $name: pid $p, log $log"
