// vkreplay child: command recording and submission.
//
// With auto_barriers (the default) the driver puts a full memory barrier
// (ALL_COMMANDS -> ALL_COMMANDS, MEMORY_WRITE -> MEMORY_READ|MEMORY_WRITE)
// before every command recorded outside a rendering scope, so cases need not
// spell out synchronization unless synchronization is what they test.
#include "child.hpp"

#include <cstring>

namespace {

VkOffset3D off3(const json& v) {
    if (v.is_null()) return {0, 0, 0};
    return {v[0].get<int32_t>(), v.size() > 1 ? v[1].get<int32_t>() : 0, v.size() > 2 ? v[2].get<int32_t>() : 0};
}
VkExtent3D ext3(const json& v) {
    return {v[0].get<uint32_t>(), v.size() > 1 ? v[1].get<uint32_t>() : 1, v.size() > 2 ? v[2].get<uint32_t>() : 1};
}

VkImageAspectFlags aspects_or(const json& v, VkImageAspectFlags dflt, const std::string& ctx) {
    if (v.is_null()) return dflt;
    return (VkImageAspectFlags)parse_flags("VkImageAspectFlagBits", "VK_IMAGE_ASPECT_", v, ctx);
}

VkImageSubresourceLayers sub_layers(const json& s, const Image& im, const std::string& ctx) {
    VkImageAspectFlags a = im.aspects;
    if (a == (VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)) a = VK_IMAGE_ASPECT_DEPTH_BIT;
    return {aspects_or(s.value("aspect", json()), a, ctx), (uint32_t)opt_u64(s, "mip", 0),
            (uint32_t)opt_u64(s, "base_layer", 0), (uint32_t)opt_u64(s, "layers", 1)};
}

VkImageSubresourceRange sub_range(const json& s, const Image& im, const std::string& ctx) {
    return {aspects_or(s.value("aspect", json()), im.aspects, ctx), (uint32_t)opt_u64(s, "base_mip", 0),
            (uint32_t)opt_u64(s, "mips", VK_REMAINING_MIP_LEVELS), (uint32_t)opt_u64(s, "base_layer", 0),
            (uint32_t)opt_u64(s, "layers", VK_REMAINING_ARRAY_LAYERS)};
}

VkClearColorValue clear_color(const json& v, const std::string& ctx) {
    VkClearColorValue c{};
    if (v.contains("f32")) for (int k = 0; k < 4; ++k) c.float32[k] = v["f32"][k].get<float>();
    else if (v.contains("u32")) for (int k = 0; k < 4; ++k) c.uint32[k] = v["u32"][k].get<uint32_t>();
    else if (v.contains("i32")) for (int k = 0; k < 4; ++k) c.int32[k] = v["i32"][k].get<int32_t>();
    else throw CaseError(ctx + ": a clear color is {\"f32\"|\"u32\"|\"i32\": [4 values]}");
    return c;
}

VkPipelineStageFlags stages_of(const json& v, VkPipelineStageFlags dflt, const std::string& ctx) {
    if (v.is_null()) return dflt;
    return (VkPipelineStageFlags)parse_flags("VkPipelineStageFlagBits", "VK_PIPELINE_STAGE_", v, ctx);
}
VkAccessFlags access_of(const json& v, VkAccessFlags dflt, const std::string& ctx) {
    if (v.is_null()) return dflt;
    return (VkAccessFlags)parse_flags("VkAccessFlagBits", "VK_ACCESS_", v, ctx);
}

void full_barrier(VkCommandBuffer cb) {
    VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr, VK_ACCESS_MEMORY_WRITE_BIT,
                       VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 1, &mb, 0,
                         nullptr, 0, nullptr);
}

VkAttachmentLoadOp load_op(const json& v, const std::string& ctx) {
    return (VkAttachmentLoadOp)parse_enum("VkAttachmentLoadOp", "VK_ATTACHMENT_LOAD_OP_", v.is_null() ? json("load") : v, ctx);
}
VkAttachmentStoreOp store_op(const json& v, const std::string& ctx) {
    return (VkAttachmentStoreOp)parse_enum("VkAttachmentStoreOp", "VK_ATTACHMENT_STORE_OP_", v.is_null() ? json("store") : v, ctx);
}

