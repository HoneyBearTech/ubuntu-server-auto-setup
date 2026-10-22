#!/usr/bin/env bash
# Installs Ubuntu from the remastered ISO in a VM set up the way Proxmox VE sets up the role's VMs: q35, OVMF
# with Secure Boot and Microsoft's keys enrolled, a TPM 2.0 (swtpm) and the NoCloud seed, with the user-data
# the proxmox_vm role renders for vm_disk_encryption. Then it boots the installed system and checks:
#   - the firmware booted the installer and the installed system with Secure Boot on (shim, GRUB, kernel signed),
#   - the root disk is LUKS2 with a TPM2 key slot, and its first boot needs nobody to type a passphrase,
#   - the role's first-boot tasks (roles/proxmox_vm/tasks/first_boot.yml, run here by Ansible as provisioning
#     runs them) bind that slot to PCR 7, and later boots and reboots stay unattended,
#   - after those tasks, no file on the installed disk holds the passphrase,
#   - the recovery path in docs/security.md works: with the TPM slot gone the passphrase unlocks the disk at
#     the console, and enrolling the TPM again brings back unattended boots.
# Needs Linux with KVM, qemu-system-x86, ovmf, swtpm, xorriso, openssh-client, python3 and Ansible (the role's
# template is rendered by Ansible). Used by .github/workflows/installer-iso.yml after tests/real-iso.sh.
# Usage: tests/secure-boot-install.sh <remastered ISO> [work dir]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ISO="$(realpath "$1")"
W="${2:-$(mktemp -d "${TMPDIR:-/tmp}/usas-sb.XXXXXX")}"
mkdir -p "$W"
W="$(realpath "$W")"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.secboot.fd}"
OVMF_VARS="${OVMF_VARS:-/usr/share/OVMF/OVMF_VARS_4M.ms.fd}"
SSH_PORT=2299
# The installer matches the network card by its MAC address (provision_nics, as tasks/main.yml builds it).
VM_MAC=BC:24:11:00:00:99
INSTALL_TIMEOUT="${INSTALL_TIMEOUT:-1800}"
UBUNTU_MIRROR="${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu}"
UBUNTU_SECURITY_MIRROR="${UBUNTU_SECURITY_MIRROR:-http://security.ubuntu.com/ubuntu}"
qemu_pid=""
swtpm_pid=""

stop_vm() {
  if [ -n "$qemu_pid" ] && kill -0 "$qemu_pid" 2>/dev/null; then
    echo "screendump $W/screen-last.ppm" | python3 "$W/monitor.py" "$W/monitor.sock" || true
    kill "$qemu_pid" 2>/dev/null || true
    wait "$qemu_pid" 2>/dev/null || true
  fi
  qemu_pid=""
  if [ -n "$swtpm_pid" ]; then kill "$swtpm_pid" 2>/dev/null || true; wait "$swtpm_pid" 2>/dev/null || true; fi
  swtpm_pid=""
}
cleanup() {
  local status=$?
  stop_vm
  if [ "$status" -ne 0 ]; then echo "FAILED (logs and screen dumps in $W)" >&2; fi
}
trap cleanup EXIT
step() { printf '\n=== %s\n' "$*"; }

for tool in qemu-system-x86_64 qemu-img swtpm xorriso ssh ssh-keygen python3 ansible-playbook openssl; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool is missing" >&2; exit 1; }
done
[ -r "$OVMF_CODE" ] && [ -r "$OVMF_VARS" ] || { echo "ERROR: no OVMF Secure Boot firmware at $OVMF_CODE" >&2; exit 1; }
if [ -w /dev/kvm ]; then ACCEL=(-accel kvm -cpu host); else
  echo "WARNING: no KVM, the install will be very slow" >&2; ACCEL=(-accel tcg -cpu max); fi

