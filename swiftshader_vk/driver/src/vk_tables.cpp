// vkreplay: the generated Vulkan tables and the lookups over them.
#include "common.hpp"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <iterator>
#include <map>
#include <unordered_map>

// The generated file opens its own `namespace vkt` and uses json, enum_name and
// flag_names from common.hpp.
#include "vk_tables.inc"

namespace vkt {

namespace {
std::string upper_copy(std::string s) {
    for (auto& c : s) c = (char)std::toupper((unsigned char)c);
    return s;
}

struct Index {
    std::unordered_map<std::string, int64_t> by_name;                 // "Type|UPPERCASE NAME" -> value
    std::unordered_map<std::string, std::string> first_name;          // "Type|value" -> NAME
    std::map<std::string, std::vector<std::pair<int64_t, std::string>>> bits;   // FlagBits -> [(bit, name)]
    std::unordered_map<int64_t, const FormatInfo*> formats;
    std::unordered_map<std::string, const StructInfo*> structs;
    std::map<std::string, std::vector<const StructInfo*>> by_category;
};

const Index& index() {
    static const Index idx = [] {
        Index x;
        for (size_t i = 0; i < kEnumCount; ++i) {
            const EnumEntry& e = kEnums[i];
            // case-insensitive: names like VK_FORMAT_ASTC_4x4_UNORM_BLOCK contain lower case
            x.by_name.emplace(std::string(e.type) + "|" + upper_copy(e.name), e.value);
            x.first_name.emplace(std::string(e.type) + "|" + std::to_string(e.value), e.name);
            if (std::strstr(e.type, "FlagBits") && e.value != 0 && (e.value & (e.value - 1)) == 0)
                x.bits[e.type].emplace_back(e.value, e.name);
        }
        for (size_t i = 0; i < kFormatCount; ++i) x.formats.emplace(kFormats[i].value, &kFormats[i]);
        for (size_t i = 0; i < kStructCount; ++i) {
            x.structs.emplace(kStructs[i].name, &kStructs[i]);
            // aliases share a serializer; list each sType once per category
            auto& v = x.by_category[kStructs[i].category];
            if (std::none_of(v.begin(), v.end(), [&](const StructInfo* s) { return s->stype == kStructs[i].stype && s->stype != 0; }))
                v.push_back(&kStructs[i]);
        }
        return x;
    }();
    return idx;
}
}  // namespace

bool enum_value(const std::string& type, const std::string& name, int64_t* out) {
    auto it = index().by_name.find(type + "|" + upper_copy(name));
    if (it == index().by_name.end()) return false;
    *out = it->second;
    return true;
}

std::string enum_name(const char* type, int64_t value) {
    auto it = index().first_name.find(std::string(type) + "|" + std::to_string(value));
    return it == index().first_name.end() ? std::to_string(value) : it->second;
}

json flag_names(const char* bits_type, uint64_t value) {
    json out = json::array();
    auto it = index().bits.find(bits_type);
    uint64_t left = value;
    if (it != index().bits.end()) {
        std::vector<std::pair<int64_t, std::string>> sorted = it->second;
        std::sort(sorted.begin(), sorted.end());
        for (auto& [bit, name] : sorted) {
            if ((left & (uint64_t)bit) && std::none_of(out.begin(), out.end(), [&](const json& n) { return n == name; })) {
                if (value & (uint64_t)bit) out.push_back(name);
                left &= ~(uint64_t)bit;
            }
        }
    }
    if (left) out.push_back(left);
    return out;
}

const FormatInfo* format_info(VkFormat f) {
    auto it = index().formats.find((int64_t)f);
    return it == index().formats.end() ? nullptr : it->second;
}

const StructInfo* struct_info(const std::string& name) {
    auto it = index().structs.find(name);
    return it == index().structs.end() ? nullptr : it->second;
}

const std::vector<const StructInfo*>& structs_of(const std::string& category) {
    static const std::vector<const StructInfo*> none;
    auto it = index().by_category.find(category);
    return it == index().by_category.end() ? none : it->second;
}

}  // namespace vkt

// ------------------------------------------------------------------ JSON helpers

const json& req(const json& j, const char* key, const std::string& ctx) {
    if (!j.is_object() || !j.contains(key)) throw CaseError(ctx + ": missing '" + key + "'");
    return j.at(key);
}
std::string req_str(const json& j, const char* key, const std::string& ctx) {
    const json& v = req(j, key, ctx);
    if (!v.is_string()) throw CaseError(ctx + ": '" + key + "' must be a string");
    return v.get<std::string>();
}
uint64_t req_u64(const json& j, const char* key, const std::string& ctx) {
    const json& v = req(j, key, ctx);
    if (!v.is_number_integer() || v.get<int64_t>() < 0) throw CaseError(ctx + ": '" + key + "' must be a non-negative integer");
    return v.get<uint64_t>();
}
std::string opt_str(const json& j, const char* key, const std::string& dflt) {
    if (!j.is_object() || !j.contains(key) || j.at(key).is_null()) return dflt;
    if (!j.at(key).is_string()) throw CaseError(std::string("'") + key + "' must be a string");
    return j.at(key).get<std::string>();
}
uint64_t opt_u64(const json& j, const char* key, uint64_t dflt) {
    if (!j.is_object() || !j.contains(key) || j.at(key).is_null()) return dflt;
    const json& v = j.at(key);
    if (!v.is_number_integer() || v.get<int64_t>() < 0) throw CaseError(std::string("'") + key + "' must be a non-negative integer");
    return v.get<uint64_t>();
}
int64_t opt_i64(const json& j, const char* key, int64_t dflt) {
    if (!j.is_object() || !j.contains(key) || j.at(key).is_null()) return dflt;
    if (!j.at(key).is_number_integer()) throw CaseError(std::string("'") + key + "' must be an integer");
    return j.at(key).get<int64_t>();
}
double opt_f64(const json& j, const char* key, double dflt) {
    if (!j.is_object() || !j.contains(key) || j.at(key).is_null()) return dflt;
    if (!j.at(key).is_number()) throw CaseError(std::string("'") + key + "' must be a number");
    return j.at(key).get<double>();
}
bool opt_bool(const json& j, const char* key, bool dflt) {
    if (!j.is_object() || !j.contains(key) || j.at(key).is_null()) return dflt;
    if (!j.at(key).is_boolean()) throw CaseError(std::string("'") + key + "' must be true or false");
    return j.at(key).get<bool>();
}

