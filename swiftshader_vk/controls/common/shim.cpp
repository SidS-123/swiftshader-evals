// Wrapper ICD for the Stage 8 controls (PLAN_v1.md §12, docs/DESIGN.md "Controls").
//
// Built as libvk_candidate.so and loaded by vkreplay like any candidate. It dlopens a
// real driver (SHIM_TARGET: SwiftShader LLVM by default, or Subzero / lavapipe), makes
// SHIM_INI its working directory first (SwiftShader reads SwiftShader.ini from the
// working directory only: the oracle's ThreadCount=4, or ThreadCount=1), and forwards
// every entry point. Exactly one CONTROL_* macro, set at build time, alters one thing;
// with none it is the reference. vkreplay gives the candidate a clean environment, so
// the alteration cannot be chosen at run time.
//
// Dispatchable handles are the real driver's own (the loader writes its dispatch
// pointer into them, which the real driver reserves), so forwarding is a lookup:
// vk_icdGetInstanceProcAddr / vk_icdGetPhysicalDeviceProcAddr / vkGetDeviceProcAddr
// return the shim's function for an intercepted name the real driver implements, and
// the real driver's otherwise.
//
// CONTROL_POISON is not a control but a corpus gate (tools/determinism.py): every new
// allocation is filled with 0xA5 before the case sees it, so a case whose outputs differ
// from the oracle's reads memory it never wrote (uninitialised contents are stable inside
// one process, which repeat runs cannot reveal).
//
// Readback alterations act on image -> buffer copies (how vkreplay reads every image
// snapshot): a vkCmdCopyImageToBuffer is remembered with its command buffer, the
// command buffer's copies become pending at vkQueueSubmit, and after the next
// successful vkWaitForFences / vkQueueWaitIdle / vkDeviceWaitIdle the shim rewrites
// the bytes through the destination buffer's mapping (vkreplay maps its staging
// buffers before the copy and reads them after the wait). Buffer snapshots are read
// through the case buffers' own mappings and are never altered.
#include <vulkan/vulkan.h>

#include <dlfcn.h>
#include <signal.h>
#include <unistd.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#ifndef SHIM_TARGET
#define SHIM_TARGET "/opt/swiftshader/llvm/libvk_swiftshader.so"
#endif
#ifndef SHIM_INI
#define SHIM_INI "/opt/ssvk/ini/default"
#endif

#ifdef CONTROL_HARDCODE_PUBLIC
#include "public_hashes.h"  // kPublicCaseHashes[], generated at build time from corpus/public
#endif

#define EXPORT extern "C" __attribute__((visibility("default")))

typedef VkResult(VKAPI_PTR* PFN_Negotiate)(uint32_t*);
typedef PFN_vkVoidFunction(VKAPI_PTR* PFN_GPDPA)(VkInstance, const char*);