# Sends one command to QEMU's monitor (used for screen dumps).
cat >"$W/monitor.py" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
s.sendall(sys.stdin.read().encode() + b"\n")
time.sleep(1)
EOF
# Types the passphrase (from the environment) on the VM's screen, through QEMU's monitor.
cat >"$W/sendkeys.py" <<'EOF'
import os, socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
for c in os.environ["DISK_PASSPHRASE"] + "\n":
    s.sendall(("sendkey " + ("ret" if c == "\n" else c) + "\n").encode())
    time.sleep(0.1)
time.sleep(1)
EOF
# Watches the serial console for the passphrase prompt and types the passphrase (from the environment).
cat >"$W/type-passphrase.py" <<'EOF'
import os, socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
s.settimeout(1)
seen, deadline = b"", time.time() + int(sys.argv[3])
with open(sys.argv[2], "ab") as log:
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        log.write(chunk)
        seen = (seen + chunk)[-4096:]
        if b"assphrase for" in seen:
            time.sleep(2)
            s.sendall(os.environ["DISK_PASSPHRASE"].encode() + b"\r")
            print("typed the passphrase at the prompt")
            sys.exit(0)
sys.exit("no passphrase prompt on the serial console")
EOF

step "Rendering the role's user-data for an encrypted VM"
ssh-keygen -q -t ed25519 -N '' -C secure-boot-test -f "$W/id_ed25519"
DISK_PASSPHRASE="sbtest$(openssl rand -hex 16)"
export DISK_PASSPHRASE
printf '%s' "$DISK_PASSPHRASE" >"$W/passphrase"
cat >"$W/render.yml" <<EOF
- name: Render the seed the way the proxmox_vm role does
  hosts: localhost
  gather_facts: false
  vars_files: [$ROOT/roles/proxmox_vm/defaults/main.yml, $ROOT/roles/proxmox_vm/vars/main.yml]
  tasks:
    - name: Check the passphrase the way the role does
      ansible.builtin.assert:
        that: vm_disk_passphrase is match(provision_disk_passphrase_pattern)
    - name: Render user-data and meta-data
      ansible.builtin.template:
        src: $ROOT/roles/proxmox_vm/templates/{{ item }}.j2
        dest: $W/seed/{{ item }}
        mode: "0600"
      loop: [user-data, meta-data]
EOF
mkdir -p "$W/seed"
cat >"$W/vars.json" <<EOF
{"inventory_hostname": "sb-test", "inventory_hostname_short": "sb-test", "pve_vmid": 100,
 "admin_user": "sysadmin", "ansible_host": "10.0.2.15", "net_prefix": 24, "net_gateway": "10.0.2.2",
 "net_dns": ["10.0.2.3"], "provision_password_hash": "$(openssl passwd -6 test-password)",
 "provision_ssh_keys": ["$(cat "$W/id_ed25519.pub")"], "vm_disk_encryption": true,
 "vm_disk_passphrase": "$DISK_PASSPHRASE", "net_mode": "static",
 "provision_nics": [{"key": "net0", "mode": "static", "mac": "$VM_MAC", "mtu": "", "address": "10.0.2.15",
                     "prefix": 24, "gateway": "10.0.2.2"}]}
EOF
ANSIBLE_CONFIG="$ROOT/ansible.cfg" ansible-playbook -i localhost, -c local "$W/render.yml" -e "@$W/vars.json" \
  | sed -n '/PLAY RECAP/,$p'
# For the test only: the installer also records PCR 7 as it measured it, to compare with the installed
# system's (the install changes Secure Boot variables, which is why the role binds the slot after the first
# boot), and uses Ubuntu's main archive with retries instead of a mirror picked by geolocation, which has
# failed apt-get update from the CI runners. Everything else is the role's seed as rendered.
python3 - "$W/seed/user-data" "$UBUNTU_MIRROR" "$UBUNTU_SECURITY_MIRROR" <<'EOF'
import sys, yaml
with open(sys.argv[1]) as f:
    doc = yaml.safe_load(f)
