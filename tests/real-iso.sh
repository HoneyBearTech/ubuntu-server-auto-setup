#!/usr/bin/env bash
# Remasters the real Ubuntu live-server ISO in the test "Proxmox node" container and checks the result,
# including that the BIOS and UEFI boot records match the original. Downloads about 3 GB. The role's own
# defaults apply (not tests/run.sh's test key), so Ubuntu's real signature on SHA256SUMS is checked with
# the CD image signing key that ships in roles/installer_iso/files/.
# Used by .github/workflows/installer-iso.yml; runs locally too (needs Docker).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-iso.XXXXXX")"
export TEST_TMP ANSIBLE_CONFIG="$ROOT/ansible.cfg"
PVE=usas-test-pve
ISO_DIR=/var/lib/vz/template/iso
trap 'docker rm -f "$PVE" >/dev/null 2>&1 || true; rm -rf "$TEST_TMP"' EXIT

ssh-keygen -q -t ed25519 -N '' -f "$TEST_TMP/id_ed25519"
docker build -q -t usas-test-pve:latest -f tests/images/pve-node/Dockerfile tests/fixtures >/dev/null
docker rm -f "$PVE" >/dev/null 2>&1 || true
docker run -d --name "$PVE" -p 127.0.0.1:2211:22 usas-test-pve:latest >/dev/null
docker exec -i "$PVE" sh -c 'cat > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys' \
  <"$TEST_TMP/id_ed25519.pub"
for _ in $(seq 1 30); do ssh-keyscan -T 2 -p 2211 127.0.0.1 >"$TEST_TMP/known_hosts" 2>/dev/null && break; sleep 1; done

for run in first second; do
  echo "=== build-installer against releases.ubuntu.com ($run run)"
  ansible-playbook -i tests/inventory/hosts.yml playbooks/build-installer.yml \
    -e ubuntu_iso_base_url=https://releases.ubuntu.com/26.04 | tee "$TEST_TMP/$run.log" | sed -n '/PLAY RECAP/,$p'
done
if sed -n '/PLAY RECAP/,$p' "$TEST_TMP/second.log" | grep -Eq 'changed=[1-9]'; then
  echo "ERROR: the second run changed something" >&2
  exit 1
fi
ansible-playbook -i tests/inventory/hosts.yml tests/verify-installer.yml | sed -n '/PLAY RECAP/,$p'

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
