#!/usr/bin/env bash
# One release's real cloud image, end to end, the way the playbooks install it on Proxmox VE and then harden it:
#   - downloads the image the OS catalog pins (or takes it from the cache) and checks the catalog's checksum,
#   - renders the proxmox_vm role's own seed (cloud-user-data.j2, network-config.j2, meta-data.j2) for a static
#     address, packs it as the CIDATA ISO the role attaches,
#   - boots the image on a copy of its disk in a VM set up as Proxmox sets up the role's VMs (q35, OVMF with
#     Secure Boot and Microsoft's keys, VirtIO SCSI and network, the seed on an IDE CD-ROM) and waits for the
#     seed's poweroff, then boots it again without the seed, as provisioning does,
#   - checks what the first boot left: Secure Boot on, cloud-init disabled, the host name, the admin user's sudoers
#     file,
#   - runs playbooks/baseline.yml twice (the second run must change nothing) and tests/verify-baseline.yml,
#     moves SSH to port 2222 and checks again; on the RHEL family it also checks SELinux is enforcing and allows
#     the new port.
# That is what the containers of tests/run.sh and tests/baseline.sh can't show: a real cloud-init first boot, a
# real kernel and firewall, Secure Boot with the vendor's shim, and SELinux enforcing.
# Needs Linux with KVM, qemu-system-x86, qemu-utils, ovmf, xorriso, curl, openssh-client, python3 with
# requirements-ci.txt and the Ansible collections. Used weekly by .github/workflows/cloud-images.yml.
# Usage: tests/cloud-image-boot.sh <OS catalog id> [work dir]
#   USAS_IMAGE_CACHE  a folder that keeps downloaded images (default: the work dir)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OS="${1:?Usage: tests/cloud-image-boot.sh <OS catalog id> [work dir]}"
[[ "$OS" =~ ^[a-z][a-z0-9.-]{1,40}$ ]] || { echo "ERROR: '$OS' isn't an OS catalog id" >&2; exit 2; }
W="${2:-$(mktemp -d "${TMPDIR:-/tmp}/usas-cloud.XXXXXX")}"
mkdir -p "$W"
W="$(realpath "$W")"
CACHE="${USAS_IMAGE_CACHE:-$W}"
mkdir -p "$CACHE"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.secboot.fd}"
OVMF_VARS="${OVMF_VARS:-/usr/share/OVMF/OVMF_VARS_4M.ms.fd}"
# The VM's network card, matched by MAC address in network-config as provisioning matches it. QEMU's user network
# gives the guest 10.0.2.15 behind 10.0.2.2; the test inventory reaches srv-01 on 127.0.0.1:2201 (and 2222 once
# the baseline moves SSH there).
VM_MAC=BC:24:11:00:00:98
FIRST_BOOT_TIMEOUT="${FIRST_BOOT_TIMEOUT:-900}"
qemu_pid=""
mock_pid=""

export TEST_TMP="$W"
export ANSIBLE_CONFIG="$ROOT/ansible.cfg"
export ANSIBLE_FORCE_COLOR=0
INV=(-i tests/inventory/hosts.yml)

stop_vm() {
  if [ -n "$qemu_pid" ] && kill -0 "$qemu_pid" 2>/dev/null; then
    kill "$qemu_pid" 2>/dev/null || true
    wait "$qemu_pid" 2>/dev/null || true
  fi
  qemu_pid=""
}
cleanup() {
  local status=$?
  stop_vm
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; wait "$mock_pid" 2>/dev/null || true; fi
  if [ "$status" -ne 0 ]; then echo "FAILED (logs and the serial console in $W)" >&2; fi
}
trap cleanup EXIT
step() { printf '\n=== %s: %s\n' "$OS" "$*"; }

for tool in qemu-system-x86_64 qemu-img xorriso curl ssh ssh-keygen ssh-keyscan python3 ansible-playbook openssl; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool is missing" >&2; exit 1; }
done
[ -r "$OVMF_CODE" ] && [ -r "$OVMF_VARS" ] || { echo "ERROR: no OVMF Secure Boot firmware at $OVMF_CODE" >&2; exit 1; }
if [ -w /dev/kvm ]; then ACCEL=(-accel kvm -cpu host); else
  echo "WARNING: no KVM, the boot will be very slow" >&2; ACCEL=(-accel tcg -cpu max); fi

# Runs a playbook and fails unless it succeeds; with --idempotent also fails if anything changed.
play() {
  local idempotent=false
  if [ "$1" = "--idempotent" ]; then idempotent=true; shift; fi
  local log; log="$(mktemp "$W/play.XXXXXX")"
  if ! ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: ansible-playbook $* failed" >&2; return 1
  fi
  sed -n '/PLAY RECAP/,$p' "$log"
  if $idempotent && grep -Eq 'changed=[1-9]' <(sed -n '/PLAY RECAP/,$p' "$log"); then
    grep -B3 -E '^changed:' "$log" || true
    echo "ERROR: second run of $* changed something" >&2; return 1
  fi
}
# The firewall limits SSH to 6 new connections in 30 s per source, and every connection comes from 10.0.2.2.
cool_down() { sleep 31; }
on_vm() { ssh -i "$W/id_ed25519" -p "${port:-2201}" -o UserKnownHostsFile="$W/known_hosts" \
  -o StrictHostKeyChecking=yes -o BatchMode=yes sysadmin@127.0.0.1 "$@"; }

