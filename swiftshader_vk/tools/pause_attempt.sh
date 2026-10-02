#!/usr/bin/env bash
# pause_attempt.sh NAME -- pause a running attempt cleanly; continue it with resume_attempt.sh NAME.
# Sends SIGINT to the harness: it stops the CLI, removes the attempt's containers, records the
# solver time used and marks the attempt `interrupted` without exporting or grading it.
# The workspace, NOTES.md and checkpoints stay on disk; the resumed CLI session starts without
# the previous conversation.
set -uo pipefail
name=${1:?usage: pause_attempt.sh NAME}
cd "$(dirname "$0")/../.."
A=swiftshader_vk/runs/attempts; R=$A/$name
p=$(cat "$A/$name.pid" 2>/dev/null)
[ -n "$p" ] && kill -0 "$p" 2>/dev/null || { echo "$name is not running"; exit 1; }
ign=$(awk '/SigIgn/{print $2}' "/proc/$p/status")
if (( (16#$ign & 2) != 0 )); then
    echo "pid $p ignores SIGINT (launched with a plain nohup ... &); it cannot be paused cleanly."
    echo "See the 2026-10-02 operator pause of opus55-a1 in docs/internal/STATUS.md for the manual procedure."
    exit 1
fi
echo "$(date -Is) pause requested, pid $p" >> "$A/$name.pauses"
kill -INT "$p"
for _ in $(seq 1 120); do kill -0 "$p" 2>/dev/null || break; sleep 2; done
if kill -0 "$p" 2>/dev/null; then echo "pid $p still running after 4 minutes; not forcing it"; exit 1; fi
python3 - "$R" <<'PY'
import json, sys
from pathlib import Path
run = Path(sys.argv[1]); d = json.loads((run / "attempt.json").read_text())
left = d["budget"]["seconds"] - float(d.get("solver_seconds") or 0)
print(f"status {d.get('status')}, stop {d.get('stop_reason')}, used {d['solver_seconds'] / 3600:.3f} h, "
      f"{left / 3600:.3f} h left; exported: {(run / 'submission').exists()}")
PY
left=$(docker ps -q --filter "label=io.evalbase.owner=$name" | wc -l)
echo "containers left for $name: $left"
echo "$(date -Is) paused" >> "$A/$name.pauses"
echo "PAUSED $name; continue with: bash swiftshader_vk/tools/resume_attempt.sh $name"
