// vkreplay child: buffers, images, views, samplers, shaders, descriptors, uploads.
//
// Conventions that keep cases short (documented in CASE_FORMAT.md):
//  - every buffer and image also gets TRANSFER_SRC | TRANSFER_DST usage, so the
//    driver can upload and read back (turn off with "auto_usage": false);
//  - memory comes from the first type that is HOST_VISIBLE | HOST_COHERENT and
//    allowed by the requirements, else the first allowed type;
//  - every image is moved to VK_IMAGE_LAYOUT_GENERAL right after creation and
//    stays there unless a command's explicit barrier moves it.
#include "child.hpp"

#include <cmath>
#include <cstring>

namespace {

// ---------------------------------------------------------------- buffers

void op_buffer(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    VkBufferCreateInfo ci{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    ci.size = req_u64(op, "size", ctx);
    ci.usage = (VkBufferUsageFlags)parse_flags("VkBufferUsageFlagBits", "VK_BUFFER_USAGE_", op.value("usage", json()), ctx);
    if (opt_bool(op, "auto_usage", true)) ci.usage |= VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    ci.flags = (VkBufferCreateFlags)parse_flags("VkBufferCreateFlagBits", "VK_BUFFER_CREATE_", op.value("flags", json()), ctx);
    ci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    Buffer b;
    b.size = ci.size;
    if (VKC(st, vkCreateBuffer, st.device, &ci, nullptr, &b.buf) != VK_SUCCESS) return;
    VkMemoryRequirements mr{};
    vkGetBufferMemoryRequirements(st.device, b.buf, &mr);
    bool hv = false;
    uint32_t type = st.memory_type(mr.memoryTypeBits,
                                   VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, &hv, &b.coherent);
    VkMemoryAllocateFlagsInfo fi{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO};
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, type};
    if (ci.usage & VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT) {
        fi.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
        ai.pNext = &fi;
    }
    if (VKC(st, vkAllocateMemory, st.device, &ai, nullptr, &b.mem) != VK_SUCCESS) {
        vkDestroyBuffer(st.device, b.buf, nullptr);
        return;
    }
    if (VKC(st, vkBindBufferMemory, st.device, b.buf, b.mem, 0) != VK_SUCCESS) return;
    if (hv && VKC(st, vkMapMemory, st.device, b.mem, 0, VK_WHOLE_SIZE, 0, &b.map) != VK_SUCCESS) b.map = nullptr;
    if (b.map && opt_bool(op, "zero", true)) std::memset(b.map, 0, (size_t)b.size);
    st.buffers[name] = b;
}

// A staging buffer the driver itself owns (not declared by the case).
bool make_staging(State& st, VkDeviceSize size, Buffer* out) {
    VkBufferCreateInfo ci{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    ci.size = size;
    ci.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    if (VKC(st, vkCreateBuffer, st.device, &ci, nullptr, &out->buf) != VK_SUCCESS) return false;
    VkMemoryRequirements mr{};
    vkGetBufferMemoryRequirements(st.device, out->buf, &mr);
    bool hv = false;
    uint32_t type = st.memory_type(mr.memoryTypeBits,
                                   VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, &hv, &out->coherent);
    if (!hv) return false;
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, type};
    if (VKC(st, vkAllocateMemory, st.device, &ai, nullptr, &out->mem) != VK_SUCCESS) return false;
    if (VKC(st, vkBindBufferMemory, st.device, out->buf, out->mem, 0) != VK_SUCCESS) return false;
    if (VKC(st, vkMapMemory, st.device, out->mem, 0, VK_WHOLE_SIZE, 0, &out->map) != VK_SUCCESS) return false;
    out->size = size;
    return true;
}

void free_staging(State& st, Buffer& b) {
    if (b.map) vkUnmapMemory(st.device, b.mem);
    if (b.buf) vkDestroyBuffer(st.device, b.buf, nullptr);
    if (b.mem) vkFreeMemory(st.device, b.mem, nullptr);
    b = Buffer{};
}

void flush_if_needed(State& st, const Buffer& b) {
    if (b.coherent || !b.map) return;
    VkMappedMemoryRange r{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, nullptr, b.mem, 0, VK_WHOLE_SIZE};
    VKC(st, vkFlushMappedMemoryRanges, st.device, 1, &r);
}

// Deterministic tiny nudges for the oracle's perturbation runs (never for a candidate).
void perturb(State& st, const json& op, std::vector<uint8_t>& data) {
    if (st.perturb.empty() || !op.contains("perturb")) return;
    const json& p = op["perturb"];
    std::string cls = opt_str(p, "class", "");
    bool pos = st.perturb == "vtxjitter" && cls == "position";
    bool tex = st.perturb == "texcoord_ulp" && cls == "texcoord";
    if (!pos && !tex) return;
    uint64_t stride = opt_u64(p, "stride", 16), offset = opt_u64(p, "offset", 0), comps = opt_u64(p, "components", 2);
    uint64_t h = 0x9e3779b97f4a7c15ull ^ (uint64_t)st.op_index;
    for (uint64_t base = offset; base + comps * 4 <= data.size(); base += stride) {
        for (uint64_t c = 0; c < comps; ++c) {
            float f;
            std::memcpy(&f, &data[base + c * 4], 4);
            h ^= h << 13; h ^= h >> 7; h ^= h << 17;
            bool up = h & 1;
            if (pos) f += (up ? 1.0f : -1.0f) * std::ldexp(1.0f, -17);      // below 1/256 px at <= 256 px
            else f = std::nextafter(f, up ? INFINITY : -INFINITY);          // one ULP
            std::memcpy(&data[base + c * 4], &f, 4);
        }
        if (stride == 0) break;
    }
}

void op_upload(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "buffer", ctx);
    std::vector<uint8_t> data = data_bytes(op, st, ctx);
    Buffer& b = st.get(st.buffers, name, "buffer");
    uint64_t off = opt_u64(op, "offset", 0);
    if (off + data.size() > b.size) throw CaseError(ctx + ": upload past the end of '" + name + "'");
    perturb(st, op, data);
    if (data.empty()) return;
    if (b.map) {
        std::memcpy(static_cast<uint8_t*>(b.map) + off, data.data(), data.size());
        flush_if_needed(st, b);
        return;
    }
    Buffer s;
    if (!make_staging(st, data.size(), &s)) { free_staging(st, s); throw Skip("staging buffer failed"); }
    std::memcpy(s.map, data.data(), data.size());
    flush_if_needed(st, s);
    st.one_shot([&](VkCommandBuffer cb) {
        VkBufferCopy c{0, off, data.size()};
        vkCmdCopyBuffer(cb, s.buf, b.buf, 1, &c);
    });
    free_staging(st, s);
}

