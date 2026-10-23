#!/bin/sh
# Runs a command up to four times, waiting 15, 30 and then 45 seconds between tries, and exits with the last
# try's status. For downloads that fail now and then for reasons outside this repository (Ansible Galaxy
# answering with an error or dropping the connection): CI and the Dockerfile run the collection installs
# through it. POSIX sh, since the image's build stage has no bash.
# Usage: scripts/retry.sh <command> [<argument>...]
set -u

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <command> [<argument>...]" >&2
    exit 2
fi

tries=4
try=1
while :; do
    "$@" && exit 0
    status=$?
    if [ "$try" -ge "$tries" ]; then
        echo "error: '$*' failed $tries times (last exit status $status)" >&2
        exit "$status"
    fi
    wait=$((try * 15))
    echo "'$*' failed (exit status $status, try $try of $tries); trying again in $wait seconds" >&2
    sleep "$wait"
    try=$((try + 1))
done