doc["autoinstall"]["late-commands"].append(
    "curtin in-target -- tpm2_pcrread sha256:7 > /target/var/log/usas-test-installer-pcr7.txt 2>&1 || true")
doc["autoinstall"]["apt"] = {
    "geoip": False,
    "primary": [{"arches": ["default"], "uri": sys.argv[2]}],
    "security": [{"arches": ["default"], "uri": sys.argv[3]}],
    "conf": 'Acquire::Retries "10";\n',
}
with open(sys.argv[1], "w") as f:
    f.write("#cloud-config\n" + yaml.safe_dump(doc, width=4096))
EOF
xorriso -as mkisofs -quiet -output "$W/seed.iso" -volid CIDATA -joliet -rock "$W/seed/user-data" "$W/seed/meta-data"
rm -rf "$W/seed" "$W/vars.json"

cp "$OVMF_VARS" "$W/efivars.fd"
mkdir -p "$W/tpm"
qemu-img create -q -f qcow2 "$W/disk.qcow2" 32G

# Starts the VM (its TPM first, as Proxmox does). Extra arguments are added to QEMU's.
start_vm() {
  local name="$1"; shift
  rm -f "$W/swtpm.sock"
  swtpm socket --tpm2 --tpmstate "dir=$W/tpm" --ctrl "type=unixio,path=$W/swtpm.sock" \
    --log "file=$W/swtpm.log" &
  swtpm_pid=$!
  for _ in $(seq 1 50); do [ -S "$W/swtpm.sock" ] && break; sleep 0.1; done
  qemu-system-x86_64 -machine q35,smm=on "${ACCEL[@]}" -smp 2 -m 4096 -name "$name" \
    -global driver=cfi.pflash01,property=secure,value=on \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,unit=1,file=$W/efivars.fd" \
    -chardev "socket,id=chrtpm,path=$W/swtpm.sock" -tpmdev emulator,id=tpm0,chardev=chrtpm \
    -device tpm-tis,tpmdev=tpm0 \
    -device virtio-scsi-pci,id=scsihw0 \
    -drive "if=none,id=drive-scsi0,file=$W/disk.qcow2,format=qcow2,discard=unmap" \
    -device scsi-hd,drive=drive-scsi0,bus=scsihw0.0,bootindex=1 \
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22" -device "virtio-net-pci,netdev=net0,mac=$VM_MAC" \
    -display none -vga std -monitor "unix:$W/monitor.sock,server,nowait" "$@" &
  qemu_pid=$!
}

# Waits for the VM to power itself off, then stops its TPM.
wait_off() {
  local deadline=$((SECONDS + $1))
  while kill -0 "$qemu_pid" 2>/dev/null; do
    [ $SECONDS -lt $deadline ] || { echo "ERROR: the VM didn't power off within $1 s" >&2; exit 1; }
    sleep 5
  done
  wait "$qemu_pid" || true
  qemu_pid=""
  stop_vm
}

ssh_vm() {
  ssh -i "$W/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR -o ConnectTimeout=10 sysadmin@127.0.0.1 "$@"
}

wait_ssh() {
  local deadline=$((SECONDS + $1))
  while [ $SECONDS -lt $deadline ]; do
    if ssh_vm true 2>/dev/null; then return 0; fi
    kill -0 "$qemu_pid" 2>/dev/null || { echo "ERROR: the VM stopped" >&2; return 1; }
    sleep 5
  done
  echo "ERROR: no SSH within $1 s (if the screen dump shows a passphrase prompt, the TPM didn't unlock)" >&2
  return 1
}

boot_id() { ssh_vm cat /proc/sys/kernel/random/boot_id; }

# Waits for SSH; if the VM doesn't come up unattended, types the passphrase on its console to get in and
# show why, then fails.
wait_unattended() {
  wait_ssh "$1" && return 0
  echo "screendump $W/screen-stuck-$SECONDS.ppm" | python3 "$W/monitor.py" "$W/monitor.sock" || true
  echo "Typing the passphrase on the console to find out why" >&2
  for _ in 1 2 3; do python3 "$W/sendkeys.py" "$W/monitor.sock"; wait_ssh 120 && break; done
  diagnose
  exit 1
}