step "Finding the image the OS catalog pins"
read -r URL CHECKSUM FILE < <(python3 - "$OS" <<'EOF'
import sys, yaml
catalog = yaml.safe_load(open("roles/os_catalog/vars/catalog.yml"))["os_catalog"]
entry = next((e for e in catalog if e["id"] == sys.argv[1]), None)
if entry is None or "cloud_image" not in entry["methods"]:
    sys.exit(f"the OS catalog has no cloud image for {sys.argv[1]}")
image = entry["methods"]["cloud_image"]
print(image["url"], image["checksum"], image["file"])
EOF
)
[[ "$URL" =~ ^https://[A-Za-z0-9./_+~-]+$ && "$CHECKSUM" =~ ^sha(256|512):[0-9a-f]+$ \
   && "$FILE" =~ ^[A-Za-z0-9._+-]+\.qcow2$ ]] || { echo "ERROR: unexpected catalog entry for $OS" >&2; exit 1; }
ALGO="${CHECKSUM%%:*}"
DIGEST="${CHECKSUM#*:}"
echo "$FILE ($ALGO) from $URL"

step "Downloading it, or taking it from the cache, and checking the catalog's checksum"
check() { echo "$DIGEST  $CACHE/$FILE" | "${ALGO}sum" -c --quiet -; }
if [ -f "$CACHE/$FILE" ] && check; then
  echo "cached copy matches"
else
  rm -f "$CACHE/$FILE"
  curl -fL --retry 3 --speed-limit 500000 --speed-time 60 -o "$CACHE/$FILE.part" "$URL"
  mv "$CACHE/$FILE.part" "$CACHE/$FILE"
  check || { rm -f "$CACHE/$FILE"; echo "ERROR: $FILE doesn't match the checksum the catalog pins" >&2; exit 1; }
fi

step "Rendering the role's seed"
ssh-keygen -q -t ed25519 -N '' -C cloud-image-test -f "$W/id_ed25519"
cp "$W/id_ed25519.pub" "$W/test.keys"
mkdir -p "$W/seed"
cat >"$W/render.yml" <<EOF
- name: Render the seed the way the proxmox_vm role does for a cloud image
  hosts: localhost
  gather_facts: false
  vars_files: [$ROOT/roles/proxmox_vm/defaults/main.yml, $ROOT/roles/proxmox_vm/vars/main.yml]
  tasks:
    - name: Render user-data, network-config and meta-data
      ansible.builtin.template:
        src: $ROOT/roles/proxmox_vm/templates/{{ item.src }}
        dest: $W/seed/{{ item.dest }}
        mode: "0600"
      loop:
        - {src: cloud-user-data.j2, dest: user-data}
        - {src: network-config.j2, dest: network-config}
        - {src: meta-data.j2, dest: meta-data}
EOF
cat >"$W/vars.json" <<EOF
{"inventory_hostname": "srv-01", "inventory_hostname_short": "srv-01", "pve_vmid": 4061,
 "admin_user": "sysadmin", "net_mode": "static", "net_dns": ["10.0.2.3"], "net_search": [],
 "provision_password_hash": "$(openssl passwd -6 test-password)",
 "provision_ssh_keys": ["$(cat "$W/id_ed25519.pub")"],
 "provision_nics": [{"key": "net0", "mode": "static", "mac": "$VM_MAC", "mtu": "", "address": "10.0.2.15",
                     "prefix": 24, "gateway": "10.0.2.2"}]}
EOF
ansible-playbook -i localhost, -c local "$W/render.yml" -e "@$W/vars.json" | sed -n '/PLAY RECAP/,$p'
xorriso -as mkisofs -quiet -output "$W/seed.iso" -volid CIDATA -joliet -rock \
  "$W/seed/user-data" "$W/seed/meta-data" "$W/seed/network-config"
rm -rf "$W/seed" "$W/vars.json"

# The VM's disk: the image as its backing file, grown as provisioning grows it (vm_disk_gb).
qemu-img create -q -f qcow2 -b "$CACHE/$FILE" -F qcow2 "$W/disk.qcow2" 32G
cp "$OVMF_VARS" "$W/efivars.fd"