namespace {

std::mutex g_mu;
void* g_lib = nullptr;
PFN_vkGetInstanceProcAddr g_icd_gipa = nullptr;
PFN_GPDPA g_icd_gpdpa = nullptr;
PFN_Negotiate g_negotiate = nullptr;
VkInstance g_instance = VK_NULL_HANDLE;
VkDevice g_device = VK_NULL_HANDLE;
PFN_vkGetDeviceProcAddr g_real_gdpa = nullptr;
std::unordered_map<std::string, PFN_vkVoidFunction> g_inst_cache, g_dev_cache;
bool g_degraded = false;  // hardcode_public on an unknown case: behave like init_only

void die(const char* what) {
    std::fprintf(stderr, "ssvk-shim: %s: %s\n", what, dlerror() ? dlerror() : "");
}

__attribute__((constructor)) void shim_init() {
    if (chdir(SHIM_INI) != 0) std::fprintf(stderr, "ssvk-shim: cannot chdir to %s\n", SHIM_INI);
    g_lib = dlopen(SHIM_TARGET, RTLD_NOW | RTLD_LOCAL);
    if (!g_lib) { die("dlopen " SHIM_TARGET); return; }
    g_icd_gipa = (PFN_vkGetInstanceProcAddr)dlsym(g_lib, "vk_icdGetInstanceProcAddr");
    g_icd_gpdpa = (PFN_GPDPA)dlsym(g_lib, "vk_icdGetPhysicalDeviceProcAddr");
    g_negotiate = (PFN_Negotiate)dlsym(g_lib, "vk_icdNegotiateLoaderICDInterfaceVersion");
    if (!g_icd_gipa) die("no vk_icdGetInstanceProcAddr");
#ifdef CONTROL_HARDCODE_PUBLIC
    // Key on the case itself: vkreplay's child was started with "--case PATH".
    std::ifstream cl("/proc/self/cmdline", std::ios::binary);
    std::string args((std::istreambuf_iterator<char>(cl)), std::istreambuf_iterator<char>());
    std::vector<std::string> av;
    for (size_t s = 0, e; s < args.size(); s = e + 1) {
        e = args.find('\0', s);
        if (e == std::string::npos) e = args.size();
        av.push_back(args.substr(s, e - s));
    }
    std::string path;
    for (size_t i = 0; i + 1 < av.size(); ++i)
        if (av[i] == "--case") path = av[i + 1];
    std::ifstream cf(path, std::ios::binary);
    std::string bytes((std::istreambuf_iterator<char>(cf)), std::istreambuf_iterator<char>());
    uint64_t h = 1469598103934665603ull;  // FNV-1a 64
    for (unsigned char c : bytes) { h ^= c; h *= 1099511628211ull; }
    bool known = false;
    for (uint64_t k : kPublicCaseHashes) known = known || (k == h);
    g_degraded = !known;
#endif
}

PFN_vkVoidFunction real_inst(const char* name) {
    std::lock_guard<std::mutex> l(g_mu);
    auto it = g_inst_cache.find(name);
    if (it != g_inst_cache.end()) return it->second;
    PFN_vkVoidFunction f = g_icd_gipa ? g_icd_gipa(g_instance, name) : nullptr;
    if (!f && g_icd_gpdpa && g_instance) f = g_icd_gpdpa(g_instance, name);
    g_inst_cache[name] = f;
    return f;
}

PFN_vkVoidFunction real_dev(const char* name) {
    std::lock_guard<std::mutex> l(g_mu);
    auto it = g_dev_cache.find(name);
    if (it != g_dev_cache.end()) return it->second;
    PFN_vkVoidFunction f = (g_real_gdpa && g_device) ? g_real_gdpa(g_device, name) : nullptr;
    g_dev_cache[name] = f;
    return f;
}

#define RI(fn) ((PFN_##fn)real_inst(#fn))
#define RD(fn) ((PFN_##fn)real_dev(#fn))

// ------------------------------------------------------------------ readback bookkeeping

struct ImageInfo { VkFormat format; };
struct BufInfo { VkDeviceMemory mem = VK_NULL_HANDLE; VkDeviceSize offset = 0; VkDeviceSize size = 0; };
struct Readback {
    VkImage image;
    VkBuffer buffer;
    VkDeviceSize offset;
    VkImageAspectFlags aspect;
    uint32_t mip, w, h, d, layers;
    bool tight;  // one region, packed rows: the whole range is this image's texels
};

std::unordered_map<uint64_t, ImageInfo> g_images;
std::unordered_map<uint64_t, BufInfo> g_buffers;
std::unordered_map<uint64_t, uint8_t*> g_maps;  // memory -> pointer to its byte 0
std::unordered_map<VkCommandBuffer, std::vector<Readback>> g_cb_readbacks;
std::vector<Readback> g_pending;
std::map<std::string, std::string> g_last;  // stale_frame: previous readback per image/aspect/mip

template <class H> uint64_t key(H h) { return (uint64_t)(uintptr_t)h; }

enum Kind { K_OTHER, K_UNORM8, K_F16, K_F32 };
struct Fmt { uint32_t bytes; Kind kind; uint32_t channels; bool srgb; };

Fmt color_fmt(VkFormat f) {
    switch (f) {
        case VK_FORMAT_R8_UNORM: return {1, K_UNORM8, 1, false};
        case VK_FORMAT_R8_SRGB: return {1, K_UNORM8, 1, true};
        case VK_FORMAT_R8G8_UNORM: return {2, K_UNORM8, 2, false};
        case VK_FORMAT_R8G8_SRGB: return {2, K_UNORM8, 2, true};
        case VK_FORMAT_R8G8B8A8_UNORM: case VK_FORMAT_B8G8R8A8_UNORM: case VK_FORMAT_A8B8G8R8_UNORM_PACK32:
            return {4, K_UNORM8, 4, false};
        case VK_FORMAT_R8G8B8A8_SRGB: case VK_FORMAT_B8G8R8A8_SRGB: case VK_FORMAT_A8B8G8R8_SRGB_PACK32:
            return {4, K_UNORM8, 4, true};
        case VK_FORMAT_R16_SFLOAT: return {2, K_F16, 1, false};
        case VK_FORMAT_R16G16_SFLOAT: return {4, K_F16, 2, false};
        case VK_FORMAT_R16G16B16A16_SFLOAT: return {8, K_F16, 4, false};
        case VK_FORMAT_R32_SFLOAT: return {4, K_F32, 1, false};
        case VK_FORMAT_R32G32_SFLOAT: return {8, K_F32, 2, false};
        case VK_FORMAT_R32G32B32A32_SFLOAT: return {16, K_F32, 4, false};
        default: return {0, K_OTHER, 0, false};
    }
}

uint32_t depth_bytes(VkFormat f, VkImageAspectFlags aspect) {
    if (aspect & VK_IMAGE_ASPECT_STENCIL_BIT) return 1;
    switch (f) {
        case VK_FORMAT_D16_UNORM: return 2;
        case VK_FORMAT_D32_SFLOAT: case VK_FORMAT_D32_SFLOAT_S8_UINT: return 4;
        default: return 0;
    }
}

float half_to_float(uint16_t h) {
    uint32_t s = (h >> 15) & 1, e = (h >> 10) & 31, m = h & 1023;
    float v = e == 0 ? std::ldexp((float)m, -24) : e == 31 ? (m ? NAN : INFINITY) : std::ldexp((float)(m | 1024), (int)e - 25);
    return s ? -v : v;
}
uint16_t float_to_half(float f) {
    uint32_t x; std::memcpy(&x, &f, 4);
    uint32_t s = (x >> 16) & 0x8000; int e = (int)((x >> 23) & 255) - 127 + 15; uint32_t m = x & 0x7fffff;
    if (((x >> 23) & 255) == 255) return (uint16_t)(s | 0x7c00 | (m ? 0x200 : 0));
    if (e >= 31) return (uint16_t)(s | 0x7c00);
    if (e <= 0) return (uint16_t)s;
    uint32_t r = (uint32_t)(s | ((uint32_t)e << 10) | (m >> 13));
    if ((m & 0x1fff) > 0x1000 || ((m & 0x1fff) == 0x1000 && (r & 1))) ++r;  // round to nearest even
    return (uint16_t)r;
}
float srgb_to_linear(float c) { return c <= 0.04045f ? c / 12.92f : std::pow((c + 0.055f) / 1.055f, 2.4f); }

void transform(const Readback& rb, uint8_t* p, size_t n) {
    VkFormat f = g_images.count(key(rb.image)) ? g_images[key(rb.image)].format : VK_FORMAT_UNDEFINED;
    bool color = (rb.aspect & VK_IMAGE_ASPECT_COLOR_BIT) != 0;
    (void)f; (void)color; (void)p; (void)n;
#if defined(CONTROL_GAIN_X2) || defined(CONTROL_SRGB_MISHANDLED) || defined(CONTROL_ROUND_TRUNC)
    if (!color) return;
    Fmt ft = color_fmt(f);
    if (ft.kind == K_OTHER) return;
    size_t texels = n / ft.bytes;
    for (size_t t = 0; t < texels; ++t) {
        for (uint32_t c = 0; c < ft.channels; ++c) {
            bool alpha = ft.channels == 4 && c == 3;
#if defined(CONTROL_GAIN_X2)
            if (alpha) continue;
            if (ft.kind == K_UNORM8) { uint8_t& v = p[t * ft.bytes + c]; v = (uint8_t)std::min(255, 2 * v); }
            else if (ft.kind == K_F16) {
                uint16_t h; std::memcpy(&h, p + t * ft.bytes + 2 * c, 2);
                h = float_to_half(2.0f * half_to_float(h)); std::memcpy(p + t * ft.bytes + 2 * c, &h, 2);
            } else {
                float v; std::memcpy(&v, p + t * ft.bytes + 4 * c, 4);
                v *= 2.0f; std::memcpy(p + t * ft.bytes + 4 * c, &v, 4);
            }
#elif defined(CONTROL_SRGB_MISHANDLED)
            // the stored sRGB code is replaced by the linear value it encodes: encode skipped
            if (!ft.srgb || alpha) continue;
            uint8_t& v = p[t * ft.bytes + c];
            v = (uint8_t)std::lround(255.0f * srgb_to_linear(v / 255.0f));
#elif defined(CONTROL_ROUND_TRUNC)
            // truncating instead of rounding: about half of the codes land one below
            if (ft.kind != K_UNORM8 || alpha) continue;
            uint64_t z = (t * 4 + c) * 0x9E3779B97F4A7C15ull;
            z ^= z >> 29;
            uint8_t& v = p[t * ft.bytes + c];
            if ((z & 1) && v > 0) --v;
#endif
        }
    }
#endif
#if defined(CONTROL_AUX_GARBAGE)
    // garbage in every depth / stencil readback
    if (color) return;
    uint64_t z = 0x243F6A8885A308D3ull;
    for (size_t i = 0; i < n; ++i) { z = z * 6364136223846793005ull + 1442695040888963407ull; p[i] = (uint8_t)(z >> 56); }
#endif
#if defined(CONTROL_STALE_FRAME)
    // the previous readback of the same image (aspect, mip) is returned again
    std::string k = std::to_string(key(rb.image)) + "/" + std::to_string(rb.aspect) + "/" + std::to_string(rb.mip);
    std::string now((const char*)p, n);
    auto it = g_last.find(k);
    if (it != g_last.end() && it->second.size() == n) std::memcpy(p, it->second.data(), n);
    g_last[k] = now;
#endif
}

void apply_pending() {
    std::vector<Readback> todo;
    {
        std::lock_guard<std::mutex> l(g_mu);
        todo.swap(g_pending);
    }
    for (const Readback& rb : todo) {
        auto bi = g_buffers.find(key(rb.buffer));
        if (bi == g_buffers.end()) continue;
        auto mi = g_maps.find(key(bi->second.mem));
        if (mi == g_maps.end() || !rb.tight) continue;
        VkFormat f = g_images.count(key(rb.image)) ? g_images[key(rb.image)].format : VK_FORMAT_UNDEFINED;
        uint32_t tb = (rb.aspect & VK_IMAGE_ASPECT_COLOR_BIT) ? color_fmt(f).bytes : depth_bytes(f, rb.aspect);
        size_t avail = bi->second.size > rb.offset ? (size_t)(bi->second.size - rb.offset) : 0;
        size_t n = tb ? (size_t)tb * rb.w * rb.h * rb.d * rb.layers : avail;
        if (n > avail) n = avail;
        transform(rb, mi->second + bi->second.offset + rb.offset, n);
    }
}

constexpr bool kReadback =
#if defined(CONTROL_GAIN_X2) || defined(CONTROL_SRGB_MISHANDLED) || defined(CONTROL_ROUND_TRUNC) || \
    defined(CONTROL_AUX_GARBAGE) || defined(CONTROL_STALE_FRAME)
    true;
#else
    false;
#endif

// ------------------------------------------------------------------ intercepted entry points

VKAPI_ATTR VkResult VKAPI_CALL shim_CreateInstance(const VkInstanceCreateInfo* ci, const VkAllocationCallbacks* a,
                                                   VkInstance* out) {
    auto real = (PFN_vkCreateInstance)(g_icd_gipa ? g_icd_gipa(VK_NULL_HANDLE, "vkCreateInstance") : nullptr);
    if (!real) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = real(ci, a, out);
    if (r == VK_SUCCESS) {
        std::lock_guard<std::mutex> l(g_mu);
        g_instance = *out;
        g_inst_cache.clear();
    }
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_CreateDevice(VkPhysicalDevice pd, const VkDeviceCreateInfo* ci,
                                                 const VkAllocationCallbacks* a, VkDevice* out) {
#if defined(CONTROL_INIT_ONLY)
    return VK_ERROR_INITIALIZATION_FAILED;
#endif
    if (g_degraded) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = RI(vkCreateDevice)(pd, ci, a, out);
    if (r == VK_SUCCESS) {
        std::lock_guard<std::mutex> l(g_mu);
        g_device = *out;
        g_real_gdpa = (PFN_vkGetDeviceProcAddr)(g_icd_gipa(g_instance, "vkGetDeviceProcAddr"));
        g_dev_cache.clear();
    }
    return r;
}

// --- physical-device queries (wrong_limits, wrong_errors)

VKAPI_ATTR void VKAPI_CALL shim_GetPhysicalDeviceProperties(VkPhysicalDevice pd, VkPhysicalDeviceProperties* p) {
    RI(vkGetPhysicalDeviceProperties)(pd, p);
#if defined(CONTROL_WRONG_LIMITS)
    p->limits.maxImageDimension2D /= 2;
    p->limits.maxBoundDescriptorSets -= 1;
#endif
}

VKAPI_ATTR void VKAPI_CALL shim_GetPhysicalDeviceProperties2(VkPhysicalDevice pd, VkPhysicalDeviceProperties2* p) {
    RI(vkGetPhysicalDeviceProperties2)(pd, p);
#if defined(CONTROL_WRONG_LIMITS)
    p->properties.limits.maxImageDimension2D /= 2;
    p->properties.limits.maxBoundDescriptorSets -= 1;
#endif
}

VKAPI_ATTR void VKAPI_CALL shim_GetPhysicalDeviceFormatProperties(VkPhysicalDevice pd, VkFormat f, VkFormatProperties* p) {
    RI(vkGetPhysicalDeviceFormatProperties)(pd, f, p);
#if defined(CONTROL_WRONG_LIMITS)
    if (f == VK_FORMAT_R8G8B8A8_UNORM) p->optimalTilingFeatures &= ~VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT;
#endif
}

VKAPI_ATTR void VKAPI_CALL shim_GetPhysicalDeviceFormatProperties2(VkPhysicalDevice pd, VkFormat f, VkFormatProperties2* p) {
    RI(vkGetPhysicalDeviceFormatProperties2)(pd, f, p);
#if defined(CONTROL_WRONG_LIMITS)
    if (f == VK_FORMAT_R8G8B8A8_UNORM) {
        p->formatProperties.optimalTilingFeatures &= ~VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT;
        for (auto* s = (VkBaseOutStructure*)p->pNext; s; s = s->pNext)
            if (s->sType == VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3)
                ((VkFormatProperties3*)s)->optimalTilingFeatures &= ~VK_FORMAT_FEATURE_2_STORAGE_IMAGE_BIT;
    }
#endif
}

VKAPI_ATTR VkResult VKAPI_CALL shim_GetPhysicalDeviceImageFormatProperties(
    VkPhysicalDevice pd, VkFormat f, VkImageType t, VkImageTiling ti, VkImageUsageFlags u, VkImageCreateFlags fl,
    VkImageFormatProperties* p) {
    VkResult r = RI(vkGetPhysicalDeviceImageFormatProperties)(pd, f, t, ti, u, fl, p);
#if defined(CONTROL_WRONG_ERRORS)
    if (r == VK_ERROR_FORMAT_NOT_SUPPORTED) { std::memset(p, 0, sizeof(*p)); r = VK_SUCCESS; }
#endif
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_GetPhysicalDeviceImageFormatProperties2(
    VkPhysicalDevice pd, const VkPhysicalDeviceImageFormatInfo2* i, VkImageFormatProperties2* p) {
    VkResult r = RI(vkGetPhysicalDeviceImageFormatProperties2)(pd, i, p);
#if defined(CONTROL_WRONG_ERRORS)
    if (r == VK_ERROR_FORMAT_NOT_SUPPORTED) { std::memset(&p->imageFormatProperties, 0, sizeof(p->imageFormatProperties)); r = VK_SUCCESS; }
#endif
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_EnumerateDeviceExtensionProperties(VkPhysicalDevice pd, const char* layer,
                                                                       uint32_t* n, VkExtensionProperties* p) {
    VkResult r = RI(vkEnumerateDeviceExtensionProperties)(pd, layer, n, p);
#if defined(CONTROL_WRONG_ERRORS)
    if (r == VK_INCOMPLETE) r = VK_SUCCESS;
#endif
    return r;
}

// --- resources

VKAPI_ATTR VkResult VKAPI_CALL shim_CreateImage(VkDevice d, const VkImageCreateInfo* ci, const VkAllocationCallbacks* a,
                                                VkImage* out) {
    VkResult r = RD(vkCreateImage)(d, ci, a, out);
    if (r == VK_SUCCESS) g_images[key(*out)] = {ci->format};
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_CreateBuffer(VkDevice d, const VkBufferCreateInfo* ci, const VkAllocationCallbacks* a,
                                                 VkBuffer* out) {
    VkResult r = RD(vkCreateBuffer)(d, ci, a, out);
    if (r == VK_SUCCESS) g_buffers[key(*out)].size = ci->size;
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_BindBufferMemory(VkDevice d, VkBuffer b, VkDeviceMemory m, VkDeviceSize off) {
    VkResult r = RD(vkBindBufferMemory)(d, b, m, off);
    if (r == VK_SUCCESS) { g_buffers[key(b)].mem = m; g_buffers[key(b)].offset = off; }
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_BindBufferMemory2(VkDevice d, uint32_t n, const VkBindBufferMemoryInfo* i) {
    VkResult r = RD(vkBindBufferMemory2)(d, n, i);
    if (r == VK_SUCCESS)
        for (uint32_t k = 0; k < n; ++k) { g_buffers[key(i[k].buffer)].mem = i[k].memory; g_buffers[key(i[k].buffer)].offset = i[k].memoryOffset; }
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_MapMemory(VkDevice d, VkDeviceMemory m, VkDeviceSize off, VkDeviceSize size,
                                              VkMemoryMapFlags f, void** pp) {
    VkResult r = RD(vkMapMemory)(d, m, off, size, f, pp);
    if (r == VK_SUCCESS && pp && *pp) g_maps[key(m)] = (uint8_t*)*pp - off;
    return r;
}

VKAPI_ATTR void VKAPI_CALL shim_UnmapMemory(VkDevice d, VkDeviceMemory m) {
    g_maps.erase(key(m));
    RD(vkUnmapMemory)(d, m);
}

VKAPI_ATTR VkResult VKAPI_CALL shim_AllocateMemory(VkDevice d, const VkMemoryAllocateInfo* ai,
                                                   const VkAllocationCallbacks* a, VkDeviceMemory* m) {
    VkResult r = RD(vkAllocateMemory)(d, ai, a, m);
#if defined(CONTROL_POISON)
    void* p = nullptr;
    if (r == VK_SUCCESS && RD(vkMapMemory)(d, *m, 0, VK_WHOLE_SIZE, 0, &p) == VK_SUCCESS && p) {
        std::memset(p, 0xA5, (size_t)ai->allocationSize);
        RD(vkUnmapMemory)(d, *m);
    }
#endif
    return r;
}

VKAPI_ATTR void VKAPI_CALL shim_GetBufferMemoryRequirements(VkDevice d, VkBuffer b, VkMemoryRequirements* r) {
    RD(vkGetBufferMemoryRequirements)(d, b, r);
#if defined(CONTROL_MALFORMED)
    r->size = 1ull << 60;  // absurd
#endif
}

VKAPI_ATTR void VKAPI_CALL shim_GetImageMemoryRequirements(VkDevice d, VkImage im, VkMemoryRequirements* r) {
    RD(vkGetImageMemoryRequirements)(d, im, r);
#if defined(CONTROL_MALFORMED)
    r->size = 1ull << 60;
#endif
}

VKAPI_ATTR VkResult VKAPI_CALL shim_CreateSampler(VkDevice d, const VkSamplerCreateInfo* ci, const VkAllocationCallbacks* a,
                                                  VkSampler* out) {
#if defined(CONTROL_NEAREST_FILTER)
    VkSamplerCreateInfo c = *ci;
    c.magFilter = VK_FILTER_NEAREST;
    c.minFilter = VK_FILTER_NEAREST;
    return RD(vkCreateSampler)(d, &c, a, out);
#else
    return RD(vkCreateSampler)(d, ci, a, out);
#endif
}

VKAPI_ATTR VkResult VKAPI_CALL shim_CreateGraphicsPipelines(VkDevice d, VkPipelineCache pc, uint32_t n,
                                                            const VkGraphicsPipelineCreateInfo* ci,
                                                            const VkAllocationCallbacks* a, VkPipeline* out) {
#if defined(CONTROL_HALF_PIXEL)
    // every static viewport moves half a pixel right and down
    std::vector<VkGraphicsPipelineCreateInfo> c(ci, ci + n);
    std::vector<VkPipelineViewportStateCreateInfo> vs(n);
    std::vector<std::vector<VkViewport>> vps(n);
    for (uint32_t i = 0; i < n; ++i) {
        if (!c[i].pViewportState || !c[i].pViewportState->pViewports) continue;
        vs[i] = *c[i].pViewportState;
        vps[i].assign(vs[i].pViewports, vs[i].pViewports + vs[i].viewportCount);
        for (VkViewport& v : vps[i]) { v.x += 0.5f; v.y += 0.5f; }
        vs[i].pViewports = vps[i].data();
        c[i].pViewportState = &vs[i];
    }
    return RD(vkCreateGraphicsPipelines)(d, pc, n, c.data(), a, out);
#else
    return RD(vkCreateGraphicsPipelines)(d, pc, n, ci, a, out);
#endif
}

// --- commands

VKAPI_ATTR VkResult VKAPI_CALL shim_BeginCommandBuffer(VkCommandBuffer cb, const VkCommandBufferBeginInfo* bi) {
    g_cb_readbacks.erase(cb);
    return RD(vkBeginCommandBuffer)(cb, bi);
}

VKAPI_ATTR void VKAPI_CALL shim_CmdCopyImageToBuffer(VkCommandBuffer cb, VkImage im, VkImageLayout l, VkBuffer b,
                                                     uint32_t n, const VkBufferImageCopy* r) {
    RD(vkCmdCopyImageToBuffer)(cb, im, l, b, n, r);
    if (!kReadback) return;
    for (uint32_t i = 0; i < n; ++i) {
        const VkBufferImageCopy& c = r[i];
        bool tight = n == 1 && c.bufferRowLength == 0 && c.bufferImageHeight == 0 &&
                     c.imageOffset.x == 0 && c.imageOffset.y == 0 && c.imageOffset.z == 0;
        g_cb_readbacks[cb].push_back({im, b, c.bufferOffset, c.imageSubresource.aspectMask, c.imageSubresource.mipLevel,
                                      c.imageExtent.width, c.imageExtent.height, c.imageExtent.depth,
                                      c.imageSubresource.layerCount, tight});
    }
}

VKAPI_ATTR void VKAPI_CALL shim_CmdSetViewport(VkCommandBuffer cb, uint32_t first, uint32_t n, const VkViewport* v) {
#if defined(CONTROL_HALF_PIXEL)
    std::vector<VkViewport> c(v, v + n);
    for (VkViewport& x : c) { x.x += 0.5f; x.y += 0.5f; }
    RD(vkCmdSetViewport)(cb, first, n, c.data());
#else
    RD(vkCmdSetViewport)(cb, first, n, v);
#endif
}

VKAPI_ATTR void VKAPI_CALL shim_CmdSetViewportWithCount(VkCommandBuffer cb, uint32_t n, const VkViewport* v) {
#if defined(CONTROL_HALF_PIXEL)
    std::vector<VkViewport> c(v, v + n);
    for (VkViewport& x : c) { x.x += 0.5f; x.y += 0.5f; }
    RD(vkCmdSetViewportWithCount)(cb, n, c.data());
#else
    RD(vkCmdSetViewportWithCount)(cb, n, v);
#endif
}

// --- submission and waits

int g_submits = 0;

void note_submit(uint32_t n, const VkSubmitInfo* s) {
    if (!kReadback) return;
    std::lock_guard<std::mutex> l(g_mu);
    for (uint32_t i = 0; i < n; ++i)
        for (uint32_t k = 0; k < s[i].commandBufferCount; ++k) {
            auto it = g_cb_readbacks.find(s[i].pCommandBuffers[k]);
            if (it != g_cb_readbacks.end()) g_pending.insert(g_pending.end(), it->second.begin(), it->second.end());
        }
}

VKAPI_ATTR VkResult VKAPI_CALL shim_QueueSubmit(VkQueue q, uint32_t n, const VkSubmitInfo* s, VkFence f) {
#if defined(CONTROL_CRASH)
    if (++g_submits == 2) raise(SIGSEGV);
#endif
#if defined(CONTROL_SUCCESS_EVERYWHERE)
    (void)q; (void)n; (void)s; (void)f;
    return VK_SUCCESS;  // nothing is executed; waits below report success
#endif
    note_submit(n, s);
    return RD(vkQueueSubmit)(q, n, s, f);
}

VKAPI_ATTR VkResult VKAPI_CALL shim_WaitForFences(VkDevice d, uint32_t n, const VkFence* f, VkBool32 all, uint64_t t) {
#if defined(CONTROL_SUCCESS_EVERYWHERE)
    (void)d; (void)n; (void)f; (void)all; (void)t;
    return VK_SUCCESS;
#endif
    VkResult r = RD(vkWaitForFences)(d, n, f, all, t);
#if defined(CONTROL_WRONG_ERRORS)
    if (r == VK_TIMEOUT) r = VK_SUCCESS;
#endif
    if (r == VK_SUCCESS) apply_pending();
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_GetFenceStatus(VkDevice d, VkFence f) {
#if defined(CONTROL_SUCCESS_EVERYWHERE)
    (void)d; (void)f;
    return VK_SUCCESS;
#endif
    VkResult r = RD(vkGetFenceStatus)(d, f);
#if defined(CONTROL_WRONG_ERRORS)
    if (r == VK_NOT_READY) r = VK_SUCCESS;
#endif
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_WaitSemaphores(VkDevice d, const VkSemaphoreWaitInfo* i, uint64_t t) {
#if defined(CONTROL_SUCCESS_EVERYWHERE)
    (void)d; (void)i; (void)t;
    return VK_SUCCESS;
#endif
    VkResult r = RD(vkWaitSemaphores)(d, i, t);
#if defined(CONTROL_WRONG_ERRORS)
    if (r == VK_TIMEOUT) r = VK_SUCCESS;
#endif
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_GetQueryPoolResults(VkDevice d, VkQueryPool p, uint32_t first, uint32_t n, size_t size,
                                                        void* data, VkDeviceSize stride, VkQueryResultFlags fl) {
#if defined(CONTROL_SUCCESS_EVERYWHERE)
    (void)d; (void)p; (void)first; (void)n; (void)stride; (void)fl;
    std::memset(data, 0, size);  // nothing ran; a wait would never end
    return VK_SUCCESS;
#endif
    return RD(vkGetQueryPoolResults)(d, p, first, n, size, data, stride, fl);
}

VKAPI_ATTR VkResult VKAPI_CALL shim_QueueWaitIdle(VkQueue q) {
    VkResult r = RD(vkQueueWaitIdle)(q);
    if (r == VK_SUCCESS) apply_pending();
    return r;
}

VKAPI_ATTR VkResult VKAPI_CALL shim_DeviceWaitIdle(VkDevice d) {
    VkResult r = RD(vkDeviceWaitIdle)(d);
    if (r == VK_SUCCESS) apply_pending();
    return r;
}

PFN_vkVoidFunction shim_lookup(const char* name);

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL shim_GetDeviceProcAddr(VkDevice d, const char* name) {
    PFN_vkVoidFunction real = g_real_gdpa ? g_real_gdpa(d, name) : nullptr;
    if (!real) return nullptr;
    PFN_vkVoidFunction ours = shim_lookup(name);
    return ours ? ours : real;
}

#define ENTRY(n, f) {n, (PFN_vkVoidFunction)f}
const std::unordered_map<std::string, PFN_vkVoidFunction>& table() {
    static const std::unordered_map<std::string, PFN_vkVoidFunction> t = {
        ENTRY("vkCreateInstance", shim_CreateInstance),
        ENTRY("vkCreateDevice", shim_CreateDevice),
        ENTRY("vkGetDeviceProcAddr", shim_GetDeviceProcAddr),
        ENTRY("vkGetPhysicalDeviceProperties", shim_GetPhysicalDeviceProperties),
        ENTRY("vkGetPhysicalDeviceProperties2", shim_GetPhysicalDeviceProperties2),
        ENTRY("vkGetPhysicalDeviceProperties2KHR", shim_GetPhysicalDeviceProperties2),
        ENTRY("vkGetPhysicalDeviceFormatProperties", shim_GetPhysicalDeviceFormatProperties),
        ENTRY("vkGetPhysicalDeviceFormatProperties2", shim_GetPhysicalDeviceFormatProperties2),
        ENTRY("vkGetPhysicalDeviceFormatProperties2KHR", shim_GetPhysicalDeviceFormatProperties2),
        ENTRY("vkGetPhysicalDeviceImageFormatProperties", shim_GetPhysicalDeviceImageFormatProperties),
        ENTRY("vkGetPhysicalDeviceImageFormatProperties2", shim_GetPhysicalDeviceImageFormatProperties2),
        ENTRY("vkGetPhysicalDeviceImageFormatProperties2KHR", shim_GetPhysicalDeviceImageFormatProperties2),
        ENTRY("vkEnumerateDeviceExtensionProperties", shim_EnumerateDeviceExtensionProperties),
        ENTRY("vkCreateImage", shim_CreateImage),
        ENTRY("vkCreateBuffer", shim_CreateBuffer),
        ENTRY("vkBindBufferMemory", shim_BindBufferMemory),
        ENTRY("vkBindBufferMemory2", shim_BindBufferMemory2),
        ENTRY("vkBindBufferMemory2KHR", shim_BindBufferMemory2),
        ENTRY("vkAllocateMemory", shim_AllocateMemory),
        ENTRY("vkMapMemory", shim_MapMemory),
        ENTRY("vkUnmapMemory", shim_UnmapMemory),
        ENTRY("vkGetBufferMemoryRequirements", shim_GetBufferMemoryRequirements),
        ENTRY("vkGetImageMemoryRequirements", shim_GetImageMemoryRequirements),
        ENTRY("vkCreateSampler", shim_CreateSampler),
        ENTRY("vkCreateGraphicsPipelines", shim_CreateGraphicsPipelines),
        ENTRY("vkBeginCommandBuffer", shim_BeginCommandBuffer),
        ENTRY("vkCmdCopyImageToBuffer", shim_CmdCopyImageToBuffer),
        ENTRY("vkCmdSetViewport", shim_CmdSetViewport),
        ENTRY("vkCmdSetViewportWithCount", shim_CmdSetViewportWithCount),
        ENTRY("vkCmdSetViewportWithCountEXT", shim_CmdSetViewportWithCount),
        ENTRY("vkQueueSubmit", shim_QueueSubmit),
        ENTRY("vkWaitForFences", shim_WaitForFences),
        ENTRY("vkGetFenceStatus", shim_GetFenceStatus),
        ENTRY("vkWaitSemaphores", shim_WaitSemaphores),
        ENTRY("vkWaitSemaphoresKHR", shim_WaitSemaphores),
        ENTRY("vkGetQueryPoolResults", shim_GetQueryPoolResults),
        ENTRY("vkQueueWaitIdle", shim_QueueWaitIdle),
        ENTRY("vkDeviceWaitIdle", shim_DeviceWaitIdle),
    };
    return t;
}

PFN_vkVoidFunction shim_lookup(const char* name) {
    auto it = table().find(name);
    return it == table().end() ? nullptr : it->second;
}

}  // namespace

// ------------------------------------------------------------------ ICD exports

EXPORT VKAPI_ATTR VkResult VKAPI_CALL vk_icdNegotiateLoaderICDInterfaceVersion(uint32_t* v) {
    if (!g_negotiate) return VK_ERROR_INCOMPATIBLE_DRIVER;
    return g_negotiate(v);
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetInstanceProcAddr(VkInstance instance, const char* name) {
    if (!g_icd_gipa) return nullptr;
    PFN_vkVoidFunction real = g_icd_gipa(instance, name);
    if (!real) return nullptr;
    PFN_vkVoidFunction ours = shim_lookup(name);
    return ours ? ours : real;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetPhysicalDeviceProcAddr(VkInstance instance, const char* name) {
    if (!g_icd_gpdpa) return nullptr;
    PFN_vkVoidFunction real = g_icd_gpdpa(instance, name);
    if (!real) return nullptr;
    PFN_vkVoidFunction ours = shim_lookup(name);
    return ours ? ours : real;
}
