#!/usr/bin/env bash
# Remasters the real Ubuntu live-server ISO in the test "Proxmox node" container and checks the result,
# including that the BIOS and UEFI boot records match the original. Downloads about 3 GB. The role's own
# defaults apply (not tests/run.sh's test key), so Ubuntu's real signature on SHA256SUMS is checked with
# the CD image signing key that ships in roles/installer_iso/files/.
# build-installer asks the Proxmox API where to build, so tests/mock_pve.py answers; only the one node container
# this script starts is in the run (--limit pve-test).
# Used by .github/workflows/installer-iso.yml; runs locally too (needs Docker). Each play's full log goes to
# USAS_LOG_DIR if it's set (CI keeps it as an artifact); a play that fails shows its failed tasks.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-iso.XXXXXX")"
export TEST_TMP ANSIBLE_CONFIG="$ROOT/ansible.cfg"
PVE=usas-test-pve
ISO_DIR=/var/lib/vz/template/iso
LOGS="${USAS_LOG_DIR:-$TEST_TMP/logs}"
mkdir -p "$LOGS"
mock_pid=""
trap '[ -n "$mock_pid" ] && kill "$mock_pid" 2>/dev/null; docker rm -f "$PVE" >/dev/null 2>&1 || true; rm -rf "$TEST_TMP"' EXIT

ssh-keygen -q -t ed25519 -N '' -f "$TEST_TMP/id_ed25519"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 \
  -keyout "$TEST_TMP/api.key" -out "$TEST_TMP/api.crt" >/dev/null 2>&1
python3 tests/mock_pve.py --cert "$TEST_TMP/api.crt" --key "$TEST_TMP/api.key" \
  --keys-file "$TEST_TMP/id_ed25519.pub" >"$LOGS/mock-pve.log" 2>&1 &
mock_pid=$!
docker build -q -t usas-test-pve:latest -f tests/images/pve-node/Dockerfile tests/fixtures >/dev/null
docker rm -f "$PVE" >/dev/null 2>&1 || true
docker run -d --name "$PVE" -p 127.0.0.1:2211:22 usas-test-pve:latest >/dev/null
docker exec -i "$PVE" sh -c 'cat > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys' \
  <"$TEST_TMP/id_ed25519.pub"
for _ in $(seq 1 30); do ssh-keyscan -T 2 -p 2211 127.0.0.1 >"$TEST_TMP/known_hosts" 2>/dev/null && break; sleep 1; done
for _ in $(seq 1 30); do curl -fsk https://127.0.0.1:8006/mock/state >/dev/null 2>&1 && break; sleep 1; done
# A copy of the original ISO from an earlier run (CI caches it) saves the 3 GB download. The role still checks
# it against the signed SHA256SUMS and downloads it again if it doesn't match.
if [ -n "${USAS_ISO_CACHE:-}" ]; then
  cache="${USAS_ISO_CACHE/#\~/$HOME}"
  for iso in "$cache"/ubuntu-*-live-server-amd64.iso; do
    [ -f "$iso" ] && docker cp "$iso" "$PVE:$ISO_DIR/" && echo "=== Using the cached $(basename "$iso")"
  done
fi

# Runs a playbook into $LOGS/NAME.log and shows its recap; if it fails, shows the failed tasks instead.
play() {
  local name="$1"; shift
  if ! ansible-playbook -i tests/inventory/hosts.yml "$@" --limit pve-test >"$LOGS/$name.log" 2>&1; then
    tests/show-failures.sh "$LOGS/$name.log"
    echo "ERROR: ansible-playbook $* failed; the full log is $LOGS/$name.log" >&2
    return 1
  fi
  sed -n '/PLAY RECAP/,$p' "$LOGS/$name.log"
}

for run in first second; do
  echo "=== build-installer against releases.ubuntu.com ($run run)"
  play "build-installer-$run" playbooks/build-installer.yml -e ubuntu_iso_base_url=https://releases.ubuntu.com/26.04
done
if sed -n '/PLAY RECAP/,$p' "$LOGS/build-installer-second.log" | grep -Eq 'changed=[1-9]'; then
  grep -B3 -E '^changed:' "$LOGS/build-installer-second.log" || true
  echo "ERROR: the second run changed something" >&2
  exit 1
fi
play verify-installer tests/verify-installer.yml

echo "=== Boot records (original, then remastered); everything but the block addresses must match"
records() {
  docker exec "$PVE" xorriso -indev "$ISO_DIR/$1" -report_el_torito plain 2>/dev/null \
    | grep -E '^El Torito (boot img|img path|img opts|img blks)' | awk '{ $NF = ($3 == "boot") ? "" : $NF; print }'
}
records ubuntu-26.04.1-live-server-amd64.iso | tee "$TEST_TMP/orig.txt"
records ubuntu-26.04.1-live-server-amd64-autoinstall.iso | tee "$TEST_TMP/new.txt"
grep -q 'UEFI' "$TEST_TMP/orig.txt"
grep -q 'BIOS' "$TEST_TMP/orig.txt"
diff -u "$TEST_TMP/orig.txt" "$TEST_TMP/new.txt"
# For tests/secure-boot-install.sh, which boots it.
if [ -n "${USAS_KEEP_ISO:-}" ]; then
  docker cp "$PVE:$ISO_DIR/ubuntu-26.04.1-live-server-amd64-autoinstall.iso" "$USAS_KEEP_ISO"
fi
echo "=== The real ISO remasters correctly"