# Starts the VM, with the guest agent's channel that Proxmox adds for agent=1 (the baseline starts the agent).
# Extra arguments are added to QEMU's.
start_vm() {
  local name="$1"; shift
  qemu-system-x86_64 -machine q35,smm=on "${ACCEL[@]}" -smp 2 -m 2048 -name "$name" \
    -global driver=cfi.pflash01,property=secure,value=on \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,unit=1,file=$W/efivars.fd" \
    -device virtio-scsi-pci,id=scsihw0 \
    -drive "if=none,id=drive-scsi0,file=$W/disk.qcow2,format=qcow2,discard=unmap" \
    -device scsi-hd,drive=drive-scsi0,bus=scsihw0.0,bootindex=1 \
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:2201-:22,hostfwd=tcp:127.0.0.1:2222-:2222" \
    -device "virtio-net-pci,netdev=net0,mac=$VM_MAC" \
    -device virtio-serial-pci,id=qga0 -chardev "socket,path=$W/qga.sock,server=on,wait=off,id=qga" \
    -device virtserialport,bus=qga0.0,chardev=qga,name=org.qemu.guest_agent.0 \
    -display none -serial "file:$W/serial-$name.log" "$@" &
  qemu_pid=$!
}

step "First boot with the seed: cloud-init sets the server up and powers it off"
start_vm first -drive "if=none,id=drive-ide2,file=$W/seed.iso,format=raw,media=cdrom,readonly=on" \
  -device ide-cd,drive=drive-ide2,bus=ide.2
started=$SECONDS
while kill -0 "$qemu_pid" 2>/dev/null; do
  if (( SECONDS - started > FIRST_BOOT_TIMEOUT )); then
    tail -n 40 "$W/serial-first.log" >&2 || true
    echo "ERROR: the VM didn't power off within $FIRST_BOOT_TIMEOUT s" >&2; exit 1
  fi
  sleep 5
done
wait "$qemu_pid" || true
qemu_pid=""
echo "powered off after $(( SECONDS - started )) s"

step "Booting it again without the seed"
start_vm second
for _ in $(seq 1 120); do
  ssh-keyscan -T 2 -p 2201 127.0.0.1 >"$W/known_hosts" 2>/dev/null && [ -s "$W/known_hosts" ] && break
  sleep 2
done
[ -s "$W/known_hosts" ] || { tail -n 40 "$W/serial-second.log" >&2; echo "ERROR: no SSH on the VM" >&2; exit 1; }
# sshd answers before systemd has finished booting; until then pam_nologin refuses every user but root.
for _ in $(seq 1 60); do on_vm test ! -e /run/nologin 2>/dev/null && break; sleep 2; done

step "What the first boot left"
on_vm 'test -e /etc/cloud/cloud-init.disabled && sudo -n test -e /etc/sudoers.d/90-ansible-sysadmin && hostname' \
  | grep -qx srv-01 || { echo "ERROR: cloud-init didn't set up the server as the seed says" >&2; exit 1; }
# The baseline deletes cloud-init's cached copies of the seed only when this says disabled (roles/common).
ci_status="$(on_vm 'cloud-init status 2>&1; cloud-init --version 2>&1' || true)"
grep -q '^status: disabled' <<<"$ci_status" \
  || { printf '%s\n' "$ci_status" >&2; echo "ERROR: cloud-init status doesn't say disabled after the first boot" >&2; exit 1; }
# The firmware's SecureBoot variable: four attribute bytes, then 1 when Secure Boot is on.
sb="$(on_vm 'od -An -tu1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c')"
[ "$(awk '{print $NF}' <<<"$sb")" = 1 ] || { echo "ERROR: the VM didn't boot with Secure Boot on ($sb)" >&2; exit 1; }
# shellcheck disable=SC2016 # expanded on the VM
family="$(on_vm '. /etc/os-release && echo "$ID $ID_LIKE"')"
case "$family" in *rhel*|*fedora*|*centos*) family=rhel ;; *) family=debian ;; esac

# The keys server that stands in for https://github.com/<user>.keys (tests/inventory/hosts.yml's github_keys_url).
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 \
  -keyout "$W/api.key" -out "$W/api.crt" >/dev/null 2>&1
python3 tests/mock_pve.py --cert "$W/api.crt" --key "$W/api.key" --keys-file "$W/test.keys" >"$W/mock.log" 2>&1 &
mock_pid=$!
sleep 2

step "The baseline: first run, second run, checks"
play playbooks/baseline.yml --limit srv-01
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01
cool_down
play tests/verify-baseline.yml
cool_down

step "Move SSH to port 2222, check, rerun on the new port"
play playbooks/baseline.yml --limit srv-01 -e ssh_port=2222
cool_down
play tests/verify-baseline.yml -e expect_port=2222 -e ansible_port=2222
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 -e ssh_port=2222 -e ansible_port=2222
cool_down

if [ "$family" = rhel ]; then
  step "SELinux is enforcing, and sshd holds port 2222 under it"
  # sshd can only bind 2222 under enforcing SELinux once the baseline labelled the port ssh_port_t (seport), so
  # the checks above on port 2222 already prove the label; this makes sure SELinux was enforcing meanwhile.
  port=2222 on_vm getenforce | grep -qx Enforcing || { echo "ERROR: SELinux isn't enforcing" >&2; exit 1; }
  port=2222 on_vm 'ss -ltn' | grep -q ':2222 ' || { echo "ERROR: sshd isn't listening on 2222" >&2; exit 1; }
fi
echo "=== $OS: the cloud image booted and the baseline passed"
