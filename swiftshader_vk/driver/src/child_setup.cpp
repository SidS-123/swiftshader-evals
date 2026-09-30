// vkreplay child: instance, device and physical-device queries.
#include "child.hpp"

#include <algorithm>
#include <cstring>
#include <memory>

namespace {

uint32_t parse_api_version(const json& v, const std::string& ctx) {
    if (v.is_number_integer()) return v.get<uint32_t>();
    std::string s = v.is_string() ? v.get<std::string>() : "";
    int maj = 0, min = 0, patch = 0;
    if (std::sscanf(s.c_str(), "%d.%d.%d", &maj, &min, &patch) < 2) throw CaseError(ctx + ": api_version like \"1.3\"");
    return VK_MAKE_API_VERSION(0, maj, min, patch);
}

std::string version_text(uint32_t v) {
    return std::to_string(VK_API_VERSION_MAJOR(v)) + "." + std::to_string(VK_API_VERSION_MINOR(v)) + "." +
           std::to_string(VK_API_VERSION_PATCH(v));
}

VKAPI_ATTR VkBool32 VKAPI_CALL on_debug(VkDebugUtilsMessageSeverityFlagBitsEXT sev, VkDebugUtilsMessageTypeFlagsEXT,
                                        const VkDebugUtilsMessengerCallbackDataEXT* data, void* user) {
    State* st = static_cast<State*>(user);
    if (sev & (VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT)) {
        std::string msg = data && data->pMessage ? data->pMessage : "";
        st->send({{"t", "validation"}, {"i", st->op_index},
                  {"level", sev & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT ? "error" : "warning"},
                  {"msg", msg}});
    }
    return VK_FALSE;
}

// A pNext chain of zeroed structs, each with its sType set.
struct Chain {
    std::vector<std::unique_ptr<uint8_t[]>> bufs;
    std::vector<const vkt::StructInfo*> infos;
    void* head = nullptr;
    void* add(const vkt::StructInfo* si) {
        auto b = std::make_unique<uint8_t[]>(si->size + 16);
        std::memset(b.get(), 0, si->size + 16);
        auto* base = reinterpret_cast<VkBaseOutStructure*>(b.get());
        base->sType = si->stype;
        base->pNext = static_cast<VkBaseOutStructure*>(head);
        head = b.get();
        infos.push_back(si);
        bufs.push_back(std::move(b));
        return head;
    }
};

std::vector<const vkt::StructInfo*> pick_structs(State& st, const json& sel, const std::string& category,
                                                const std::string& ctx) {
    std::vector<const vkt::StructInfo*> out;
    if (sel.is_string() && sel.get<std::string>() == "all") {
        for (const vkt::StructInfo* si : vkt::structs_of(category))
            if (si->stype && requirement_met(si->requirement, st.api_version, st.device_extensions)) out.push_back(si);
        return out;
    }
    if (!sel.is_array()) throw CaseError(ctx + ": expected \"all\" or a list of struct names");
    for (const json& n : sel) {
        const vkt::StructInfo* si = vkt::struct_info(n.get<std::string>());
        if (!si || std::string(si->category) != category) throw CaseError(ctx + ": unknown " + category + " struct " + n.dump());
        if (si->stype && requirement_met(si->requirement, st.api_version, st.device_extensions)) out.push_back(si);
    }
    return out;
}

// ---------------------------------------------------------------- instance

void op_instance(State& st, const json& op, const std::string& ctx) {
    if (st.instance_tried) throw CaseError(ctx + ": only one instance per case");
    st.instance_tried = true;
    uint32_t api = op.contains("api_version") ? parse_api_version(op["api_version"], ctx) : VK_API_VERSION_1_3;
    std::string app = opt_str(op, "app_name", "vkreplay");
    std::vector<std::string> exts, layers;
    if (op.contains("extensions")) for (const json& e : op["extensions"]) exts.push_back(e.get<std::string>());
    if (op.contains("layers")) for (const json& e : op["layers"]) layers.push_back(e.get<std::string>());
    if (st.validate) {
        layers.push_back("VK_LAYER_KHRONOS_validation");
        exts.push_back(VK_EXT_DEBUG_UTILS_EXTENSION_NAME);
    }
    std::vector<const char*> ep, lp;
    for (auto& e : exts) ep.push_back(e.c_str());
    for (auto& l : layers) lp.push_back(l.c_str());
    VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO, nullptr, app.c_str(), 1, "vkreplay", 1, api};
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ci.pApplicationInfo = &ai;
    ci.enabledExtensionCount = (uint32_t)ep.size();
    ci.ppEnabledExtensionNames = ep.data();
    ci.enabledLayerCount = (uint32_t)lp.size();
    ci.ppEnabledLayerNames = lp.data();
    VkDebugUtilsMessengerCreateInfoEXT dci{VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT};
    dci.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
    dci.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT;
    dci.pfnUserCallback = on_debug;
    dci.pUserData = &st;
    if (st.validate) ci.pNext = &dci;
    if (VKC(st, vkCreateInstance, &ci, nullptr, &st.instance) != VK_SUCCESS) {
        st.instance = VK_NULL_HANDLE;
        return;
    }
    if (st.validate) {
        auto create = (PFN_vkCreateDebugUtilsMessengerEXT)vkGetInstanceProcAddr(st.instance, "vkCreateDebugUtilsMessengerEXT");
        if (create) create(st.instance, &dci, nullptr, &st.messenger);
    }
    uint32_t n = 0;
    if (VKC(st, vkEnumeratePhysicalDevices, st.instance, &n, nullptr) != VK_SUCCESS || n == 0) return;
    std::vector<VkPhysicalDevice> devs(n);
    VkResult r = VKC(st, vkEnumeratePhysicalDevices, st.instance, &n, devs.data());
    if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || n == 0) return;
    uint32_t idx = (uint32_t)opt_u64(op, "device_index", 0);
    if (idx >= n) throw Skip("device_index " + std::to_string(idx) + " but only " + std::to_string(n) + " devices");
    st.phys = devs[idx];
    VkPhysicalDeviceProperties props{};
    vkGetPhysicalDeviceProperties(st.phys, &props);
    st.api_version = props.apiVersion;
    uint32_t en = 0;
    if (VKC(st, vkEnumerateDeviceExtensionProperties, st.phys, nullptr, &en, nullptr) == VK_SUCCESS) {
        std::vector<VkExtensionProperties> ext(en);
        if (VKC(st, vkEnumerateDeviceExtensionProperties, st.phys, nullptr, &en, ext.data()) == VK_SUCCESS)
            for (uint32_t i = 0; i < en; ++i) st.device_extensions.push_back(ext[i].extensionName);
    }
    vkGetPhysicalDeviceMemoryProperties(st.phys, &st.memprops);
}