// ---------------------------------------------------------------- images

void op_image(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    Image im;
    im.def = st.plan.images.at(name);
    im.aspects = format_aspects(im.def.format);
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.flags = (VkImageCreateFlags)parse_flags("VkImageCreateFlagBits", "VK_IMAGE_CREATE_", op.value("flags", json()), ctx);
    ci.imageType = im.def.type;
    ci.format = im.def.format;
    ci.extent = {im.def.width, im.def.height, im.def.depth};
    ci.mipLevels = im.def.mips;
    ci.arrayLayers = im.def.layers;
    ci.samples = (VkSampleCountFlagBits)im.def.samples;
    ci.tiling = (VkImageTiling)parse_enum("VkImageTiling", "VK_IMAGE_TILING_", op.value("tiling", json("optimal")), ctx);
    ci.usage = (VkImageUsageFlags)parse_flags("VkImageUsageFlagBits", "VK_IMAGE_USAGE_", op.value("usage", json()), ctx);
    if (opt_bool(op, "auto_usage", true)) ci.usage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    ci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (VKC(st, vkCreateImage, st.device, &ci, nullptr, &im.img) != VK_SUCCESS) return;
    VkMemoryRequirements mr{};
    vkGetImageMemoryRequirements(st.device, im.img, &mr);
    uint32_t type = st.memory_type(mr.memoryTypeBits, 0, nullptr, nullptr);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, type};
    if (VKC(st, vkAllocateMemory, st.device, &ai, nullptr, &im.mem) != VK_SUCCESS) {
        vkDestroyImage(st.device, im.img, nullptr);
        return;
    }
    if (VKC(st, vkBindImageMemory, st.device, im.img, im.mem, 0) != VK_SUCCESS) return;
    bool ok = st.one_shot([&](VkCommandBuffer cb) {
        VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
        b.srcAccessMask = 0;
        b.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT;
        b.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
        b.newLayout = VK_IMAGE_LAYOUT_GENERAL;
        b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        b.image = im.img;
        b.subresourceRange = full_range(im);
        vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr,
                             0, nullptr, 1, &b);
    });
    if (!ok) st.note("error", "initial layout transition failed");
    st.images[name] = im;
}

