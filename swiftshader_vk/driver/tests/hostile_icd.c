/* A deliberately hostile candidate ICD for vkreplay's trust-boundary tests.
 *
 * When the loader loads it, it tries to (1) overwrite the output directory's
 * ledger and plant a snapshot, (2) leave a background process that tries again
 * after the parent has written its outputs, and (3) crash inside
 * vkCreateInstance. A correct vkreplay records a crash, keeps its own ledger,
 * and none of the hostile writes survive.
 *
 * Built by driver/tests/run_driver_tests.sh; never part of any image.
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <vulkan/vk_icd.h>
#include <vulkan/vulkan_core.h>

static void try_write(const char* what) {
    /* /out is the output mount in the grading container. (vkreplay gives the
       child a clean environment, so this cannot come from a variable.) */
    const char* out = "/out";
    char path[512];
    snprintf(path, sizeof path, "%s/%s", out, what);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        fprintf(stderr, "hostile: open %s: %s\n", path, strerror(errno));
        return;
    }
    const char* fake = "{\"exit\": \"ok\", \"events\": [], \"hostile\": true}\n";
    if (write(fd, fake, strlen(fake)) < 0) fprintf(stderr, "hostile: write failed\n");
    close(fd);
    fprintf(stderr, "hostile: WROTE %s\n", path);
}

__attribute__((constructor)) static void on_load(void) {
    try_write("ledger.json");
    try_write("snap_000.ssnap");
    pid_t p = fork();
    if (p == 0) {
        setsid();
        sleep(3);
        try_write("ledger.json");
        try_write("late.txt");
        _exit(0);
    }
}

static VKAPI_ATTR VkResult VKAPI_CALL hostile_create_instance(const VkInstanceCreateInfo* ci, const VkAllocationCallbacks* a,
                                                              VkInstance* out) {
    (void)ci; (void)a; (void)out;
    raise(SIGSEGV);
    return VK_ERROR_INITIALIZATION_FAILED;
}

static VKAPI_ATTR VkResult VKAPI_CALL hostile_enum_ext(const char* layer, uint32_t* n, VkExtensionProperties* p) {
    (void)layer; (void)p;
    *n = 0;
    return VK_SUCCESS;
}

VKAPI_ATTR VkResult VKAPI_CALL vk_icdNegotiateLoaderICDInterfaceVersion(uint32_t* v) {
    if (*v > 5) *v = 5;
    return VK_SUCCESS;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetInstanceProcAddr(VkInstance inst, const char* name) {
    (void)inst;
    if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)hostile_create_instance;
    if (!strcmp(name, "vkEnumerateInstanceExtensionProperties")) return (PFN_vkVoidFunction)hostile_enum_ext;
    return NULL;
}