// ---------------------------------------------------------------- device

void op_device(State& st, const json& op, const std::string& ctx) {
    if (st.device_tried) throw CaseError(ctx + ": only one device per case");
    st.device_tried = true;
    st.need_instance();
    std::vector<std::string> exts;
    if (op.contains("extensions")) for (const json& e : op["extensions"]) exts.push_back(e.get<std::string>());
    std::vector<const char*> ep;
    for (auto& e : exts) ep.push_back(e.c_str());

    // Features: {"VkPhysicalDeviceFeatures": {"fillModeNonSolid": true}, "VkPhysicalDeviceVulkan13Features": {...}}
    VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
    Chain chain;
    if (op.contains("features")) {
        const json& fs = op["features"];
        if (!fs.is_object()) throw CaseError(ctx + ": 'features' must be an object of struct -> {field: bool}");
        for (auto& [sname, fields] : fs.items()) {
            const vkt::StructInfo* si = vkt::struct_info(sname);
            if (!si || std::string(si->category) != "feature" || !si->bools)
                throw CaseError(ctx + ": unknown feature struct '" + sname + "'");
            uint8_t* base;
            if (sname == "VkPhysicalDeviceFeatures") base = reinterpret_cast<uint8_t*>(&f2.features);
            else base = static_cast<uint8_t*>(chain.add(si));
            for (auto& [field, val] : fields.items()) {
                const vkt::BoolField* bf = nullptr;
                for (size_t k = 0; k < si->nbools; ++k)
                    if (field == si->bools[k].name) bf = &si->bools[k];
                if (!bf) throw CaseError(ctx + ": " + sname + " has no feature '" + field + "'");
                *reinterpret_cast<VkBool32*>(base + bf->offset) = val.get<bool>() ? VK_TRUE : VK_FALSE;
            }
        }
    }
    f2.pNext = chain.head;

    uint32_t qn = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(st.phys, &qn, nullptr);
    std::vector<VkQueueFamilyProperties> qf(qn);
    vkGetPhysicalDeviceQueueFamilyProperties(st.phys, &qn, qf.data());
    if (qn == 0) throw Skip("the physical device reports no queue families");
    st.queue_family = 0;
    for (uint32_t i = 0; i < qn; ++i)
        if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { st.queue_family = i; break; }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, nullptr, 0, st.queue_family, 1, &prio};
    VkDeviceCreateInfo ci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    ci.pNext = &f2;
    ci.queueCreateInfoCount = 1;
    ci.pQueueCreateInfos = &qci;
    ci.enabledExtensionCount = (uint32_t)ep.size();
    ci.ppEnabledExtensionNames = ep.data();
    if (VKC(st, vkCreateDevice, st.phys, &ci, nullptr, &st.device) != VK_SUCCESS) {
        st.device = VK_NULL_HANDLE;
        return;
    }
    vkGetDeviceQueue(st.device, st.queue_family, 0, &st.queue);
    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, nullptr,
                                VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, st.queue_family};
    if (VKC(st, vkCreateCommandPool, st.device, &pci, nullptr, &st.cmd_pool) != VK_SUCCESS) st.cmd_pool = VK_NULL_HANDLE;
}