VkImageAspectFlags parse_aspects(const json& v, VkImageAspectFlags dflt, const std::string& ctx) {
    if (v.is_null()) return dflt;
    return (VkImageAspectFlags)parse_flags("VkImageAspectFlagBits", "VK_IMAGE_ASPECT_", v, ctx);
}

void op_upload_image(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "image", ctx);
    std::vector<uint8_t> data = data_bytes(op, st, ctx);
    Image& im = st.get(st.images, name, "image");
    VkImageAspectFlags aspect = parse_aspects(op.value("aspect", json()), im.aspects == (VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT)
                                                  ? VK_IMAGE_ASPECT_DEPTH_BIT : im.aspects, ctx);
    uint32_t mip = (uint32_t)opt_u64(op, "mip", 0);
    uint32_t base_layer = (uint32_t)opt_u64(op, "base_layer", 0);
    uint32_t layers = (uint32_t)opt_u64(op, "layers", 1);
    if (mip >= im.def.mips || base_layer + layers > im.def.layers) throw CaseError(ctx + ": mip/layer out of range");
    uint32_t w = std::max(1u, im.def.width >> mip), h = std::max(1u, im.def.height >> mip), d = std::max(1u, im.def.depth >> mip);
    VkOffset3D off{0, 0, 0};
    VkExtent3D ext{w, h, d};
    if (op.contains("offset")) { auto o = op["offset"]; off = {o[0].get<int32_t>(), o.size() > 1 ? o[1].get<int32_t>() : 0, o.size() > 2 ? o[2].get<int32_t>() : 0}; }
    if (op.contains("extent")) { auto e = op["extent"]; ext = {e[0].get<uint32_t>(), e.size() > 1 ? e[1].get<uint32_t>() : 1, e.size() > 2 ? e[2].get<uint32_t>() : 1}; }
    // size check: whole blocks for compressed formats, the aspect's texel size otherwise
    const vkt::FormatInfo* fi = vkt::format_info(im.def.format);
    uint64_t need = 0;
    if (fi && (fi->compressed[0] || fi->block_extent[0] > 1)) {
        uint64_t bx = (ext.width + fi->block_extent[0] - 1) / fi->block_extent[0];
        uint64_t by = (ext.height + fi->block_extent[1] - 1) / fi->block_extent[1];
        need = bx * by * ext.depth * (uint64_t)fi->block_size * layers;
    } else {
        uint32_t tb = readback_texel_bytes(im.def.format, aspect);
        if (!tb) throw CaseError(ctx + ": cannot upload this format/aspect");
        need = (uint64_t)tb * ext.width * ext.height * ext.depth * layers;
    }
    if (data.size() < need) throw CaseError(ctx + ": data has " + std::to_string(data.size()) + " bytes, the region needs " + std::to_string(need));
    Buffer s;
    if (!make_staging(st, need, &s)) { free_staging(st, s); throw Skip("staging buffer failed"); }
    std::memcpy(s.map, data.data(), need);
    flush_if_needed(st, s);
    st.one_shot([&](VkCommandBuffer cb) {
        VkBufferImageCopy c{};
        c.imageSubresource = {aspect, mip, base_layer, layers};
        c.imageOffset = off;
        c.imageExtent = ext;
        vkCmdCopyBufferToImage(cb, s.buf, im.img, VK_IMAGE_LAYOUT_GENERAL, 1, &c);
    });
    free_staging(st, s);
}

