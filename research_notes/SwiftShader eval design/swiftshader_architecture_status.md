# SwiftShader: Architecture, API Surface, Size, Build, Health, Testing and Determinism (as of 2026-09-26)

Method note: most facts below come from a clone of https://github.com/google/swiftshader (a mirror of https://swiftshader.googlesource.com/SwiftShader) at HEAD `1e80438d2b93` (committed 2026-09-16), plus web sources. Where I cite a file, the URL points to `master` on the GitHub mirror, and the fact was checked against that HEAD. Line counts are my own `wc -l` over `.cpp/.hpp/.h/.c/.inc` files (they include comments and blank lines).

## 1. Which APIs does SwiftShader implement today? What is its conformance status?

### Takeaway
SwiftShader today is **only a Vulkan 1.3 ICD** (API_VERSION = VK_API_VERSION_1_3, SPIR-V target env Vulkan 1.3). It advertises about 115 extension names. The legacy D3D8/D3D9 code was deleted in April 2020 and the legacy OpenGL ES/EGL code in April 2022. GLES is now provided by layering ANGLE on top ("SwANGLE"). The README claims Vulkan 1.3 conformance (Khronos submission #717) and GLES 3.1 conformance through ANGLE (submission #906). I could not independently render those entries from the Khronos site.

### Cited Findings
- The README describes SwiftShader as "a high-performance CPU-based implementation of the Vulkan 1.3 graphics API". It notes that ANGLE can be layered on top to give OpenGL ES 3.1 ("SwANGLE"). — [README.md](https://github.com/google/swiftshader/blob/master/README.md)
- README footnotes: "Vulkan 1.3 conformance: https://www.khronos.org/conformance/adopters/conformant-products#submission_717" and "OpenGL ES 3.1 conformance: …/opengles#submission_906". — [README.md](https://github.com/google/swiftshader/blob/master/README.md)
- `src/Vulkan/VkConfig.hpp` contains `constexpr uint32_t API_VERSION = VK_API_VERSION_1_3;` and `constexpr spv_target_env SPIRV_VERSION = SPV_ENV_VULKAN_1_3;`. — [VkConfig.hpp](https://github.com/google/swiftshader/blob/master/src/Vulkan/VkConfig.hpp)
- 115 distinct `*_EXTENSION_NAME` strings appear in the extension tables in `libVulkan.cpp` (instance and device extensions together). — [libVulkan.cpp](https://github.com/google/swiftshader/blob/master/src/Vulkan/libVulkan.cpp)
  - Notable ones: VK_KHR_dynamic_rendering (+ local_read), VK_KHR_synchronization2, VK_KHR_timeline_semaphore, VK_KHR_vulkan_memory_model, VK_KHR_maintenance4, VK_KHR_shader_integer_dot_product, VK_KHR_sampler_ycbcr_conversion, VK_KHR_multiview, VK_KHR_spirv_1_4, VK_KHR_shader_float_controls, VK_EXT_graphics_pipeline_library / VK_KHR_pipeline_library, VK_EXT_extended_dynamic_state(2), VK_EXT_vertex_input_dynamic_state, VK_EXT_descriptor_indexing, VK_EXT_robustness2, VK_EXT_image_robustness, VK_EXT_pipeline_robustness, VK_EXT_host_image_copy, VK_EXT_line_rasterization, VK_EXT_provoking_vertex, VK_EXT_blend_operation_advanced, VK_EXT_custom_border_color, VK_EXT_depth_clip_enable/control, VK_EXT_4444_formats, VK_EXT_image_drm_format_modifier, VK_KHR_unified_image_layouts, VK_KHR_internally_synchronized_queues (added 2026-07-06), VK_KHR/EXT_surface_maintenance1 and swapchain_maintenance1 (added 2025-08-11).
  - Platform/WSI ones: VK_KHR_xcb/wayland/win32_surface, VK_EXT_metal_surface, VK_MVK_macos_surface, VK_EXT_headless_surface, VK_EXT_directfb_surface, VK_KHR_display, VK_ANDROID_native_buffer, VK_ANDROID_external_memory_android_hardware_buffer, VK_FUCHSIA_external_memory/semaphore, VK_EXT_external_memory_host.
- Core features **not** supported (VkPhysicalDeviceFeatures in `VkPhysicalDevice.cpp`): geometryShader, tessellationShader, shaderFloat64, sparseBinding, wideLines, multiViewport, textureCompressionASTC_HDR. Supported: robustBufferAccess, fillModeNonSolid, samplerAnisotropy, and texture compression for ETC2, BC and ASTC_LDR (ASTC_LDR can be turned off at build time with `SWIFTSHADER_ENABLE_ASTC`). — [VkPhysicalDevice.cpp](https://github.com/google/swiftshader/blob/master/src/Vulkan/VkPhysicalDevice.cpp); [CMakeLists.txt](https://github.com/google/swiftshader/blob/master/CMakeLists.txt)
- Subgroup size equals `sw::SIMD::Width`, which is 4: `SIMD.cpp` asserts `SIMD::Width == 4` throughout. — [VkPhysicalDevice.cpp](https://github.com/google/swiftshader/blob/master/src/Vulkan/VkPhysicalDevice.cpp); [SIMD.cpp](https://github.com/google/swiftshader/blob/master/src/Reactor/SIMD.cpp)
- Removal history, from git log:
  - "Remove the D3D9 and D3D8 source code" (f99302c4) on 2020-04-02.
  - "Remove the OpenGL ES 1.1 build target" on 2021-04-09.
  - "Regres: Remove GLES tests from CI test runs" on 2021-02-10.
  - "Remove OpenGL ES targets from the CMake build" on 2021-12-09.
  - "Regres: remove OpenGL ES testlists" on 2022-02-25.
  - "Delete the legacy OpenGL ES implementation's source code" (c0a055bf) on 2022-04-19.
  - Sources: [commit f99302c4](https://github.com/google/swiftshader/commit/f99302c4); [commit c0a055bf](https://github.com/google/swiftshader/commit/c0a055bf)
- `docs/Index.md` is marked ":warning: **Out of date**". It still describes OpenGL/EGL, `src/Renderer`, `src/Shader`, and a GLSL compiler using Flex/Bison. None of these exist any more. — [docs/Index.md](https://github.com/google/swiftshader/blob/master/docs/Index.md)
- The Chromium docs still say SwiftShader implements "Vulkan and OpenGL ES", but the GLES path is SwANGLE (`--use-gl=angle --use-angle=swiftshader`). — [Chromium docs: Using Chromium with SwiftShader](https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/swiftshader.md)

### Inferences
- An eval should target the Vulkan 1.3 ICD only. Anything framed as "SwiftShader GLES/D3D" refers to code that has been gone since 2020–2022. It survives only in git history, e.g. the 2022 commit before c0a055bf.
- There are no geometry, tessellation or fp64 shaders. The shader stages are vertex, fragment and compute, which limits the surface a reimplementation would have to cover.

### Gaps
- I could not render the Khronos conformant-products pages to confirm submission #717's date, CTS version or platform. WebFetch returned only entries in the ~774–997 range. The claim of Vulkan 1.3 conformance rests on the README's link. I found no evidence of a Vulkan 1.4 conformance submission. The Vulkan headers were updated to 1.4.355 (2026-06-30), but API_VERSION is still 1.3.
- I did not enumerate the Vulkan 1.3 core feature structs (e.g. which optional 1.1/1.2/1.3 features are VK_FALSE) beyond the core-1.0 list above.

## 2. Architecture: components, SPIR-V to CPU compilation, JIT backends, threading

### Takeaway
There are four layers:
- **Vulkan object layer** (`src/Vulkan`)
- **Device/renderer** (`src/Device`): draw scheduling, clipping, rasterizer, blitter, texture decoders, routine caches
- **Pipeline** (`src/Pipeline`): SpirvShader, which emits Reactor code for SPIR-V, plus vertex/setup/pixel/compute routines and the sampler
- **Reactor** (`src/Reactor`): a C++-embedded DSL that JITs through LLVM or Subzero

`src/System` holds utilities and `src/WSI` holds surfaces and swapchains. The Marl fiber scheduler provides multithreading. Shaders are not interpreted. Each SPIR-V module plus its pipeline state is compiled into a specialized SIMD-4 native routine ("SIMT over 4 lanes").

### Cited Findings
- The layer design is API → Renderer → Reactor → JIT. Reactor is "an embedded language for C++ to dynamically generate code in a WYSIWYG fashion". Operations are recorded in an in-memory IR and "materialized by the JIT into a function". Reactor types mirror C types with capitals (e.g. `Float y = 1 - x;`), with `If()/Else/For()`. — [docs/Index.md (marked out of date)](https://github.com/google/swiftshader/blob/master/docs/Index.md); [docs/Reactor.md](https://github.com/google/swiftshader/blob/master/docs/Reactor.md)
- Processing routines: VertexProcessor, SetupProcessor and PixelProcessor each produce a Reactor routine specialized to state and cache it. SetupRoutine does culling, gradients and rasterization setup. PixelRoutine plus QuadRasterizer handles per-pixel work (depth, stencil, blending). SamplerCore implements texture sampling. (Paths in Index.md are stale; the files now live in `src/Device` and `src/Pipeline`.) — [docs/Index.md](https://github.com/google/swiftshader/blob/master/docs/Index.md)
- Current files:
  - `src/Pipeline`: SpirvShader.cpp/.hpp plus split files (SpirvShaderArithmetic, ControlFlow, GLSLstd450, Group, Image, Instructions, Memory, Sampling, Spec, Debugger), SpirvBinary, SpirvProfiler, ComputeProgram, VertexProgram/Routine, PixelProgram/Routine, SetupRoutine, SamplerCore, ShaderCore, Constants.
  - `src/Device`: Renderer, Clipper, QuadRasterizer, Rasterizer, SetupProcessor, VertexProcessor, PixelProcessor, Blitter, ASTC_Decoder, BC_Decoder, ETC_Decoder, RoutineCache, Sampler.
  - `src/Reactor`: Reactor.cpp/hpp, Nucleus.hpp (backend interface), LLVMReactor.cpp, LLVMJIT.cpp, SubzeroReactor.cpp, Optimizer.cpp, SIMD.cpp, Coroutine.hpp, CPUID, ExecutableMemory.
  - `src/WSI`: Xcb, Wayland, Win32, Metal, Headless, Display, DirectFB surfaces, VkSwapchainKHR.
  - Source: [src tree](https://github.com/google/swiftshader/tree/master/src)
- Reactor backends in CMake: `REACTOR_BACKEND` ∈ {LLVM (default), LLVM-Submodule, Subzero}.
  - Default `SWIFTSHADER_LLVM_VERSION` is "10.0". loongarch64 is the exception and uses "16.0".
  - Bundled trees: `third_party/llvm-10.0`, `third_party/llvm-16.0`, plus an `llvm-project` submodule.
  - Source: [CMakeLists.txt](https://github.com/google/swiftshader/blob/master/CMakeLists.txt)
- GN (Chromium) builds: `use_swiftshader_with_subzero = supports_subzero && !is_msan`. `supports_subzero` is false on arm64, mips64el, ppc64, riscv64 and loong64, so Chromium x86/x64 builds use Subzero and ARM64 uses LLVM. `docs/Subzero.md` says: "For Chrome builds that use the BUILD.gn files, Subzero is the default as it produces significantly smaller binaries than with LLVM." Subzero is a fork of PNaCl's JIT. — [src/Reactor/reactor.gni](https://github.com/google/swiftshader/blob/master/src/Reactor/reactor.gni); [docs/Subzero.md](https://github.com/google/swiftshader/blob/master/docs/Subzero.md)
- Threading uses Marl ("a hybrid thread / fiber task scheduler written in C++ 11"; vendored in `third_party/marl`).
  - Renderer.hpp defines `MaxBatchSize = 128` primitives, `MaxClusterCount = 16` (pixel clusters, each with a `marl::Ticket`), and `MaxDrawCount = 16` in-flight draws (`marl::BoundedPool`).
  - Per-cluster occlusion counts are summed.
  - Sources: [third_party/marl/README.md](https://github.com/google/swiftshader/blob/master/third_party/marl/README.md); [src/Device/Renderer.hpp](https://github.com/google/swiftshader/blob/master/src/Device/Renderer.hpp)
- Runtime config: an optional `SwiftShader.ini` in the working directory, e.g. `[Processor] ThreadCount=4, AffinityMask=0xf`, `[Profiler] EnableSpirvProfiling`. Options are defined in `src/System/SwiftConfig.hpp`. — [docs/RuntimeConfiguration.md](https://github.com/google/swiftshader/blob/master/docs/RuntimeConfiguration.md)
- Other docs in `docs/`: Reactor.md, ReactorDebugInfo.md, Subzero.md, LLVM.md, SamplingRoutines.md, TimelineSemaphores.md, VulkanShaderDebugging.md (a cppdap-based shader debugger, `SWIFTSHADER_ENABLE_VULKAN_DEBUGGER`), dEQP.md, Regres.md, plus PDFs on Exp/Log and Sin/Cos optimizations. — [docs/](https://github.com/google/swiftshader/tree/master/docs)
- A Khronos 2019 talk by Alexis Hétu presents SwiftShader as a Vulkan "reference implementation and fallback" (historical, 2019). — [Khronos: SwiftShader Reference Implementation and Fallback](https://www.khronos.org/developers/linkto/swiftshader-reference-implementation-and-fallback)

### Inferences
- SPIR-V → CPU flow:
  1. SPIRV-Tools validates and optimizes the SPIR-V. (It is in third_party. I did not verify every call site, but SPIRV_VERSION is a `spv_target_env`.)
  2. `SpirvShader` parses it into an internal representation.
  3. At pipeline or draw-state specialization time, `SpirvShader` emits Reactor operations per instruction, operating on `SIMD::Float/Int` (4 lanes). Divergent control flow uses active-lane masks.
  4. Reactor/Nucleus lowers to LLVM IR or Subzero IR and JITs.
  5. The routine is cached keyed by state.
- This is a compiler, not an interpreter. A reimplementation target of "SPIR-V interpreter" would be a different design than the oracle, but could still be checked against it at the output level.

### Gaps
- I did not trace the exact SPIRV-Tools optimizer pass list used at shader module creation. It is in `VkPipeline.cpp`/`VkShaderModule.cpp` and was not inspected.

## 3. Code size, languages, dependencies, build systems

### Takeaway
First-party code is about **100k lines of C++**. Test and tool code adds about 19k lines of C++/Go. Vendored third-party code is far larger: LLVM 16 is about 6.3M lines, LLVM 10 about 3.8M, and SPIRV-Tools about 590k. There are three build systems: CMake (standalone), GN (Chromium/ANGLE/Dawn) and Android.bp (AOSP).

### Cited Findings
First-party lines (own count at HEAD 1e80438d, all C/C++ source and header files) — [src tree](https://github.com/google/swiftshader/tree/master/src):

| Component | Lines |
|---|---|
| src/Vulkan | 31,468 |
| src/Reactor | 26,857 |
| src/Pipeline | 24,581 |
| src/Device | 10,997 |
| src/System | 3,655 |
| src/WSI | 2,625 |
| **Total** | **≈100,200** |

Test lines — [tests tree](https://github.com/google/swiftshader/tree/master/tests):

| Test directory | Lines |
|---|---|
| tests/regres (Go) | 6,425 |
| ReactorUnitTests | 4,590 |
| VulkanUnitTests | 2,756 |
| VulkanWrapper | 1,962 |
| VulkanBenchmarks | 800 |
| MathUnitTests | 643 |
| SystemUnitTests | 507 |

Third-party code and dependencies:
- Vendored third_party C/C++ lines (own count) — [third_party](https://github.com/google/swiftshader/tree/master/third_party):

  | Directory | Lines |
  |---|---|
  | llvm-16.0 | ≈6.31M |
  | llvm-10.0 | ≈3.77M |
  | SPIRV-Tools | ≈588k |
  | subzero | ≈133k |
  | llvm-subzero | ≈74k |
  | SPIRV-Headers | ≈29k |
  | marl | ≈11k |
  | astc-encoder | ≈4k |

- Submodules (not populated in my clone): cppdap, googletest, json, libbacktrace/src, PowerVR_Examples, benchmark, glslang, git-hooks, llvm-project. — [.gitmodules](https://github.com/google/swiftshader/blob/master/.gitmodules)
- CMake options include `SWIFTSHADER_BUILD_TESTS` (default TRUE), `SWIFTSHADER_BUILD_BENCHMARKS` (FALSE), `SWIFTSHADER_BUILD_PVR` (FALSE), and WSI toggles (XCB/Wayland TRUE; DirectFB/D2D FALSE). The build is `cd build && cmake .. && cmake --build . --parallel && ./vk-unittests`. The output is `libvk_swiftshader.{so,dll}` plus `vk_swiftshader_icd.json`, which is used via `VK_ICD_FILENAMES`. — [CMakeLists.txt](https://github.com/google/swiftshader/blob/master/CMakeLists.txt); [README.md](https://github.com/google/swiftshader/blob/master/README.md)
- The README says code must be formatted with clang-format 11.0.1 (presubmit.sh). Changes go through Gerrit at swiftshader-review.googlesource.com; GitHub is a mirror. — [README.md](https://github.com/google/swiftshader/blob/master/README.md)
- Top-level files include BUILD.gn, Android.bp, CMakeLists.txt, CMakeSettings.json, and README.chromium (added 2026-09-16 for SBOM metadata). — [repo root](https://github.com/google/swiftshader)
- Language standard: "Pin targets using LLVM10 and LLVM16 to C++20" (2026-01-07). There were C++23 build fixes in Sept–Oct 2025. — [commit log](https://github.com/google/swiftshader/commits/master)

### Inferences
- A C++ toolchain plus CMake builds it standalone with no network access beyond submodules. The LLVM 10 default keeps build time dominated by LLVM; a full build compiles a few million lines. For an eval harness, prebuilding the oracle once and caching it is advisable.

### Gaps
- There is no official per-component LOC figure; the numbers above are my own raw counts. I did not count the Go code outside `tests/regres` or the Python/shell tooling.

## 4. Project activity 2024–2026, maintainers, deprecations, users

### Takeaway
The project is in **low-volume maintenance mode with occasional feature work**. There were about 232 commits from 2024-01 to 2026-09, roughly 1–15 per month. About 35% are "SwiftShader Regression Bot" test-list updates. The main human maintainer is Shahbaz Youssefi (Google/ANGLE), with owners syoussefi, geofflang and ynovikov. Chrome is deprecating automatic WebGL fallback to SwiftShader for security reasons, but SwiftShader remains a Vulkan backend for ANGLE (SwANGLE), for Dawn, for headless CI, and for the Android emulator.

### Cited Findings
- Commits per month since 2024-01 (own git log count):
  - 2024: 4–13 per month.
  - 2025: 5–15 per month (peak Jun–Aug 2025).
  - 2026: Jan 10, Feb 3, Mar 1, Apr 1, May 8, Jun 6, Jul 3, Aug 5, Sep 1 (to 2026-09-16).
  - Total 2024-01..2026-09: 232 commits.
  - Source: [commit history](https://github.com/google/swiftshader/commits/master)
- Top authors since 2024: SwiftShader Regression Bot 82, Shahbaz Youssefi 34, David Neto 11, Jason Macnak 9, Levi Zim 7, Romaric Jodin 6, Devon Loehr 6, Yuly Novikov 5, Wang Qing 5. "Regres: Update test lists" commits since 2024: 82. The last one was 2026-01-26. — [commit history](https://github.com/google/swiftshader/commits/master)
- OWNERS: syoussefi@, geofflang@, ynovikov@ (active). Last-resort owners: sugoi@, chrisforbes@, cwallez@, amaiorano@, natsu@, schuffelen@. — [OWNERS](https://github.com/google/swiftshader/blob/master/OWNERS)
- Notable changes 2025–2026:
  - Implement VK_KHR_surface/swapchain_maintenance1 (2025-08-11).
  - Reland "Make Reactor buildable with LLVM 18" (2025-09-08).
  - Fix Vulkan semaphore data race (2025-10-31).
  - Support cmake build for loongarch64 (2026-01-07).
  - Update primitive batch size calc for line/point polygon modes (2026-04-29).
  - "Implement full ARM/AArch64 multiarch compatibility for SwiftShader Reactor" (2026-05-04).
  - Fix D/S resolve dst range (2026-05-26).
  - Fix Windows/macOS/arm64 builds with LLVM 16 and "Default to use llvm16" (2026-05-27). That was **reverted 2026-06-08** because it "Breaks the Swiftshader -> Dawn roll" (undefined symbol `llvm::MCSymbolizer::~MCSymbolizer`).
  - Vulkan headers → 1.4.355 (2026-06-30).
  - Wayland WSI fixes (2026-06/07).
  - Implement VK_KHR_internally_synchronized_queues (2026-07-06).
  - "Do not use deprecated LLVM Typed pointer functions" (landed, reverted, relanded 2026-07/08).
  - Source: [commit 5b0479bd (revert)](https://github.com/google/swiftshader/commit/5b0479bd); [commit history](https://github.com/google/swiftshader/commits/master)
- Subzero has **not** been removed. It is still the GN default on x86 (see §2). I found no deprecation commit.
- The LLVM 10 → 16 migration is in progress but stalled: the CMake default is still 10.0 as of HEAD. — [CMakeLists.txt](https://github.com/google/swiftshader/blob/master/CMakeLists.txt)
- Chrome's automatic SwiftShader WebGL fallback is deprecated, for two reasons:
  - "SwiftShader is a high security risk due to JIT-ed code running in Chromium's GPU process".
  - A poor user experience.
  - Opt-in is `--enable-unsafe-swiftshader`.
  - Switches: `--use-gl=angle --use-angle=swiftshader` (SwANGLE as the GLES driver) and `--use-vulkan=swiftshader`.
  - Source: [Chromium docs](https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/swiftshader.md)
- Timeline: the deprecation has been noted in DevTools since Chrome 130. Starting in Chrome 139, some users are opted in to the removal, ramping to 100% (per search summary of the blink-dev intent and chromestatus; I could not fetch chromestatus directly). — [blink-dev Intent to Remove: SwiftShader Fallback](https://groups.google.com/a/chromium.org/g/blink-dev/c/yhFguWS_3pM); [chromestatus 5166674414927872](https://chromestatus.com/feature/5166674414927872); [crbug 40277080](https://issues.chromium.org/issues/40277080)
- Other users:
  - The Android Emulator offers SwiftShader software rendering for GLES and Vulkan. — [Android Developers: emulator acceleration](https://developer.android.com/studio/run/emulator-acceleration)
  - The Dawn roll is a named downstream consumer in the revert message above; Dawn tests its Vulkan backend on SwiftShader.
  - Android.bp exists for AOSP builds.
  - Sources: [commit 5b0479bd](https://github.com/google/swiftshader/commit/5b0479bd); [Android.bp](https://github.com/google/swiftshader/blob/master/Android.bp)

### Inferences
- The codebase is stable and slow-moving, which suits an eval oracle: the target will not drift much. Pin a commit anyway.
- The regression bot's test-list updates stopped after 2026-01-26. Either Regres CI stopped posting test-list updates or it paused. Treat the checked-in test lists (dated 2026-01-26) as the latest published pass-count snapshot.

### Gaps
- I did not verify that Flutter's CI uses SwiftShader. Search results only surfaced unrelated Flutter/WebGPU packages.
- I found no explicit "maintenance mode" announcement; that label is my inference from activity.
- The swiftshader Google Group was not reviewed in depth.

## 5. How SwiftShader is tested

### Takeaway
There are gtest unit suites for Reactor, Vulkan, System and Math, plus benchmarks. The main correctness signal is the Khronos VK-GL-CTS (dEQP), run by Regres, a Go tool. Regres runs presubmit on Gerrit (posting result diffs as review comments) and nightly on master (with coverage). The checked-in test list snapshot (2026-01-26) records **416,472 PASS**, 28 FAIL, 3 CRASH, 1,940 ASSERT, 5 UNIMPLEMENTED, 21 UNSUPPORTED and 2,486,911 NOT_SUPPORTED. The dEQP commit is pinned.

### Cited Findings
- `tests/` contains ReactorUnitTests, VulkanUnitTests, SystemUnitTests, MathUnitTests, VulkanWrapper (a helper lib), Reactor/Pipeline/System/Vulkan benchmarks, regres, kokoro (CI configs), presubmit.sh and check_build_files. — [tests/](https://github.com/google/swiftshader/tree/master/tests)
- Approximate gtest `TEST*` macro counts, from a grep of `^TEST|^TEST_F|^TEST_P|^TYPED_TEST`:
  - ReactorUnitTests.cpp 106 (+5 in ReactorSIMD.cpp)
  - VulkanUnitTests: ComputeTests 17, BasicTests 3, DrawTests 1
  - SystemUnitTests about 25
  - MathUnitTests 11
  - Some tests may use custom macros, so these are lower bounds.
  - Source: [tests/](https://github.com/google/swiftshader/tree/master/tests)
- Regres: "a collection of tools to perform dEQP presubmit and continuous integration testing and code coverage evaluation". It does:
  - Presubmit per Gerrit patchset: it builds and tests the change against its parent and posts differences as a review comment. It only tests changes authored or reviewed by a Googler.
  - A nightly run on master with coverage at swiftshader-regres.github.io/swiftshader-coverage.
  - A local dEQP runner with wildcard/regex matching.
  - Source: [docs/Regres.md](https://github.com/google/swiftshader/blob/master/docs/Regres.md)
- Pinned CTS: `tests/regres/deqp.json` gives remote KhronosGroup/VK-GL-CTS, sha `f55c0a8afef05cf5a9dcadc366fbe8b51d84e81c`, with patch `deqp-x11.patch`. — [tests/regres/deqp.json](https://github.com/google/swiftshader/blob/master/tests/regres/deqp.json)
- Test lists in `tests/regres/testlists/`: vk-master.txt and vk-wsi.txt, plus per-status files. The vk-master status file line counts at HEAD (last updated "Regres: Update test lists @ b0c7e1fb", 2026-01-26) are PASS 416,472; NOT_SUPPORTED 2,486,911; ASSERT 1,940; FAIL 28; UNSUPPORTED 21; UNIMPLEMENTED 5; CRASH 3; TIMEOUT/ABORT/INTERNAL_ERROR/UNREACHABLE 0. — [tests/regres/testlists](https://github.com/google/swiftshader/tree/master/tests/regres/testlists)
- `ci-tests.json` and `full-tests.json` define the CI and full runs. On 2025-11-07, "Regres: Reduce number of parallel test processes to half system's CPUs". — [tests/regres](https://github.com/google/swiftshader/tree/master/tests/regres); [commit history](https://github.com/google/swiftshader/commits/master)
- `docs/dEQP.md` documents building and running dEQP against SwiftShader locally. — [docs/dEQP.md](https://github.com/google/swiftshader/blob/master/docs/dEQP.md)

### Inferences
- The "ASSERT" bucket (1,940) is tests that hit SwiftShader debug asserts, most likely in debug builds. It should not be counted as passes for an oracle.
- NOT_SUPPORTED dominates because of missing geometry/tessellation/fp64/sparse and many extensions.
- For an eval, the PASS list gives a ready-made, per-test-case, fine-grained scoring set. Example: "reimplement X; run the dEQP subset under group Y that SwiftShader passes".

### Gaps
- There are no published pass counts outside the checked-in test lists. I did not fetch a live Regres Gerrit comment.

## 6. Determinism (suitability as an eval oracle)

### Takeaway
There is **no documented determinism guarantee**. Code evidence suggests outputs are deterministic for a fixed build, CPU feature set, backend and thread count, as long as the workload is order-independent. Known variance sources:
- CPUID-dependent code paths (SSE4.1 and similar)
- LLVM vs Subzero backends and LLVM version (10 vs 16)
- x86 vs ARM lowering
- "relaxedPrecision" math approximations
- Multithreaded ordering, which matters for atomics, occlusion sums and anything order-dependent

### Cited Findings
- `SpirvShaderDebug.hpp` has a comment on per-instruction tracing: "Very handy for performing text diffs when the thread count is reduced to 1 and execution is deterministic". This implies multi-threaded execution is not assumed to be deterministic in trace order. — [src/Pipeline/SpirvShaderDebug.hpp](https://github.com/google/swiftshader/blob/master/src/Pipeline/SpirvShaderDebug.hpp)
- `Optimizer.cpp` has a comment about avoiding "undeterministic unordered_map behavior", which shows the developers care about deterministic codegen. — [src/Reactor/Optimizer.cpp](https://github.com/google/swiftshader/blob/master/src/Reactor/Optimizer.cpp)
- CPU-feature-dependent codegen: `LLVMReactor.cpp` has 17 references to `CPUID::`/`supportsSSE4_1` and `SubzeroReactor.cpp` has 31. — [src/Reactor](https://github.com/google/swiftshader/tree/master/src/Reactor)
- Precision knobs:
  - `Rcp(x, relaxedPrecision, exactAtPow2)`, `RcpSqrt(x, relaxedPrecision)` in Reactor.hpp.
  - `Sin/Exp2(..., bool relaxedPrecision)` in ShaderCore.hpp.
  - A `RelaxedPrecision` decoration flag in SpirvShader.hpp.
  - Transcendentals are custom polynomial approximations (see the Exp-Log and Sin-Cos optimization PDFs in docs/).
  - Sources: [Reactor.hpp](https://github.com/google/swiftshader/blob/master/src/Reactor/Reactor.hpp); [ShaderCore.hpp](https://github.com/google/swiftshader/blob/master/src/Pipeline/ShaderCore.hpp); [docs/](https://github.com/google/swiftshader/tree/master/docs)
- ThreadCount and AffinityMask can be pinned via SwiftShader.ini, which enables single-threaded runs. — [docs/RuntimeConfiguration.md](https://github.com/google/swiftshader/blob/master/docs/RuntimeConfiguration.md)
- Rasterization work is split into 16 clusters with per-cluster occlusion counters, synchronized via marl tickets for ordered access. — [src/Device/Renderer.hpp](https://github.com/google/swiftshader/blob/master/src/Device/Renderer.hpp)
- Third-party claims on cross-architecture determinism are only issue-tracker discussions (e.g. "does SwiftShader give the same pixels on Linux x86-64?"). They are not authoritative and are not used as evidence here. — [github issue (low-quality source)](https://github.com/thorwhalen/an/issues/31)

### Inferences
- For an oracle:
  - Pin the commit, backend (e.g. CMake LLVM 10 or Subzero), host ISA (x86-64 only) and ThreadCount.
  - Prefer exactly specified operations: format conversion, blits with nearest filtering, integer ops, rasterization coverage, depth/stencil. Avoid transcendental-heavy shaders.
  - Use ULP/threshold comparisons where the Vulkan spec permits precision slack, as dEQP itself does.
- Fixed-function pixel outputs (rasterizer coverage, blending with 8-bit UNORM) should be bit-stable across runs on the same machine. Validate empirically with repeated runs before relying on this.

### Gaps
- There is no official statement or measurement of run-to-run or cross-CPU determinism. An empirical check (N runs × thread counts × x86/ARM) is recommended.

## 7. License and redistribution

### Takeaway
SwiftShader is Apache-2.0 ("not an official Google product"). Vendored dependencies carry their own licenses: LLVM 10 is Apache-2.0 with LLVM exceptions (older LLVM parts under the UIUC license), and SPIRV-Tools, marl and astc-encoder are Apache-2.0. Redistributing the source in an eval is permitted with NOTICE and license retention. The main practical risk is **training contamination**: the repo is public and widely mirrored and forked on GitHub, so models have likely seen it.

### Cited Findings
- `LICENSE.txt` is the Apache License, Version 2.0; the README carries an Apache-2.0 badge. — [LICENSE.txt](https://github.com/google/swiftshader/blob/master/LICENSE.txt)
- "This is not an official Google product." — [README.md](https://github.com/google/swiftshader/blob/master/README.md)
- README.chromium and license metadata were added on 2026-09-16 (sbom-quality). — [commit history](https://github.com/google/swiftshader/commits/master)
- Many public forks exist (e.g. LOLHenry/swiftshader, Pandinosaurus/swiftshader), and a SourceForge mirror. — [LOLHenry fork](https://github.com/LOLHenry/swiftshader); [SourceForge mirror](https://sourceforge.net/projects/swiftshader.mirror/)

### Inferences
- The license is not a blocker. Contamination is a design concern: hidden splits should rely on behavior (CTS subsets, new format/state combinations) rather than on reproducing the source verbatim.

### Gaps
- I did not audit each third_party LICENSE file individually.

## 8. Natural self-contained reimplementation targets and difficulty

### Takeaway
The best-bounded targets, in rough order of increasing difficulty:
1. Texture decoders (BC / ETC2 / ASTC)
2. Format conversion and the blitter
3. System utilities
4. The Reactor DSL on a toy backend
5. SamplerCore semantics
6. Clipper and rasterizer setup
7. SpirvShader subsets (compute-only)
8. The Vulkan object layer

All except the Vulkan object layer can be tested against SwiftShader outputs or dEQP subsets.

### Cited Findings (component facts supporting the ranking)
- Decoders: `src/Device/{BC_Decoder, ETC_Decoder, ASTC_Decoder}.cpp`. ASTC also uses `third_party/astc-encoder` (≈4k lines). These are pure functions from block to texels, with no JIT. — [src/Device](https://github.com/google/swiftshader/tree/master/src/Device)
- Blitter: `src/Device/Blitter.cpp`. It performs vkCmdBlitImage/copy/resolve/clear, using Reactor-generated routines for format conversion. — [src/Device](https://github.com/google/swiftshader/tree/master/src/Device)
- Formats: `src/Vulkan/VkFormat.cpp` holds format properties, component sizes and compatibility. — [src/Vulkan](https://github.com/google/swiftshader/tree/master/src/Vulkan)
- Reactor: about 27k lines including both backends. ReactorUnitTests (≈4.6k lines, 100+ tests) is an existing oracle-style test suite for a reimplementation. — [src/Reactor](https://github.com/google/swiftshader/tree/master/src/Reactor); [tests/ReactorUnitTests](https://github.com/google/swiftshader/tree/master/tests/ReactorUnitTests)
- SpirvShader: split across roughly 11 files in `src/Pipeline`, about 24.6k lines for the whole Pipeline dir. It covers arithmetic, GLSL.std.450, control flow, memory, image/sampling, group ops and spec constants. — [src/Pipeline](https://github.com/google/swiftshader/tree/master/src/Pipeline)
- System: LRUCache, Synchronization and the Configurator each have unit tests in SystemUnitTests. — [tests/SystemUnitTests](https://github.com/google/swiftshader/tree/master/tests/SystemUnitTests)

### Inferences (difficulty estimates are my judgment)

| Target | Difficulty | Approx. size | Oracle |
|---|---|---|---|
| BC1–7 / ETC2 / EAC decoders | Easy–medium | ~1–3k lines | Exact bit match vs SwiftShader decode, or dEQP `texture.compressed.*` |
| ASTC LDR decoder | Medium–hard | ~2k+ lines | Bitwise |
| Format pack/unpack and conversion (VkFormat + Blitter scalar semantics) | Medium | Scalar C++ version | Exact for integer/UNORM; rounding rules matter |
| System utils (LRU cache, config parser, synchronization) | Easy | Small | Existing unit tests |
| Reactor DSL front-end with an interpreter or simple x86 backend | Hard | Large | ReactorUnitTests, but they assume JIT function-pointer semantics |
| SamplerCore semantics (filtering, addressing, mip selection, cube, border colors) as scalar reference | Hard | Large | dEQP `texture.*` / `pipeline.sampler.*`, which allow precision tolerances |
| Clipper, triangle setup and edge functions/coverage (incl. multisample positions, top-left rule, line/point rasterization) | Medium–hard | Moderate | Mostly exact integer/fixed-point |
| SPIR-V compute subset (interpreter or Reactor-based) | Hard | Large | dEQP `compute.*` / `spirv_assembly.*` subsets, or VulkanUnitTests ComputeTests |
| Full Vulkan object layer (31k lines) | Very hard | Very large | Needs everything else to be meaningful |

### Gaps
- There is no public per-dEQP-group pass breakdown. Computing one would take grouping the vk-master-PASS.txt names by prefix, which is straightforward to do from the checked-in list.