// ---------------------------------------------------------------- queries

json query_structs(State& st, const json& sel, const std::string& category, const std::string& ctx) {
    json out = json::object();
    Chain chain;
    for (const vkt::StructInfo* si : pick_structs(st, sel, category, ctx)) chain.add(si);
    if (category == "property") {
        VkPhysicalDeviceProperties2 p2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, chain.head};
        vkGetPhysicalDeviceProperties2(st.phys, &p2);
        const vkt::StructInfo* base = vkt::struct_info("VkPhysicalDeviceProperties");
        base->serialize(&p2.properties, out["VkPhysicalDeviceProperties"]);
    } else {
        VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, chain.head};
        vkGetPhysicalDeviceFeatures2(st.phys, &f2);
        const vkt::StructInfo* base = vkt::struct_info("VkPhysicalDeviceFeatures");
        base->serialize(&f2.features, out["VkPhysicalDeviceFeatures"]);
    }
    for (size_t k = 0; k < chain.infos.size(); ++k) {
        json j;
        chain.infos[k]->serialize(chain.bufs[k].get(), j);
        out[chain.infos[k]->name] = j;
    }
    return out;
}

void op_query(State& st, const json& op, const std::string& ctx) {
    const json& what = req(op, "what", ctx);
    json v = json::object();
    if (what.contains("api_version")) {
        uint32_t iv = 0;
        VKC(st, vkEnumerateInstanceVersion, &iv);
        v["instance_version"] = version_text(iv);
    }
    if (what.contains("instance_extensions")) {
        uint32_t n = 0;
        VKC(st, vkEnumerateInstanceExtensionProperties, nullptr, &n, nullptr);
        std::vector<VkExtensionProperties> e(std::max(n, 1u));   // never NULL: that would be a count query
        VKC(st, vkEnumerateInstanceExtensionProperties, nullptr, &n, e.data());
        json m = json::object();
        for (uint32_t i = 0; i < n; ++i) m[e[i].extensionName] = e[i].specVersion;
        v["instance_extensions"] = m;
    }
    bool needs_phys = false;
    for (const char* k : {"properties", "features", "formats", "memory", "queue_families", "device_extensions", "device_version"})
        if (what.contains(k)) needs_phys = true;
    if (needs_phys) {
        st.need_instance();
        if (what.contains("device_version")) v["device_version"] = version_text(st.api_version);
        if (what.contains("properties")) v["properties"] = query_structs(st, what["properties"], "property", ctx);
        if (what.contains("features")) v["features"] = query_structs(st, what["features"], "feature", ctx);
        if (what.contains("device_extensions")) {
            uint32_t n = 0;
            VKC(st, vkEnumerateDeviceExtensionProperties, st.phys, nullptr, &n, nullptr);
            std::vector<VkExtensionProperties> e(std::max(n, 1u));   // never NULL: that would be a count query
            VKC(st, vkEnumerateDeviceExtensionProperties, st.phys, nullptr, &n, e.data());
            json m = json::object();
            for (uint32_t i = 0; i < n; ++i) m[e[i].extensionName] = e[i].specVersion;
            v["device_extensions"] = m;
        }
        if (what.contains("memory")) {
            VkPhysicalDeviceMemoryProperties mp{};
            vkGetPhysicalDeviceMemoryProperties(st.phys, &mp);
            vkt::struct_info("VkPhysicalDeviceMemoryProperties")->serialize(&mp, v["memory"]);
        }
        if (what.contains("queue_families")) {
            uint32_t n = 0;
            vkGetPhysicalDeviceQueueFamilyProperties(st.phys, &n, nullptr);
            std::vector<VkQueueFamilyProperties> q(n);
            vkGetPhysicalDeviceQueueFamilyProperties(st.phys, &n, q.data());
            json a = json::array();
            for (uint32_t i = 0; i < n; ++i) {
                json j;
                vkt::struct_info("VkQueueFamilyProperties")->serialize(&q[i], j);
                a.push_back(j);
            }
            v["queue_families"] = a;
        }
        if (what.contains("formats")) {
            const json& sel = what["formats"];
            std::vector<VkFormat> fmts;
            if (sel.is_string() && sel.get<std::string>() == "all") {
                int64_t f;
                // "all": the core 1.0 formats, the 1.3-core 4444 formats, and the
                // maintenance5 formats only when the device advertises maintenance5
                // (querying them otherwise is invalid usage).
                for (int64_t x = 1; x < 200; ++x)
                    if (vkt::format_info((VkFormat)x)) fmts.push_back((VkFormat)x);
                std::vector<const char*> extra = {"VK_FORMAT_A4R4G4B4_UNORM_PACK16", "VK_FORMAT_A4B4G4R4_UNORM_PACK16"};
                if (std::find(st.device_extensions.begin(), st.device_extensions.end(), "VK_KHR_maintenance5") !=
                    st.device_extensions.end()) {
                    extra.push_back("VK_FORMAT_A1B5G5R5_UNORM_PACK16");
                    extra.push_back("VK_FORMAT_A8_UNORM");
                }
                for (const char* e : extra)
                    if (vkt::enum_value("VkFormat", e, &f)) fmts.push_back((VkFormat)f);
            } else {
                for (const json& n : sel) fmts.push_back(parse_format(n, ctx));
            }
            json m = json::object();
            const bool v13 = st.api_version >= VK_API_VERSION_1_3;
            for (VkFormat f : fmts) {
                VkFormatProperties3 p3{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3};
                VkFormatProperties2 p2{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2, v13 ? &p3 : nullptr};
                vkGetPhysicalDeviceFormatProperties2(st.phys, f, &p2);
                json j;
                vkt::struct_info("VkFormatProperties")->serialize(&p2.formatProperties, j);
                if (v13) {
                    json j3;
                    vkt::struct_info("VkFormatProperties3")->serialize(&p3, j3);
                    j["VkFormatProperties3"] = j3;
                }
                m[vkt::enum_name("VkFormat", f)] = j;
            }
            v["formats"] = m;
        }
    }
    st.event(v);
}

