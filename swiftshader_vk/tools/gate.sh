#!/usr/bin/env bash
# gate.sh [SPLIT] [PREFIX...] -- generate a split and run the Stage 7 gates on it:
#   validity (validation layer, 0 spec violations), exit ok, no skipped op (unless the
#   case's meta says "expect_skips": a case built to observe a failing creation),
#   repeats and ThreadCount 4/1 byte-identical, outputs unchanged when every allocation
#   starts as 0xA5 instead of whatever the allocator left (no case reads memory it never
#   wrote) (tools/determinism.py), and print the cases that fail any gate.
# PREFIX limits the gated cases to names starting with one of the prefixes.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
cd "$here/.."
# shellcheck disable=SC1091
source .envrc
split=${1:-public}; shift || true
dir=$here/corpus/$split
rm -rf "$dir"                     # generation is deterministic: no stale cases survive a rename
python -m evalbase.corpus.common "$split" >/dev/null
only=()
for p in "$@"; do
    for f in "$dir"/"$p"*.json; do [ -e "$f" ] && only+=("$(basename "$f" .json)"); done
done
out=$here/runs/gate-$split
rm -rf "$out"
args=(--assets "$here/corpus/assets" --out "$out" --repeats 2 --jobs 4)
[ ${#only[@]} -gt 0 ] && args+=(--only "${only[@]}")
python "$here/tools/determinism.py" "$dir" "${args[@]}" > "$out.log" 2>&1 || { tail -20 "$out.log"; exit 1; }
python - "$out/determinism.json" "$dir" <<'EOF'
import json, os, sys
R = json.load(open(sys.argv[1]))
def expect_skips(case):
    doc = json.load(open(os.path.join(sys.argv[2], case + ".json")))
    return bool((doc.get("meta") or {}).get("expect_skips"))
# A skipped op means something the case builds was not created on the reference: the
# case would grade a candidate on empty outputs (Stage 8 found four such cases).
bad = [r for r in R if r["exit"] != "ok" or r["validation_count"] or not r["repeat_identical"]
       or not r["threads_identical"] or not r.get("poison_identical", True)
       or (r["skips"] and not expect_skips(r["case"]))]
print(f"gated {len(R)} cases: {len(R) - len(bad)} pass, {len(bad)} fail")
for r in sorted(bad, key=lambda r: r["case"]):
    print(f'  {r["case"]}: exit={r["exit"]} {r.get("error") or ""}')
    for v in r["validation"][:3]:
        print("     V:", v[:300].replace("\n", " "))
    if not r.get("poison_identical", True):
        print("     reads unwritten memory:", [f'{d.get("snapshot")}/{d.get("item")}' if d["what"] == "snapshot" else d["what"]
                                                for d in r["poison"]["diffs"]][:6])
    if not r["repeat_identical"] or not r["threads_identical"]:
        print("     nondeterministic:", [d for b in r["repeat"] + r["threads"] for d in b["diffs"]][:2])
    for s in r["skips"][:2]:
        print("     skip:", s[:200])
EOF
