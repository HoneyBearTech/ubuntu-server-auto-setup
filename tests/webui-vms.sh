#!/usr/bin/env bash
# Integration test of the VMs page's runs (tests/webui_vms.py): the container image, the mock Proxmox API and an
# Ubuntu 26.04 systemd container standing in for a VM the project didn't create, on a private Docker network.
# The mock lists that container as VM 4071 (its guest agent reports the container's address) and as VM 4072,
# a guest that claims the same address. The driver pins host keys, runs the baseline in check mode, refuses a
# real run until a check passed and it is confirmed, runs it, checks that a second check changes nothing and
# that a VM whose pinned key doesn't match is refused, and reads the audit log. Needs Docker and Python 3
# (standard library only). Container names are this test's own (usas-vms-*), so it can run next to make test.
# Usage: tests/webui-vms.sh [image]   (default: builds the image from this checkout as usas:vms)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
IMAGE="${1:-}"
NET=usas-vms
APP=usas-vms-app
MOCK=usas-vms-pve
SRV=usas-vms-srv
APP_PORT="${APP_PORT:-18280}"
MOCK_PORT="${MOCK_PORT:-18206}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-vms.XXXXXX")"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- app log"; docker logs "$APP" 2>&1 | tail -n 60 || true
    echo "--- mock log"; docker logs "$MOCK" 2>&1 | tail -n 20 || true
  fi
  docker rm -f "$APP" "$MOCK" "$SRV" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$TMP"
  [ "$status" -eq 0 ] && echo "VMs page integration test passed" || echo "VMs page integration test FAILED" >&2
}
trap cleanup EXIT

if [ -z "$IMAGE" ]; then
  IMAGE=usas:vms
  docker build -q -t "$IMAGE" . >/dev/null
fi
docker build -q -t usas-test-srv:latest tests/images/ubuntu-target >/dev/null

openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=pve-mock \
  -addext "subjectAltName=DNS:pve-mock,IP:127.0.0.1" -keyout "$TMP/api.key" -out "$TMP/api.crt" >/dev/null 2>&1
ssh-keygen -q -t ed25519 -N '' -C vms@ubuntu-server-auto-setup -f "$TMP/id_ed25519"
cp "$TMP/id_ed25519.pub" "$TMP/test.keys"
chmod 755 "$TMP" && chmod 644 "$TMP"/*

docker rm -f "$APP" "$MOCK" "$SRV" >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null

# A VM someone else set up: Ubuntu 26.04 with the image's own user "ubuntu", passwordless sudo, nothing of ours.
docker run -d --name "$SRV" --hostname app-4071 --network "$NET" --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock usas-test-srv:latest >/dev/null
docker exec "$SRV" sh -c 'usermod -aG sudo ubuntu \
  && echo "ubuntu ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/91-ubuntu && chmod 0440 /etc/sudoers.d/91-ubuntu \
  && install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.ssh && echo "127.0.1.1 app-4071" >> /etc/hosts'
ADDRESS="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$SRV")"
test -n "$ADDRESS"

docker run -d --name "$MOCK" --network "$NET" --network-alias pve-mock -p "127.0.0.1:$MOCK_PORT:8006" \
  -v "$ROOT/tests/mock_pve.py:/mock/mock_pve.py:ro" -v "$TMP:/certs:ro" --entrypoint python "$IMAGE" \
  /mock/mock_pve.py --cert /certs/api.crt --key /certs/api.key --keys-file /certs/test.keys --bind 0.0.0.0 \
  --vm-target "4071=$ADDRESS" --vm-target "4072=$ADDRESS" >/dev/null
docker run -d --name "$APP" --network "$NET" -p "127.0.0.1:$APP_PORT:8080" \
  --read-only --tmpfs /tmp --tmpfs /home/usas:uid=10001,gid=10001,mode=0700 --cap-drop ALL \
  --security-opt no-new-privileges:true "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' "$APP")" = healthy ]; then break; fi
  sleep 1
done
test "$(docker inspect -f '{{.State.Health.Status}}' "$APP")" = healthy
code="$(docker logs "$APP" 2>&1 | sed -n 's/^ *Setup code: //p' | tail -n 1)"
test -n "$code"
shadow_before="$(docker exec "$SRV" grep '^ubuntu:' /etc/shadow)"

python3 tests/webui_vms.py --url "http://localhost:$APP_PORT" --setup-code "$code" --ca "$TMP/api.crt" \
  --app "$APP" --srv "$SRV" --address "$ADDRESS"

# The VM outside the inventory got none of the Vault's secrets, and its user's password is untouched.
if docker exec "$SRV" sh -c 'grep -rIl -e "vms admin password" -e 00000000-0000-4000-8000-000000000000 /etc /home /root /var/log 2>/dev/null'; then
  echo "ERROR: a secret of the web UI reached the VM" >&2
  exit 1
fi
test "$(docker exec "$SRV" grep '^ubuntu:' /etc/shadow)" = "$shadow_before"
if docker exec "$APP" sh -c 'grep -rl -e 00000000-0000-4000-8000-000000000000 -e "vms admin password" /data/runs /data/audit.jsonl'; then
  echo "ERROR: a secret is in a run file or the audit log" >&2
  exit 1
fi
if [ -n "$(docker exec "$APP" find /data -type f -perm /077)" ]; then
  echo "ERROR: files in /data that others can read:" >&2
  docker exec "$APP" find /data -type f -perm /077 >&2
  exit 1
fi