// {"op": "enumerate", "name": ..., "what": "instance_extensions" | "device_extensions" | "physical_devices"
//  | "instance_layers", "capacity": N}: the two-call idiom with a caller-sized array, so
// VK_INCOMPLETE is observable.
void op_enumerate(State& st, const json& op, const std::string& ctx) {
    std::string what = req_str(op, "what", ctx);
    json v = json::object();
    uint32_t total = 0;
    VkResult r1, r2 = VK_SUCCESS;
    json names = json::array();
    auto cap_of = [&](uint32_t t) { return (uint32_t)std::min<uint64_t>(opt_u64(op, "capacity", t), t + 16); };
    if (what == "instance_extensions") {
        r1 = VKC(st, vkEnumerateInstanceExtensionProperties, nullptr, &total, nullptr);
        uint32_t n = cap_of(total);
        std::vector<VkExtensionProperties> e(std::max(n, 1u));   // never NULL: that would be a count query
        r2 = VKC(st, vkEnumerateInstanceExtensionProperties, nullptr, &n, e.data());
        for (uint32_t i = 0; i < n; ++i) names.push_back(e[i].extensionName);
    } else if (what == "instance_layers") {
        r1 = VKC(st, vkEnumerateInstanceLayerProperties, &total, nullptr);
        uint32_t n = cap_of(total);
        std::vector<VkLayerProperties> e(std::max(n, 1u));
        r2 = VKC(st, vkEnumerateInstanceLayerProperties, &n, e.data());
        for (uint32_t i = 0; i < n; ++i) names.push_back(e[i].layerName);
    } else if (what == "device_extensions") {
        st.need_instance();
        r1 = VKC(st, vkEnumerateDeviceExtensionProperties, st.phys, nullptr, &total, nullptr);
        uint32_t n = cap_of(total);
        std::vector<VkExtensionProperties> e(std::max(n, 1u));   // never NULL: that would be a count query
        r2 = VKC(st, vkEnumerateDeviceExtensionProperties, st.phys, nullptr, &n, e.data());
        for (uint32_t i = 0; i < n; ++i) names.push_back(e[i].extensionName);
    } else if (what == "physical_devices") {
        if (!st.instance) { st.need_instance(); }
        r1 = VKC(st, vkEnumeratePhysicalDevices, st.instance, &total, nullptr);
        uint32_t n = cap_of(total);
        std::vector<VkPhysicalDevice> e(std::max(n, 1u));
        r2 = VKC(st, vkEnumeratePhysicalDevices, st.instance, &n, e.data());
        for (uint32_t i = 0; i < n; ++i) names.push_back(i);
    } else {
        throw CaseError(ctx + ": unknown 'what' '" + what + "'");
    }
    v["count_result"] = result_name(r1);
    v["total"] = total;
    v["result"] = result_name(r2);
    v["returned"] = names.size();
    v["names"] = names;
    st.event(v);
}