reboot_vm() {
  local before; before="$(boot_id)"
  ssh_vm sudo systemctl reboot || true
  sleep 15
  wait_unattended "${1:-600}"
  [ "$(boot_id)" != "$before" ] || { echo "ERROR: the VM didn't reboot" >&2; exit 1; }
}

step "Installing from the remastered ISO under Secure Boot (the installer powers the VM off)"
start_vm install -serial "file:$W/serial-install.log" \
  -drive "if=none,id=drive-ide2,media=cdrom,readonly=on,file=$ISO" -device ide-cd,drive=drive-ide2,bus=ide.0,bootindex=0 \
  -drive "if=none,id=drive-ide3,media=cdrom,readonly=on,file=$W/seed.iso" -device ide-cd,drive=drive-ide3,bus=ide.1
start=$SECONDS
while kill -0 "$qemu_pid" 2>/dev/null; do
  if [ $((SECONDS - start)) -gt "$INSTALL_TIMEOUT" ]; then
    echo "ERROR: the install didn't finish within $INSTALL_TIMEOUT s" >&2
    exit 1
  fi
  if [ $(((SECONDS - start) % 300)) -lt 10 ]; then
    echo "screendump $W/screen-install-$((SECONDS - start)).ppm" | python3 "$W/monitor.py" "$W/monitor.sock" || true
  fi
  sleep 10
done
wait_off 10
echo "installed in $((SECONDS - start)) s"

# What the installed system says about its disk, its TPM and its boots so far.
diagnose() {
  # shellcheck disable=SC2016 # expanded on the VM
  ssh_vm 'set -x; cat /etc/crypttab; sudo lsinitrd -f etc/crypttab; sudo lsinitrd | grep -E "tpm2|cryptsetup" | head -20
    sudo cryptsetup luksDump --dump-json-metadata "$(sudo blkid -t TYPE=crypto_LUKS -o device)"
    cat /var/log/usas-test-installer-pcr7.txt; sudo tpm2_pcrread sha256:7
    sudo journalctl -b -o short-monotonic --no-pager | grep -iE "tpm|cryptsetup|pcr|sbat|secure ?boot" | head -60
    dpkg -l shim-signed grub-efi-amd64-signed secureboot-db 2>/dev/null | tail -3
    sudo grep -hiE "late-commands|cryptenroll|enroll-tpm2" /var/log/installer/subiquity-server-debug.log | tail -20' || true
}

# Checks the LUKS header: two key slots (the passphrase's and the TPM's), and the TPM token's PCRs.
check_luks() {
  ssh_vm "sudo cryptsetup luksDump --dump-json-metadata $DEV" | python3 -c '
import json, sys
meta, pcrs = json.load(sys.stdin), json.loads(sys.argv[1])
tokens = [t for t in meta["tokens"].values() if t["type"] == "systemd-tpm2"]
assert len(meta["keyslots"]) == 2, meta["keyslots"].keys()
assert len(tokens) == 1 and tokens[0]["tpm2-pcrs"] == pcrs, tokens
print("LUKS2, key slots", sorted(meta["keyslots"]), "- TPM2 token on slot", tokens[0]["keyslots"], "bound to PCRs", pcrs)' "$1"
}

step "First boot from the disk: the TPM unlocks it, nobody types a passphrase"
start_vm installed -serial "file:$W/serial-boot.log"
wait_unattended 600
ssh_vm 'mokutil --sb-state; lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINTS'
ssh_vm 'mokutil --sb-state' | grep -qx 'SecureBoot enabled'
DEV="$(ssh_vm 'sudo blkid -t TYPE=crypto_LUKS -o device')"
ssh_vm "sudo cryptsetup luksDump $DEV" | tee "$W/luksdump.txt"
check_luks '[]'
diagnose

