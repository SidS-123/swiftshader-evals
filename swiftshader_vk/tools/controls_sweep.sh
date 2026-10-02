#!/usr/bin/env bash
# controls_sweep.sh [CONTROL...] -- grade every control (or the ones named) on both splits,
# one at a time, and append to runs/controls.log (PLAN_v1.md §12 steps 4 and 9: run on an
# otherwise idle host, on AC power, without sleep). Each run lands in
# runs/control-<name>-<split>/ for controls_summary. The host's power source is logged at
# the start and with every run (Windows, through WSL interop); a run measured on battery
# or across a standby (Windows Kernel-Power log) is re-run before its timing is used.
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
cd "$here/.."
# shellcheck disable=SC1091
source .envrc
log=$here/runs/controls.log
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
power() {   # "ac", "battery" or "unknown"
    local s
    s=$("$PS" -NoProfile -Command '(Get-CimInstance Win32_Battery).BatteryStatus' 2>/dev/null | tr -d '\r')
    case "$s" in 2) echo ac ;; "") echo unknown ;; *) echo battery ;; esac
}
names=("$@")
if [ ${#names[@]} -eq 0 ]; then
    mapfile -t names < <(python - <<'EOF'
import os
from evalbase.interfaces import load_instance
print("\n".join(load_instance(os.environ["EVALBASE_INSTANCE"]).controls.names()))
EOF
)
fi
{
    echo "=== controls start $(date -Is) ==="
    echo "image id: $(docker image inspect --format '{{.Id}}' ssvk-ref:1)"
    echo "metric: $(python -c 'import os; from evalbase.interfaces import load_instance; print(load_instance(os.environ["EVALBASE_INSTANCE"]).metric.version)')"
    echo "controls: ${names[*]}"
    echo "power: $(power)"
} >> "$log"
H=(--corpus swiftshader_vk/corpus/hidden --cache swiftshader_vk/runs/refcache-hidden)
for n in "${names[@]}"; do
    for split in public hidden; do
        out=$here/runs/control-$n-$split
        glog=$here/runs/controls-logs/$n-$split.log    # not beside the run dir: controls_summary globs control-*
        mkdir -p "$here/runs/controls-logs"
        rm -rf "$out"
        t0=$(date +%s)
        if [ "$split" = public ]; then
            python -m evalbase.grader.cli control "$n" --out "$out" --label "control-$n-$split" > "$glog" 2>&1
        else
            python -m evalbase.grader.cli "${H[@]}" control "$n" --out "$out" --label "control-$n-$split" > "$glog" 2>&1
        fi
        rc=$?
        line=$(grep -E "^== .* overall=" "$glog" | tail -1)
        echo "$n $split rc=$rc $(( $(date +%s) - t0 ))s power=$(power) start=$(date -d @"$t0" -Is) $line" >> "$log"
    done
done
echo "=== controls end $(date -Is) ===" >> "$log"