VkRenderingAttachmentInfo attachment(State& st, const json& a, bool depth, const std::string& ctx) {
    VkRenderingAttachmentInfo ai{VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO};
    ai.imageView = st.get(st.views, req_str(a, "view", ctx), "view").view;
    ai.imageLayout = parse_layout(a.value("layout", json("general")), ctx);
    ai.loadOp = load_op(a.value("load", json()), ctx);
    ai.storeOp = store_op(a.value("store", json()), ctx);
    if (a.contains("clear")) {
        const json& c = a["clear"];
        if (depth) ai.clearValue.depthStencil = {(float)opt_f64(c, "depth", 1.0), (uint32_t)opt_u64(c, "stencil", 0)};
        else ai.clearValue.color = clear_color(c, ctx);
    }
    if (a.contains("resolve_view")) {
        ai.resolveImageView = st.get(st.views, a["resolve_view"].get<std::string>(), "view").view;
        ai.resolveImageLayout = VK_IMAGE_LAYOUT_GENERAL;
        ai.resolveMode = (VkResolveModeFlagBits)parse_flags("VkResolveModeFlagBits", "VK_RESOLVE_MODE_",
                                                            a.value("resolve_mode", json("average")), ctx);
    }
    return ai;
}

std::vector<uint8_t> small_data(const json& v, const State& st, const std::string& ctx) {
    json wrapper = {{"data", v}};
    return data_bytes(wrapper, st, ctx);
}

}  // namespace