VkComponentSwizzle parse_swizzle(const json& v, const std::string& ctx) {
    return (VkComponentSwizzle)parse_enum("VkComponentSwizzle", "VK_COMPONENT_SWIZZLE_", v, ctx);
}

void op_view(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    std::string iname = req_str(op, "image", ctx);
    Image& im = st.get(st.images, iname, "image");
    VkImageViewCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    ci.image = im.img;
    std::string vt = opt_str(op, "view_type", im.def.type == VK_IMAGE_TYPE_3D ? "3d" : im.def.type == VK_IMAGE_TYPE_1D ? "1d" : "2d");
    static const std::map<std::string, VkImageViewType> types = {
        {"1d", VK_IMAGE_VIEW_TYPE_1D}, {"2d", VK_IMAGE_VIEW_TYPE_2D}, {"3d", VK_IMAGE_VIEW_TYPE_3D},
        {"cube", VK_IMAGE_VIEW_TYPE_CUBE}, {"1d_array", VK_IMAGE_VIEW_TYPE_1D_ARRAY},
        {"2d_array", VK_IMAGE_VIEW_TYPE_2D_ARRAY}, {"cube_array", VK_IMAGE_VIEW_TYPE_CUBE_ARRAY}};
    if (!types.count(vt)) throw CaseError(ctx + ": unknown view_type '" + vt + "'");
    ci.viewType = types.at(vt);
    ci.format = op.contains("format") ? parse_format(op["format"], ctx) : im.def.format;
    if (op.contains("swizzle")) {
        const json& s = op["swizzle"];
        ci.components = {parse_swizzle(s[0], ctx), parse_swizzle(s[1], ctx), parse_swizzle(s[2], ctx), parse_swizzle(s[3], ctx)};
    }
    VkImageAspectFlags dflt = im.aspects == (VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT) && vt != "attachment"
                                  ? im.aspects : im.aspects;
    ci.subresourceRange.aspectMask = parse_aspects(op.value("aspect", json()), dflt, ctx);
    ci.subresourceRange.baseMipLevel = (uint32_t)opt_u64(op, "base_mip", 0);
    ci.subresourceRange.levelCount = (uint32_t)opt_u64(op, "mips", VK_REMAINING_MIP_LEVELS);
    ci.subresourceRange.baseArrayLayer = (uint32_t)opt_u64(op, "base_layer", 0);
    ci.subresourceRange.layerCount = (uint32_t)opt_u64(op, "layers", VK_REMAINING_ARRAY_LAYERS);
    View v;
    v.image = iname;
    if (VKC(st, vkCreateImageView, st.device, &ci, nullptr, &v.view) != VK_SUCCESS) return;
    st.views[name] = v;
}

void op_buffer_view(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    Buffer& b = st.get(st.buffers, req_str(op, "buffer", ctx), "buffer");
    VkBufferViewCreateInfo ci{VK_STRUCTURE_TYPE_BUFFER_VIEW_CREATE_INFO};
    ci.buffer = b.buf;
    ci.format = parse_format(req(op, "format", ctx), ctx);
    ci.offset = opt_u64(op, "offset", 0);
    ci.range = opt_u64(op, "range", VK_WHOLE_SIZE);
    VkBufferView v = VK_NULL_HANDLE;
    if (VKC(st, vkCreateBufferView, st.device, &ci, nullptr, &v) != VK_SUCCESS) return;
    st.buffer_views[name] = v;
}

