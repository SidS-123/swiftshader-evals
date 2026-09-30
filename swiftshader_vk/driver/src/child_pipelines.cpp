// vkreplay child: compute and graphics pipelines (dynamic rendering, monolithic).
#include "child.hpp"

#include <cstring>

namespace {

// Specialization constants: [{"id": 0, "u32": 5}, {"id": 1, "f32": 0.5}, {"id": 2, "bool": true}]
struct Spec {
    std::vector<VkSpecializationMapEntry> entries;
    std::vector<uint8_t> data;
    VkSpecializationInfo info{};
    const VkSpecializationInfo* build(const json& list, const std::string& ctx) {
        if (list.is_null()) return nullptr;
        for (const json& e : list) {
            uint32_t id = (uint32_t)req_u64(e, "id", ctx);
            uint32_t off = (uint32_t)data.size();
            auto put = [&](const void* p, size_t n) {
                const uint8_t* b = static_cast<const uint8_t*>(p);
                data.insert(data.end(), b, b + n);
                entries.push_back({id, off, n});
            };
            if (e.contains("u32")) { uint32_t v = e["u32"].get<uint32_t>(); put(&v, 4); }
            else if (e.contains("i32")) { int32_t v = e["i32"].get<int32_t>(); put(&v, 4); }
            else if (e.contains("f32")) { float v = e["f32"].get<float>(); put(&v, 4); }
            else if (e.contains("bool")) { VkBool32 v = e["bool"].get<bool>() ? VK_TRUE : VK_FALSE; put(&v, 4); }
            else if (e.contains("u64")) { uint64_t v = e["u64"].get<uint64_t>(); put(&v, 8); }
            else if (e.contains("f64")) { double v = e["f64"].get<double>(); put(&v, 8); }
            else throw CaseError(ctx + ": a spec constant needs u32/i32/f32/bool/u64/f64");
        }
        info = {(uint32_t)entries.size(), entries.data(), data.size(), data.data()};
        return &info;
    }
};

void op_compute_pipeline(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    VkPipelineLayout layout = st.get(st.pipeline_layouts, req_str(op, "layout", ctx), "pipeline_layout");
    VkShaderModule mod = st.get(st.shaders, req_str(op, "shader", ctx), "shader");
    std::string entry = opt_str(op, "entry", "main");
    Spec spec;
    VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, mod,
                entry.c_str(), spec.build(op.value("spec", json()), ctx)};
    ci.layout = layout;
    Pipeline p;
    p.bind_point = VK_PIPELINE_BIND_POINT_COMPUTE;
    if (VKC(st, vkCreateComputePipelines, st.device, VK_NULL_HANDLE, 1, &ci, nullptr, &p.pipe) != VK_SUCCESS) return;
    st.pipelines[name] = p;
}

VkBlendFactor bf(const json& v, const char* dflt, const std::string& ctx) {
    return (VkBlendFactor)parse_enum("VkBlendFactor", "VK_BLEND_FACTOR_", v.is_null() ? json(dflt) : v, ctx);
}
VkBlendOp bop(const json& v, const std::string& ctx) {
    return (VkBlendOp)parse_enum("VkBlendOp", "VK_BLEND_OP_", v.is_null() ? json("add") : v, ctx);
}
VkCompareOp cmp(const json& v, const char* dflt, const std::string& ctx) {
    return (VkCompareOp)parse_enum("VkCompareOp", "VK_COMPARE_OP_", v.is_null() ? json(dflt) : v, ctx);
}
VkStencilOpState stencil_state(const json& s, const std::string& ctx) {
    VkStencilOpState o{};
    auto sop = [&](const char* k) {
        return (VkStencilOp)parse_enum("VkStencilOp", "VK_STENCIL_OP_", s.value(k, json("keep")), ctx);
    };
    o.failOp = sop("fail");
    o.passOp = sop("pass");
    o.depthFailOp = sop("depth_fail");
    o.compareOp = cmp(s.value("compare", json()), "always", ctx);
    o.compareMask = (uint32_t)opt_u64(s, "compare_mask", 0xff);
    o.writeMask = (uint32_t)opt_u64(s, "write_mask", 0xff);
    o.reference = (uint32_t)opt_u64(s, "reference", 0);
    return o;
}

