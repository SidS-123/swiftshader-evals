#!/usr/bin/env bash
# build_ref.sh [TARGET] -- build the ssvk-ref image from pins.lock.
# Writes the build log to docs/internal/build-logs/ and prints the image id.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
target=${1:-ref}
args=()
while IFS='=' read -r k v; do
    [[ -z $k || $k == \#* ]] && continue
    args+=(--build-arg "$k=$v")
done < "$here/pins.lock"
logdir=$here/../docs/internal/build-logs
mkdir -p "$logdir"
log=$logdir/ref-$target-$(date -u +%Y%m%dT%H%M%SZ).log
tag=ssvk-ref:1
[[ $target != ref ]] && tag=ssvk-ref-stage:$target
DOCKER_BUILDKIT=1 docker build --progress=plain -f "$here/ref.Dockerfile" --target "$target" \
    --build-context driver="$here/../driver" \
    "${args[@]}" -t "$tag" "$here" 2>&1 | tee "$log"
id=$(docker image inspect --format '{{.Id}}' "$tag")
echo "image $tag id $id" | tee -a "$log"
