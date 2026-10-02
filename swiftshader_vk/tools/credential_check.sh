#!/usr/bin/env bash
# credential_check.sh [RUN_DIR...] -- credential hygiene (PLAN_v1.md §13 step 6):
#   1. no API key in the harness environment (Claude Max: the CLI authenticates with its login);
#   2. the sandbox never mounts the host's ~/.claude (or any home directory) -- read from the
#      sandbox mounts each run recorded;
#   3. no credential-shaped string in a run directory.
# Default run dirs: the no-key smokes and the wire check.
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
dirs=("$@")
[ ${#dirs[@]} -eq 0 ] && dirs=("$here/runs/smoke-claude-code" "$here/runs/smoke-openrouter" "$here/runs/wire")
fail=0
for v in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN OPENAI_API_KEY; do
    [ -n "${!v:-}" ] && { echo "FAIL: $v is set in the harness environment"; fail=1; }
done
[ $fail = 0 ] && echo "ok   no API key or token variable in the harness environment"
# 2. what the sandboxes mounted: the harness records each sandbox's docker argv verbatim
#    (tools-state.json); every bind must be that run's own workspace at /task, nothing else
binds=$(grep -rhoE 'type=bind,src=[^",]*,dst=[^",]*' "${dirs[@]}" 2>/dev/null | sort -u)
if [ -z "$binds" ]; then
    echo "FAIL: no recorded sandbox mount found (nothing to check)"; fail=1
else
    other=$(echo "$binds" | grep -vE 'src=[^,]*/workspace,dst=/task$')
    if [ -n "$other" ]; then echo "FAIL: sandbox mounts other than the workspace: $other"; fail=1
    else echo "ok   every recorded sandbox mount is the run's own workspace at /task ($(echo "$binds" | wc -l) sandboxes; no ~/.claude, no home)"; fi
fi
# 3. credential-shaped strings
c=$(grep -rlE 'sk-ant-[A-Za-z0-9_-]{8,}|sk-or-v1-[A-Za-z0-9]{8,}|"(access|refresh)Token" *: *"[^"]{8,}|Bearer [A-Za-z0-9._-]{20,}' "${dirs[@]}" 2>/dev/null | head -5)
if [ -n "$c" ]; then echo "FAIL: credential-shaped strings in: $c"; fail=1
else echo "ok   no credential-shaped string in ${#dirs[@]} run directories"; fi
exit $fail