void record_commands(State& st, VkCommandBuffer cb, const json& cmds, bool auto_barriers, const std::string& octx) {
    if (!cmds.is_array()) throw CaseError(octx + ": 'cmds' must be a list");
    bool in_rendering = false;
    for (size_t k = 0; k < cmds.size(); ++k) {
        const json& c = cmds[k];
        std::string cmd = req_str(c, "cmd", octx);
        std::string ctx = octx + " cmd " + std::to_string(k) + " (" + cmd + ")";
        if (auto_barriers && !in_rendering && cmd != "end_rendering" && cmd != "end_render_pass") full_barrier(cb);

        if (cmd == "begin_rendering") {
            std::vector<VkRenderingAttachmentInfo> colors;
            if (c.contains("color"))
                for (const json& a : c["color"]) colors.push_back(attachment(st, a, false, ctx));
            VkRenderingAttachmentInfo dep{}, sten{};
            VkRenderingInfo ri{VK_STRUCTURE_TYPE_RENDERING_INFO};
            const json& area = req(c, "area", ctx);
            ri.renderArea = {{area[0].get<int32_t>(), area[1].get<int32_t>()}, {area[2].get<uint32_t>(), area[3].get<uint32_t>()}};
            ri.layerCount = (uint32_t)opt_u64(c, "layers", 1);
            ri.colorAttachmentCount = (uint32_t)colors.size();
            ri.pColorAttachments = colors.data();
            if (c.contains("depth")) { dep = attachment(st, c["depth"], true, ctx); ri.pDepthAttachment = &dep; }
            if (c.contains("stencil")) { sten = attachment(st, c["stencil"], true, ctx); ri.pStencilAttachment = &sten; }
            vkCmdBeginRendering(cb, &ri);
            in_rendering = true;
        } else if (cmd == "end_rendering") {
            vkCmdEndRendering(cb);
            in_rendering = false;
        } else if (cmd == "begin_render_pass") {
            // {"render_pass", "framebuffer", "area": [x, y, w, h], "clears": [{"f32": [...]} | {"depth", "stencil"}]}
            VkRenderPassBeginInfo bi{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
            bi.renderPass = st.get(st.render_passes, req_str(c, "render_pass", ctx), "render_pass").rp;
            bi.framebuffer = st.get(st.framebuffers, req_str(c, "framebuffer", ctx), "framebuffer");
            const json& area = req(c, "area", ctx);
            bi.renderArea = {{area[0].get<int32_t>(), area[1].get<int32_t>()}, {area[2].get<uint32_t>(), area[3].get<uint32_t>()}};
            std::vector<VkClearValue> clears;
            if (c.contains("clears")) {
                for (const json& v : c["clears"]) {
                    VkClearValue cv{};
                    if (v.contains("depth") || v.contains("stencil"))
                        cv.depthStencil = {(float)opt_f64(v, "depth", 1.0), (uint32_t)opt_u64(v, "stencil", 0)};
                    else
                        cv.color = clear_color(v, ctx);
                    clears.push_back(cv);
                }
            }
            bi.clearValueCount = (uint32_t)clears.size();
            bi.pClearValues = clears.data();
            vkCmdBeginRenderPass(cb, &bi, VK_SUBPASS_CONTENTS_INLINE);
            in_rendering = true;
        } else if (cmd == "next_subpass") {
            vkCmdNextSubpass(cb, VK_SUBPASS_CONTENTS_INLINE);
        } else if (cmd == "end_render_pass") {
            vkCmdEndRenderPass(cb);
            in_rendering = false;
        } else if (cmd == "bind_pipeline") {
            Pipeline& p = st.get(st.pipelines, req_str(c, "pipeline", ctx), "pipeline");
            vkCmdBindPipeline(cb, p.bind_point, p.pipe);
        } else if (cmd == "bind_vertex_buffers") {
            std::vector<VkBuffer> bufs;
            std::vector<VkDeviceSize> offs;
            for (const json& b : req(c, "buffers", ctx)) {
                bufs.push_back(st.get(st.buffers, req_str(b, "buffer", ctx), "buffer").buf);
                offs.push_back(opt_u64(b, "offset", 0));
            }
            vkCmdBindVertexBuffers(cb, (uint32_t)opt_u64(c, "first", 0), (uint32_t)bufs.size(), bufs.data(), offs.data());
        } else if (cmd == "bind_index_buffer") {
            vkCmdBindIndexBuffer(cb, st.get(st.buffers, req_str(c, "buffer", ctx), "buffer").buf, opt_u64(c, "offset", 0),
                                 (VkIndexType)parse_enum("VkIndexType", "VK_INDEX_TYPE_", c.value("type", json("uint32")), ctx));
        } else if (cmd == "bind_sets") {
            VkPipelineLayout pl = st.get(st.pipeline_layouts, req_str(c, "layout", ctx), "pipeline_layout");
            std::vector<VkDescriptorSet> sets;
            for (const json& s : req(c, "sets", ctx)) sets.push_back(st.get(st.desc_sets, s.get<std::string>(), "desc_set").set);
            std::vector<uint32_t> dyn;
            if (c.contains("dynamic_offsets")) for (const json& d : c["dynamic_offsets"]) dyn.push_back(d.get<uint32_t>());
            VkPipelineBindPoint bp = opt_str(c, "bind_point", "compute") == "graphics" ? VK_PIPELINE_BIND_POINT_GRAPHICS
                                                                                       : VK_PIPELINE_BIND_POINT_COMPUTE;
            vkCmdBindDescriptorSets(cb, bp, pl, (uint32_t)opt_u64(c, "first", 0), (uint32_t)sets.size(), sets.data(),
                                    (uint32_t)dyn.size(), dyn.data());
        } else if (cmd == "push_constants") {
            VkPipelineLayout pl = st.get(st.pipeline_layouts, req_str(c, "layout", ctx), "pipeline_layout");
            std::vector<uint8_t> d = small_data(req(c, "data", ctx), st, ctx);
            vkCmdPushConstants(cb, pl, parse_stages(c.value("stages", json("all")), ctx), (uint32_t)opt_u64(c, "offset", 0),
                               (uint32_t)d.size(), d.data());
        } else if (cmd == "set_viewport") {
            std::vector<VkViewport> vs;
            for (const json& v : req(c, "viewports", ctx))
                vs.push_back({v[0].get<float>(), v[1].get<float>(), v[2].get<float>(), v[3].get<float>(),
                              v.size() > 4 ? v[4].get<float>() : 0.0f, v.size() > 5 ? v[5].get<float>() : 1.0f});
            vkCmdSetViewport(cb, 0, (uint32_t)vs.size(), vs.data());
        } else if (cmd == "set_scissor") {
            std::vector<VkRect2D> rs;
            for (const json& s : req(c, "scissors", ctx))
                rs.push_back({{s[0].get<int32_t>(), s[1].get<int32_t>()}, {s[2].get<uint32_t>(), s[3].get<uint32_t>()}});
            vkCmdSetScissor(cb, 0, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "set_blend_constants") {
            float k4[4];
            for (int i = 0; i < 4; ++i) k4[i] = req(c, "values", ctx)[i].get<float>();
            vkCmdSetBlendConstants(cb, k4);
        } else if (cmd == "set_depth_bias") {
            const json& v = req(c, "values", ctx);
            vkCmdSetDepthBias(cb, v[0].get<float>(), v[1].get<float>(), v[2].get<float>());
        } else if (cmd == "set_depth_bounds") {
            const json& v = req(c, "values", ctx);
            vkCmdSetDepthBounds(cb, v[0].get<float>(), v[1].get<float>());
        } else if (cmd == "set_stencil_compare_mask" || cmd == "set_stencil_write_mask" || cmd == "set_stencil_reference") {
            VkStencilFaceFlags face = (VkStencilFaceFlags)parse_flags("VkStencilFaceFlagBits", "VK_STENCIL_FACE_",
                                                                      c.value("face", json("front_and_back")), ctx);
            uint32_t v = (uint32_t)req_u64(c, "value", ctx);
            if (cmd == "set_stencil_compare_mask") vkCmdSetStencilCompareMask(cb, face, v);
            else if (cmd == "set_stencil_write_mask") vkCmdSetStencilWriteMask(cb, face, v);
            else vkCmdSetStencilReference(cb, face, v);
        } else if (cmd == "set_line_width") {
            vkCmdSetLineWidth(cb, (float)opt_f64(c, "value", 1.0));
        } else if (cmd == "set_cull_mode") {
            vkCmdSetCullMode(cb, (VkCullModeFlags)parse_flags("VkCullModeFlagBits", "VK_CULL_MODE_", req(c, "value", ctx), ctx));
        } else if (cmd == "set_front_face") {
            vkCmdSetFrontFace(cb, (VkFrontFace)parse_enum("VkFrontFace", "VK_FRONT_FACE_", req(c, "value", ctx), ctx));
        } else if (cmd == "set_primitive_topology") {
            vkCmdSetPrimitiveTopology(cb, (VkPrimitiveTopology)parse_enum("VkPrimitiveTopology", "VK_PRIMITIVE_TOPOLOGY_", req(c, "value", ctx), ctx));
        } else if (cmd == "set_depth_test_enable") {
            vkCmdSetDepthTestEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "set_depth_write_enable") {
            vkCmdSetDepthWriteEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "set_depth_compare_op") {
            vkCmdSetDepthCompareOp(cb, (VkCompareOp)parse_enum("VkCompareOp", "VK_COMPARE_OP_", req(c, "value", ctx), ctx));
        } else if (cmd == "set_depth_bounds_test_enable") {
            vkCmdSetDepthBoundsTestEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "set_stencil_test_enable") {
            vkCmdSetStencilTestEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "set_stencil_op") {
            auto sop = [&](const char* k) { return (VkStencilOp)parse_enum("VkStencilOp", "VK_STENCIL_OP_", c.value(k, json("keep")), ctx); };
            vkCmdSetStencilOp(cb, (VkStencilFaceFlags)parse_flags("VkStencilFaceFlagBits", "VK_STENCIL_FACE_", c.value("face", json("front_and_back")), ctx),
                              sop("fail"), sop("pass"), sop("depth_fail"),
                              (VkCompareOp)parse_enum("VkCompareOp", "VK_COMPARE_OP_", c.value("compare", json("always")), ctx));
        } else if (cmd == "set_rasterizer_discard_enable") {
            vkCmdSetRasterizerDiscardEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "set_depth_bias_enable") {
            vkCmdSetDepthBiasEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "set_primitive_restart_enable") {
            vkCmdSetPrimitiveRestartEnable(cb, opt_bool(c, "value", true));
        } else if (cmd == "draw") {
            vkCmdDraw(cb, (uint32_t)req_u64(c, "vertices", ctx), (uint32_t)opt_u64(c, "instances", 1),
                      (uint32_t)opt_u64(c, "first_vertex", 0), (uint32_t)opt_u64(c, "first_instance", 0));
        } else if (cmd == "draw_indexed") {
            vkCmdDrawIndexed(cb, (uint32_t)req_u64(c, "indices", ctx), (uint32_t)opt_u64(c, "instances", 1),
                             (uint32_t)opt_u64(c, "first_index", 0), (int32_t)opt_i64(c, "vertex_offset", 0),
                             (uint32_t)opt_u64(c, "first_instance", 0));
        } else if (cmd == "draw_indirect" || cmd == "draw_indexed_indirect") {
            VkBuffer b = st.get(st.buffers, req_str(c, "buffer", ctx), "buffer").buf;
            uint32_t n = (uint32_t)opt_u64(c, "count", 1);
            uint32_t stride = (uint32_t)opt_u64(c, "stride", cmd == "draw_indirect" ? 16 : 20);
            if (cmd == "draw_indirect") vkCmdDrawIndirect(cb, b, opt_u64(c, "offset", 0), n, stride);
            else vkCmdDrawIndexedIndirect(cb, b, opt_u64(c, "offset", 0), n, stride);
        } else if (cmd == "dispatch") {
            const json& g = req(c, "groups", ctx);
            vkCmdDispatch(cb, g[0].get<uint32_t>(), g.size() > 1 ? g[1].get<uint32_t>() : 1, g.size() > 2 ? g[2].get<uint32_t>() : 1);
        } else if (cmd == "dispatch_base") {
            const json& b = req(c, "base", ctx);
            const json& g = req(c, "groups", ctx);
            vkCmdDispatchBase(cb, b[0].get<uint32_t>(), b[1].get<uint32_t>(), b[2].get<uint32_t>(), g[0].get<uint32_t>(),
                              g[1].get<uint32_t>(), g[2].get<uint32_t>());
        } else if (cmd == "dispatch_indirect") {
            vkCmdDispatchIndirect(cb, st.get(st.buffers, req_str(c, "buffer", ctx), "buffer").buf, opt_u64(c, "offset", 0));
        } else if (cmd == "copy_buffer") {
            VkBuffer s = st.get(st.buffers, req_str(c, "src", ctx), "buffer").buf;
            VkBuffer d = st.get(st.buffers, req_str(c, "dst", ctx), "buffer").buf;
            std::vector<VkBufferCopy> rs;
            for (const json& r : req(c, "regions", ctx)) rs.push_back({r[0].get<uint64_t>(), r[1].get<uint64_t>(), r[2].get<uint64_t>()});
            vkCmdCopyBuffer(cb, s, d, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "fill_buffer") {
            vkCmdFillBuffer(cb, st.get(st.buffers, req_str(c, "buffer", ctx), "buffer").buf, opt_u64(c, "offset", 0),
                            opt_u64(c, "size", VK_WHOLE_SIZE), (uint32_t)req_u64(c, "value", ctx));
        } else if (cmd == "update_buffer") {
            std::vector<uint8_t> d = small_data(req(c, "data", ctx), st, ctx);
            vkCmdUpdateBuffer(cb, st.get(st.buffers, req_str(c, "buffer", ctx), "buffer").buf, opt_u64(c, "offset", 0), d.size(), d.data());
        } else if (cmd == "copy_image") {
            Image& s = st.get(st.images, req_str(c, "src", ctx), "image");
            Image& d = st.get(st.images, req_str(c, "dst", ctx), "image");
            std::vector<VkImageCopy> rs;
            for (const json& r : req(c, "regions", ctx))
                rs.push_back({sub_layers(r.value("src_sub", json::object()), s, ctx), off3(r.value("src_offset", json())),
                              sub_layers(r.value("dst_sub", json::object()), d, ctx), off3(r.value("dst_offset", json())),
                              ext3(req(r, "extent", ctx))});
            vkCmdCopyImage(cb, s.img, VK_IMAGE_LAYOUT_GENERAL, d.img, VK_IMAGE_LAYOUT_GENERAL, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "copy_buffer_to_image" || cmd == "copy_image_to_buffer") {
            Buffer& b = st.get(st.buffers, req_str(c, "buffer", ctx), "buffer");
            Image& im = st.get(st.images, req_str(c, "image", ctx), "image");
            std::vector<VkBufferImageCopy> rs;
            for (const json& r : req(c, "regions", ctx))
                rs.push_back({opt_u64(r, "buffer_offset", 0), (uint32_t)opt_u64(r, "row_length", 0), (uint32_t)opt_u64(r, "image_height", 0),
                              sub_layers(r.value("sub", json::object()), im, ctx), off3(r.value("offset", json())), ext3(req(r, "extent", ctx))});
            if (cmd == "copy_buffer_to_image")
                vkCmdCopyBufferToImage(cb, b.buf, im.img, VK_IMAGE_LAYOUT_GENERAL, (uint32_t)rs.size(), rs.data());
            else
                vkCmdCopyImageToBuffer(cb, im.img, VK_IMAGE_LAYOUT_GENERAL, b.buf, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "blit_image") {
            Image& s = st.get(st.images, req_str(c, "src", ctx), "image");
            Image& d = st.get(st.images, req_str(c, "dst", ctx), "image");
            std::vector<VkImageBlit> rs;
            for (const json& r : req(c, "regions", ctx)) {
                VkImageBlit b{};
                b.srcSubresource = sub_layers(r.value("src_sub", json::object()), s, ctx);
                b.dstSubresource = sub_layers(r.value("dst_sub", json::object()), d, ctx);
                b.srcOffsets[0] = off3(req(r, "src_offsets", ctx)[0]);
                b.srcOffsets[1] = off3(r["src_offsets"][1]);
                b.dstOffsets[0] = off3(req(r, "dst_offsets", ctx)[0]);
                b.dstOffsets[1] = off3(r["dst_offsets"][1]);
                rs.push_back(b);
            }
            vkCmdBlitImage(cb, s.img, VK_IMAGE_LAYOUT_GENERAL, d.img, VK_IMAGE_LAYOUT_GENERAL, (uint32_t)rs.size(), rs.data(),
                           (VkFilter)parse_enum("VkFilter", "VK_FILTER_", c.value("filter", json("nearest")), ctx));
        } else if (cmd == "resolve_image") {
            Image& s = st.get(st.images, req_str(c, "src", ctx), "image");
            Image& d = st.get(st.images, req_str(c, "dst", ctx), "image");
            std::vector<VkImageResolve> rs;
            for (const json& r : req(c, "regions", ctx))
                rs.push_back({sub_layers(r.value("src_sub", json::object()), s, ctx), off3(r.value("src_offset", json())),
                              sub_layers(r.value("dst_sub", json::object()), d, ctx), off3(r.value("dst_offset", json())),
                              ext3(req(r, "extent", ctx))});
            vkCmdResolveImage(cb, s.img, VK_IMAGE_LAYOUT_GENERAL, d.img, VK_IMAGE_LAYOUT_GENERAL, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "clear_color_image") {
            Image& im = st.get(st.images, req_str(c, "image", ctx), "image");
            VkClearColorValue cc = clear_color(req(c, "color", ctx), ctx);
            std::vector<VkImageSubresourceRange> rs;
            if (c.contains("ranges")) for (const json& r : c["ranges"]) rs.push_back(sub_range(r, im, ctx));
            else rs.push_back(full_range(im));
            vkCmdClearColorImage(cb, im.img, VK_IMAGE_LAYOUT_GENERAL, &cc, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "clear_depth_stencil_image") {
            Image& im = st.get(st.images, req_str(c, "image", ctx), "image");
            VkClearDepthStencilValue v{(float)opt_f64(c, "depth", 1.0), (uint32_t)opt_u64(c, "stencil", 0)};
            std::vector<VkImageSubresourceRange> rs;
            if (c.contains("ranges")) for (const json& r : c["ranges"]) rs.push_back(sub_range(r, im, ctx));
            else rs.push_back(full_range(im));
            vkCmdClearDepthStencilImage(cb, im.img, VK_IMAGE_LAYOUT_GENERAL, &v, (uint32_t)rs.size(), rs.data());
        } else if (cmd == "clear_attachments") {
            std::vector<VkClearAttachment> as;
            for (const json& a : req(c, "attachments", ctx)) {
                VkClearAttachment x{};
                x.aspectMask = aspects_or(a.value("aspect", json()), VK_IMAGE_ASPECT_COLOR_BIT, ctx);
                x.colorAttachment = (uint32_t)opt_u64(a, "color_attachment", 0);
                if (x.aspectMask & VK_IMAGE_ASPECT_COLOR_BIT) x.clearValue.color = clear_color(req(a, "clear", ctx), ctx);
                else x.clearValue.depthStencil = {(float)opt_f64(a["clear"], "depth", 1.0), (uint32_t)opt_u64(a["clear"], "stencil", 0)};
                as.push_back(x);
            }
            std::vector<VkClearRect> rs;
            for (const json& r : req(c, "rects", ctx))
                rs.push_back({{{r[0].get<int32_t>(), r[1].get<int32_t>()}, {r[2].get<uint32_t>(), r[3].get<uint32_t>()}},
                              r.size() > 4 ? r[4].get<uint32_t>() : 0, r.size() > 5 ? r[5].get<uint32_t>() : 1});
            vkCmdClearAttachments(cb, (uint32_t)as.size(), as.data(), (uint32_t)rs.size(), rs.data());
        } else if (cmd == "barrier") {
            VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr,
                               access_of(c.value("src_access", json()), VK_ACCESS_MEMORY_WRITE_BIT, ctx),
                               access_of(c.value("dst_access", json()), VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT, ctx)};
            std::vector<VkImageMemoryBarrier> ibs;
            if (c.contains("images")) {
                for (const json& b : c["images"]) {
                    Image& im = st.get(st.images, req_str(b, "image", ctx), "image");
                    VkImageMemoryBarrier x{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
                    x.srcAccessMask = mb.srcAccessMask;
                    x.dstAccessMask = mb.dstAccessMask;
                    x.oldLayout = parse_layout(b.value("old", json("general")), ctx);
                    x.newLayout = parse_layout(b.value("new", json("general")), ctx);
                    x.srcQueueFamilyIndex = x.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                    x.image = im.img;
                    x.subresourceRange = sub_range(b, im, ctx);
                    ibs.push_back(x);
                }
            }
            vkCmdPipelineBarrier(cb, stages_of(c.value("src_stage", json()), VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, ctx),
                                 stages_of(c.value("dst_stage", json()), VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, ctx),
                                 opt_bool(c, "by_region", false) ? VK_DEPENDENCY_BY_REGION_BIT : 0, 1, &mb, 0, nullptr,
                                 (uint32_t)ibs.size(), ibs.data());
        } else if (cmd == "reset_query_pool") {
            QueryPool& q = st.get(st.query_pools, req_str(c, "pool", ctx), "query_pool");
            vkCmdResetQueryPool(cb, q.pool, (uint32_t)opt_u64(c, "first", 0), (uint32_t)opt_u64(c, "count", q.count));
        } else if (cmd == "begin_query") {
            QueryPool& q = st.get(st.query_pools, req_str(c, "pool", ctx), "query_pool");
            vkCmdBeginQuery(cb, q.pool, (uint32_t)opt_u64(c, "query", 0), opt_bool(c, "precise", false) ? VK_QUERY_CONTROL_PRECISE_BIT : 0);
        } else if (cmd == "end_query") {
            QueryPool& q = st.get(st.query_pools, req_str(c, "pool", ctx), "query_pool");
            vkCmdEndQuery(cb, q.pool, (uint32_t)opt_u64(c, "query", 0));
        } else if (cmd == "write_timestamp") {
            QueryPool& q = st.get(st.query_pools, req_str(c, "pool", ctx), "query_pool");
            vkCmdWriteTimestamp(cb, (VkPipelineStageFlagBits)stages_of(c.value("stage", json()), VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, ctx),
                                q.pool, (uint32_t)opt_u64(c, "query", 0));
        } else if (cmd == "copy_query_results") {
            QueryPool& q = st.get(st.query_pools, req_str(c, "pool", ctx), "query_pool");
            vkCmdCopyQueryPoolResults(cb, q.pool, (uint32_t)opt_u64(c, "first", 0), (uint32_t)opt_u64(c, "count", q.count),
                                      st.get(st.buffers, req_str(c, "buffer", ctx), "buffer").buf, opt_u64(c, "offset", 0),
                                      opt_u64(c, "stride", 8),
                                      (VkQueryResultFlags)parse_flags("VkQueryResultFlagBits", "VK_QUERY_RESULT_", c.value("flags", json()), ctx));
        } else if (cmd == "set_event" || cmd == "reset_event") {
            VkEvent e = st.get(st.events, req_str(c, "event", ctx), "event");
            VkPipelineStageFlags s = stages_of(c.value("stage", json()), VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, ctx);
            if (cmd == "set_event") vkCmdSetEvent(cb, e, s);
            else vkCmdResetEvent(cb, e, s);
        } else if (cmd == "wait_events") {
            std::vector<VkEvent> es;
            for (const json& e : req(c, "events", ctx)) es.push_back(st.get(st.events, e.get<std::string>(), "event"));
            VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr, VK_ACCESS_MEMORY_WRITE_BIT,
                               VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT};
            vkCmdWaitEvents(cb, (uint32_t)es.size(), es.data(),
                            stages_of(c.value("src_stage", json()), VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, ctx),
                            stages_of(c.value("dst_stage", json()), VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, ctx), 1, &mb, 0, nullptr, 0,
                            nullptr);
        } else {
            throw CaseError(ctx + ": unknown command '" + cmd + "'");
        }
    }
    if (in_rendering) throw CaseError(octx + ": a rendering scope or render pass is not ended");
}

namespace {

VkCommandBuffer alloc_cb(State& st) {
    st.need_device();
    if (!st.cmd_pool) throw Skip("no command pool");
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, nullptr, st.cmd_pool,
                                   VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb = VK_NULL_HANDLE;
    if (VKC(st, vkAllocateCommandBuffers, st.device, &ai, &cb) != VK_SUCCESS) throw Skip("vkAllocateCommandBuffers failed");
    return cb;
}

bool record_into(State& st, VkCommandBuffer cb, const json& op, VkCommandBufferUsageFlags usage, const std::string& ctx) {
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, nullptr, usage, nullptr};
    if (VKC(st, vkBeginCommandBuffer, cb, &bi) != VK_SUCCESS) return false;
    record_commands(st, cb, req(op, "cmds", ctx), opt_bool(op, "auto_barriers", true), ctx);
    return VKC(st, vkEndCommandBuffer, cb) == VK_SUCCESS;
}

// {"op": "exec", "cmds": [...]}: record, submit, wait -- the common case.
void op_exec(State& st, const json& op, const std::string& ctx) {
    VkCommandBuffer cb = alloc_cb(st);
    if (record_into(st, cb, op, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, ctx)) {
        VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        VkFence f = VK_NULL_HANDLE;
        if (VKC(st, vkCreateFence, st.device, &fi, nullptr, &f) == VK_SUCCESS) {
            VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
            si.commandBufferCount = 1;
            si.pCommandBuffers = &cb;
            if (VKC(st, vkQueueSubmit, st.queue, 1, &si, f) == VK_SUCCESS)
                VKC(st, vkWaitForFences, st.device, 1, &f, VK_TRUE, UINT64_MAX);
            vkDestroyFence(st.device, f, nullptr);
        }
    }
    vkFreeCommandBuffers(st.device, st.cmd_pool, 1, &cb);
}

// {"op": "record", "name": "cb0", "cmds": [...], "simultaneous": false}: a named command buffer for "submit".
void op_record(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    VkCommandBuffer cb = alloc_cb(st);
    VkCommandBufferUsageFlags usage = opt_bool(op, "simultaneous", false) ? VK_COMMAND_BUFFER_USAGE_SIMULTANEOUS_USE_BIT : 0;
    if (!record_into(st, cb, op, usage, ctx)) return;
    st.cmd_buffers[name] = cb;
}

// {"op": "submit", "cbs": [...], "wait": [{"semaphore", "value", "stage"}], "signal": [{"semaphore", "value"}], "fence": name}
void op_submit(State& st, const json& op, const std::string& ctx) {
    st.need_device();
    std::vector<VkCommandBuffer> cbs;
    if (op.contains("cbs")) for (const json& c : op["cbs"]) cbs.push_back(st.get(st.cmd_buffers, c.get<std::string>(), "command buffer"));
    std::vector<VkSemaphore> ws, ss;
    std::vector<uint64_t> wv, sv;
    std::vector<VkPipelineStageFlags> wst;
    bool timeline = false;
    if (op.contains("wait"))
        for (const json& w : op["wait"]) {
            Semaphore& s = st.get(st.semaphores, req_str(w, "semaphore", ctx), "semaphore");
            ws.push_back(s.sem);
            wv.push_back(opt_u64(w, "value", 0));
            wst.push_back(stages_of(w.value("stage", json()), VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, ctx));
            timeline |= s.timeline;
        }
    if (op.contains("signal"))
        for (const json& w : op["signal"]) {
            Semaphore& s = st.get(st.semaphores, req_str(w, "semaphore", ctx), "semaphore");
            ss.push_back(s.sem);
            sv.push_back(opt_u64(w, "value", 0));
            timeline |= s.timeline;
        }
    VkTimelineSemaphoreSubmitInfo ti{VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO};
    ti.waitSemaphoreValueCount = (uint32_t)wv.size();
    ti.pWaitSemaphoreValues = wv.data();
    ti.signalSemaphoreValueCount = (uint32_t)sv.size();
    ti.pSignalSemaphoreValues = sv.data();
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.pNext = timeline ? &ti : nullptr;
    si.waitSemaphoreCount = (uint32_t)ws.size();
    si.pWaitSemaphores = ws.data();
    si.pWaitDstStageMask = wst.data();
    si.commandBufferCount = (uint32_t)cbs.size();
    si.pCommandBuffers = cbs.data();
    si.signalSemaphoreCount = (uint32_t)ss.size();
    si.pSignalSemaphores = ss.data();
    VkFence f = op.contains("fence") ? st.get(st.fences, op["fence"].get<std::string>(), "fence") : VK_NULL_HANDLE;
    VKC(st, vkQueueSubmit, st.queue, 1, &si, f);
}

}  // namespace

void register_command_ops(std::map<std::string, OpFn>& ops) {
    ops["exec"] = op_exec;
    ops["record"] = op_record;
    ops["submit"] = op_submit;
}
