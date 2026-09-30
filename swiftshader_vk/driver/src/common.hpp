// vkreplay: shared declarations (tables, JSON helpers, errors).
#pragma once

#include <vulkan/vulkan_core.h>

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

using json = nlohmann::ordered_json;

#define VKREPLAY_VERSION "1.0.0"

namespace vkt {

struct EnumEntry { const char* type; const char* name; int64_t value; };
struct FlagType { const char* flags; const char* bits; bool is64; };
struct FormatComponent { const char* name; int bits; const char* numeric; };
struct FormatInfo {
    const char* name; int64_t value; const char* cls; int block_size; int texels_per_block;
    int block_extent[3]; int packed; const char* compressed; int ncomp; FormatComponent comps[4];
};
struct BoolField { const char* name; size_t offset; };
struct StructInfo {
    const char* name; VkStructureType stype; size_t size; const char* category;
    void (*serialize)(const void*, json&); const BoolField* bools; size_t nbools; const char* requirement;
};

// Lookups over the generated tables.
bool enum_value(const std::string& type, const std::string& name, int64_t* out);
std::string enum_name(const char* type, int64_t value);            // "VK_..." or the number as text
json flag_names(const char* bits_type, uint64_t value);            // ["VK_..._BIT", ...] (+ leftover number)
const FormatInfo* format_info(VkFormat f);
const StructInfo* struct_info(const std::string& name);
const std::vector<const StructInfo*>& structs_of(const std::string& category);

}  // namespace vkt

// A malformed case: the driver stops with exit "driver_error" (never the candidate's fault).
struct CaseError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// JSON field helpers. `ctx` names the op in error messages.
const json& req(const json& j, const char* key, const std::string& ctx);
std::string req_str(const json& j, const char* key, const std::string& ctx);
uint64_t req_u64(const json& j, const char* key, const std::string& ctx);
std::string opt_str(const json& j, const char* key, const std::string& dflt);
uint64_t opt_u64(const json& j, const char* key, uint64_t dflt);
int64_t opt_i64(const json& j, const char* key, int64_t dflt);
double opt_f64(const json& j, const char* key, double dflt);
bool opt_bool(const json& j, const char* key, bool dflt);

// Enum / flag parsing from case JSON. Accepts the full name ("VK_FORMAT_R8G8B8A8_UNORM"),
// the name without the type prefix ("R8G8B8A8_UNORM"), in any case, and a bare integer.
int64_t parse_enum(const char* type, const char* prefix, const json& v, const std::string& ctx);
// Flags: a list of names (prefix and the _BIT suffix optional), a single name, or an integer.
uint64_t parse_flags(const char* bits_type, const char* prefix, const json& v, const std::string& ctx);

VkFormat parse_format(const json& v, const std::string& ctx);

// Readback layout of one aspect of a format: bytes per texel as copied to a buffer.
// Returns 0 for formats/aspects vkreplay cannot read back (compressed, multi-planar).
uint32_t readback_texel_bytes(VkFormat f, VkImageAspectFlags aspect);
VkImageAspectFlags format_aspects(VkFormat f);

std::string result_name(VkResult r);
bool requirement_met(const char* requirement, uint32_t api_version, const std::vector<std::string>& exts);
