#!/bin/sh
# ssvk: entry point of the ssvk-ref image.
#
#   ssvk info                              pinned versions and unit-test summaries
#   ssvk with <icd> [--ini V] CMD...       run CMD with only that ICD visible to the loader
#   ssvk vulkaninfo <icd> [ARGS...]        shorthand for `with <icd> vulkaninfo ARGS`
#   ssvk drive CASE OUT ASSETS             replay CASE on the oracle (see below)
#   ssvk drive-candidate DIR CASE OUT ASSETS   replay CASE on DIR/libvk_candidate.so
#   ssvk drive-lavapipe CASE OUT ASSETS    replay CASE on Mesa lavapipe (fairness runs)
#
# <icd> is llvm (oracle of record), subzero or lavapipe. The oracle runs with
# /opt/ssvk/ini/<V>/ as its working directory, because SwiftShader reads
# SwiftShader.ini from there; V is `default` (ThreadCount=4) unless given.
#
# `drive` reads the variant from the environment (set by instance.py):
#   DRIVER_PERTURB      "" | subzero | lavapipe | vtxjitter | texcoord_ulp
#   DRIVER_SAMPLE_MULT  1, or 2 = the thread-count variant (ini threads1), the
#                       reference cache's determinism run
#   DRIVER_VALIDATE     1 = run with the Khronos validation layer (generator gate)
#   DRIVER_TIMEOUT      seconds before vkreplay stops the case (default 600)
set -eu

manifest() {
    case $1 in
        llvm) echo /opt/ssvk/icd/swiftshader-llvm.json ;;
        subzero) echo /opt/ssvk/icd/swiftshader-subzero.json ;;
        lavapipe) echo /opt/ssvk/icd/lavapipe.json ;;
        *) echo "ssvk: unknown ICD '$1' (llvm | subzero | lavapipe)" >&2; exit 2 ;;
    esac
}

use_icd() {
    icd=$(manifest "$1")
    export VK_DRIVER_FILES="$icd" VK_ICD_FILENAMES="$icd"
}

common_flags() {
    flags="--timeout ${DRIVER_TIMEOUT:-600}"
    [ "${DRIVER_VALIDATE:-}" = 1 ] && flags="$flags --validate"
    echo "$flags"
}

cmd=${1:-info}
[ $# -gt 0 ] && shift
case $cmd in
info)
    cat /opt/ssvk/VERSIONS
    echo "vkreplay $(vkreplay --version | cut -d' ' -f2)"
    for b in llvm subzero; do echo "--- swiftshader/$b unit tests"; cat /opt/swiftshader/$b/unittests.txt; done
    ;;
with)
    use_icd "$1"; shift
    ini=default
    if [ "${1:-}" = "--ini" ]; then ini=$2; shift 2; fi
    [ -d "/opt/ssvk/ini/$ini" ] || { echo "ssvk: unknown ini variant '$ini'" >&2; exit 2; }
    cd "/opt/ssvk/ini/$ini"
    exec "$@"
    ;;
vulkaninfo)
    b=$1; shift
    exec "$0" with "$b" vulkaninfo "$@"
    ;;
drive)
    [ $# -eq 3 ] || { echo "usage: ssvk drive CASE OUT ASSETS" >&2; exit 2; }
    icd=llvm perturb=
    case ${DRIVER_PERTURB:-} in
        "") ;;
        subzero|lavapipe) icd=$DRIVER_PERTURB ;;
        vtxjitter|texcoord_ulp) perturb="--perturb $DRIVER_PERTURB" ;;
        *) echo "ssvk: unknown perturbation '$DRIVER_PERTURB'" >&2; exit 2 ;;
    esac
    ini=default
    [ "${DRIVER_SAMPLE_MULT:-1}" = 2 ] && ini=threads1
    # shellcheck disable=SC2046,SC2086
    exec vkreplay --icd "$(manifest $icd)" --cwd "/opt/ssvk/ini/$ini" $perturb $(common_flags) "$@"
    ;;
drive-candidate)
    [ $# -eq 4 ] || { echo "usage: ssvk drive-candidate DIR CASE OUT ASSETS" >&2; exit 2; }
    dir=$1; shift
    # shellcheck disable=SC2046
    exec vkreplay --candidate "$dir" $(common_flags) "$@"
    ;;
drive-lavapipe)
    DRIVER_PERTURB=lavapipe exec "$0" drive "$@"
    ;;
*)
    echo "ssvk: unknown command '$cmd'" >&2
    exit 2
    ;;
esac
