#!/bin/sh
# ssvk: entry point of the ssvk-ref image.
#
#   ssvk info                              pinned versions and unit-test summaries
#   ssvk with <icd> [--ini V] CMD...       run CMD with only that ICD visible to the loader
#   ssvk vulkaninfo <icd> [ARGS...]        shorthand for `with <icd> vulkaninfo ARGS`
#   ssvk drive | drive-candidate | drive-lavapipe ...    the replay driver (Stage 4)
#
# <icd> is llvm (oracle of record), subzero or lavapipe. The oracle runs with
# /opt/ssvk/ini/<V>/ as its working directory, because SwiftShader reads
# SwiftShader.ini from there; V is `default` (ThreadCount=4) unless given.
set -eu

use_icd() {
    icd=/opt/ssvk/icd/$1.json
    case $1 in llvm) icd=/opt/ssvk/icd/swiftshader-llvm.json ;; subzero) icd=/opt/ssvk/icd/swiftshader-subzero.json ;; esac
    [ -f "$icd" ] || { echo "ssvk: unknown ICD '$1' (llvm | subzero | lavapipe)" >&2; exit 2; }
    export VK_DRIVER_FILES="$icd" VK_ICD_FILENAMES="$icd"
}

cmd=${1:-info}
[ $# -gt 0 ] && shift
case $cmd in
info)
    cat /opt/ssvk/VERSIONS
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
drive|drive-candidate|drive-lavapipe)
    echo "ssvk: '$cmd' needs vkreplay, which is built in Stage 4" >&2
    exit 3
    ;;
*)
    echo "ssvk: unknown command '$cmd'" >&2
    exit 2
    ;;
esac
