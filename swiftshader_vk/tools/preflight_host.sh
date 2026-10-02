#!/usr/bin/env bash
# preflight_host.sh -- the host checks before a paid attempt (PLAN_v1.md §14, gate G-PAID).
# Read-only: changes nothing, spends no usage. Exit 0 only if every check passes.
#   CLI      Claude Code >= MIN_CLI (the API refuses claude-opus-5-5 from older CLIs), logged in
#            to the Max subscription, no ANTHROPIC_* variable in the environment (D9)
#   Power    on AC; sleep and hibernate on AC disabled; lid close on AC does nothing
#            (standby freezes the WSL VM and with it the attempt's clock and timings)
#   Host     the three images present (digests printed); no evalBase-managed container running
set -uo pipefail
MIN_CLI=${MIN_CLI:-2.1.280}
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
PCFG=/mnt/c/Windows/System32/powercfg.exe
fail=0
ok()  { printf 'PASS  %s\n' "$*"; }
bad() { printf 'FAIL  %s\n' "$*"; fail=1; }

# --- CLI
v=$(claude --version 2>/dev/null | awk '{print $1}')
if [ -n "$v" ] && [ "$(printf '%s\n%s\n' "$MIN_CLI" "$v" | sort -V | head -1)" = "$MIN_CLI" ]; then
    ok "Claude Code $v (>= $MIN_CLI)"
else
    bad "Claude Code ${v:-missing}; need >= $MIN_CLI (sudo npm install -g @anthropic-ai/claude-code@latest)"
fi
auth=$(claude auth status 2>/dev/null)
if printf '%s' "$auth" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(not (d.get("loggedIn") and d.get("subscriptionType") == "max" and d.get("authMethod") == "claude.ai"))' 2>/dev/null; then
    ok "logged in: claude.ai, subscription max"
else
    bad "claude auth status is not a claude.ai Max login"
fi
if env | grep -qi '^ANTHROPIC_'; then
    bad "ANTHROPIC_* variable set: $(env | grep -i '^ANTHROPIC_' | cut -d= -f1 | tr '\n' ' ')"
else
    ok "no ANTHROPIC_* variable in the environment"
fi

# --- Power (Windows host)
bs=$("$PS" -NoProfile -Command '(Get-CimInstance Win32_Battery).BatteryStatus' 2>/dev/null | tr -d '\r')
[ "$bs" = 2 ] && ok "on AC power" || bad "not on AC power (BatteryStatus=$bs)"
acval() {   # powercfg subgroup setting -> current AC value (decimal)
    "$PCFG" /qh SCHEME_CURRENT "$1" "$2" 2>/dev/null | tr -d '\r' |   # /qh: LIDACTION is a hidden setting
        awk -F': ' '/Current AC Power Setting Index/ {print strtonum($2)}'
}
s=$(acval SUB_SLEEP STANDBYIDLE); [ "$s" = 0 ] && ok "sleep on AC: never" || bad "sleep on AC after ${s:-?} s (powercfg /change standby-timeout-ac 0)"
h=$(acval SUB_SLEEP HIBERNATEIDLE); [ "$h" = 0 ] && ok "hibernate on AC: never" || bad "hibernate on AC after ${h:-?} s (powercfg /change hibernate-timeout-ac 0)"
l=$(acval SUB_BUTTONS LIDACTION); [ "$l" = 0 ] && ok "lid close on AC: do nothing" || bad "lid close on AC sleeps (action ${l:-?}); keep the lid open or set it to Do nothing"
scheme=$("$PCFG" /getactivescheme 2>/dev/null | tr -d '\r' | sed 's/.*(\(.*\)).*/\1/')
mode=$("$PS" -NoProfile -Command '(Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes" -ErrorAction SilentlyContinue).ActiveOverlayAcPowerScheme' 2>/dev/null | tr -d '\r')
case "$mode" in
    ded574b5-45a0-4f42-8737-46345c09c238) ok "power mode on AC: Best performance (plan: $scheme)";;
    *) bad "power mode on AC is not Best performance (overlay ${mode:-none, i.e. Balanced}); Settings > System > Power > Power mode";;
esac

# --- Host
for i in ssvk-ref:1 ssvk-solver:1 ssvk-cand:1; do
    d=$(docker image inspect --format '{{.Id}}' "$i" 2>/dev/null)
    [ -n "$d" ] && ok "$i $d" || bad "$i missing"
done
n=$(docker ps -q --filter label=io.evalbase.managed | wc -l)
[ "$n" = 0 ] && ok "no evalBase-managed container running" || bad "$n evalBase-managed container(s) running"

echo
[ $fail = 0 ] && echo "PREFLIGHT PASSED" || echo "PREFLIGHT FAILED"
exit $fail
