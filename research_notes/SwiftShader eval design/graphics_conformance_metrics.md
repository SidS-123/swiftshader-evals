# Graphics conformance and performance metrics for grading a SwiftShader reimplementation (as of 2026-09-26)

Scope note: I fetched primary sources (the Khronos VK-GL-CTS source and README, SwiftShader README and Regres docs, Mesa CI docs, Skia Gold Go docs, the Amber docs, NVIDIA FLIP, GraphicsFuzz, and the OOPSLA'17 and PLDI'21 papers). Some pages could not be fetched: the deqp-runner README (the freedesktop GitLab Anubis wall returned "Access Denied"), Chromium's gpu_pixel_testing_with_gold.md (HTTP 503), the XDC 2025 CTS slides (the PDF text could not be extracted), and the Vulkan spec limits table (the page was too large or the table was not found). Items marked **[background, unverified this session]** come from prior knowledge and must be checked before anyone quotes them.

## 1. Khronos Vulkan CTS (VK-GL-CTS / dEQP): structure, counts, result codes, conformance judging

### Takeaway
You must run the CTS against the checked-in mustpass list `external/vulkancts/mustpass/main/vk-default.txt`. A conformant run may contain only these statuses: Pass, NotSupported, QualityWarning, CompatibilityWarning and Waiver. Any Fail, Crash, Timeout or InternalError disqualifies the run. The suite has millions of cases, so the pass/fail unit is the individual dEQP case, and the list is sharded into "fractions".

