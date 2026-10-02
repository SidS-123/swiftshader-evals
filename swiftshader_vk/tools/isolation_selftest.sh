#!/usr/bin/env bash
# isolation_selftest.sh -- the isolation check must pass on ssvk-solver:1 and ssvk-cand:1 and
# fail on a contaminated copy of each (SwiftShader hidden under an innocent name, plus a
# stray ICD manifest). PLAN_v1.md §13 exit: "isolation check passes on the solver image and
# fails on a deliberately contaminated image".
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
check=$here/isolation_check.sh
work=$(mktemp -d)
trap 'rm -rf "$work"; docker image rm -f ssvk-contaminated-solver:test ssvk-contaminated-cand:test >/dev/null 2>&1' EXIT
docker run --rm --entrypoint cat ssvk-ref:1 /opt/swiftshader/llvm/libvk_swiftshader.so > "$work/libhelper.so"
printf '{"file_format_version":"1.0.0","ICD":{"library_path":"/usr/lib/x86_64-linux-gnu/libhelper.so","api_version":"1.3.0"}}\n' > "$work/helper_icd.json"
status=0
run() {   # image expect
    out=$(docker run --rm --network none --user 1000:1000 --entrypoint bash -v "$check:/check.sh:ro" "$1" /check.sh 2>&1)
    rc=$?
    if { [ "$2" = pass ] && [ $rc -eq 0 ]; } || { [ "$2" = fail ] && [ $rc -ne 0 ]; }; then
        echo "ok   $1: expected $2 ($(echo "$out" | head -1))"
    else
        echo "BAD  $1: expected $2, got rc=$rc: $out"; status=1
    fi
}
for base in solver cand; do
    cat > "$work/Dockerfile" <<EOF
FROM ssvk-$base:1
USER root
COPY libhelper.so /usr/lib/x86_64-linux-gnu/libhelper.so
COPY helper_icd.json /usr/share/vulkan/icd.d/helper_icd.json
EOF
    docker build -q -t "ssvk-contaminated-$base:test" "$work" >/dev/null
    run "ssvk-$base:1" pass
    run "ssvk-contaminated-$base:test" fail
done
exit $status
