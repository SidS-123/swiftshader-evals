// Starter: the smallest ICD the Vulkan loader accepts. It negotiates the
// loader-ICD interface, creates an instance, and reports no physical devices,
// so every case gets as far as vkEnumeratePhysicalDevices and no further.
// Replace all of it.
//
// The loader refuses an ICD that does not expose every core 1.0 instance-level
// entry point (even with zero devices, where they are never called), and treats
// an ICD without vkEnumerateInstanceVersion as Vulkan 1.0.
#include <vulkan/vk_icd.h>
#include <vulkan/vulkan_core.h>

#include <cstdlib>
#include <cstring>

namespace {

// Dispatchable handles (VkInstance, VkPhysicalDevice, VkDevice, VkQueue,
// VkCommandBuffer) must start with the loader's dispatch pointer slot.
struct Instance {
    VK_LOADER_DATA loader_data;
};

VKAPI_ATTR VkResult VKAPI_CALL EnumerateInstanceVersion(uint32_t* version) {
    *version = VK_API_VERSION_1_3;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL CreateInstance(const VkInstanceCreateInfo*, const VkAllocationCallbacks*, VkInstance* out) {
    auto* inst = static_cast<Instance*>(std::calloc(1, sizeof(Instance)));
    if (!inst) return VK_ERROR_OUT_OF_HOST_MEMORY;
    set_loader_magic_value(inst);
    *out = reinterpret_cast<VkInstance>(inst);
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL DestroyInstance(VkInstance instance, const VkAllocationCallbacks*) {
    std::free(reinterpret_cast<Instance*>(instance));
}

VKAPI_ATTR VkResult VKAPI_CALL EnumerateInstanceExtensionProperties(const char*, uint32_t* count, VkExtensionProperties*) {
    *count = 0;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL EnumeratePhysicalDevices(VkInstance, uint32_t* count, VkPhysicalDevice*) {
    *count = 0;
    return VK_SUCCESS;
}

// With no physical devices none of these is ever called; they exist so the loader accepts the ICD.
VKAPI_ATTR void VKAPI_CALL GetPhysicalDeviceFeatures(VkPhysicalDevice, VkPhysicalDeviceFeatures*) {}
VKAPI_ATTR void VKAPI_CALL GetPhysicalDeviceFormatProperties(VkPhysicalDevice, VkFormat, VkFormatProperties*) {}
VKAPI_ATTR VkResult VKAPI_CALL GetPhysicalDeviceImageFormatProperties(VkPhysicalDevice, VkFormat, VkImageType, VkImageTiling,
                                                                      VkImageUsageFlags, VkImageCreateFlags,
                                                                      VkImageFormatProperties*) {
    return VK_ERROR_FORMAT_NOT_SUPPORTED;
}
VKAPI_ATTR void VKAPI_CALL GetPhysicalDeviceProperties(VkPhysicalDevice, VkPhysicalDeviceProperties*) {}
VKAPI_ATTR void VKAPI_CALL GetPhysicalDeviceQueueFamilyProperties(VkPhysicalDevice, uint32_t* count, VkQueueFamilyProperties*) {
    *count = 0;
}
VKAPI_ATTR void VKAPI_CALL GetPhysicalDeviceMemoryProperties(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties*) {}
VKAPI_ATTR void VKAPI_CALL GetPhysicalDeviceSparseImageFormatProperties(VkPhysicalDevice, VkFormat, VkImageType,
                                                                        VkSampleCountFlagBits, VkImageUsageFlags, VkImageTiling,
                                                                        uint32_t* count, VkSparseImageFormatProperties*) {
    *count = 0;
}
VKAPI_ATTR VkResult VKAPI_CALL EnumerateDeviceExtensionProperties(VkPhysicalDevice, const char*, uint32_t* count,
                                                                  VkExtensionProperties*) {
    *count = 0;
    return VK_SUCCESS;
}
VKAPI_ATTR VkResult VKAPI_CALL CreateDevice(VkPhysicalDevice, const VkDeviceCreateInfo*, const VkAllocationCallbacks*, VkDevice*) {
    return VK_ERROR_INITIALIZATION_FAILED;
}
VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL GetDeviceProcAddr(VkDevice, const char*) {
    return nullptr;
}

struct Entry {
    const char* name;
    PFN_vkVoidFunction fn;
};
#define ENTRY(n) {"vk" #n, reinterpret_cast<PFN_vkVoidFunction>(n)}
const Entry kInstanceEntries[] = {
    ENTRY(EnumerateInstanceVersion), ENTRY(CreateInstance), ENTRY(DestroyInstance),
    ENTRY(EnumerateInstanceExtensionProperties), ENTRY(EnumeratePhysicalDevices), ENTRY(GetPhysicalDeviceFeatures),
    ENTRY(GetPhysicalDeviceFormatProperties), ENTRY(GetPhysicalDeviceImageFormatProperties),
    ENTRY(GetPhysicalDeviceProperties), ENTRY(GetPhysicalDeviceQueueFamilyProperties),
    ENTRY(GetPhysicalDeviceMemoryProperties), ENTRY(GetPhysicalDeviceSparseImageFormatProperties),
    ENTRY(EnumerateDeviceExtensionProperties), ENTRY(CreateDevice), ENTRY(GetDeviceProcAddr),
};
#undef ENTRY

}  // namespace

extern "C" {

VKAPI_ATTR VkResult VKAPI_CALL vk_icdNegotiateLoaderICDInterfaceVersion(uint32_t* version) {
    if (*version > 5) *version = 5;
    return VK_SUCCESS;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetInstanceProcAddr(VkInstance, const char* name) {
    for (const Entry& e : kInstanceEntries)
        if (!std::strcmp(name, e.name)) return e.fn;
    return nullptr;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetPhysicalDeviceProcAddr(VkInstance, const char*) {
    return nullptr;
}

}  // extern "C"
