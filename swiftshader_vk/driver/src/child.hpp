// vkreplay: the untrusted child -- executes a case's ops through the Vulkan loader.
#pragma once

#include "case_model.hpp"
#include "protocol.hpp"

#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

// An op whose inputs were never created (an earlier call failed): the op is
// skipped and noted, and the case goes on. Distinct from CaseError, which means
// the case itself is malformed.
struct Skip : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct Buffer {
    VkBuffer buf = VK_NULL_HANDLE;
    VkDeviceMemory mem = VK_NULL_HANDLE;
    VkDeviceSize size = 0;
    void* map = nullptr;
    bool coherent = true;
};
struct Image {
    VkImage img = VK_NULL_HANDLE;
    VkDeviceMemory mem = VK_NULL_HANDLE;
    ImageDef def;
    VkImageAspectFlags aspects = 0;
};
struct View {
    VkImageView view = VK_NULL_HANDLE;
    std::string image;
};
struct DescLayout {
    VkDescriptorSetLayout layout = VK_NULL_HANDLE;
    std::vector<VkDescriptorSetLayoutBinding> bindings;
    bool update_after_bind = false;
};
struct Pipeline {
    VkPipeline pipe = VK_NULL_HANDLE;
    VkPipelineBindPoint bind_point = VK_PIPELINE_BIND_POINT_COMPUTE;
};
struct DescSet {
    VkDescriptorSet set = VK_NULL_HANDLE;
    VkDescriptorPool pool = VK_NULL_HANDLE;
    std::string layout;
};
struct QueryPool {
    VkQueryPool pool = VK_NULL_HANDLE;
    VkQueryType type = VK_QUERY_TYPE_OCCLUSION;
    uint32_t count = 0;
    uint32_t stat_count = 1;
};
struct Semaphore {
    VkSemaphore sem = VK_NULL_HANDLE;
    bool timeline = false;
};

struct State {
    int fd_out = -1, fd_in = -1;
    std::string assets_dir;
    std::string perturb;
    bool validate = false;
    int64_t op_index = -1;
    CasePlan plan;

    VkInstance instance = VK_NULL_HANDLE;
    VkDebugUtilsMessengerEXT messenger = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    uint32_t api_version = 0;                  // the physical device's
    std::vector<std::string> device_extensions;   // supported by the physical device
    VkPhysicalDeviceMemoryProperties memprops{};
    VkDevice device = VK_NULL_HANDLE;
    uint32_t queue_family = 0;
    VkQueue queue = VK_NULL_HANDLE;
    VkCommandPool cmd_pool = VK_NULL_HANDLE;
    bool instance_tried = false, device_tried = false;

    // Every name a case declares, created or not: a name that was declared but
    // whose creation failed makes dependent ops a Skip; an undeclared name is a
    // CaseError.
    std::set<std::string> declared;
    std::map<std::string, Buffer> buffers;
    std::map<std::string, Image> images;
    std::map<std::string, View> views;
    std::map<std::string, VkBufferView> buffer_views;
    std::map<std::string, VkSampler> samplers;
    std::map<std::string, VkShaderModule> shaders;
    std::map<std::string, DescLayout> desc_layouts;
    std::map<std::string, VkPipelineLayout> pipeline_layouts;
    std::map<std::string, DescSet> desc_sets;
    std::map<std::string, Pipeline> pipelines;
    std::map<std::string, VkCommandBuffer> cmd_buffers;
    std::map<std::string, VkFence> fences;
    std::map<std::string, Semaphore> semaphores;
    std::map<std::string, VkEvent> events;
    std::map<std::string, QueryPool> query_pools;

    // messaging
    void send(const json& head, const void* blob = nullptr, size_t len = 0);
    void call(const char* fn, VkResult r);
    void note(const std::string& level, const std::string& msg);
    void event(const json& value);

    // lookups: CaseError if never declared, Skip if declared but not created
    template <class M>
    auto& get(M& map, const std::string& name, const char* what) {
        auto it = map.find(name);
        if (it != map.end()) return it->second;
        if (declared.count(name)) throw Skip(std::string(what) + " '" + name + "' was not created");
        throw CaseError(std::string("unknown ") + what + " '" + name + "'");
    }
    void declare(const std::string& name, const std::string& ctx);
    void need_device();
    void need_instance();

    uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags want, bool* host_visible, bool* coherent);
    // Record `fn` into a fresh command buffer, submit it, wait. Returns false on a failed call.
    bool one_shot(const std::function<void(VkCommandBuffer)>& fn);
    void wait_queue_idle();
};

// Vulkan call wrapper: logs the call's VkResult under the current op.
#define VKC(st, fn, ...) ([&]() { VkResult r__ = fn(__VA_ARGS__); (st).call(#fn, r__); return r__; }())

// Ops by group (child_*.cpp). Each takes the op's JSON and a context string for errors.
using OpFn = void (*)(State&, const json&, const std::string&);
void register_setup_ops(std::map<std::string, OpFn>& ops);
void register_resource_ops(std::map<std::string, OpFn>& ops);
void register_pipeline_ops(std::map<std::string, OpFn>& ops);
void register_command_ops(std::map<std::string, OpFn>& ops);
void register_sync_ops(std::map<std::string, OpFn>& ops);
void register_output_ops(std::map<std::string, OpFn>& ops);

// Helpers shared across groups.
std::vector<uint8_t> data_bytes(const json& op, const State& st, const std::string& ctx);
void record_commands(State& st, VkCommandBuffer cb, const json& cmds, bool auto_barriers, const std::string& ctx);
VkImageSubresourceRange full_range(const Image& im);
VkShaderStageFlags parse_stages(const json& v, const std::string& ctx);
VkImageLayout parse_layout(const json& v, const std::string& ctx);