void op_sampler(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    VkSamplerCreateInfo ci{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    auto filt = [&](const char* k) { return (VkFilter)parse_enum("VkFilter", "VK_FILTER_", op.value(k, json("nearest")), ctx); };
    auto addr = [&](const char* k) {
        return (VkSamplerAddressMode)parse_enum("VkSamplerAddressMode", "VK_SAMPLER_ADDRESS_MODE_", op.value(k, json("repeat")), ctx);
    };
    ci.magFilter = filt("mag");
    ci.minFilter = filt("min");
    ci.mipmapMode = (VkSamplerMipmapMode)parse_enum("VkSamplerMipmapMode", "VK_SAMPLER_MIPMAP_MODE_", op.value("mipmap", json("nearest")), ctx);
    ci.addressModeU = addr("address_u");
    ci.addressModeV = addr("address_v");
    ci.addressModeW = addr("address_w");
    ci.mipLodBias = (float)opt_f64(op, "lod_bias", 0.0);
    ci.anisotropyEnable = op.contains("anisotropy");
    ci.maxAnisotropy = (float)opt_f64(op, "anisotropy", 1.0);
    ci.compareEnable = op.contains("compare");
    if (ci.compareEnable) ci.compareOp = (VkCompareOp)parse_enum("VkCompareOp", "VK_COMPARE_OP_", op["compare"], ctx);
    ci.minLod = (float)opt_f64(op, "min_lod", 0.0);
    ci.maxLod = (float)opt_f64(op, "max_lod", VK_LOD_CLAMP_NONE);
    ci.borderColor = (VkBorderColor)parse_enum("VkBorderColor", "VK_BORDER_COLOR_", op.value("border", json("float_transparent_black")), ctx);
    ci.unnormalizedCoordinates = opt_bool(op, "unnormalized", false);
    VkSampler s = VK_NULL_HANDLE;
    if (VKC(st, vkCreateSampler, st.device, &ci, nullptr, &s) != VK_SUCCESS) return;
    st.samplers[name] = s;
}

void op_shader(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    std::vector<uint8_t> code = data_bytes(op, st, ctx);
    st.need_device();
    if (code.size() % 4 || code.empty()) throw CaseError(ctx + ": SPIR-V size must be a non-zero multiple of 4");
    VkShaderModuleCreateInfo ci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    ci.codeSize = code.size();
    ci.pCode = reinterpret_cast<const uint32_t*>(code.data());
    VkShaderModule m = VK_NULL_HANDLE;
    if (VKC(st, vkCreateShaderModule, st.device, &ci, nullptr, &m) != VK_SUCCESS) return;
    st.shaders[name] = m;
}

// ---------------------------------------------------------------- descriptors

VkDescriptorType parse_dtype(const json& v, const std::string& ctx) {
    return (VkDescriptorType)parse_enum("VkDescriptorType", "VK_DESCRIPTOR_TYPE_", v, ctx);
}

void op_desc_layout(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    DescLayout dl;
    std::vector<VkDescriptorBindingFlags> bflags;
    std::vector<std::vector<VkSampler>> immutables;
    const json& bs = req(op, "bindings", ctx);
    immutables.reserve(bs.size());
    bool any_flags = false;
    for (const json& b : bs) {
        VkDescriptorSetLayoutBinding lb{};
        lb.binding = (uint32_t)req_u64(b, "binding", ctx);
        lb.descriptorType = parse_dtype(req(b, "type", ctx), ctx);
        lb.descriptorCount = (uint32_t)opt_u64(b, "count", 1);
        lb.stageFlags = parse_stages(b.value("stages", json("all")), ctx);
        immutables.emplace_back();
        if (b.contains("immutable_samplers")) {
            for (const json& s : b["immutable_samplers"]) immutables.back().push_back(st.get(st.samplers, s.get<std::string>(), "sampler"));
            lb.pImmutableSamplers = immutables.back().data();
        }
        VkDescriptorBindingFlags f = (VkDescriptorBindingFlags)parse_flags("VkDescriptorBindingFlagBits", "VK_DESCRIPTOR_BINDING_",
                                                                           b.value("flags", json()), ctx);
        any_flags |= f != 0;
        bflags.push_back(f);
        dl.bindings.push_back(lb);
    }
    VkDescriptorSetLayoutBindingFlagsCreateInfo bfi{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO};
    bfi.bindingCount = (uint32_t)bflags.size();
    bfi.pBindingFlags = bflags.data();
    VkDescriptorSetLayoutCreateInfo ci{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    ci.pNext = any_flags ? &bfi : nullptr;
    ci.flags = (VkDescriptorSetLayoutCreateFlags)parse_flags("VkDescriptorSetLayoutCreateFlagBits", "VK_DESCRIPTOR_SET_LAYOUT_CREATE_",
                                                             op.value("flags", json()), ctx);
    dl.update_after_bind = ci.flags & VK_DESCRIPTOR_SET_LAYOUT_CREATE_UPDATE_AFTER_BIND_POOL_BIT;
    ci.bindingCount = (uint32_t)dl.bindings.size();
    ci.pBindings = dl.bindings.data();
    if (VKC(st, vkCreateDescriptorSetLayout, st.device, &ci, nullptr, &dl.layout) != VK_SUCCESS) return;
    for (auto& b : dl.bindings) b.pImmutableSamplers = nullptr;   // pointers into `immutables` die here
    st.desc_layouts[name] = dl;
}

void op_pipeline_layout(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    std::vector<VkDescriptorSetLayout> sets;
    if (op.contains("set_layouts"))
        for (const json& s : op["set_layouts"]) sets.push_back(st.get(st.desc_layouts, s.get<std::string>(), "desc_layout").layout);
    std::vector<VkPushConstantRange> pcs;
    if (op.contains("push_constants"))
        for (const json& p : op["push_constants"])
            pcs.push_back({parse_stages(p.value("stages", json("all")), ctx), (uint32_t)opt_u64(p, "offset", 0),
                           (uint32_t)req_u64(p, "size", ctx)});
    VkPipelineLayoutCreateInfo ci{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    ci.setLayoutCount = (uint32_t)sets.size();
    ci.pSetLayouts = sets.data();
    ci.pushConstantRangeCount = (uint32_t)pcs.size();
    ci.pPushConstantRanges = pcs.data();
    VkPipelineLayout pl = VK_NULL_HANDLE;
    if (VKC(st, vkCreatePipelineLayout, st.device, &ci, nullptr, &pl) != VK_SUCCESS) return;
    st.pipeline_layouts[name] = pl;
}

void write_descriptors(State& st, VkDescriptorSet set, const json& writes, const std::string& ctx) {
    // Storage for the infos must outlive the vkUpdateDescriptorSets call.
    std::vector<std::vector<VkDescriptorBufferInfo>> binfos;
    std::vector<std::vector<VkDescriptorImageInfo>> iinfos;
    std::vector<std::vector<VkBufferView>> tviews;
    std::vector<VkWriteDescriptorSet> ws;
    binfos.reserve(writes.size());
    iinfos.reserve(writes.size());
    tviews.reserve(writes.size());
    for (const json& w : writes) {
        VkWriteDescriptorSet x{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        x.dstSet = set;
        x.dstBinding = (uint32_t)req_u64(w, "binding", ctx);
        x.dstArrayElement = (uint32_t)opt_u64(w, "array_element", 0);
        x.descriptorType = parse_dtype(req(w, "type", ctx), ctx);
        binfos.emplace_back();
        iinfos.emplace_back();
        tviews.emplace_back();
        if (w.contains("buffers")) {
            for (const json& b : w["buffers"]) {
                Buffer& buf = st.get(st.buffers, req_str(b, "buffer", ctx), "buffer");
                binfos.back().push_back({buf.buf, opt_u64(b, "offset", 0), opt_u64(b, "range", VK_WHOLE_SIZE)});
            }
            x.descriptorCount = (uint32_t)binfos.back().size();
            x.pBufferInfo = binfos.back().data();
        } else if (w.contains("images")) {
            for (const json& im : w["images"]) {
                VkDescriptorImageInfo ii{};
                if (im.contains("sampler")) ii.sampler = st.get(st.samplers, im["sampler"].get<std::string>(), "sampler");
                if (im.contains("view")) ii.imageView = st.get(st.views, im["view"].get<std::string>(), "view").view;
                ii.imageLayout = parse_layout(im.value("layout", json("general")), ctx);
                iinfos.back().push_back(ii);
            }
            x.descriptorCount = (uint32_t)iinfos.back().size();
            x.pImageInfo = iinfos.back().data();
        } else if (w.contains("texel_buffers")) {
            for (const json& v : w["texel_buffers"]) tviews.back().push_back(st.get(st.buffer_views, v.get<std::string>(), "buffer_view"));
            x.descriptorCount = (uint32_t)tviews.back().size();
            x.pTexelBufferView = tviews.back().data();
        } else {
            throw CaseError(ctx + ": a write needs 'buffers', 'images' or 'texel_buffers'");
        }
        if (x.descriptorCount == 0) throw CaseError(ctx + ": empty descriptor write");
        ws.push_back(x);
    }
    vkUpdateDescriptorSets(st.device, (uint32_t)ws.size(), ws.data(), 0, nullptr);
}

void op_desc_set(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    std::string lname = req_str(op, "layout", ctx);
    DescLayout& dl = st.get(st.desc_layouts, lname, "desc_layout");
    std::map<VkDescriptorType, uint32_t> counts;
    for (auto& b : dl.bindings) counts[b.descriptorType] += std::max(1u, b.descriptorCount);
    std::vector<VkDescriptorPoolSize> sizes;
    for (auto& [t, n] : counts) sizes.push_back({t, n});
    VkDescriptorPoolCreateInfo pci{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    pci.flags = dl.update_after_bind ? VK_DESCRIPTOR_POOL_CREATE_UPDATE_AFTER_BIND_BIT : 0;
    pci.maxSets = 1;
    pci.poolSizeCount = (uint32_t)sizes.size();
    pci.pPoolSizes = sizes.data();
    DescSet ds;
    ds.layout = lname;
    if (VKC(st, vkCreateDescriptorPool, st.device, &pci, nullptr, &ds.pool) != VK_SUCCESS) return;
    VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, nullptr, ds.pool, 1, &dl.layout};
    if (VKC(st, vkAllocateDescriptorSets, st.device, &ai, &ds.set) != VK_SUCCESS) return;
    st.desc_sets[name] = ds;
    if (op.contains("writes")) write_descriptors(st, ds.set, op["writes"], ctx);
}

void op_desc_write(State& st, const json& op, const std::string& ctx) {
    DescSet& ds = st.get(st.desc_sets, req_str(op, "set", ctx), "desc_set");
    write_descriptors(st, ds.set, req(op, "writes", ctx), ctx);
}

}  // namespace

// Used by the output ops for readback.
bool vkr_make_staging(State& st, VkDeviceSize size, Buffer* out) { return make_staging(st, size, out); }
void vkr_free_staging(State& st, Buffer& b) { free_staging(st, b); }

void register_resource_ops(std::map<std::string, OpFn>& ops) {
    ops["buffer"] = op_buffer;
    ops["upload"] = op_upload;
    ops["image"] = op_image;
    ops["upload_image"] = op_upload_image;
    ops["view"] = op_view;
    ops["buffer_view"] = op_buffer_view;
    ops["sampler"] = op_sampler;
    ops["shader"] = op_shader;
    ops["desc_layout"] = op_desc_layout;
    ops["pipeline_layout"] = op_pipeline_layout;
    ops["desc_set"] = op_desc_set;
    ops["desc_write"] = op_desc_write;
}
