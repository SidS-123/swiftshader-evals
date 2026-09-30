# syntax=docker/dockerfile:1
# ssvk-ref: the trusted oracle image (PLAN_v1.md §6). Every input is pinned in
# pins.lock and arrives as a build arg; build with ./build_ref.sh.
#
#   /opt/swiftshader/{llvm,subzero}/   libvk_swiftshader.so (+ unit-test logs)
#   /opt/vk/                           loader, validation layers, vulkaninfo,
#                                      glslangValidator, spirv-{val,dis,as,opt}
#   /opt/lavapipe/                     Mesa lavapipe (fairness comparator only)
#   /opt/ssvk/icd/*.json               ICD manifests with absolute paths
#   /opt/ssvk/ini/<variant>/           SwiftShader.ini per run variant (cwd of the oracle)

ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS base
ARG APT_SNAPSHOT
ENV DEBIAN_FRONTEND=noninteractive
# Bootstrap: CA certificates so apt can reach the https snapshot service; every
# package installed after this resolves from APT_SNAPSHOT.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && echo "APT::Snapshot \"${APT_SNAPSHOT}\";" > /etc/apt/apt.conf.d/50snapshot

# ------------------------------------------------------------------ toolchain
FROM base AS build
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      build-essential gcc-13 g++-13 cmake ninja-build git python3 python3-venv pkg-config \
 && rm -rf /var/lib/apt/lists/*
COPY fetch.sh retry.sh /usr/local/bin/
ARG JOBS=8

# ------------------------------------------------------------------ SwiftShader
FROM build AS ss-src
ARG SWIFTSHADER_URL
ARG SWIFTSHADER_COMMIT
ARG SWIFTSHADER_GLSLANG_COMMIT
ARG SWIFTSHADER_GOOGLETEST_COMMIT
RUN fetch.sh "$SWIFTSHADER_URL" "$SWIFTSHADER_COMMIT" /src/swiftshader \
 && fetch.sh https://github.com/KhronosGroup/glslang.git "$SWIFTSHADER_GLSLANG_COMMIT" /src/swiftshader/third_party/glslang \
 && fetch.sh https://github.com/google/googletest.git "$SWIFTSHADER_GOOGLETEST_COMMIT" /src/swiftshader/third_party/googletest

# One build per Reactor backend. The unit tests run here (from the source root,
# as SwiftShader's CI does); results go to a log checked by build_ref.sh, so a
# failing test is visible without silently breaking or passing the build.
FROM ss-src AS ss-llvm
ARG SWIFTSHADER_LLVM_VERSION
ARG JOBS
RUN cmake -S /src/swiftshader -B /src/swiftshader/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DREACTOR_BACKEND=LLVM -DSWIFTSHADER_LLVM_VERSION=${SWIFTSHADER_LLVM_VERSION} \
      -DSWIFTSHADER_BUILD_WSI_XCB=FALSE -DSWIFTSHADER_BUILD_WSI_WAYLAND=FALSE \
      -DSWIFTSHADER_WARNINGS_AS_ERRORS=FALSE -DSWIFTSHADER_BUILD_TESTS=TRUE \
 && cmake --build /src/swiftshader/build -j ${JOBS}
COPY run_ss_unittests.sh /usr/local/bin/
RUN run_ss_unittests.sh /src/swiftshader/build /out

FROM ss-src AS ss-subzero
ARG JOBS
RUN cmake -S /src/swiftshader -B /src/swiftshader/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DREACTOR_BACKEND=Subzero \
      -DSWIFTSHADER_BUILD_WSI_XCB=FALSE -DSWIFTSHADER_BUILD_WSI_WAYLAND=FALSE \
      -DSWIFTSHADER_WARNINGS_AS_ERRORS=FALSE -DSWIFTSHADER_BUILD_TESTS=TRUE \
 && cmake --build /src/swiftshader/build -j ${JOBS}
COPY run_ss_unittests.sh /usr/local/bin/
RUN run_ss_unittests.sh /src/swiftshader/build /out

# ------------------------------------------------------------------ Khronos SDK components
# Headers, loader, validation layers, vulkaninfo, glslang, SPIRV-Tools, all at
# VULKAN_SDK_TAG. UPDATE_DEPS fetches each project's known-good (pinned) deps.
FROM build AS khronos
ARG VULKAN_SDK_TAG
ARG JOBS
ENV KHR=https://github.com/KhronosGroup
RUN fetch.sh $KHR/Vulkan-Headers.git "$VULKAN_SDK_TAG" /src/Vulkan-Headers \
 && cmake -S /src/Vulkan-Headers -B /b/headers -G Ninja -DCMAKE_INSTALL_PREFIX=/opt/vk \
 && cmake --install /b/headers
RUN fetch.sh $KHR/Vulkan-Loader.git "$VULKAN_SDK_TAG" /src/Vulkan-Loader \
 && retry.sh cmake -S /src/Vulkan-Loader -B /b/loader -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/vk -DVULKAN_HEADERS_INSTALL_DIR=/opt/vk \
      -DBUILD_WSI_XCB_SUPPORT=OFF -DBUILD_WSI_XLIB_SUPPORT=OFF -DBUILD_WSI_WAYLAND_SUPPORT=OFF \
      -DBUILD_TESTS=OFF \
 && cmake --build /b/loader -j ${JOBS} && cmake --install /b/loader
RUN fetch.sh $KHR/SPIRV-Headers.git "$VULKAN_SDK_TAG" /src/SPIRV-Headers \
 && fetch.sh $KHR/SPIRV-Tools.git "$VULKAN_SDK_TAG" /src/SPIRV-Tools \
 && cmake -S /src/SPIRV-Tools -B /b/spirv-tools -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/vk -DSPIRV-Headers_SOURCE_DIR=/src/SPIRV-Headers \
      -DSPIRV_SKIP_TESTS=ON -DSPIRV_WERROR=OFF \
 && cmake --build /b/spirv-tools -j ${JOBS} && cmake --install /b/spirv-tools
RUN fetch.sh $KHR/glslang.git "$VULKAN_SDK_TAG" /src/glslang \
 && cmake -S /src/glslang -B /b/glslang -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/vk -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DBUILD_EXTERNAL=OFF \
 && cmake --build /b/glslang -j ${JOBS} && cmake --install /b/glslang
# vulkaninfo builds against the headers and loader above: UPDATE_DEPS would build
# its own loader copy, with WSI on.
RUN fetch.sh $KHR/Vulkan-Tools.git "$VULKAN_SDK_TAG" /src/Vulkan-Tools \
 && cmake -S /src/Vulkan-Tools -B /b/tools -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/vk -DCMAKE_PREFIX_PATH=/opt/vk -DUPDATE_DEPS=OFF \
      -DBUILD_CUBE=OFF -DBUILD_ICD=OFF \
      -DBUILD_WSI_XCB_SUPPORT=OFF -DBUILD_WSI_XLIB_SUPPORT=OFF -DBUILD_WSI_WAYLAND_SUPPORT=OFF \
      -DBUILD_WSI_DIRECTFB_SUPPORT=OFF -DBUILD_TESTS=OFF \
 && cmake --build /b/tools --target vulkaninfo -j ${JOBS} \
 && install -m 0755 "$(find /b/tools -type f -name vulkaninfo -perm -u+x | head -1)" /opt/vk/bin/vulkaninfo
RUN fetch.sh $KHR/Vulkan-ValidationLayers.git "$VULKAN_SDK_TAG" /src/Vulkan-ValidationLayers \
 && retry.sh cmake -S /src/Vulkan-ValidationLayers -B /b/vvl -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/vk -DUPDATE_DEPS=ON -DBUILD_WERROR=OFF -DBUILD_TESTS=OFF \
      -DBUILD_WSI_XCB_SUPPORT=OFF -DBUILD_WSI_XLIB_SUPPORT=OFF -DBUILD_WSI_WAYLAND_SUPPORT=OFF \
 && cmake --build /b/vvl -j ${JOBS} && cmake --install /b/vvl
RUN echo "$VULKAN_SDK_TAG" > /opt/vk/SDK_TAG

# vulkaninfo requests the loader's instance version (1.4), and SwiftShader, a
# Vulkan 1.3 driver, fails vkCreateInstance for apiVersion > 1.3 with
# VK_ERROR_INCOMPATIBLE_DRIVER. Cap vulkaninfo's request at 1.3, the eval's
# target version. This patches the tool, not the oracle.
FROM khronos AS vulkaninfo13
ARG JOBS
RUN f=/src/Vulkan-Tools/vulkaninfo/vulkaninfo.h \
 && grep -q "api_version = APIVersion(instance_version);" "$f" \
 && sed -i "s/api_version = APIVersion(instance_version);/if (instance_version > VK_API_VERSION_1_3) instance_version = VK_API_VERSION_1_3; api_version = APIVersion(instance_version);/" "$f" \
 && grep -q "instance_version = VK_API_VERSION_1_3" "$f" \
 && cmake --build /b/tools --target vulkaninfo -j ${JOBS} \
 && install -m 0755 "$(find /b/tools -type f -name vulkaninfo -perm -u+x | head -1)" /opt/vk/bin/vulkaninfo

# ------------------------------------------------------------------ Mesa lavapipe (D12)
FROM build AS mesa
ARG MESA_TAG
ARG MESON_VERSION
ARG JOBS
RUN apt-get -o Acquire::Retries=10 update -qq && apt-get -o Acquire::Retries=20 -o Acquire::http::Pipeline-Depth=0 -o Acquire::Queue-Mode=access install -y -qq --no-install-recommends \
      llvm-18-dev bison flex \
      zlib1g-dev libzstd-dev libexpat1-dev libelf-dev libdrm-dev \
 && rm -rf /var/lib/apt/lists/*
ARG MAKO_VERSION
ARG PYYAML_VERSION
ARG PACKAGING_VERSION
# Meson runs from this venv, so Mesa's Python build deps must live in it too.
RUN python3 -m venv /opt/meson && retry.sh /opt/meson/bin/pip install -q "meson==${MESON_VERSION}" \
      "mako==${MAKO_VERSION}" "pyyaml==${PYYAML_VERSION}" "packaging==${PACKAGING_VERSION}"
# Mesa compiles its internal GLSL shaders with glslangValidator: use the pinned one.
COPY --from=khronos /opt/vk /opt/vk
ENV PATH=/opt/meson/bin:/opt/vk/bin:$PATH
RUN fetch.sh https://gitlab.freedesktop.org/mesa/mesa.git "$MESA_TAG" /src/mesa \
 && meson setup /b/mesa /src/mesa --prefix=/opt/lavapipe --libdir=lib \
      --buildtype=release -Db_ndebug=true \
      -Dvulkan-drivers=swrast -Dgallium-drivers=llvmpipe -Dplatforms= \
      -Dglx=disabled -Degl=disabled -Dgbm=disabled -Dopengl=false -Dgles1=disabled -Dgles2=disabled \
      -Dllvm=enabled -Dshared-llvm=enabled -Dvideo-codecs= -Dbuild-tests=false \
      -Dvalgrind=disabled -Dlibunwind=disabled -Dzstd=enabled \
 && ninja -C /b/mesa -j ${JOBS} install

# ------------------------------------------------------------------ vkreplay (Stage 4)
# The replay driver. Its source arrives as the named build context `driver`
# (build_ref.sh passes --build-context driver=../driver), so the cached stages
# above do not depend on it.
FROM khronos AS vkreplay-deps
ARG NLOHMANN_JSON_TAG
RUN fetch.sh https://github.com/nlohmann/json.git "$NLOHMANN_JSON_TAG" /src/json

FROM vkreplay-deps AS vkreplay-build
ARG JOBS
COPY --from=driver . /src/vkreplay
RUN cmake -S /src/vkreplay -B /b/vkreplay -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/opt/vk \
      -DVK_XML=/opt/vk/share/vulkan/registry/vk.xml -DJSON_INCLUDE=/src/json/single_include \
      -DCMAKE_INSTALL_PREFIX=/opt/vkreplay \
 && cmake --build /b/vkreplay -j ${JOBS} && cmake --install /b/vkreplay

# ------------------------------------------------------------------ the oracle image
FROM base AS ref
RUN apt-get -o Acquire::Retries=10 update -qq && apt-get -o Acquire::Retries=20 -o Acquire::http::Pipeline-Depth=0 -o Acquire::Queue-Mode=access install -y -qq --no-install-recommends \
      libstdc++6 libllvm18 zlib1g libzstd1 libexpat1 libelf1t64 libdrm2 python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ss-llvm    /src/swiftshader/build/Linux/libvk_swiftshader.so /opt/swiftshader/llvm/
COPY --from=ss-llvm    /out/                       /opt/swiftshader/llvm/
COPY --from=ss-subzero /src/swiftshader/build/Linux/libvk_swiftshader.so /opt/swiftshader/subzero/
COPY --from=ss-subzero /out/                       /opt/swiftshader/subzero/
COPY --from=khronos    /opt/vk                     /opt/vk
COPY --from=vulkaninfo13 /opt/vk/bin/vulkaninfo    /opt/vk/bin/vulkaninfo
COPY --from=mesa       /opt/lavapipe               /opt/lavapipe
COPY --from=vkreplay-build /opt/vkreplay           /opt/vkreplay
COPY icd/ /opt/ssvk/icd/
COPY ini/ /opt/ssvk/ini/
COPY ssvk-entry.sh /usr/local/bin/ssvk
ARG SWIFTSHADER_COMMIT
ARG MESA_TAG
RUN printf 'swiftshader=%s\nmesa=%s\nvulkan_sdk=%s\n' "$SWIFTSHADER_COMMIT" "$MESA_TAG" "$(cat /opt/vk/SDK_TAG)" > /opt/ssvk/VERSIONS \
 && dpkg-query -W > /opt/ssvk/PACKAGES \
 && userdel -r ubuntu && useradd -m -u 1000 runner \
 && groupadd -g 2000 cand && useradd -M -u 2000 -g 2000 -s /usr/sbin/nologin cand
# uid 2000 runs vkreplay's untrusted child (the ICD under test); it owns nothing.
ENV PATH=/opt/vkreplay/bin:/opt/vk/bin:$PATH \
    LD_LIBRARY_PATH=/opt/vk/lib \
    VK_LAYER_PATH=/opt/vk/share/vulkan/explicit_layer.d \
    VK_LOADER_LAYERS_DISABLE=~implicit~
ENTRYPOINT ["/usr/local/bin/ssvk"]
CMD ["info"]
