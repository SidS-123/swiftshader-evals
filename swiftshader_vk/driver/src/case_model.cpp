// vkreplay: the case plan shared by the parent and the child.
#include "case_model.hpp"

#include <set>

bool safe_name(const std::string& s) {
    if (s.empty() || s.size() > 64 || s[0] == '.') return false;
    for (char c : s)
        if (!(std::isalnum((unsigned char)c) || c == '_' || c == '-' || c == '.')) return false;
    return true;
}

uint32_t elem_bytes(const std::string& e) {
    static const std::map<std::string, uint32_t> sizes = {
        {"u8", 1}, {"i8", 1}, {"u16", 2}, {"i16", 2}, {"f16", 2}, {"u32", 4}, {"i32", 4},
        {"f32", 4}, {"u64", 8}, {"i64", 8}, {"f64", 8}};
    auto it = sizes.find(e);
    return it == sizes.end() ? 0 : it->second;
}

std::string aspect_name(VkImageAspectFlags a) {
    switch (a) {
        case VK_IMAGE_ASPECT_COLOR_BIT: return "color";
        case VK_IMAGE_ASPECT_DEPTH_BIT: return "depth";
        case VK_IMAGE_ASPECT_STENCIL_BIT: return "stencil";
        default: return std::to_string(a);
    }
}

static VkImageAspectFlags parse_aspect_one(const json& v, const std::string& ctx) {
    std::string s = v.is_string() ? v.get<std::string>() : "";
    if (s == "color") return VK_IMAGE_ASPECT_COLOR_BIT;
    if (s == "depth") return VK_IMAGE_ASPECT_DEPTH_BIT;
    if (s == "stencil") return VK_IMAGE_ASPECT_STENCIL_BIT;
    throw CaseError(ctx + ": aspect must be color, depth or stencil");
}

static std::vector<SnapItem> plan_items(const json& list, const CasePlan& p, const std::string& ctx) {
    if (!list.is_array() || list.empty() || list.size() > 16)
        throw CaseError(ctx + ": 'items' must be a list of 1..16 items");
    std::vector<SnapItem> items;
    std::set<std::string> names;
    uint64_t total = 0;
    for (const json& it : list) {
        SnapItem s;
        s.name = req_str(it, "name", ctx);
        if (!safe_name(s.name) || !names.insert(s.name).second)
            throw CaseError(ctx + ": item name '" + s.name + "' is not a unique safe name");
        if (it.contains("buffer")) {
            s.is_buffer = true;
            s.resource = req_str(it, "buffer", ctx);
            auto b = p.buffers.find(s.resource);
            if (b == p.buffers.end()) throw CaseError(ctx + ": unknown buffer '" + s.resource + "'");
            s.offset = opt_u64(it, "offset", 0);
            s.elem = opt_str(it, "elem", "u8");
            uint32_t eb = elem_bytes(s.elem);
            if (!eb) throw CaseError(ctx + ": unknown elem '" + s.elem + "'");
            if (s.offset > b->second) throw CaseError(ctx + ": offset past the end of '" + s.resource + "'");
            s.bytes = opt_u64(it, "size", b->second - s.offset);
            if (s.offset + s.bytes > b->second || s.bytes % eb)
                throw CaseError(ctx + ": item '" + s.name + "' size must fit the buffer and be a multiple of its elem");
        } else {
            s.resource = req_str(it, "image", ctx);
            auto im = p.images.find(s.resource);
            if (im == p.images.end()) throw CaseError(ctx + ": unknown image '" + s.resource + "'");
            const ImageDef& d = im->second;
            if (d.samples != 1) throw CaseError(ctx + ": '" + s.resource + "' is multisampled; resolve it first");
            s.format = d.format;
            s.aspect = it.contains("aspect") ? parse_aspect_one(it["aspect"], ctx) : VK_IMAGE_ASPECT_COLOR_BIT;
            if (!(format_aspects(d.format) & s.aspect))
                throw CaseError(ctx + ": '" + s.resource + "' has no " + aspect_name(s.aspect) + " aspect");
            s.mip = (uint32_t)opt_u64(it, "mip", 0);
            if (s.mip >= d.mips) throw CaseError(ctx + ": mip out of range");
            s.base_layer = (uint32_t)opt_u64(it, "base_layer", 0);
            s.layers = (uint32_t)opt_u64(it, "layers", d.layers - std::min(s.base_layer, d.layers));
            if (s.layers == 0 || s.base_layer + s.layers > d.layers) throw CaseError(ctx + ": layer range out of range");
            s.width = std::max(1u, d.width >> s.mip);
            s.height = std::max(1u, d.height >> s.mip);
            s.depth = std::max(1u, d.depth >> s.mip);
            s.texel_bytes = readback_texel_bytes(d.format, s.aspect);
            if (!s.texel_bytes) throw CaseError(ctx + ": cannot read back this format/aspect (compressed or multi-planar)");
            s.bytes = (uint64_t)s.texel_bytes * s.width * s.height * s.depth * s.layers;
        }
        total += s.bytes;
        items.push_back(s);
    }
    if (total > (256ull << 20)) throw CaseError(ctx + ": snapshot larger than 256 MiB");
    return items;
}