void op_image_format_props(State& st, const json& op, const std::string& ctx) {
    st.need_instance();
    VkFormat f = parse_format(req(op, "format", ctx), ctx);
    std::string t = opt_str(op, "type", "2d");
    VkImageType type = t == "1d" ? VK_IMAGE_TYPE_1D : t == "3d" ? VK_IMAGE_TYPE_3D : VK_IMAGE_TYPE_2D;
    VkImageTiling tiling = (VkImageTiling)parse_enum("VkImageTiling", "VK_IMAGE_TILING_", op.value("tiling", json("optimal")), ctx);
    VkImageUsageFlags usage = (VkImageUsageFlags)parse_flags("VkImageUsageFlagBits", "VK_IMAGE_USAGE_", req(op, "usage", ctx), ctx);
    VkImageCreateFlags flags = (VkImageCreateFlags)parse_flags("VkImageCreateFlagBits", "VK_IMAGE_CREATE_", op.value("flags", json()), ctx);
    VkImageFormatProperties p{};
    VkResult r = VKC(st, vkGetPhysicalDeviceImageFormatProperties, st.phys, f, type, tiling, usage, flags, &p);
    json v = {{"result", result_name(r)}};
    if (r == VK_SUCCESS) vkt::struct_info("VkImageFormatProperties")->serialize(&p, v["properties"]);
    st.event(v);
}

}  // namespace

void register_setup_ops(std::map<std::string, OpFn>& ops) {
    ops["instance"] = op_instance;
    ops["device"] = op_device;
    ops["query"] = op_query;
    ops["enumerate"] = op_enumerate;
    ops["image_format_props"] = op_image_format_props;
}