// Graphics pipeline state, each block optional with Vulkan's usual defaults:
// {"op": "graphics_pipeline", "name", "layout", "vs", "fs", "vs_entry", "fs_entry", "vs_spec", "fs_spec",
//  "vertex_input": {"bindings": [{"binding", "stride", "rate"}], "attributes": [{"location", "binding", "format", "offset"}]},
//  "topology", "primitive_restart", "viewport": [x, y, w, h, min_depth, max_depth], "scissor": [x, y, w, h],
//  "rasterization": {"polygon_mode", "cull", "front_face", "depth_clamp", "discard", "depth_bias": [c, clamp, slope], "line_width"},
//  "multisample": {"samples", "sample_shading", "sample_mask", "alpha_to_coverage", "alpha_to_one"},
//  "depth_stencil": {"test", "write", "compare", "bounds": [min, max], "stencil": {"front": {...}, "back": {...}}},
//  "blend": {"logic_op", "constants": [r, g, b, a], "attachments": [{"enable", "src_color", "dst_color", "color_op",
//            "src_alpha", "dst_alpha", "alpha_op", "write_mask"}]},
//  "dynamic": ["viewport", "scissor", ...],
//  "rendering": {"color_formats": [...], "depth_format", "stencil_format"}}
void op_graphics_pipeline(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    VkPipelineLayout layout = st.get(st.pipeline_layouts, req_str(op, "layout", ctx), "pipeline_layout");
    std::vector<VkPipelineShaderStageCreateInfo> stages;
    Spec vspec, fspec;
    std::string vs_entry = opt_str(op, "vs_entry", "main"), fs_entry = opt_str(op, "fs_entry", "main");
    VkShaderModule vs = st.get(st.shaders, req_str(op, "vs", ctx), "shader");
    stages.push_back({VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_VERTEX_BIT, vs,
                      vs_entry.c_str(), vspec.build(op.value("vs_spec", json()), ctx)});
    if (op.contains("fs")) {
        VkShaderModule fs = st.get(st.shaders, req_str(op, "fs", ctx), "shader");
        stages.push_back({VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_FRAGMENT_BIT, fs,
                          fs_entry.c_str(), fspec.build(op.value("fs_spec", json()), ctx)});
    }

    std::vector<VkVertexInputBindingDescription> vbind;
    std::vector<VkVertexInputAttributeDescription> vattr;
    const json vi = op.value("vertex_input", json::object());
    if (vi.contains("bindings"))
        for (const json& b : vi["bindings"])
            vbind.push_back({(uint32_t)req_u64(b, "binding", ctx), (uint32_t)req_u64(b, "stride", ctx),
                             opt_str(b, "rate", "vertex") == "instance" ? VK_VERTEX_INPUT_RATE_INSTANCE : VK_VERTEX_INPUT_RATE_VERTEX});
    if (vi.contains("attributes"))
        for (const json& a : vi["attributes"])
            vattr.push_back({(uint32_t)req_u64(a, "location", ctx), (uint32_t)opt_u64(a, "binding", 0),
                             parse_format(req(a, "format", ctx), ctx), (uint32_t)opt_u64(a, "offset", 0)});
    VkPipelineVertexInputStateCreateInfo vis{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    vis.vertexBindingDescriptionCount = (uint32_t)vbind.size();
    vis.pVertexBindingDescriptions = vbind.data();
    vis.vertexAttributeDescriptionCount = (uint32_t)vattr.size();
    vis.pVertexAttributeDescriptions = vattr.data();

    VkPipelineInputAssemblyStateCreateInfo ia{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};
    ia.topology = (VkPrimitiveTopology)parse_enum("VkPrimitiveTopology", "VK_PRIMITIVE_TOPOLOGY_",
                                                  op.value("topology", json("triangle_list")), ctx);
    ia.primitiveRestartEnable = opt_bool(op, "primitive_restart", false);

    // Viewport and scissor: static unless listed in "dynamic".
    VkViewport vp{0, 0, 1, 1, 0, 1};
    VkRect2D sc{{0, 0}, {1, 1}};
    if (op.contains("viewport")) {
        const json& v = op["viewport"];
        vp = {v[0].get<float>(), v[1].get<float>(), v[2].get<float>(), v[3].get<float>(),
              v.size() > 4 ? v[4].get<float>() : 0.0f, v.size() > 5 ? v[5].get<float>() : 1.0f};
        sc = {{(int32_t)vp.x, (int32_t)vp.y}, {(uint32_t)vp.width, (uint32_t)std::abs(vp.height)}};
    }
    if (op.contains("scissor")) {
        const json& s = op["scissor"];
        sc = {{s[0].get<int32_t>(), s[1].get<int32_t>()}, {s[2].get<uint32_t>(), s[3].get<uint32_t>()}};
    }
    VkPipelineViewportStateCreateInfo vps{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
    vps.viewportCount = 1;
    vps.pViewports = &vp;
    vps.scissorCount = 1;
    vps.pScissors = &sc;

    const json rs_j = op.value("rasterization", json::object());
    VkPipelineRasterizationStateCreateInfo rs{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
    rs.depthClampEnable = opt_bool(rs_j, "depth_clamp", false);
    rs.rasterizerDiscardEnable = opt_bool(rs_j, "discard", false);
    rs.polygonMode = (VkPolygonMode)parse_enum("VkPolygonMode", "VK_POLYGON_MODE_", rs_j.value("polygon_mode", json("fill")), ctx);
    rs.cullMode = (VkCullModeFlags)parse_flags("VkCullModeFlagBits", "VK_CULL_MODE_", rs_j.value("cull", json("none")), ctx);
    rs.frontFace = (VkFrontFace)parse_enum("VkFrontFace", "VK_FRONT_FACE_", rs_j.value("front_face", json("counter_clockwise")), ctx);
    if (rs_j.contains("depth_bias")) {
        const json& d = rs_j["depth_bias"];
        rs.depthBiasEnable = VK_TRUE;
        rs.depthBiasConstantFactor = d[0].get<float>();
        rs.depthBiasClamp = d[1].get<float>();
        rs.depthBiasSlopeFactor = d[2].get<float>();
    }
    rs.lineWidth = (float)opt_f64(rs_j, "line_width", 1.0);

    const json ms_j = op.value("multisample", json::object());
    VkPipelineMultisampleStateCreateInfo ms{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};
    ms.rasterizationSamples = (VkSampleCountFlagBits)opt_u64(ms_j, "samples", 1);
    ms.sampleShadingEnable = ms_j.contains("sample_shading");
    ms.minSampleShading = (float)opt_f64(ms_j, "sample_shading", 0.0);
    VkSampleMask mask = (VkSampleMask)opt_u64(ms_j, "sample_mask", 0xffffffffu);
    ms.pSampleMask = ms_j.contains("sample_mask") ? &mask : nullptr;
    ms.alphaToCoverageEnable = opt_bool(ms_j, "alpha_to_coverage", false);
    ms.alphaToOneEnable = opt_bool(ms_j, "alpha_to_one", false);

    const json ds_j = op.value("depth_stencil", json::object());
    VkPipelineDepthStencilStateCreateInfo ds{VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO};
    ds.depthTestEnable = opt_bool(ds_j, "test", false);
    ds.depthWriteEnable = opt_bool(ds_j, "write", false);
    ds.depthCompareOp = cmp(ds_j.value("compare", json()), "less", ctx);
    if (ds_j.contains("bounds")) {
        ds.depthBoundsTestEnable = VK_TRUE;
        ds.minDepthBounds = ds_j["bounds"][0].get<float>();
        ds.maxDepthBounds = ds_j["bounds"][1].get<float>();
    } else {
        ds.maxDepthBounds = 1.0f;
    }
    if (ds_j.contains("stencil")) {
        const json& s = ds_j["stencil"];
        ds.stencilTestEnable = VK_TRUE;
        ds.front = stencil_state(s.value("front", json::object()), ctx);
        ds.back = s.contains("back") ? stencil_state(s["back"], ctx) : ds.front;
    }

    const json rend = op.value("rendering", json::object());
    std::vector<VkFormat> color_formats;
    if (rend.contains("color_formats"))
        for (const json& f : rend["color_formats"]) color_formats.push_back(parse_format(f, ctx));
    VkPipelineRenderingCreateInfo ri{VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO};
    ri.colorAttachmentCount = (uint32_t)color_formats.size();
    ri.pColorAttachmentFormats = color_formats.data();
    ri.depthAttachmentFormat = rend.contains("depth_format") ? parse_format(rend["depth_format"], ctx) : VK_FORMAT_UNDEFINED;
    ri.stencilAttachmentFormat = rend.contains("stencil_format") ? parse_format(rend["stencil_format"], ctx) : VK_FORMAT_UNDEFINED;

    const json bl_j = op.value("blend", json::object());
    std::vector<VkPipelineColorBlendAttachmentState> atts;
    const json att_list = bl_j.value("attachments", json::array());
    for (size_t k = 0; k < color_formats.size(); ++k) {
        const json a = k < att_list.size() ? att_list[k] : json::object();
        VkPipelineColorBlendAttachmentState s{};
        s.blendEnable = opt_bool(a, "enable", false);
        s.srcColorBlendFactor = bf(a.value("src_color", json()), "one", ctx);
        s.dstColorBlendFactor = bf(a.value("dst_color", json()), "zero", ctx);
        s.colorBlendOp = bop(a.value("color_op", json()), ctx);
        s.srcAlphaBlendFactor = bf(a.value("src_alpha", json()), "one", ctx);
        s.dstAlphaBlendFactor = bf(a.value("dst_alpha", json()), "zero", ctx);
        s.alphaBlendOp = bop(a.value("alpha_op", json()), ctx);
        std::string wm = opt_str(a, "write_mask", "rgba");
        s.colorWriteMask = 0;
        for (char c : wm) {
            if (c == 'r') s.colorWriteMask |= VK_COLOR_COMPONENT_R_BIT;
            else if (c == 'g') s.colorWriteMask |= VK_COLOR_COMPONENT_G_BIT;
            else if (c == 'b') s.colorWriteMask |= VK_COLOR_COMPONENT_B_BIT;
            else if (c == 'a') s.colorWriteMask |= VK_COLOR_COMPONENT_A_BIT;
            else throw CaseError(ctx + ": write_mask is letters from 'rgba'");
        }
        atts.push_back(s);
    }
    VkPipelineColorBlendStateCreateInfo cb{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
    if (bl_j.contains("logic_op")) {
        cb.logicOpEnable = VK_TRUE;
        cb.logicOp = (VkLogicOp)parse_enum("VkLogicOp", "VK_LOGIC_OP_", bl_j["logic_op"], ctx);
    }
    cb.attachmentCount = (uint32_t)atts.size();
    cb.pAttachments = atts.data();
    if (bl_j.contains("constants"))
        for (int k = 0; k < 4; ++k) cb.blendConstants[k] = bl_j["constants"][k].get<float>();

    std::vector<VkDynamicState> dyn;
    if (op.contains("dynamic"))
        for (const json& d : op["dynamic"]) dyn.push_back((VkDynamicState)parse_enum("VkDynamicState", "VK_DYNAMIC_STATE_", d, ctx));
    VkPipelineDynamicStateCreateInfo dsi{VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO};
    dsi.dynamicStateCount = (uint32_t)dyn.size();
    dsi.pDynamicStates = dyn.data();

    VkGraphicsPipelineCreateInfo ci{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};
    ci.pNext = &ri;
    ci.stageCount = (uint32_t)stages.size();
    ci.pStages = stages.data();
    ci.pVertexInputState = &vis;
    ci.pInputAssemblyState = &ia;
    ci.pViewportState = rs.rasterizerDiscardEnable ? nullptr : &vps;
    ci.pRasterizationState = &rs;
    ci.pMultisampleState = rs.rasterizerDiscardEnable ? nullptr : &ms;
    bool has_ds = ri.depthAttachmentFormat != VK_FORMAT_UNDEFINED || ri.stencilAttachmentFormat != VK_FORMAT_UNDEFINED;
    ci.pDepthStencilState = has_ds && !rs.rasterizerDiscardEnable ? &ds : nullptr;
    ci.pColorBlendState = color_formats.empty() || rs.rasterizerDiscardEnable ? nullptr : &cb;
    ci.pDynamicState = dyn.empty() ? nullptr : &dsi;
    ci.layout = layout;
    Pipeline p;
    p.bind_point = VK_PIPELINE_BIND_POINT_GRAPHICS;
    if (VKC(st, vkCreateGraphicsPipelines, st.device, VK_NULL_HANDLE, 1, &ci, nullptr, &p.pipe) != VK_SUCCESS) return;
    st.pipelines[name] = p;
}

}  // namespace

void register_pipeline_ops(std::map<std::string, OpFn>& ops) {
    ops["compute_pipeline"] = op_compute_pipeline;
    ops["graphics_pipeline"] = op_graphics_pipeline;
}
