#!/usr/bin/env bash
# dev_build.sh -- compile vkreplay in the pinned toolchain stage without rebuilding
# the image (fast edit/compile loop). Output: ~/.cache/ssvk/vkreplay-build/vkreplay.
# Needs the ssvk-ref-stage:vkreplay-deps image (images/build_ref.sh vkreplay-deps).
# The binary of record is the one images/build_ref.sh builds into ssvk-ref.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${VKREPLAY_BUILD_DIR:-$HOME/.cache/ssvk/vkreplay-build}
mkdir -p "$out"
docker run --rm --network none --user "$(id -u):$(id -g)" \
    -v "$here:/src/vkreplay:ro" -v "$out:/b" ssvk-ref-stage:vkreplay-deps \
    bash -c 'cmake -S /src/vkreplay -B /b -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/opt/vk \
               -DVK_XML=/opt/vk/share/vulkan/registry/vk.xml -DJSON_INCLUDE=/src/json/single_include >/dev/null \
             && cmake --build /b -j "${JOBS:-8}"'
echo "built $out/vkreplay"
