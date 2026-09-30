// vkreplay: replay one case against a Vulkan driver (the oracle or a candidate ICD).
//
//   vkreplay [--candidate DIR | --icd MANIFEST] [options] CASE OUTDIR ASSETS
//
// The process started by the user is the trusted parent; it re-executes this
// binary with --child to run the case in an untrusted process. See parent.cpp.
#include <cstring>

int parent_main(int argc, char** argv);
int child_main(int argc, char** argv);

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--child") == 0) return child_main(argc - 1, argv + 1);
    return parent_main(argc, argv);
}
