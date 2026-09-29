#!/bin/sh
# run_ss_unittests.sh BUILD OUT -- run SwiftShader's own unit tests from the
# source root (as its CI does) and write OUT/unittests.txt: one
# "<suite> rc=<code>" line per suite, then each suite's gtest summary.
set -u
build=$1 out=$2
mkdir -p "$out"
cd /src/swiftshader
: > "$out/unittests.txt"
for t in ReactorUnitTests system-unittests math-unittests vk-unittests; do
    if [ -x "$build/$t" ]; then
        "$build/$t" > "$out/$t.log" 2>&1
        echo "$t rc=$?" >> "$out/unittests.txt"
    else
        echo "$t rc=missing" >> "$out/unittests.txt"
    fi
done
for t in ReactorUnitTests system-unittests math-unittests vk-unittests; do
    [ -f "$out/$t.log" ] && { echo "== $t"; grep -E '^\[  (PASSED|FAILED)  \]|tests? ran|DISABLED' "$out/$t.log"; } >> "$out/unittests.txt"
done
cat "$out/unittests.txt"
