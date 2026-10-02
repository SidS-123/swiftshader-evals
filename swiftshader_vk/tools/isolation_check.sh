#!/usr/bin/env bash
# isolation_check.sh -- fails (exit 1, one line per problem) if the image it runs in gives
# a candidate anything to forward to or build a JIT on (PLAN_v1.md §13, D3). Run inside the
# solver image (TaskSpec.isolation_check, by the no-key smoke) and the candidate grading
# image; a deliberately contaminated image must fail it (tools/isolation_selftest.sh).
fail=0
bad() { echo "ISOLATION FAIL: $*"; fail=1; }
roots="/usr /opt /lib /lib64 /etc /var /home /root /tmp /srv"

# 1. no Vulkan driver manifest but the candidate's own (vkreplay writes that one at run time)
m=$(find $roots -xdev \( -name '*icd*.json' -o -path '*/icd.d/*.json' -o -path '*/icd/*.json' \) 2>/dev/null | head -5)
[ -n "$m" ] && bad "ICD manifests visible: $m"

# 2. no driver, compiler-backend or shader-compiler library
l=$(find $roots -xdev \( -name 'libvk_swiftshader*' -o -name 'libvulkan_lvp*' -o -name 'libLLVM*' \
      -o -name 'libclang*' -o -name 'libSPIRV-Tools*' -o -name 'libSPIRV.*' -o -name 'libglslang*' \
      -o -name 'libMachineIndependent*' -o -name 'libgccjit*' -o -name 'libvulkan_*.so*' \) 2>/dev/null | head -8)
[ -n "$l" ] && bad "forbidden libraries: $l"
command -v clang >/dev/null 2>&1 && bad "clang present: $(command -v clang)"
command -v glslangValidator >/dev/null 2>&1 && bad "glslangValidator present"
command -v spirv-opt >/dev/null 2>&1 && bad "spirv-opt present"

# 3. no network
if command -v python3 >/dev/null 2>&1; then
    python3 - <<'EOF' && bad "network reachable"
import socket, sys
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3).close()
    sys.exit(0)
except OSError:
    sys.exit(1)
EOF
fi
getent hosts github.com >/dev/null 2>&1 && bad "DNS resolves github.com"

# 4. no SwiftShader or lavapipe build on disk under another name
s=$(grep -rlsI --binary-files=text --exclude="$(basename "$0")" -e 'SwiftShader Device' -e 'llvmpipe (LLVM' $roots 2>/dev/null | head -5)
[ -n "$s" ] && bad "reference driver strings found in: $s"

[ $fail = 0 ] && echo "isolation ok"
exit $fail
