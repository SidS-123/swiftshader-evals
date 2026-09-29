#!/bin/sh
# retry.sh CMD... -- run a network-touching build step up to 5 times.
n=0
until "$@"; do
    n=$((n + 1))
    [ "$n" -ge 5 ] && { echo "retry.sh: giving up: $*" >&2; exit 1; }
    sleep $((n * 10))
done
