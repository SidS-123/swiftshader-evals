// vkreplay child: fences, semaphores, events, query pools, snapshots, timed runs.
#include "child.hpp"

#include <cstring>

bool vkr_make_staging(State& st, VkDeviceSize size, Buffer* out);
void vkr_free_staging(State& st, Buffer& b);

namespace {

// ---------------------------------------------------------------- fences

void op_fence(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    VkFenceCreateInfo ci{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, nullptr,
                         opt_bool(op, "signaled", false) ? (VkFenceCreateFlags)VK_FENCE_CREATE_SIGNALED_BIT : 0u};
    VkFence f = VK_NULL_HANDLE;
    if (VKC(st, vkCreateFence, st.device, &ci, nullptr, &f) != VK_SUCCESS) return;
    st.fences[name] = f;
}

std::vector<VkFence> fence_list(State& st, const json& op, const std::string& ctx) {
    std::vector<VkFence> fs;
    for (const json& f : req(op, "fences", ctx)) fs.push_back(st.get(st.fences, f.get<std::string>(), "fence"));
    return fs;
}

void op_reset_fence(State& st, const json& op, const std::string& ctx) {
    auto fs = fence_list(st, op, ctx);
    VKC(st, vkResetFences, st.device, (uint32_t)fs.size(), fs.data());
}

void op_wait_fence(State& st, const json& op, const std::string& ctx) {
    auto fs = fence_list(st, op, ctx);
    VkResult r = VKC(st, vkWaitForFences, st.device, (uint32_t)fs.size(), fs.data(), opt_bool(op, "all", true),
                     opt_u64(op, "timeout_ns", UINT64_MAX));
    st.event({{"result", result_name(r)}});
}

void op_fence_status(State& st, const json& op, const std::string& ctx) {
    VkFence f = st.get(st.fences, req_str(op, "fence", ctx), "fence");
    st.event({{"result", result_name(VKC(st, vkGetFenceStatus, st.device, f))}});
}

// ---------------------------------------------------------------- semaphores

void op_semaphore(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    Semaphore s;
    s.timeline = opt_bool(op, "timeline", false);
    VkSemaphoreTypeCreateInfo ti{VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO, nullptr, VK_SEMAPHORE_TYPE_TIMELINE,
                                 opt_u64(op, "initial", 0)};
    VkSemaphoreCreateInfo ci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, s.timeline ? &ti : nullptr, 0};
    if (VKC(st, vkCreateSemaphore, st.device, &ci, nullptr, &s.sem) != VK_SUCCESS) return;
    st.semaphores[name] = s;
}

void op_signal_semaphore(State& st, const json& op, const std::string& ctx) {
    Semaphore& s = st.get(st.semaphores, req_str(op, "semaphore", ctx), "semaphore");
    VkSemaphoreSignalInfo si{VK_STRUCTURE_TYPE_SEMAPHORE_SIGNAL_INFO, nullptr, s.sem, req_u64(op, "value", ctx)};
    VKC(st, vkSignalSemaphore, st.device, &si);
}

void op_wait_semaphores(State& st, const json& op, const std::string& ctx) {
    std::vector<VkSemaphore> ss;
    std::vector<uint64_t> vs;
    for (const json& p : req(op, "semaphores", ctx)) {
        ss.push_back(st.get(st.semaphores, p[0].get<std::string>(), "semaphore").sem);
        vs.push_back(p[1].get<uint64_t>());
    }
    VkSemaphoreWaitInfo wi{VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO, nullptr,
                           opt_bool(op, "any", false) ? (VkSemaphoreWaitFlags)VK_SEMAPHORE_WAIT_ANY_BIT : 0u,
                           (uint32_t)ss.size(), ss.data(), vs.data()};
    VkResult r = VKC(st, vkWaitSemaphores, st.device, &wi, opt_u64(op, "timeout_ns", UINT64_MAX));
    st.event({{"result", result_name(r)}});
}

void op_semaphore_value(State& st, const json& op, const std::string& ctx) {
    Semaphore& s = st.get(st.semaphores, req_str(op, "semaphore", ctx), "semaphore");
    uint64_t v = 0;
    VkResult r = VKC(st, vkGetSemaphoreCounterValue, st.device, s.sem, &v);
    st.event({{"result", result_name(r)}, {"value", v}});
}