namespace {
std::string up(std::string s) {
    for (auto& c : s) c = (char)std::toupper((unsigned char)c);
    return s;
}
}  // namespace

int64_t parse_enum(const char* type, const char* prefix, const json& v, const std::string& ctx) {
    if (v.is_number_integer()) return v.get<int64_t>();
    if (!v.is_string()) throw CaseError(ctx + ": expected a " + type + " name");
    std::string s = up(v.get<std::string>());
    int64_t out;
    if (vkt::enum_value(type, s, &out)) return out;
    if (vkt::enum_value(type, std::string(prefix) + s, &out)) return out;
    throw CaseError(ctx + ": unknown " + type + " '" + v.get<std::string>() + "'");
}

uint64_t parse_flags(const char* bits_type, const char* prefix, const json& v, const std::string& ctx) {
    if (v.is_null()) return 0;
    if (v.is_number_integer()) return v.get<uint64_t>();
    auto one = [&](const json& e) -> uint64_t {
        if (e.is_number_integer()) return e.get<uint64_t>();
        if (!e.is_string()) throw CaseError(ctx + ": flag names must be strings");
        std::string s = up(e.get<std::string>());
        int64_t out;
        for (const std::string& cand : {s, std::string(prefix) + s, s + "_BIT", std::string(prefix) + s + "_BIT"})
            if (vkt::enum_value(bits_type, cand, &out)) return (uint64_t)out;
        throw CaseError(ctx + ": unknown " + bits_type + " '" + e.get<std::string>() + "'");
    };
    if (v.is_array()) {
        uint64_t f = 0;
        for (const json& e : v) f |= one(e);
        return f;
    }
    return one(v);
}

VkFormat parse_format(const json& v, const std::string& ctx) {
    return (VkFormat)parse_enum("VkFormat", "VK_FORMAT_", v, ctx);
}

VkImageAspectFlags format_aspects(VkFormat f) {
    const vkt::FormatInfo* fi = vkt::format_info(f);
    if (!fi) return VK_IMAGE_ASPECT_COLOR_BIT;
    VkImageAspectFlags a = 0;
    for (int i = 0; i < fi->ncomp; ++i) {
        if (!std::strcmp(fi->comps[i].name, "D")) a |= VK_IMAGE_ASPECT_DEPTH_BIT;
        if (!std::strcmp(fi->comps[i].name, "S")) a |= VK_IMAGE_ASPECT_STENCIL_BIT;
    }
    return a ? a : VK_IMAGE_ASPECT_COLOR_BIT;
}

uint32_t readback_texel_bytes(VkFormat f, VkImageAspectFlags aspect) {
    const vkt::FormatInfo* fi = vkt::format_info(f);
    if (!fi || fi->compressed[0] || fi->texels_per_block != 1 || fi->block_extent[0] != 1) return 0;
    if (aspect == VK_IMAGE_ASPECT_STENCIL_BIT) return 1;
    if (aspect == VK_IMAGE_ASPECT_DEPTH_BIT) {
        switch (f) {
            case VK_FORMAT_D16_UNORM: case VK_FORMAT_D16_UNORM_S8_UINT: return 2;
            case VK_FORMAT_X8_D24_UNORM_PACK32: case VK_FORMAT_D24_UNORM_S8_UINT: return 4;
            case VK_FORMAT_D32_SFLOAT: case VK_FORMAT_D32_SFLOAT_S8_UINT: return 4;
            default: return 0;
        }
    }
    if (aspect == VK_IMAGE_ASPECT_COLOR_BIT) return (uint32_t)fi->block_size;
    return 0;
}

std::string result_name(VkResult r) { return vkt::enum_name("VkResult", (int64_t)r); }

bool requirement_met(const char* requirement, uint32_t api_version, const std::vector<std::string>& exts) {
    std::string s = requirement ? requirement : "";
    if (s.empty()) return true;
    size_t pos = 0;
    while (pos <= s.size()) {
        size_t bar = s.find('|', pos);
        std::string tok = s.substr(pos, bar == std::string::npos ? std::string::npos : bar - pos);
        size_t v = tok.find("VERSION_");
        if (v != std::string::npos) {
            int maj = 0, min = 0;
            if (std::sscanf(tok.c_str() + v + 8, "%d_%d", &maj, &min) == 2 &&
                api_version >= VK_MAKE_API_VERSION(0, maj, min, 0))
                return true;
        } else if (std::find(exts.begin(), exts.end(), tok) != exts.end()) {
            return true;
        }
        if (bar == std::string::npos) break;
        pos = bar + 1;
    }
    return false;
}
