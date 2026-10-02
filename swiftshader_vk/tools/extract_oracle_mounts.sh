#!/usr/bin/env bash
# extract_oracle_mounts.sh -- copy the real drivers out of ssvk-ref:1 into runs/oracle-mount/
# (git-ignored) for the controls' read-only mounts (controls/<name>/control.json). Candidates
# are replayed in ssvk-cand:1, which holds no driver; only control runs see these mounts.
#
#   runs/oracle-mount/swiftshader/    -> /opt/swiftshader   (LLVM and Subzero builds)
#   runs/oracle-mount/ini/            -> /opt/ssvk/ini      (SwiftShader.ini per variant)
#   runs/oracle-mount/lavapipe/       -> /opt/lavapipe
#   runs/oracle-mount/lavapipe-deps/  -> /opt/lavapipe-deps (LLVM 18 and the other libraries
#                                         lavapipe links; on LD_LIBRARY_PATH for that control)
#
# Prints the sha256 of each driver library in the image and in the copy; they must match.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
out=$here/runs/oracle-mount
rm -rf "$out" && mkdir -p "$out/lavapipe-deps"
cid=$(docker create ssvk-ref:1)
trap 'docker rm -f "$cid" >/dev/null' EXIT
docker cp -q "$cid:/opt/swiftshader" "$out/swiftshader"
docker cp -q "$cid:/opt/ssvk/ini" "$out/ini"
docker cp -q "$cid:/opt/lavapipe" "$out/lavapipe"
deps=$(docker run --rm --entrypoint bash ssvk-ref:1 -c \
    "ldd /opt/lavapipe/lib/libvulkan_lvp.so | awk '/=> \\//{print \$3}' | grep -v -e '/libc.so' -e '/libm.so' -e '/libstdc++' -e '/libgcc_s'")
for d in $deps; do docker cp -q -L "$cid:$d" "$out/lavapipe-deps/"; done
docker run --rm --entrypoint bash ssvk-ref:1 -c \
    'sha256sum /opt/swiftshader/llvm/libvk_swiftshader.so /opt/swiftshader/subzero/libvk_swiftshader.so /opt/lavapipe/lib/libvulkan_lvp.so' \
    | sed 's#/opt/#image   /opt/#'
(cd "$out" && sha256sum swiftshader/llvm/libvk_swiftshader.so swiftshader/subzero/libvk_swiftshader.so lavapipe/lib/libvulkan_lvp.so \
    | sed 's#  #  copy   #')
echo "lavapipe deps: $(ls "$out/lavapipe-deps" | tr '\n' ' ')"
