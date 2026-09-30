// vkreplay: the untrusted child. Executes ops in order and reports to the parent.
#include "child.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>

// ------------------------------------------------------------------ State

void State::send(const json& head, const void* blob, size_t len) {
    if (!proto::write_msg(fd_out, head, blob, len)) std::_Exit(99);   // the parent is gone
}

void State::call(const char* fn, VkResult r) {
    send({{"t", "call"}, {"i", op_index}, {"f", fn}, {"r", result_name(r)}});
}

void State::note(const std::string& level, const std::string& msg) {
    send({{"t", "note"}, {"i", op_index}, {"level", level}, {"msg", msg}});
}

void State::event(const json& value) {
    send({{"t", "event"}, {"i", op_index}, {"value", value}});
}

void State::declare(const std::string& name, const std::string& ctx) {
    if (!safe_name(name)) throw CaseError(ctx + ": 'name' must match [A-Za-z0-9_.-]{1,64}");
    if (!declared.insert(name).second) throw CaseError(ctx + ": name '" + name + "' is already used");
}

void State::need_instance() {
    if (instance == VK_NULL_HANDLE || phys == VK_NULL_HANDLE) {
        if (!instance_tried) throw CaseError("no 'instance' op before this op");
        throw Skip("no instance / physical device");
    }
}

void State::need_device() {
    if (device == VK_NULL_HANDLE) {
        if (!device_tried) throw CaseError("no 'device' op before this op");
        throw Skip("no device");
    }
}

uint32_t State::memory_type(uint32_t bits, VkMemoryPropertyFlags want, bool* host_visible, bool* coherent) {
    int best = -1;
    for (uint32_t i = 0; i < memprops.memoryTypeCount && i < 32; ++i) {
        if (!(bits & (1u << i))) continue;
        if ((memprops.memoryTypes[i].propertyFlags & want) == want) { best = (int)i; break; }
        if (best < 0) best = (int)i;
    }
    if (best < 0) throw Skip("no memory type satisfies the requirements");
    VkMemoryPropertyFlags f = memprops.memoryTypes[best].propertyFlags;
    if (host_visible) *host_visible = f & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT;
    if (coherent) *coherent = f & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    return (uint32_t)best;
}

bool State::one_shot(const std::function<void(VkCommandBuffer)>& fn) {
    need_device();
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, nullptr, cmd_pool,
                                   VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb = VK_NULL_HANDLE;
    if (VKC(*this, vkAllocateCommandBuffers, device, &ai, &cb) != VK_SUCCESS) return false;
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, nullptr,
                                VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, nullptr};
    bool ok = VKC(*this, vkBeginCommandBuffer, cb, &bi) == VK_SUCCESS;
    if (ok) {
        fn(cb);
        ok = VKC(*this, vkEndCommandBuffer, cb) == VK_SUCCESS;
    }
    VkFence fence = VK_NULL_HANDLE;
    VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, nullptr, 0};
    if (ok) ok = VKC(*this, vkCreateFence, device, &fi, nullptr, &fence) == VK_SUCCESS;
    if (ok) {
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.commandBufferCount = 1;
        si.pCommandBuffers = &cb;
        ok = VKC(*this, vkQueueSubmit, queue, 1, &si, fence) == VK_SUCCESS;
        if (ok) ok = VKC(*this, vkWaitForFences, device, 1, &fence, VK_TRUE, UINT64_MAX) == VK_SUCCESS;
    }
    if (fence) vkDestroyFence(device, fence, nullptr);
    vkFreeCommandBuffers(device, cmd_pool, 1, &cb);
    return ok;
}

void State::wait_queue_idle() {
    if (queue) VKC(*this, vkQueueWaitIdle, queue);
}

// ------------------------------------------------------------------ shared helpers

VkImageSubresourceRange full_range(const Image& im) {
    return {im.aspects, 0, VK_REMAINING_MIP_LEVELS, 0, VK_REMAINING_ARRAY_LAYERS};
}

VkShaderStageFlags parse_stages(const json& v, const std::string& ctx) {
    return (VkShaderStageFlags)parse_flags("VkShaderStageFlagBits", "VK_SHADER_STAGE_", v, ctx);
}

