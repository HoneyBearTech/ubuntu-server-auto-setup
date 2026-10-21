#!/usr/bin/env bash
# Browser tests of the web UI (Playwright, webui/frontend/e2e): keyboard use, focus, live regions and axe-core
# with real layout. Sets up what the smoke test (tests/webui-smoke.sh) does, without Docker: a certificate for
# the mock Proxmox API (tests/mock_pve.py), a published key, the mock itself, and the web UI from this checkout
# with an empty data directory, whose first-run setup code goes to the tests. The VM the run provisions already
# exists in the mock, so the run is a real ansible-playbook run that needs no SSH.
# Needs Python with requirements-ci.txt, the Ansible collections (collections/ or ANSIBLE_COLLECTIONS_PATH),
# openssl, ssh-keygen, curl, and the app's packages and Playwright's Chromium in webui/frontend
# (npm ci; npx playwright install chromium). Builds the app first, so the tests see this checkout's code.
# Usage: tests/webui-browser.sh [playwright test arguments]   for example --headed or -g "keyboard"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
APP_PORT="${APP_PORT:-18180}"
MOCK_PORT="${MOCK_PORT:-18106}"
KEYS_PORT="${KEYS_PORT:-18181}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-browser.XXXXXX")"
PIDS=()

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- app log"; tail -n 80 "$TMP/app.log" 2>/dev/null || true
    echo "--- mock log"; tail -n 20 "$TMP/mock.log" 2>/dev/null || true
  fi
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf "$TMP"
  [ "$status" -eq 0 ] && echo "Browser tests passed" || echo "Browser tests FAILED" >&2
}
trap cleanup EXIT

(cd webui/frontend && npm run --silent build >/dev/null)

# The same test files as the smoke test, for 127.0.0.1 instead of a Docker network.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -keyout "$TMP/api.key" -out "$TMP/api.crt" >/dev/null 2>&1
ssh-keygen -q -t ed25519 -N '' -C browser@ubuntu-server-auto-setup -f "$TMP/id_ed25519"
cp "$TMP/id_ed25519.pub" "$TMP/test.keys"

"$PYTHON" tests/mock_pve.py --cert "$TMP/api.crt" --key "$TMP/api.key" --keys-file "$TMP/test.keys" \
  --port "$MOCK_PORT" --keys-port "$KEYS_PORT" --bind 127.0.0.1 >"$TMP/mock.log" 2>&1 &
PIDS+=($!)
if [ -z "${ANSIBLE_COLLECTIONS_PATH:-}" ] && [ -d collections ]; then
  export ANSIBLE_COLLECTIONS_PATH="$ROOT/collections"
fi
USAS_HOST=127.0.0.1 USAS_PORT="$APP_PORT" USAS_PROJECT_DIR="$ROOT" USAS_DATA_DIR="$TMP/data" PYTHONPATH=webui \
  "$PYTHON" -m usas_web >"$TMP/app.log" 2>&1 &
PIDS+=($!)

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/healthz" >/dev/null 2>&1 &&
     curl -fsS --cacert "$TMP/api.crt" "https://127.0.0.1:$MOCK_PORT/mock/state" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$APP_PORT/healthz" >/dev/null
code="$(sed -n 's/^ *Setup code: //p' "$TMP/app.log" | tail -n 1)"
test -n "$code"

# The VM the run provisions exists already, so provisioning only asks the API and skips it.
curl -fsS --cacert "$TMP/api.crt" -X POST \
  -H "Authorization: PVEAPIToken=ansible@pve!test=00000000-0000-4000-8000-000000000000" \
  -d vmid=4061 -d name=srv-01 "https://127.0.0.1:$MOCK_PORT/api2/json/nodes/pve-test/qemu" >/dev/null

cd webui/frontend
USAS_URL="http://localhost:$APP_PORT" USAS_SETUP_CODE="$code" USAS_PVE_PORT="$MOCK_PORT" \
  USAS_PVE_CA="$TMP/api.crt" USAS_KEYS_URL="http://127.0.0.1:$KEYS_PORT/{{ github_user }}.keys" \
  npx playwright test "$@"
