#!/usr/bin/env bash
# wire_check.sh [MODEL] -- the tool boundary on the wire for the claude-code harness (PLAN_v1.md
# §13 step 5). Launches a real attempt (sandbox, MCP bridge, Claude Code with the harness's exact
# flags) against tools/wire_recorder.py instead of the Anthropic API, records the first request
# body, and stops. No request reaches the real API (no usage spent); request headers are never
# stored. Pass: exactly the five mcp__ssvk__* tools and nothing else.
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
cd "$here/.."
# shellcheck disable=SC1091
source .envrc
model=${1:-claude-opus-5-5}
out=$here/runs/wire
chmod -R u+w "$out" 2>/dev/null; rm -rf "$out"; mkdir -p "$out"   # workspaces are made read-only
port=8765
python "$here/tools/wire_recorder.py" --port $port --out "$out/request.json" --timeout 300 > "$out/recorder.log" 2>&1 &
rec=$!
sleep 1
ANTHROPIC_BASE_URL=http://127.0.0.1:$port timeout 300 python -m evalbase.harness.run attempt \
    --harness claude-code --model "$model" --budget-hours 0.05 --stop-policy budget --rate-limit-wait 0 \
    --out "$out/attempt" > "$out/attempt.log" 2>&1 &
att=$!
wait $rec; rc=$?
kill $att 2>/dev/null; sleep 3; pkill -f "$out/attempt" 2>/dev/null
python -m evalbase.harness.run cleanup --owner attempt 2>/dev/null >/dev/null
docker ps --filter label=io.evalbase.managed --format '{{.Names}}' | xargs -r docker kill >/dev/null 2>&1
cat "$out/recorder.log"
# The CLI's own stream-json init event: the tool set it offers the model, with its version.
python3 - "$out/attempt/transcript.jsonl" <<'EOF'
import json, os, sys
want = sorted(f"mcp__ssvk__{t}" for t in ("shell", "oracle", "driver", "grade_dev", "checkpoint"))
if not os.path.exists(sys.argv[1]):   # the attempt is stopped once the main request is recorded
    print("CLI INIT CHECK: no transcript (attempt stopped before writing it); the request body below decides")
    sys.exit(0)
for line in open(sys.argv[1]):
    e = json.loads(line)
    if e.get("type") == "system" and e.get("subtype") == "init":
        tools = sorted(e.get("tools", []))
        print("CLI init event: version", e.get("claude_code_version"), "| apiKeySource", e.get("apiKeySource"),
              "| mcp", [(s["name"], s["status"]) for s in e.get("mcp_servers", [])])
        print("CLI init tools:", tools)
        print("CLI INIT CHECK", "PASSED: exactly the five ssvk MCP tools" if tools == want else "FAILED")
        break
else:
    print("CLI INIT CHECK: no init event")
EOF
if [ $rc -ne 0 ]; then
    echo "WIRE CHECK (request body): no request recorded -- see $out/attempt/transcript.jsonl"
    grep -o '"final_text": "[^"]*' "$out/attempt/attempt.json" | head -1
    exit 1
fi
python3 - "$out/request.json" <<'EOF'
import json, sys
doc = json.load(open(sys.argv[1]))["body"]
tools = sorted(t.get("name") for t in doc.get("tools", []))
want = sorted(f"mcp__ssvk__{t}" for t in ("shell", "oracle", "driver", "grade_dev", "checkpoint"))
system = doc.get("system")
text = json.dumps(system)[:200] if system is not None else ""
print("model:", doc.get("model"))
print("tools:", tools)
print("system prompt starts:", text)
ok = tools == want
print("WIRE CHECK", "PASSED: exactly the five ssvk MCP tools, no built-ins" if ok else f"FAILED: expected {want}")
sys.exit(0 if ok else 1)
EOF
