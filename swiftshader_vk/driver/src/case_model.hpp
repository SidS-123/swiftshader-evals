// vkreplay: what the parent and the child both read from a case.
//
// The parent never trusts the child about the *shape* of an output: which op
// produces what, a snapshot's items, each item's size and format all come from
// this plan, built from the case file alone.
#pragma once

#include "common.hpp"

#include <map>
#include <string>
#include <vector>

struct ImageDef {
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkImageType type = VK_IMAGE_TYPE_2D;
    uint32_t width = 1, height = 1, depth = 1, mips = 1, layers = 1, samples = 1;
};

struct SnapItem {
    std::string name;
    bool is_buffer = false;
    std::string resource;              // image or buffer name
    // image items
    VkImageAspectFlags aspect = 0;
    uint32_t mip = 0, base_layer = 0, layers = 1;
    uint32_t width = 0, height = 0, depth = 0;
    VkFormat format = VK_FORMAT_UNDEFINED;
    uint32_t texel_bytes = 0;
    // buffer items
    uint64_t offset = 0;
    std::string elem = "u8";
    // both
    uint64_t bytes = 0;                // exact size the parent expects
};

enum class OutKind { None, Snapshot, Run, Event };

struct OpPlan {
    std::string op;
    std::string name;
    OutKind out = OutKind::None;
    std::vector<SnapItem> items;       // Snapshot and Run
    std::string snap_name;             // Run: the name of the snapshot it reads back
};

struct CasePlan {
    std::string name;
    std::vector<OpPlan> ops;
    std::map<std::string, ImageDef> images;
    std::map<std::string, uint64_t> buffers;
    uint64_t snapshot_count = 0;
};

// Throws CaseError on anything malformed. Limits: <= 64 snapshot ops, item
// images <= 4096 x 4096, a snapshot <= 256 MiB.
CasePlan plan_case(const json& doc);

// Names are file-name safe: [A-Za-z0-9_.-]{1,64}, not starting with '.'.
bool safe_name(const std::string& s);

uint32_t elem_bytes(const std::string& elem);   // 0 if unknown
std::string aspect_name(VkImageAspectFlags a);