static const std::set<std::string> kEventOps = {
    "query", "enumerate", "image_format_props", "read_queries", "fence_status", "event_status",
    "semaphore_value", "wait_fence", "wait_semaphores"};

CasePlan plan_case(const json& doc) {
    if (!doc.is_object()) throw CaseError("a case must be a JSON object");
    CasePlan p;
    p.name = opt_str(doc, "name", "case");
    const json& ops = req(doc, "ops", "case");
    if (!ops.is_array()) throw CaseError("case: 'ops' must be a list");
    if (ops.size() > 100000) throw CaseError("case: more than 100000 ops");
    std::set<std::string> out_names;
    for (size_t i = 0; i < ops.size(); ++i) {
        const json& op = ops[i];
        std::string ctx = "op " + std::to_string(i);
        OpPlan o;
        o.op = req_str(op, "op", ctx);
        ctx += " (" + o.op + ")";
        o.name = opt_str(op, "name", "");
        if (o.op == "image") {
            ImageDef d;
            d.format = parse_format(req(op, "format", ctx), ctx);
            std::string t = opt_str(op, "type", "2d");
            d.type = t == "1d" ? VK_IMAGE_TYPE_1D : t == "3d" ? VK_IMAGE_TYPE_3D : VK_IMAGE_TYPE_2D;
            const json& ext = req(op, "extent", ctx);
            if (!ext.is_array() || ext.empty() || ext.size() > 3) throw CaseError(ctx + ": extent must be [w], [w,h] or [w,h,d]");
            d.width = ext[0].get<uint32_t>();
            d.height = ext.size() > 1 ? ext[1].get<uint32_t>() : 1;
            d.depth = ext.size() > 2 ? ext[2].get<uint32_t>() : 1;
            if (!d.width || !d.height || !d.depth || d.width > 16384 || d.height > 16384 || d.depth > 2048)
                throw CaseError(ctx + ": extent out of range");
            d.mips = (uint32_t)opt_u64(op, "mips", 1);
            d.layers = (uint32_t)opt_u64(op, "layers", 1);
            d.samples = (uint32_t)opt_u64(op, "samples", 1);
            if (!d.mips || !d.layers || !d.samples) throw CaseError(ctx + ": mips/layers/samples must be >= 1");
            if (!safe_name(o.name) || p.images.count(o.name)) throw CaseError(ctx + ": image needs a unique safe name");
            p.images[o.name] = d;
        } else if (o.op == "buffer") {
            uint64_t size = req_u64(op, "size", ctx);
            if (!size || size > (1ull << 31)) throw CaseError(ctx + ": size must be 1..2 GiB");
            if (!safe_name(o.name) || p.buffers.count(o.name)) throw CaseError(ctx + ": buffer needs a unique safe name");
            p.buffers[o.name] = size;
        } else if (o.op == "snapshot") {
            o.out = OutKind::Snapshot;
            o.items = plan_items(req(op, "items", ctx), p, ctx);
        } else if (o.op == "run") {
            o.out = OutKind::Run;
            const json& snap = req(op, "snapshot", ctx);
            o.items = plan_items(req(snap, "items", ctx), p, ctx);
            std::string sn = req_str(snap, "name", ctx);
            if (!safe_name(sn) || !out_names.insert(sn).second) throw CaseError(ctx + ": snapshot needs a unique safe name");
            o.snap_name = sn;
            p.snapshot_count++;
        } else if (kEventOps.count(o.op)) {
            o.out = OutKind::Event;
        }
        if (o.out == OutKind::Snapshot || o.out == OutKind::Event || o.out == OutKind::Run) {
            if (!safe_name(o.name)) throw CaseError(ctx + ": needs a safe 'name' ([A-Za-z0-9_.-], <= 64)");
            if (!out_names.insert(o.name).second) throw CaseError(ctx + ": output name '" + o.name + "' is used twice");
        }
        if (o.out == OutKind::Snapshot) p.snapshot_count++;
        p.ops.push_back(std::move(o));
    }
    if (p.snapshot_count > 64) throw CaseError("case: more than 64 snapshots");
    return p;
}