VkImageLayout parse_layout(const json& v, const std::string& ctx) {
    return (VkImageLayout)parse_enum("VkImageLayout", "VK_IMAGE_LAYOUT_", v, ctx);
}

namespace {

uint16_t f32_to_f16(float f) {
    uint32_t x;
    std::memcpy(&x, &f, 4);
    uint32_t sign = (x >> 16) & 0x8000u;
    int32_t exp = (int32_t)((x >> 23) & 0xff) - 127 + 15;
    uint32_t mant = x & 0x7fffffu;
    if (((x >> 23) & 0xff) == 0xff) return (uint16_t)(sign | 0x7c00u | (mant ? 0x200u : 0));   // inf / nan
    if (exp >= 31) return (uint16_t)(sign | 0x7c00u);
    if (exp <= 0) {
        if (exp < -10) return (uint16_t)sign;
        mant |= 0x800000u;
        uint32_t shift = (uint32_t)(14 - exp);
        uint32_t half = mant >> shift;
        uint32_t rem = mant & ((1u << shift) - 1), mid = 1u << (shift - 1);
        if (rem > mid || (rem == mid && (half & 1))) half++;
        return (uint16_t)(sign | half);
    }
    uint32_t half = sign | ((uint32_t)exp << 10) | (mant >> 13);
    uint32_t rem = mant & 0x1fffu;
    if (rem > 0x1000u || (rem == 0x1000u && (half & 1))) half++;
    return (uint16_t)half;
}

template <class T>
void push_vals(std::vector<uint8_t>& out, const json& arr, const std::string& ctx) {
    for (const json& v : arr) {
        if (!v.is_number()) throw CaseError(ctx + ": data values must be numbers");
        T t = v.get<T>();
        const uint8_t* p = reinterpret_cast<const uint8_t*>(&t);
        out.insert(out.end(), p, p + sizeof(T));
    }
}

}  // namespace

std::vector<uint8_t> data_bytes(const json& op, const State& st, const std::string& ctx) {
    std::vector<uint8_t> out;
    if (op.contains("asset")) {
        std::string file = req_str(op["asset"], "file", ctx);
        if (file.find('/') != std::string::npos || file.find("..") != std::string::npos || file.empty())
            throw CaseError(ctx + ": asset names must be plain file names");
        std::ifstream f(st.assets_dir + "/" + file, std::ios::binary);
        if (!f) throw CaseError(ctx + ": asset '" + file + "' not found");
        std::ostringstream ss;
        ss << f.rdbuf();
        std::string s = ss.str();
        out.assign(s.begin(), s.end());
        return out;
    }
    const json& d = req(op, "data", ctx);
    if (!d.is_object() || d.size() < 1) throw CaseError(ctx + ": 'data' must be an object like {\"f32\": [...]}");
    uint64_t repeat = opt_u64(d, "repeat", 1);
    for (auto& [k, v] : d.items()) {
        if (k == "repeat") continue;
        if (k == "hex") {
            std::string h = v.get<std::string>();
            if (h.size() % 2) throw CaseError(ctx + ": hex data must have an even length");
            for (size_t i = 0; i < h.size(); i += 2) out.push_back((uint8_t)std::stoul(h.substr(i, 2), nullptr, 16));
            continue;
        }
        if (!v.is_array()) throw CaseError(ctx + ": data." + k + " must be a list");
        if (k == "address") {
            // [{"buffer": name, "offset": n}]: each a u64 vkGetBufferDeviceAddress(buffer) + offset
            for (const json& e : v) {
                std::string bn = req_str(e, "buffer", ctx);
                auto it = st.buffers.find(bn);
                if (it == st.buffers.end()) {
                    if (st.declared.count(bn)) throw Skip("buffer '" + bn + "' was not created");
                    throw CaseError(ctx + ": unknown buffer '" + bn + "'");
                }
                VkBufferDeviceAddressInfo ai{VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO, nullptr, it->second.buf};
                uint64_t addr = vkGetBufferDeviceAddress(st.device, &ai) + opt_u64(e, "offset", 0);
                const uint8_t* p = reinterpret_cast<const uint8_t*>(&addr);
                out.insert(out.end(), p, p + 8);
            }
            continue;
        }
        if (k == "u8") push_vals<uint8_t>(out, v, ctx);
        else if (k == "i8") push_vals<int8_t>(out, v, ctx);
        else if (k == "u16") push_vals<uint16_t>(out, v, ctx);
        else if (k == "i16") push_vals<int16_t>(out, v, ctx);
        else if (k == "u32") push_vals<uint32_t>(out, v, ctx);
        else if (k == "i32") push_vals<int32_t>(out, v, ctx);
        else if (k == "u64") push_vals<uint64_t>(out, v, ctx);
        else if (k == "i64") push_vals<int64_t>(out, v, ctx);
        else if (k == "f32") push_vals<float>(out, v, ctx);
        else if (k == "f64") push_vals<double>(out, v, ctx);
        else if (k == "f16") {
            for (const json& e : v) {
                uint16_t h = f32_to_f16(e.get<float>());
                out.push_back((uint8_t)(h & 0xff));
                out.push_back((uint8_t)(h >> 8));
            }
        } else throw CaseError(ctx + ": unknown data type '" + k + "'");
    }
    if (repeat > 1) {
        if (out.size() * repeat > (512ull << 20)) throw CaseError(ctx + ": data larger than 512 MiB");
        std::vector<uint8_t> r;
        r.reserve(out.size() * repeat);
        for (uint64_t i = 0; i < repeat; ++i) r.insert(r.end(), out.begin(), out.end());
        out.swap(r);
    }
    return out;
}

