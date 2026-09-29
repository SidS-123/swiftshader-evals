"""Count SwiftShader's CTS results by dEQP group, and map groups to our families.

    python3 swiftshader_vk/tools/cts_breakdown.py <swiftshader checkout> > swiftshader_vk/docs/internal/cts_breakdown.md

Reads tests/regres/testlists/vk-master-<STATUS>.txt at the pinned commit. PASS
counts say where SwiftShader's verified behaviour is richest; family weights
in Stage 7 start from them.
"""
from __future__ import annotations

import collections
import subprocess
import sys
from pathlib import Path

STATUSES = ["PASS", "FAIL", "CRASH", "ASSERT", "UNIMPLEMENTED", "UNSUPPORTED", "NOT_SUPPORTED",
            "QUALITY_WARNING", "COMPATIBILITY_WARNING", "INTERNAL_ERROR", "ABORT", "TIMEOUT"]

# dEQP-VK.<group>[.<sub>] prefix -> our family (PLAN_v1.md §11.1). First match wins.
# pipeline.monolithic.X is matched as pipeline.X; the two pipeline-library
# variants re-run the same tests through another construction path and are
# counted apart so they do not double the pipeline families.
FAMILY_MAP = [
    # instance / device / properties
    ("info", "inst_dev"), ("api.info", "inst_dev"), ("api.device_init", "inst_dev"),
    ("api.version_check", "inst_dev"), ("api.driver_properties", "inst_dev"),
    ("api.format_feature_flags2", "inst_dev"), ("api.maintenance3_check", "inst_dev"),
    ("api.granularity", "inst_dev"), ("api.tooling_info", "inst_dev"),
    # memory and buffers
    ("api.buffer", "mem_buf"), ("api.buffer_view", "mem_buf"), ("api.buffer_memory_requirements", "mem_buf"),
    ("api.fill_and_update_buffer", "mem_buf"), ("memory", "mem_buf"),
    # copies, blits, clears, format views
    ("api.copy_and_blit", "formats_copy_blit"), ("api.image_clearing", "formats_copy_blit"),
    ("image.host_image_copy", "formats_copy_blit"), ("image.mutable", "formats_copy_blit"),
    ("image.format_reinterpret", "formats_copy_blit"), ("image.texel_view_compatible", "formats_copy_blit"),
    ("image.extended_usage_bit_compatibility", "formats_copy_blit"), ("image.mismatched_formats", "formats_copy_blit"),
    ("image", "compute_image"), ("texel_buffer", "compute_image"),
    # shaders
    ("spirv_assembly", "compute_* (spirv_assembly)"), ("glsl", "compute_* (glsl)"), ("compute", "compute_*"),
    ("graphicsfuzz", "compute_cf (graphicsfuzz)"), ("ssbo", "compute_types"),
    ("subgroups", "compute_subgroup"), ("memory_model", "compute_atomics"),
    # pipeline state
    ("pipeline.sampler", "tex_sample"), ("pipeline.image_view", "tex_sample"), ("pipeline.image", "tex_sample"),
    ("texture", "tex_sample"),
    ("pipeline.stencil", "depth_stencil"), ("pipeline.depth", "depth_stencil"),
    ("pipeline.depth_range_unrestricted", "depth_stencil"), ("fragment_operations", "depth_stencil"),
    ("pipeline.vertex_input", "vertex_input"), ("pipeline.input_attribute_offset", "vertex_input"),
    ("pipeline.input_assembly", "vertex_input"), ("pipeline.bind_buffers_2", "vertex_input"),
    ("pipeline.no_position", "vertex_input"), ("draw", "vertex_input"),
    ("pipeline.blend", "blend"), ("pipeline.blend_operation_advanced", "blend"), ("pipeline.logic_op", "blend"),
    ("pipeline.multisample", "msaa"), ("pipeline.multisample_interpolation", "msaa"),
    ("pipeline.multisample_shader_builtin", "msaa"),
    ("pipeline.render_to_image", "mrt_renderpass"), ("pipeline.framebuffer_attachment", "mrt_renderpass"),
    ("pipeline.matched_attachments", "mrt_renderpass"), ("renderpasses", "mrt_renderpass"),
    ("imageless_framebuffer", "mrt_renderpass"),
    ("pipeline.spec_constant", "descriptors_push"), ("pipeline.push_constant", "descriptors_push"),
    ("pipeline.dynamic_offset", "descriptors_push"), ("pipeline.descriptor_limits", "descriptors_push"),
    ("binding_model", "descriptors_push"), ("descriptor_indexing", "descriptors_push"), ("ubo", "descriptors_push"),
    ("pipeline.extended_dynamic_state", "raster_tri"), ("dynamic_state", "raster_tri"), ("clipping", "raster_tri"),
    ("rasterization", "raster_tri + raster_lines_points"),
    ("pipeline.timestamp", "queries_sync"), ("query_pool", "queries_sync"),
    ("synchronization", "queries_sync"), ("synchronization2", "queries_sync"),
    ("pipeline", "api_misc (pipeline objects, caches)"),
    # errors and robustness
    ("robustness", "errors_robust"), ("api.null_handle", "errors_robust"),
    ("api.external", "out of v1 scope"), ("api", "api_misc (object management etc.)"),
    # out of scope (PLAN_v1.md §7.3)
    ("multiview", "out of v1 scope"), ("wsi", "out of v1 scope"), ("ycbcr", "out of v1 scope"),
    ("protected_memory", "out of v1 scope"), ("sparse_resources", "out of v1 scope"),
    ("tessellation", "out of v1 scope"), ("geometry", "out of v1 scope"), ("video", "out of v1 scope"),
    ("device_group", "out of v1 scope"), ("shader_object", "out of v1 scope"),
]
LIBRARY_VARIANTS = ("pipeline.fast_linked_library.", "pipeline.pipeline_library.")


