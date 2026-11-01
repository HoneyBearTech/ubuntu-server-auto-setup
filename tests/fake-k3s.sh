#!/bin/sh
# Stands in for k3s on the containers of tests/updates.sh: records every call (time, host, arguments) in
# /var/log/usas-fake-k3s.log, which survives the container's reboot, and answers what the updates role asks:
# cordon, drain (fails while /etc/usas-fake-k3s-drain-fails exists), uncordon, a node's Ready and the Longhorn
# volumes (one attached volume degraded for the first two reads, then healthy).
# printf, not echo: dash's echo would turn the \n in a jsonpath argument into a line break.
printf '%s %s %s\n' "$(date +%s.%N)" "$(cat /proc/sys/kernel/hostname)" "$*" >>/var/log/usas-fake-k3s.log
[ "$1" = kubectl ] || exit 1
shift
case "$*" in
  cordon\ *) echo "node/$2 cordoned" ;;
  uncordon\ *) echo "node/$2 uncordoned" ;;
  drain\ *)
    if [ -e /etc/usas-fake-k3s-drain-fails ]; then
      echo "error: cannot evict pod default/web-0: Cannot evict pod as it would violate the pod's disruption budget." >&2
      exit 1
    fi
    echo "node/$2 drained" ;;
  get\ node\ *) echo True ;;
  *volumes.longhorn.io*)
    reads="$(cat /var/lib/usas-fake-k3s-longhorn 2>/dev/null || echo 0)"
    echo "$((reads + 1))" >/var/lib/usas-fake-k3s-longhorn
    if [ "$reads" -lt 2 ]; then state=degraded; else state=healthy; fi
    printf 'pvc-a=attached/%s\npvc-b=detached/unknown\n' "$state" ;;
  *) echo "fake k3s: unexpected kubectl $*" >&2; exit 1 ;;
esac
