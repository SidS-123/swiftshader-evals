#!/usr/bin/env bash
# make_spec_dir.sh -- build the frozen reference material the model reads as /task/spec.
#
#   swiftshader_vk/tools/make_spec_dir.sh [--check]
#
# spec/device_profile.json is committed (tools/make_device_profile.py). Everything
# else here is fetched at the commits in images/pins.lock and is git-ignored:
#
#   spec/vulkan/        Vulkan-Docs AsciiDoc sources at VULKAN_DOCS_TAG (chapters/,
#                       appendices/, vkspec.adoc, config for attributes) -- the spec text
#   spec/vk.xml         the API registry at VULKAN_SDK_TAG (the headers' own)
#   spec/spirv/         SPIR-V grammar + headers from SPIRV-Headers at VULKAN_SDK_TAG;
#                       SPIRV.html and GLSL.std.450.html are committed copies (their
#                       download URL, date and sha256 are in spec/spirv/ORIGIN)
#   spec/dataformat/    the Khronos Data Format Specification sources at DATAFORMAT_TAG
#                       (normative decoding of compressed, packed-float, shared-exponent
#                       and sRGB formats)
#   spec/MANIFEST.sha256  sha256 of every file above (the version record of spec/, committed)
#
# --check rebuilds into a temporary directory and compares manifests.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
pin() { grep "^$1=" "$here/images/pins.lock" | cut -d= -f2-; }
DOCS=$(pin VULKAN_DOCS_TAG) SDK=$(pin VULKAN_SDK_TAG) DF=$(pin DATAFORMAT_TAG)

fetch() {  # fetch URL REF DIR  (shallow, one commit)
    mkdir -p "$3"; git -C "$3" init -q; git -C "$3" remote add origin "$1" 2>/dev/null || true
    local n=0
    until git -C "$3" fetch -q --depth 1 origin "$2"; do
        n=$((n + 1)); [ $n -ge 6 ] && { echo "fetch failed: $1 $2" >&2; exit 1; }; sleep $((n * 5))
    done
    git -C "$3" checkout -q FETCH_HEAD
}

build() {  # build DEST
    local dest=$1 tmp
    tmp=$(mktemp -d)
    fetch https://github.com/KhronosGroup/Vulkan-Docs.git "$DOCS" "$tmp/docs"
    fetch https://github.com/KhronosGroup/Vulkan-Headers.git "$SDK" "$tmp/headers"
    fetch https://github.com/KhronosGroup/SPIRV-Headers.git "$SDK" "$tmp/spirvh"
    fetch https://github.com/KhronosGroup/DataFormat.git "refs/tags/$DF" "$tmp/df"
    rm -rf "$dest/vulkan" "$dest/vk.xml" "$dest/dataformat"
    mkdir -p "$dest/vulkan" "$dest/spirv" "$dest/dataformat"
    (cd "$tmp/df" && git ls-files -z | xargs -0 cp --parents -t "$dest/dataformat")
    [ "$dest" != "$here/spec" ] && cp "$here/spec/spirv/"{SPIRV.html,GLSL.std.450.html,ORIGIN} "$dest/spirv/"
    cp -r "$tmp/docs/chapters" "$tmp/docs/appendices" "$dest/vulkan/"
    cp "$tmp/docs/vkspec.adoc" "$tmp/docs/LICENSE.adoc" "$dest/vulkan/" 2>/dev/null || cp "$tmp/docs/vkspec.adoc" "$dest/vulkan/"
    [ -d "$tmp/docs/config" ] && cp -r "$tmp/docs/config" "$dest/vulkan/"
    cp "$tmp/headers/registry/vk.xml" "$dest/vk.xml"
    cp "$tmp/spirvh/include/spirv/unified1/"{spirv.core.grammar.json,extinst.glsl.std.450.grammar.json,spirv.h,spirv.hpp,GLSL.std.450.h} "$dest/spirv/"
    printf 'Vulkan-Docs %s (%s)\nVulkan-Headers %s (%s)\nSPIRV-Headers %s (%s)\nDataFormat %s (%s)\nSPIR-V spec HTML: see spirv/ORIGIN\n' \
        "$DOCS" "$(git -C "$tmp/docs" rev-parse HEAD)" "$SDK" "$(git -C "$tmp/headers" rev-parse HEAD)" \
        "$SDK" "$(git -C "$tmp/spirvh" rev-parse HEAD)" "$DF" "$(git -C "$tmp/df" rev-parse HEAD)" > "$dest/SOURCES"
    rm -rf "$tmp"
    (cd "$dest" && find vulkan spirv dataformat vk.xml SOURCES device_profile.json -type f -print0 | sort -z | xargs -0 sha256sum) \
        > "$dest/MANIFEST.sha256"
}

if [ "${1:-}" = "--check" ]; then
    t=$(mktemp -d)
    cp "$here/spec/device_profile.json" "$t/"
    build "$t"
    if diff -q "$t/MANIFEST.sha256" "$here/spec/MANIFEST.sha256" >/dev/null; then echo "spec/: IDENTICAL"; else echo "spec/: DIFFERS"; exit 1; fi
    rm -rf "$t"
else
    build "$here/spec"
    echo "spec/: $(wc -l < "$here/spec/MANIFEST.sha256") files, $(du -sh "$here/spec" | cut -f1), manifest sha256 $(sha256sum "$here/spec/MANIFEST.sha256" | cut -c1-16)"
fi