### Cited Findings
- The mustpass lists live at `external/vulkancts/mustpass/main/vk-default.txt` (Vulkan) and `external/vulkancts/mustpass/main/vksc-default.txt` (Vulkan SC). — [VK-GL-CTS external/vulkancts/README.md](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/external/vulkancts/README.md)
- Mandatory command-line options for a conformance run: `--deqp-caselist-file=<path>/vk-default.txt --deqp-log-images=disable --deqp-log-shader-sources=disable`. — [VK-GL-CTS README](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/external/vulkancts/README.md)
- The only status codes permitted in a conformant submission are "Pass, NotSupported, QualityWarning, CompatibilityWarning, Waiver". — [VK-GL-CTS README](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/external/vulkancts/README.md)
- A submission must include the test logs (`TestResults.qpa`) from all driver builds and fractions, the `git status` and `git log` output, any applied patches, and a `STATEMENT-<adopter>` file containing `CONFORM_VERSION: <git tag>`, `PRODUCT:`, `CPU:` and `OS:`. The archive is named `VK<major><minor>_<adopter><_info>.tgz`. — [VK-GL-CTS README](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/external/vulkancts/README.md)
- Useful runner options: `--deqp-fraction=I,N` splits the run into N parallel fractions, plus `--deqp-vk-device-id`, `--deqp-log-filename`, `--deqp-archive-dir` and `--deqp-shadercache`. Vulkan SC (`deqp-vksc`) runs each test twice, once for pipeline collection and once in a subprocess. — [VK-GL-CTS README](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/external/vulkancts/README.md)
- Size: the XDC 2025 talk "Vulkan CTS Tips & Tricks" (Ricardo García, Igalia, 2025-09-29) is summarised in search snippets as describing "approximately 2.8 million Vulkan tests". I could not extract the PDF text, so treat this figure as secondary. — [XDC 2025 slides](https://www.igalia.com/downloads/slides/RicardoGarcia-VulkanCTSTipsTricks.pdf)
- Release tags for CTS versions (vulkan-cts-1.3.x.y and 1.4.x.y) are listed on the GitHub releases page. — [VK-GL-CTS releases](https://github.com/KhronosGroup/VK-GL-CTS/releases)

### Inferences
- Top-level dEQP-VK groups **[background, unverified this session]**: api, memory, pipeline, binding_model, spirv_assembly, glsl, renderpass, renderpass2, dynamic_rendering, ubo, ssbo, query_pool, draw, compute, image, texture, wsi, synchronization, synchronization2, sparse_resources, tessellation, geometry, rasterization, fragment_operations, clipping, multiview, subgroups, ycbcr, protected_memory, device_group, memory_model, transform_feedback, conditional_rendering, fragment_shading_rate, robustness, descriptor_indexing, ray_tracing_pipeline, ray_query, mesh_shader, video, shader_object, and others. Most tests are Amber-based and live under `external/vulkancts/data/vulkan/amber/`.
- For an eval, a practical headline metric is pass rate over the (feature-filtered) mustpass: Pass / (Pass + Fail + Crash + Timeout + InternalError). Report NotSupported separately, because a reimplementation could otherwise "game" the score by advertising fewer features. Anchor the denominator to the features the reference SwiftShader exposes. For example, score only cases where reference SwiftShader returns Pass.
- QualityWarning and CompatibilityWarning are acceptable for conformance, but they show a result that deviates from the ideal. An eval could weight them at 0.5, or report them as their own bucket.

### Gaps
- I could not verify an exact case count for the 2024–2026 `vk-default.txt` from a primary source. It is best computed directly with `wc -l` on the mustpass files of the pinned CTS tag.
- I did not fetch the Khronos conformance-process documents (Adopters process, review period, waivers) in this session.

## 2. How dEQP/CTS compares rendered images and numeric results

### Takeaway
dEQP never compares rendered images exactly. Each test picks a comparator from `tcuImageCompare`: a per-channel absolute threshold (int, float or ULP), a spatial-deviation tolerant compare, a bilinear-neighbourhood compare, or a blurred "fuzzy" compare with an error score (recommended threshold 0.02–0.05). It checks against a reference produced by a CPU reference rasterizer or texture sampler. The allowed error comes from the Vulkan spec's precision rules.

### Cited Findings
- Comparators declared in `framework/common/tcuImageCompare.hpp`: `pixelThresholdCompare(…, const RGBA &threshold, …)`, `fuzzyCompare(…, float threshold, …)`, `fuzzyCompareMaxError`, `bitwiseCompare`, `floatUlpThresholdCompare(…, const UVec4 &threshold, …)`, `floatThresholdCompare(…, const Vec4 &threshold, …)` (with overloads for an ignore key and a constant reference), `intThresholdCompare(…, const UVec4 &threshold, …, bool use64Bits=false)`, `intThresholdPositionDeviationCompare(…, threshold, IVec3 maxPositionDeviation, bool acceptOutOfBoundsAsAnyValue, …)`, `intThresholdPositionDeviationErrorThresholdCompare(…, int maxAllowedFailingPixels, …)`, `dsThresholdCompare(…, float threshold, …)` for depth/stencil, `measurePixelDiffAccuracy(…, bestScoreDiff, worstScoreDiff, …)` and `bilinearCompare(…, const RGBA threshold, …)`. — [tcuImageCompare.hpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.hpp)
- **intThresholdCompare**: for each pixel, `diff = abs(refPix - cmpPix)`, and `maxDiff = max(maxDiff, diff)`. The test passes if `boolAll(lessThanEqual(maxDiff, threshold))`. — [tcuImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.cpp)
- **floatThresholdCompare**: `Vec4 diff = abs(refPix - cmpPix); isOk = boolAll(lessThanEqual(diff, threshold))`. An overload skips pixels that equal an `ignorekey`. — [tcuImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.cpp)
- **floatUlpThresholdCompare**: `UVec4 diff = computeFlushRelaxedULPDiff(refPix, cmpPix)`, with denormals flushed to zero. The test passes if every channel satisfies `diff <= threshold`. — [tcuImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.cpp)
- **intThresholdPositionDeviationCompare**: for each reference pixel, it searches a box of size `maxPositionDeviation` in the result for any pixel within `threshold`. `acceptOutOfBoundsAsAnyValue` lets pixels at the border pass automatically. The test fails if any pixel has no match. — [tcuImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.cpp)
- **fuzzyCompare**: the source describes it as doing "light blurring on both images and then does per-pixel analysis. Pixels are compared to 3x3 bilinear surface defined by adjecent pixels". The code comment says "good threshold values are in range 0.02 to 0.05", and the test passes if `difference <= threshold`. — [tcuImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.cpp)
- fuzzyCompare internals (from `tcuFuzzyImageCompare.cpp`):
  - The blur is a separable kernel `{0.1, 0.8, 0.1}`, applied horizontally and then vertically.
  - The per-channel distance is `max(|a−b| − MIN_ERR_THRESHOLD, 0)` with `MIN_ERR_THRESHOLD = 4` (in 8-bit units). The four channel terms are squared and summed.
  - Border pixels are excluded, and sampling can randomly skip pixels (`maxSampleSkip`).
  - In average mode, the accumulated squared distances are scaled by pixel count and normalised by `(255 − MIN_ERR_THRESHOLD)^4`.
  - In max-error mode the result is `sqrt(distMax2)/255`.
  - Source: [tcuFuzzyImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuFuzzyImageCompare.cpp)
- **bilinearCompare**: compares pixels "to 3x3 bilinear surface defined by adjecent pixels" to "compensate for both 1-pixel deviations in geometry and aliasing in texture data", using an RGBA threshold. — [tcuImageCompare.cpp](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/framework/common/tcuImageCompare.cpp)
- Vulkan limit definitions:
  - `subPixelPrecisionBits` is "the number of bits of subpixel precision in framebuffer coordinates xf and yf".
  - `subTexelPrecisionBits` is "the number of bits of precision in the division along an axis of an image used for minification and magnification filters. 2^subTexelPrecisionBits is the actual number of divisions along each axis".
  - Source: [Vulkan spec, Limits chapter](https://docs.vulkan.org/spec/latest/chapters/limits.html)
- SPIR-V float precision rules in the Vulkan spec appendix "Precision and Operation of SPIR-V Instructions": OpFAdd, OpFSub and OpFMul are correctly rounded; OpFDiv is 2.5 ULP (for |y| in range); exp/exp2 are 3 + 2·|x| ULP; log/log2 are 3 ULP outside [0.5, 2] with absolute error < 2^-21 inside; inversesqrt is 2 ULP; sqrt inherits from 1.0/inversesqrt; sin/cos have absolute error ≤ 2^-11 in [−π, π]; RelaxedPrecision allows mediump; the DenormPreserve and DenormFlushToZero controls apply. **Caveat:** the fetch summariser may have echoed my prompt, but these values match my background knowledge of the spec. Quote them from the page itself before relying on them. — [Vulkan spec appendix spirvenv](https://docs.vulkan.org/spec/latest/appendices/spirvenv.html)

### Inferences
- **[background, unverified this session]** Other verifiers:
  - `tcu::TexLookupVerifier` (tcuTexLookupVerifier.cpp) checks that a sampled result lies inside the set of values reachable under the allowed coordinate precision (`LookupPrecision`: coordBits, uvwBits, colorThreshold) and the LOD precision (`LodPrecision`: derivateBits, lodBits). Test code derives these precisions from `subTexelPrecisionBits` and `mipmapPrecisionBits`.
  - Rasterization tests use `tcuRasterizationVerifier`, which rasterizes triangles and lines conservatively with the allowed subpixel precision and accepts pixels in an "ambiguous" band.
  - GLES dEQP uses the reference rasterizer `rr` (framework/referencerenderer) and `sglr` (a GL-like wrapper over rr).
  - Shader-precision tests (dEQP-VK.glsl.builtin.precision.*) use interval arithmetic (`tcuInterval`, `tcuFloatFormat`) to compute the set of acceptable results.
  - Spec minimums: subPixelPrecisionBits ≥ 4, subTexelPrecisionBits ≥ 4, mipmapPrecisionBits ≥ 4 and viewportSubPixelBits ≥ 0.
- For an eval, reuse these comparators rather than PSNR. Specifically: exact or ULP comparison for buffers and compute; `intThresholdCompare`/`floatThresholdCompare` with a threshold derived from the format (about 1 LSB for UNORM8); fuzzyCompare at 0.02–0.05 for full scenes; and a position-deviation compare for rasterization edge cases.

### Gaps
- I did not fetch the exact TexLookupVerifier or RasterizationVerifier code, or typical per-test threshold constants (for example the RGBA(3,3,3,3) style thresholds in draw tests).

## 3. SwiftShader's own testing and conformance

### Takeaway
SwiftShader states that it implements Vulkan 1.3 and is conformant (Khronos submission #717). Through ANGLE ("SwANGLE") it is conformant for OpenGL ES 3.1 (submission #906). Its regression gate is the Regres tool, which diffs dEQP results between a change and its parent. The gate runs against known-passing test lists that a daily full run refreshes.

### Cited Findings
- "SwiftShader is a high-performance CPU-based implementation of the Vulkan 1.3 graphics API", with conformance linked to Khronos submission #717. — [SwiftShader README](https://swiftshader.googlesource.com/SwiftShader/+/HEAD/README.md)
- "The ANGLE project can be used to achieve a layered implementation of OpenGL ES 3.1 (aka. 'SwANGLE')", with GLES 3.1 conformance at Khronos submission #906. — [SwiftShader README](https://swiftshader.googlesource.com/SwiftShader/+/HEAD/README.md)
- Testing named in the README: dEQP, Google Test unit tests, Chromium integration, and clang-format presubmit checks. — [SwiftShader README](https://swiftshader.googlesource.com/SwiftShader/+/HEAD/README.md)
- Regres presubmit runs the `ci-tests.json` lists, which are "known-passing test lists updated by the daily run, so that failing tests for incomplete functionality are skipped, but tests that pass for new functionality *are tested*". The daily run uses `full-tests.json`. — [SwiftShader docs/Regres.md](https://github.com/google/swiftshader/blob/master/docs/Regres.md)
- Regres runs both the change and its parent, and "the results of the two changes are diffed", with the diff posted as a Gerrit review comment. The daily run posts updated test lists as a CL, and "each test is binned by status and written to the testlists directory". It also produces a coverage dashboard. — [Regres.md](https://github.com/google/swiftshader/blob/master/docs/Regres.md)
- Robustness: "crashing processes will not take down the test runner", "each process is restricted to a fraction of the system's memory", and "each test process has a time limit before they are automatically killed". — [Regres.md](https://github.com/google/swiftshader/blob/master/docs/Regres.md)
- Amber lists SwiftShader as an optional backend. — [google/amber](https://github.com/google/amber)

### Inferences
- The Regres design maps directly onto an eval harness. Take a baseline equal to the tests that reference SwiftShader passes at a pinned CTS tag, then score the candidate by diffing per-test status against that baseline (regressions vs. matches). Isolate each test process with memory and time limits so Crash and Timeout are counted, not fatal.

### Gaps
- I found no published SwiftShader pass-rate or count numbers (for example "N of M dEQP-VK passing") in the pages I fetched. The per-status test lists in the repo (`tests/regres/testlists`) would have to be counted directly.
- I did not verify Chromium's specific SwiftShader bots or WebGL-CTS-on-SwiftShader usage because the Chromium Gold doc returned 503.

## 4. Mesa CI (deqp-runner, piglit, expectations, flakes, traces)

### Takeaway
Mesa gates drivers such as lavapipe and llvmpipe with deqp-runner, run against per-driver expectation files (`*-fails.txt`, `*-flakes.txt`, `*-skips.txt`). It tracks flakes on a dashboard and enforces tight runtime budgets. Mesa also runs trace-replay tests in which a replayed frame's checksum must match the expected value in a YAML file.

### Cited Findings
- Driver maintainers should "watch the Flakes panel of the CI flakes dashboard" (jobs where an "automatic retry of a failing job produced a success a second time"). They should "track the NEW reports in jobs and add them as appropriate to the `-flakes.txt` file for your driver". — [Mesa CI docs](https://docs.mesa3d.org/ci/index.html)
- Budgets: "the test farm needs to be able to handle a whole pipeline's worth of jobs in less than 15 minutes", with test runtimes "kept to 10 minutes" as reported by deqp-runner. Farms should "produce a spurious failure no more than once a week". `DEQP_FRACTION` is used to run subsets, and piglit logs runtimes in `results.json.bz2`. — [Mesa CI docs](https://docs.mesa3d.org/ci/index.html)
- Trace testing: traces are captured with apitrace, RenderDoc or gfxreconstruct and stored in traces-db. Per-driver YAML files list the traces and their expected checksums. Comparison runs through `piglit/replayer/replayer.py compare trace -d test <trace> <expected checksum>`, and CI can be simulated with `piglit run -l verbose --timeout 300 -j10 replay …`. — [Mesa local traces doc](https://docs.mesa3d.org/ci/local-traces.html)

### Inferences
- **[background, unverified this session]** deqp-runner (Rust, gitlab.freedesktop.org/mesa/deqp-runner):
  - It supports deqp, piglit, gtest and skqp suites and groups tests into batches per process.
  - With `--baseline <fails.txt>`, `--skips` and `--flakes`, it classifies results as Pass, Fail, ExpectedFail, UnexpectedPass, Crash, Timeout, Skip, Flake, KnownFlake and Missing.
  - It re-runs failures to detect flakes, and it fails the job on any unexpected result, including an UnexpectedPass (which forces the baseline to be updated).
  - Mesa trace checksums are an md5 of the rendered frame image, so any 1-bit change "fails" and a maintainer updates the expectation.
- For an eval, this is the best-established pattern for deterministic grading with a flakiness allowance: fixed expectation files, a retry to classify flakes, and strict handling of unexpected passes and fails.

### Gaps
- The deqp-runner README was blocked by the Anubis bot wall, so the status names and flags above are unverified. I did not obtain current lavapipe fails-list sizes or pass rates.

## 5. Image comparison metrics outside CTS (Skia Gold, FLIP, Amber, others)

### Takeaway
Industry pixel testing uses either exact hashes (Mesa traces) or parameterised fuzzy matching: Skia Gold's fuzzy, Sobel and sample_area algorithms, and Amber's tolerance, RMSE and histogram-EMD expectations. Perceptual metrics such as NVIDIA FLIP produce per-pixel error maps in [0,1]. PSNR and SSIM are generic fallbacks.

### Cited Findings
- Skia Gold image-matching algorithms: `exact`, `fuzzy`, `sobel`, `sample_area` and `positive_if_only_image`. Parameter keys: `fuzzy_max_different_pixels`, `fuzzy_pixel_delta_threshold`, `fuzzy_pixel_per_channel_delta_threshold`, `fuzzy_ignored_border_thickness`, `sobel_edge_threshold`, `sample_area_width`, `sample_area_max_different_pixels_per_area` and `sample_area_channel_delta_threshold`. — [go.skia.org imgmatching](https://pkg.go.dev/go.skia.org/infra/gold-client/go/imgmatching)
- Fuzzy semantics: if PixelDeltaThreshold > 0, no pixel may have a sum of RGBA differences above the threshold. Otherwise, the per-channel max delta must not exceed PixelPerChannelDeltaThreshold. At most MaxDifferentPixels pixels may differ, and MaxDifferentPixels = 0 means an exact comparison. The Sobel variant masks edges above EdgeThreshold before fuzzy matching. — [imgmatching/fuzzy](https://pkg.go.dev/go.skia.org/infra/gold-client/go/imgmatching/fuzzy); [Skia issue 9527](https://groups.google.com/a/skia.org/g/bugs/c/uLPDZS_hKYQ/m/X6_kveGcBgAJ)
- Chromium pixel tests upload screenshots to Gold, and failures show a "gold_triage_link" to the closest approved image. Images are triaged as approved or rejected, and a new test can land with a temporary `Failure` expectation. — [Chromium gpu_testing.md](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/gpu/gpu_testing.md)
- NVIDIA FLIP:
  - It provides LDR-FLIP and HDR-FLIP, producing a per-pixel error map in [0,1] and a mean FLIP score.
  - Default viewing assumptions are 67 pixels per degree (0.7 m distance, 0.7 m monitor width, 3840 px).
  - It installs with `pip install flip-evaluator` and runs as `flip -r ref.png -t test.png`. The latest version is 1.7.
  - Source: [NVlabs/flip](https://github.com/NVlabs/flip); papers: [LDR-FLIP 2020](https://research.nvidia.com/publication/2020-07_FLIP), [HDR-FLIP 2021](https://research.nvidia.com/publication/2021-05_HDR-FLIP)
- Amber expectation grammar:
  - `EXPECT buf IDX x y SIZE w h EQ_RGBA r g b a`
  - `EXPECT buf IDX x TOLERANCE t{1,4} EQ v+` (the tolerance "may be given as a percentage by placing a '%' symbol after the value")
  - `EXPECT b1 EQ_BUFFER b2`
  - `EXPECT b1 RMSE_BUFFER b2 TOLERANCE v`
  - `EXPECT b1 EQ_HISTOGRAM_EMD_BUFFER b2 TOLERANCE v`
  - Source: [amber_script.md](https://github.com/google/amber/blob/main/docs/amber_script.md)

### Inferences
- For an eval, grade deterministically first (bit-exact or ULP where the spec requires exactness), then apply CTS-style thresholds. Use FLIP or PSNR only as secondary diagnostic scores for scene-level renders, because perceptual metrics are not what conformance uses.

### Gaps
- I did not find the default fuzzy parameter values Chromium uses per pixel test, because the Gold doc returned 503.

## 6. Performance benchmarking of software renderers

### Takeaway
I found no primary sources in this session that define a standard performance methodology for software Vulkan renderers. This area needs its own research pass.

### Cited Findings
- Mesa CI treats runtime as a budget, not a score: pipelines should finish in under 15 minutes, and deqp-runner-reported test runtime should be at most 10 minutes. — [Mesa CI docs](https://docs.mesa3d.org/ci/index.html)

### Inferences
- **[background, unverified this session]** Commonly used benchmarks: vkmark (Vulkan) and glmark2 (GL/GLES), which report FPS per scene and an aggregate score; Sascha Willems Vulkan samples; wall-clock time for CTS subsets; and Chromium perf (telemetry/pinpoint) for SwiftShader-backed WebGL. A sound eval measures relative time vs. reference SwiftShader on a fixed workload (for example a CTS fraction or selected Amber scripts). It should run repeated trials and report the median with a confidence interval, and pin CPU count and thread count.

### Gaps
- Primary documentation on vkmark and glmark2 scoring, and on how SwiftShader performance is tracked in Chromium, was not gathered.

## 7. Fuzzing and metamorphic testing (GraphicsFuzz, spirv-fuzz, Amber as a case format)

### Takeaway
Metamorphic testing addresses the missing oracle for under-specified rendering: semantics-preserving shader transformations must leave the image (nearly) unchanged. GraphicsFuzz (archived 2025-12-08) and spirv-fuzz found many driver bugs, and the minimized cases are output as Amber scripts. Amber, whose format the CTS already consumes, is a natural test-case format for an eval driver.

### Cited Findings
- Donaldson et al., "Automated Testing of Graphics Shader Compilers", OOPSLA 2017 (PACMPL 1, Article 93): GLFuzz uses semantics-preserving transformations of high-value shaders, and "over a set of 17 GPU and driver configurations, spanning the main 7 GPU designers", it found "more than 60 distinct bugs". These included a WebGL cross-tab information leak and a Windows 10 BSOD. — [ACM DL](https://dl.acm.org/doi/10.1145/3133917); [PDF](https://www.doc.ic.ac.uk/~afd/papers/2017/OOPSLA.pdf)
- Donaldson, Thomson, Teliman, Milizia, Perez Maselco and Karpiński, "Test-Case Reduction and Deduplication Almost for Free with Transformation-Based Compiler Testing", PLDI 2021. It describes spirv-fuzz, "the first compiler-testing tool for the SPIR-V intermediate representation", which reduces cases by delta-debugging the transformation sequence. — [ACM DL](https://dl.acm.org/doi/10.1145/3453483.3454092); [PDF](https://www.doc.ic.ac.uk/~afd/papers/2021/PLDI.pdf)
- GraphicsFuzz (gfauto, glsl-fuzz, glsl-reduce, spirv-fuzz, spirv-reduce) is "a set of tools for testing shader compilers". The repository was archived on December 8, 2025. — [google/graphicsfuzz](https://github.com/google/graphicsfuzz)
- Amber supports AmberScript (preferred) and VkScript (legacy), with Vulkan, Dawn and SwiftShader backends. The CTS "incorporates Amber extensively". — [google/amber](https://github.com/google/amber)

### Inferences
- **[background, unverified this session]** GraphicsFuzz-derived regression tests live in CTS as `dEQP-VK.graphicsfuzz.*` (Amber files). For an eval, Amber scripts give self-contained, API-level cases with built-in expectations (exact, tolerance, RMSE, histogram EMD). For hidden-test generation, metamorphic variants (original vs. transformed shader should render the same) provide a reference-free oracle that is hard to overfit.

### Gaps
- I did not retrieve spirv-fuzz bug counts from the PLDI'21 paper, or the exact image comparison gfauto uses. GraphicsFuzz historically used histogram/PSNR-style fuzzy comparison, but this is **unverified**.