// ------------------------------------------------------------------ main

int child_main(int argc, char** argv) {
    State st;
    std::string case_path;
    for (int i = 1; i < argc; ++i) {
        std::string s = argv[i];
        auto next = [&]() { return i + 1 < argc ? std::string(argv[++i]) : std::string(); };
        if (s == "--fd-out") st.fd_out = std::atoi(next().c_str());
        else if (s == "--fd-in") st.fd_in = std::atoi(next().c_str());
        else if (s == "--case") case_path = next();
        else if (s == "--assets") st.assets_dir = next();
        else if (s == "--perturb") st.perturb = next();
        else if (s == "--validate") st.validate = true;
    }
    if (st.fd_out < 0 || st.fd_in < 0) return 2;

    std::map<std::string, OpFn> ops;
    register_setup_ops(ops);
    register_resource_ops(ops);
    register_pipeline_ops(ops);
    register_command_ops(ops);
    register_sync_ops(ops);
    register_output_ops(ops);

    json doc;
    try {
        std::ifstream f(case_path, std::ios::binary);
        std::ostringstream ss;
        ss << f.rdbuf();
        doc = json::parse(ss.str());
        st.plan = plan_case(doc);
    } catch (const std::exception& e) {
        st.send({{"t", "case_error"}, {"i", -1}, {"msg", e.what()}});
        return 3;
    }
    const json& list = doc["ops"];
    for (size_t i = 0; i < list.size(); ++i) {
        st.op_index = (int64_t)i;
        const json& op = list[i];
        std::string name = op["op"].get<std::string>();
        std::string ctx = "op " + std::to_string(i) + " (" + name + ")";
        auto it = ops.find(name);
        try {
            if (it == ops.end()) throw CaseError(ctx + ": unknown op '" + name + "'");
            it->second(st, op, ctx);
        } catch (const Skip& e) {
            st.note("skip", e.what());
        } catch (const CaseError& e) {
            std::string msg = e.what();
            if (msg.rfind("op ", 0) != 0) msg = ctx + ": " + msg;
            st.send({{"t", "case_error"}, {"i", (int64_t)i}, {"msg", msg}});
            return 3;
        } catch (const std::exception& e) {   // json type errors, bad numbers in the case
            st.send({{"t", "case_error"}, {"i", (int64_t)i}, {"msg", ctx + ": " + e.what()}});
            return 3;
        }
    }
    st.send({{"t", "done"}});
    return 0;
}
