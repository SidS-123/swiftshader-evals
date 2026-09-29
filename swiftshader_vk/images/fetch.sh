#!/bin/sh
# fetch.sh URL REF DIR -- shallow-fetch one commit or tag into DIR, retried.
# Retries because DNS inside the WSL host drops lookups intermittently.
set -eu
url=$1 ref=$2 dir=$3
mkdir -p "$dir"
git -C "$dir" init -q
git -C "$dir" remote add origin "$url" 2>/dev/null || true
n=0
until git -C "$dir" fetch -q --depth 1 origin "$ref"; do
    n=$((n + 1))
    [ "$n" -ge 8 ] && { echo "fetch.sh: giving up on $url $ref" >&2; exit 1; }
    sleep $((n * 5))
done
git -C "$dir" checkout -q FETCH_HEAD
echo "fetched $url $ref -> $(git -C "$dir" rev-parse HEAD)"
