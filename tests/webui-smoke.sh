#!/usr/bin/env bash
# Smoke test of the container image: starts the image and the mock Proxmox API (tests/mock_pve.py) on a
# private Docker network, then drives the web UI like a browser (tests/webui_smoke.py): first-run setup,
# sign-in, the Proxmox connection test with the mock's CA certificate, the GitHub keys preview, saving every
# page, a provisioning run that the mock answers, and the VMs page against the mock's fixture VMs and guest
# agents. Needs Docker and Python 3 (standard library only).
# Usage: tests/webui-smoke.sh [image]   (default: builds the image from this checkout as usas:smoke)
# EXPECT_VERSION=<version> also checks the version the UI shows (the release passes its tag's).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
IMAGE="${1:-}"
NET=usas-smoke
APP=usas-smoke-app
MOCK=usas-smoke-pve
APP_PORT="${APP_PORT:-18080}"
MOCK_PORT="${MOCK_PORT:-18006}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-smoke.XXXXXX")"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- app log"; docker logs "$APP" 2>&1 | tail -n 80 || true
    echo "--- mock log"; docker logs "$MOCK" 2>&1 | tail -n 20 || true
  fi
  docker rm -f "$APP" "$MOCK" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$TMP"
  [ "$status" -eq 0 ] && echo "Smoke test passed" || echo "Smoke test FAILED" >&2
}
trap cleanup EXIT

if [ -z "$IMAGE" ]; then
  IMAGE=usas:smoke
  docker build -q -t "$IMAGE" . >/dev/null
fi

# The mock's certificate names it as the containers see it (pve-mock), and 127.0.0.1 for the test driver.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=pve-mock \
  -addext "subjectAltName=DNS:pve-mock,IP:127.0.0.1" -keyout "$TMP/api.key" -out "$TMP/api.crt" >/dev/null 2>&1
ssh-keygen -q -t ed25519 -N '' -C smoke@ubuntu-server-auto-setup -f "$TMP/id_ed25519"
cp "$TMP/id_ed25519.pub" "$TMP/test.keys"
chmod 755 "$TMP" && chmod 644 "$TMP"/*

docker rm -f "$APP" "$MOCK" >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null
# The mock runs in the app image (it has Python), as the image's user, with the test files mounted read-only.
docker run -d --name "$MOCK" --network "$NET" --network-alias pve-mock -p "127.0.0.1:$MOCK_PORT:8006" \
  -v "$ROOT/tests/mock_pve.py:/mock/mock_pve.py:ro" -v "$TMP:/certs:ro" --entrypoint python "$IMAGE" \
  /mock/mock_pve.py --cert /certs/api.crt --key /certs/api.key --keys-file /certs/test.keys --bind 0.0.0.0 \
  --fixture-vms >/dev/null
# The app as documented, plus the hardening from docker-compose.yml.
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

python3 tests/webui_smoke.py --url "http://localhost:$APP_PORT" --setup-code "$code" \
  --mock "https://127.0.0.1:$MOCK_PORT" --ca "$TMP/api.crt" --keys "$TMP/test.keys" \
  --version "${EXPECT_VERSION:-}"

# The image runs the playbooks with the repository's ansible.cfg, and its 30-second connection timeout.
docker exec "$APP" ansible-config dump --only-changed | grep -Eq '^DEFAULT_TIMEOUT\(/app/ansible.cfg\) = 30$'

# What the UI left in the volume: an encrypted Vault, private files, and a process that isn't root.
test "$(docker exec "$APP" id -u)" != 0
docker exec "$APP" sh -c 'head -c 14 /data/inventory/group_vars/all/vault.yml' | grep -q '^\$ANSIBLE_VAULT'
if docker exec "$APP" sh -c 'grep -rl 00000000-0000-4000-8000-000000000000 /data' 2>/dev/null; then
  echo "ERROR: the token secret is in a plain file in /data" >&2
  exit 1
fi
if [ -n "$(docker exec "$APP" find /data -type f -perm /077)" ]; then
  echo "ERROR: files in /data that others can read:" >&2
  docker exec "$APP" find /data -type f -perm /077 >&2
  exit 1
fi