// ---------------------------------------------------------------- events (VkEvent)

void op_vk_event(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    VkEventCreateInfo ci{VK_STRUCTURE_TYPE_EVENT_CREATE_INFO};
    VkEvent e = VK_NULL_HANDLE;
    if (VKC(st, vkCreateEvent, st.device, &ci, nullptr, &e) != VK_SUCCESS) return;
    st.events[name] = e;
}

void op_host_event(State& st, const json& op, const std::string& ctx) {
    VkEvent e = st.get(st.events, req_str(op, "event", ctx), "event");
    if (req_str(op, "op", ctx) == "set_event") VKC(st, vkSetEvent, st.device, e);
    else VKC(st, vkResetEvent, st.device, e);
}

void op_event_status(State& st, const json& op, const std::string& ctx) {
    VkEvent e = st.get(st.events, req_str(op, "event", ctx), "event");
    st.event({{"result", result_name(VKC(st, vkGetEventStatus, st.device, e))}});
}

void op_wait_idle(State& st, const json& op, const std::string&) {
    st.need_device();
    if (opt_bool(op, "device", false)) VKC(st, vkDeviceWaitIdle, st.device);
    else VKC(st, vkQueueWaitIdle, st.queue);
}

// ---------------------------------------------------------------- queries

void op_query_pool(State& st, const json& op, const std::string& ctx) {
    std::string name = req_str(op, "name", ctx);
    st.declare(name, ctx);
    st.need_device();
    QueryPool q;
    q.type = (VkQueryType)parse_enum("VkQueryType", "VK_QUERY_TYPE_", req(op, "type", ctx), ctx);
    q.count = (uint32_t)req_u64(op, "count", ctx);
    VkQueryPoolCreateInfo ci{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
    ci.queryType = q.type;
    ci.queryCount = q.count;
    ci.pipelineStatistics = (VkQueryPipelineStatisticFlags)parse_flags(
        "VkQueryPipelineStatisticFlagBits", "VK_QUERY_PIPELINE_STATISTIC_", op.value("statistics", json()), ctx);
    q.stat_count = q.type == VK_QUERY_TYPE_PIPELINE_STATISTICS ? (uint32_t)__builtin_popcount(ci.pipelineStatistics) : 1;
    if (VKC(st, vkCreateQueryPool, st.device, &ci, nullptr, &q.pool) != VK_SUCCESS) return;
    st.query_pools[name] = q;
}

void op_host_reset_query_pool(State& st, const json& op, const std::string& ctx) {
    QueryPool& q = st.get(st.query_pools, req_str(op, "pool", ctx), "query_pool");
    vkResetQueryPool(st.device, q.pool, (uint32_t)opt_u64(op, "first", 0), (uint32_t)opt_u64(op, "count", q.count));
}

// Timestamp values are not comparable across implementations or runs: only
// their validity is reported (non-zero, and non-decreasing in query order).
void op_read_queries(State& st, const json& op, const std::string& ctx) {
    QueryPool& q = st.get(st.query_pools, req_str(op, "pool", ctx), "query_pool");
    uint32_t first = (uint32_t)opt_u64(op, "first", 0), count = (uint32_t)opt_u64(op, "count", q.count);
    VkQueryResultFlags flags = (VkQueryResultFlags)parse_flags("VkQueryResultFlagBits", "VK_QUERY_RESULT_",
                                                               op.value("flags", json(json::array({"64", "wait"}))), ctx);
    flags |= VK_QUERY_RESULT_64_BIT;
    bool avail = flags & VK_QUERY_RESULT_WITH_AVAILABILITY_BIT;
    uint32_t per = q.stat_count + (avail ? 1 : 0);
    std::vector<uint64_t> data((size_t)count * per, 0);
    VkResult r = VKC(st, vkGetQueryPoolResults, st.device, q.pool, first, count, data.size() * 8, data.data(),
                     (VkDeviceSize)per * 8, flags);
    json v = {{"result", result_name(r)}};
    json vals = json::array();
    uint64_t prev = 0;
    bool monotonic = true;
    for (uint32_t k = 0; k < count; ++k) {
        json e;
        if (q.type == VK_QUERY_TYPE_TIMESTAMP) {
            uint64_t t = data[(size_t)k * per];
            e = {{"nonzero", t != 0}};
            if (t < prev) monotonic = false;
            prev = t;
        } else {
            json a = json::array();
            for (uint32_t s = 0; s < q.stat_count; ++s) a.push_back(data[(size_t)k * per + s]);
            e = {{"values", a}};
        }
        if (avail) e["available"] = data[(size_t)k * per + q.stat_count] != 0;
        vals.push_back(e);
    }
    v["queries"] = vals;
    if (q.type == VK_QUERY_TYPE_TIMESTAMP) v["monotonic"] = monotonic;
    st.event(v);
}

// ---------------------------------------------------------------- snapshots

// Read back every planned item into one blob; an item that cannot be read is
// reported as not ok (the parent records it missing) and contributes no bytes.
void read_items(State& st, const std::vector<SnapItem>& items, json* list, std::string* blob) {
    *list = json::array();
    if (st.device) st.wait_queue_idle();
    for (const SnapItem& s : items) {
        json e = {{"name", s.name}, {"ok", false}};
        try {
            if (!st.device) throw Skip("no device");
            if (s.is_buffer) {
                Buffer& b = st.get(st.buffers, s.resource, "buffer");
                if (b.map) {
                    if (!b.coherent) {
                        VkMappedMemoryRange r{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, nullptr, b.mem, 0, VK_WHOLE_SIZE};
                        VKC(st, vkInvalidateMappedMemoryRanges, st.device, 1, &r);
                    }
                    blob->append(static_cast<const char*>(b.map) + s.offset, s.bytes);
                    e["ok"] = true;
                } else {
                    Buffer tmp;
                    bool ok = vkr_make_staging(st, s.bytes, &tmp) && st.one_shot([&](VkCommandBuffer cb) {
                        VkBufferCopy c{s.offset, 0, s.bytes};
                        vkCmdCopyBuffer(cb, b.buf, tmp.buf, 1, &c);
                    });
                    if (ok) {
                        blob->append(static_cast<const char*>(tmp.map), s.bytes);
                        e["ok"] = true;
                    } else {
                        e["reason"] = "readback copy failed";
                    }
                    vkr_free_staging(st, tmp);
                }
            } else {
                Image& im = st.get(st.images, s.resource, "image");
                Buffer tmp;
                bool ok = vkr_make_staging(st, s.bytes, &tmp) && st.one_shot([&](VkCommandBuffer cb) {
                    VkBufferImageCopy c{};
                    c.imageSubresource = {s.aspect, s.mip, s.base_layer, s.layers};
                    c.imageExtent = {s.width, s.height, s.depth};
                    vkCmdCopyImageToBuffer(cb, im.img, VK_IMAGE_LAYOUT_GENERAL, tmp.buf, 1, &c);
                });
                if (ok) {
                    if (!tmp.coherent) {
                        VkMappedMemoryRange r{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, nullptr, tmp.mem, 0, VK_WHOLE_SIZE};
                        VKC(st, vkInvalidateMappedMemoryRanges, st.device, 1, &r);
                    }
                    blob->append(static_cast<const char*>(tmp.map), s.bytes);
                    e["ok"] = true;
                } else {
                    e["reason"] = "readback copy failed";
                }
                vkr_free_staging(st, tmp);
            }
        } catch (const Skip& sk) {
            e["reason"] = sk.what();
        }
        list->push_back(e);
    }
}

void op_snapshot(State& st, const json&, const std::string&) {
    const OpPlan& p = st.plan.ops[(size_t)st.op_index];
    json list;
    std::string blob;
    read_items(st, p.items, &list, &blob);
    st.send({{"t", "snapshot"}, {"i", st.op_index}, {"items", list}}, blob.data(), blob.size());
}

// {"op": "run", "name", "iterations": N, "warmup": 1, "cmds": [...] | "cb": name, "snapshot": {"name", "items"}}
// Timed by the parent: from its "go" to the arrival of the snapshot bytes.
void op_run(State& st, const json& op, const std::string& ctx) {
    const OpPlan& p = st.plan.ops[(size_t)st.op_index];
    uint64_t iters = req_u64(op, "iterations", ctx), warm = opt_u64(op, "warmup", 1);
    if (!iters || iters > 1000000) throw CaseError(ctx + ": iterations must be 1..1000000");
    auto fail_snapshot = [&](const std::string& why) {
        st.note("skip", why);
        st.send({{"t", "run_ready"}, {"i", st.op_index}});
        proto::Message go;
        std::string w;
        proto::read_msg(st.fd_in, &go, -1, &w);
        json list;
        std::string blob;
        read_items(st, p.items, &list, &blob);
        st.send({{"t", "snapshot"}, {"i", st.op_index}, {"items", list}}, blob.data(), blob.size());
    };
    if (!st.device || !st.cmd_pool) { fail_snapshot("no device"); return; }
    VkCommandBuffer cb = VK_NULL_HANDLE;
    bool owned = false;
    try {
        if (op.contains("cb")) {
            cb = st.get(st.cmd_buffers, op["cb"].get<std::string>(), "command buffer");
        } else {
            VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, nullptr, st.cmd_pool,
                                           VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
            if (VKC(st, vkAllocateCommandBuffers, st.device, &ai, &cb) != VK_SUCCESS) throw Skip("allocate failed");
            owned = true;
            VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
            if (VKC(st, vkBeginCommandBuffer, cb, &bi) != VK_SUCCESS) throw Skip("begin failed");
            record_commands(st, cb, req(op, "cmds", ctx), opt_bool(op, "auto_barriers", true), ctx);
            if (VKC(st, vkEndCommandBuffer, cb) != VK_SUCCESS) throw Skip("end failed");
        }
    } catch (const Skip& e) {
        fail_snapshot(e.what());
        return;
    }
    VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence = VK_NULL_HANDLE;
    if (VKC(st, vkCreateFence, st.device, &fi, nullptr, &fence) != VK_SUCCESS) { fail_snapshot("fence failed"); return; }
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cb;
    auto one = [&](bool log) {
        VkResult r = vkQueueSubmit(st.queue, 1, &si, fence);
        if (r == VK_SUCCESS) r = vkWaitForFences(st.device, 1, &fence, VK_TRUE, UINT64_MAX);
        if (r == VK_SUCCESS) r = vkResetFences(st.device, 1, &fence);
        if (log || r != VK_SUCCESS) st.call("vkQueueSubmit+vkWaitForFences+vkResetFences", r);
        return r == VK_SUCCESS;
    };
    bool ok = true;
    for (uint64_t k = 0; k < warm && ok; ++k) ok = one(k == 0);
    st.send({{"t", "run_ready"}, {"i", st.op_index}});
    proto::Message go;
    std::string why;
    if (proto::read_msg(st.fd_in, &go, -1, &why) != proto::ReadStatus::Ok) std::_Exit(98);
    for (uint64_t k = 0; k < iters && ok; ++k) ok = one(false);
    json list;
    std::string blob;
    read_items(st, p.items, &list, &blob);
    st.send({{"t", "snapshot"}, {"i", st.op_index}, {"items", list}, {"iterations_done", ok ? iters : 0}},
            blob.data(), blob.size());
    vkDestroyFence(st.device, fence, nullptr);
    if (owned) vkFreeCommandBuffers(st.device, st.cmd_pool, 1, &cb);
}

}  // namespace

void register_sync_ops(std::map<std::string, OpFn>& ops) {
    ops["fence"] = op_fence;
    ops["reset_fence"] = op_reset_fence;
    ops["wait_fence"] = op_wait_fence;
    ops["fence_status"] = op_fence_status;
    ops["semaphore"] = op_semaphore;
    ops["signal_semaphore"] = op_signal_semaphore;
    ops["wait_semaphores"] = op_wait_semaphores;
    ops["semaphore_value"] = op_semaphore_value;
    ops["event"] = op_vk_event;
    ops["set_event"] = op_host_event;
    ops["reset_event"] = op_host_event;
    ops["event_status"] = op_event_status;
    ops["wait_idle"] = op_wait_idle;
    ops["query_pool"] = op_query_pool;
    ops["reset_query_pool"] = op_host_reset_query_pool;
    ops["read_queries"] = op_read_queries;
}

void register_output_ops(std::map<std::string, OpFn>& ops) {
    ops["snapshot"] = op_snapshot;
    ops["run"] = op_run;
}