def family(test: str) -> str:
    rest = test.removeprefix("dEQP-VK.")
    if rest.startswith(LIBRARY_VARIANTS):
        return "(pipeline-library variants: duplicates, not weighted)"
    if rest.startswith("pipeline.monolithic."):
        rest = "pipeline." + rest[len("pipeline.monolithic."):]
    for prefix, fam in FAMILY_MAP:
        if rest == prefix or rest.startswith(prefix + "."):
            return fam
    return "unmapped"


def main():
    src = Path(sys.argv[1])
    lists = src / "tests" / "regres" / "testlists"
    commit = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%h %cs %s", "--", "tests/regres/testlists"],
                            capture_output=True, text=True).stdout.strip()
    counts = collections.Counter()
    by_group = collections.defaultdict(collections.Counter)
    by_family = collections.defaultdict(collections.Counter)
    for st in STATUSES:
        f = lists / f"vk-master-{st}.txt"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            counts[st] += 1
            parts = line.split(".")
            by_group[parts[1] if len(parts) > 1 else "?"][st] += 1
            by_family[family(line)][st] += 1

    total_pass = counts["PASS"]
    print("# SwiftShader CTS results by dEQP group (internal)\n")
    print(f"Source: `tests/regres/testlists/vk-master-*.txt`, last updated by `{commit}`. "
          f"Generated by `swiftshader_vk/tools/cts_breakdown.py`.\n")
    print("## Totals\n\n| Status | Tests |\n|---|---:|")
    for st in STATUSES:
        if counts[st]:
            print(f"| {st} | {counts[st]:,} |")
    print("\n## By top-level group (sorted by PASS)\n")
    print("| Group | PASS | % of PASS | FAIL | CRASH | ASSERT | NOT_SUPPORTED |\n|---|---:|---:|---:|---:|---:|---:|")
    for g, c in sorted(by_group.items(), key=lambda kv: -kv[1]["PASS"]):
        print(f"| `{g}` | {c['PASS']:,} | {100 * c['PASS'] / total_pass:.1f} | {c['FAIL']} | {c['CRASH']} | "
              f"{c['ASSERT']} | {c['NOT_SUPPORTED']:,} |")
    print("\n## Mapped to v1 families (PASS only; first-match prefix map in the script)\n")
    print("| Family | PASS | % of PASS |\n|---|---:|---:|")
    for fam, c in sorted(by_family.items(), key=lambda kv: -kv[1]["PASS"]):
        print(f"| {fam} | {c['PASS']:,} | {100 * c['PASS'] / total_pass:.1f} |")


if __name__ == "__main__":
    main()