step "The role's first-boot tasks bind the TPM slot to PCR 7"
cat >"$W/first-boot.yml" <<EOF
- name: Run the role's first-boot tasks, as provisioning does
  hosts: all
  gather_facts: false
  vars_files: [$ROOT/roles/proxmox_vm/defaults/main.yml, $ROOT/roles/proxmox_vm/vars/main.yml]
  tasks:
    - name: First boot
      ansible.builtin.include_tasks: $ROOT/roles/proxmox_vm/tasks/first_boot.yml
EOF
cat >"$W/first-boot-vars.json" <<EOF
{"ansible_port": $SSH_PORT, "ansible_user": "sysadmin", "ansible_ssh_private_key_file": "$W/id_ed25519",
 "ansible_ssh_common_args": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null", "vm_disk_encryption": true}
EOF
ANSIBLE_CONFIG="$ROOT/ansible.cfg" ansible-playbook -i "127.0.0.1," "$W/first-boot.yml" -e "@$W/first-boot-vars.json" \
  | tee "$W/first-boot.log" | sed -n '/PLAY RECAP/,$p'
grep -q 'failed=0' "$W/first-boot.log" || { cat "$W/first-boot.log"; exit 1; }
check_luks '[7]'
# shellcheck disable=SC2016 # expanded on the VM
ssh_vm 'cat /etc/crypttab; for m in $(sudo dmsetup ls --target crypt | cut -f1); do sudo cryptsetup status "$m"; done'
ssh_vm 'grep -E "^[^#].*tpm2-device=auto" /etc/crypttab' >/dev/null
ssh_vm 'sudo lsinitrd 2>/dev/null | grep -E "etc/crypttab|libtss2-tcti-device|systemd-cryptsetup$" || true'
ssh_vm 'sudo grep -h "Secure boot" /var/log/installer/installer-journal.txt 2>/dev/null | head -2 || true'

step "The passphrase is the recovery key, and after the first-boot tasks no file on the disk holds it"
ssh_vm "sudo cryptsetup open --test-passphrase --key-file=- $DEV" <"$W/passphrase"
echo "the passphrase unlocks its key slot"
if ssh_vm 'sudo grep -rlF -f - / --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev --exclude-dir=run 2>/dev/null' \
     <"$W/passphrase"; then
  echo "ERROR: the files above hold the disk passphrase" >&2
  exit 1
fi
echo "no file holds the passphrase"

step "Reboots unlock with the PCR 7 binding, without anyone too"
reboot_vm 300
reboot_vm 300
check_luks '[7]'
echo "rebooted twice unattended"

step "Recovery: without the TPM key slot the passphrase unlocks at the console; enrolling the TPM again restores unattended boots"
# The kernel and the passphrase prompt go to the serial console too, where the test can answer it.
ssh_vm 'sudo sed -i -E "s/^GRUB_CMDLINE_LINUX_DEFAULT=\"(.*)\"/GRUB_CMDLINE_LINUX_DEFAULT=\"\1 console=tty0 console=ttyS0,115200\"/" /etc/default/grub && sudo update-grub'
ssh_vm "sudo systemd-cryptenroll --wipe-slot=tpm2 $DEV"
ssh_vm sudo poweroff || true
wait_off 300
start_vm recovery -serial "unix:$W/serial.sock,server,nowait"
sleep 2
python3 "$W/type-passphrase.py" "$W/serial.sock" "$W/serial-recovery.log" 600
wait_ssh 600
# The command docs/security.md gives (PASSWORD stands in for typing the recovery passphrase at its prompt).
ssh_vm "sudo env PASSWORD=\"\$(cat)\" systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 $DEV" <"$W/passphrase"
reboot_vm
ssh_vm "sudo cryptsetup luksDump $DEV" | grep -q systemd-tpm2
echo "unattended again after enrolling the TPM again"
ssh_vm sudo poweroff || true
wait_off 300

step "Secure Boot, LUKS2 and TPM2 unlocking work with the remastered ISO"
