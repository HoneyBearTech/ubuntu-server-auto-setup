#!/usr/bin/env bash
# Integration tests: runs the real playbooks against containers and a mock Proxmox API, checks the results,
# checks that a second run changes nothing, and reports task coverage. Needs Docker (privileged containers,
# for systemd), Python with requirements-ci.txt (or requirements.txt + requirements-dev.txt) and the Ansible
# collections. Run it with `make test`. See docs/testing.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-test.XXXXXX")"
export TEST_TMP
export ANSIBLE_CONFIG="$ROOT/ansible.cfg"
export ANSIBLE_CALLBACK_PLUGINS="$ROOT/tests/callback_plugins"
export ANSIBLE_CALLBACKS_ENABLED=task_coverage
export TASK_COVERAGE_FILE="$TEST_TMP/coverage.txt"
export ANSIBLE_FORCE_COLOR=0
# The node's stand-in SHA256SUMS is signed with a test key made when its image is built; signing.yml (written
# below) points the installer role at that key instead of Ubuntu's.
INV=(-i tests/inventory/hosts.yml -e "@$TEST_TMP/signing.yml")
PVE=usas-test-pve
SRV=usas-test-srv
MIN_COVERAGE="${MIN_COVERAGE:-80}"
mock_pid=""

cleanup() {
  status=$?
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; wait "$mock_pid" 2>/dev/null || true; fi
  docker rm -f "$PVE" "$SRV" >/dev/null 2>&1 || true
  if [ "$status" -ne 0 ]; then
    echo "FAILED (logs kept in $TEST_TMP)" >&2
  else
    rm -rf "$TEST_TMP"
  fi
}
trap cleanup EXIT

step() { printf '\n=== %s\n' "$*"; }

# Runs a playbook and fails unless it succeeds; with --idempotent also fails if anything changed.
play() {
  local idempotent=false
  if [ "$1" = "--idempotent" ]; then idempotent=true; shift; fi
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  if ! ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"
    echo "ERROR: ansible-playbook $* failed" >&2
    return 1
  fi
  sed -n '/PLAY RECAP/,$p' "$log"
  if $idempotent && grep -Eq 'changed=[1-9]' <(sed -n '/PLAY RECAP/,$p' "$log"); then
    grep -B3 -E '^changed:' "$log" || true
    echo "ERROR: second run of $* changed something" >&2
    return 1
  fi
}

# Runs a playbook that must fail, and checks the output for an expected message.
play_fails() {
  local expect="$1"; shift
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  if ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"
    echo "ERROR: ansible-playbook $* should have failed" >&2
    return 1
  fi
  if ! grep -q -- "$expect" "$log"; then
    cat "$log"
    echo "ERROR: expected '$expect' in the output" >&2
    return 1
  fi
  echo "failed as expected: $expect"
}

# Every connection reaches the server from Docker's one address, so UFW's SSH rate limit (6 new connections
# in 30 s) is shared by all steps. Steps that open many connections are followed by a pause.
cool_down() { sleep 31; }

wait_ssh() {
  for _ in $(seq 1 60); do
    if ssh-keyscan -T 2 -p "$1" 127.0.0.1 >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "ERROR: no SSH on port $1" >&2
  return 1
}

step "Setting up keys, the mock Proxmox API and the containers"
ssh-keygen -q -t ed25519 -N '' -C test@ubuntu-server-auto-setup -f "$TEST_TMP/id_ed25519"
cp "$TEST_TMP/id_ed25519.pub" "$TEST_TMP/test.keys"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 \
  -keyout "$TEST_TMP/api.key" -out "$TEST_TMP/api.crt" >/dev/null 2>&1
python3 tests/mock_pve.py --cert "$TEST_TMP/api.crt" --key "$TEST_TMP/api.key" \
  --keys-file "$TEST_TMP/test.keys" >"$TEST_TMP/mock.log" 2>&1 &
mock_pid=$!

docker build -q -t usas-test-pve:latest -f tests/images/pve-node/Dockerfile tests/fixtures >/dev/null
docker build -q -t usas-test-srv:latest tests/images/ubuntu-target >/dev/null
docker rm -f "$PVE" "$SRV" >/dev/null 2>&1 || true
docker run -d --name "$PVE" -p 127.0.0.1:2211:22 usas-test-pve:latest >/dev/null
docker run -d --name "$SRV" --hostname srv-01 --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
  -p 127.0.0.1:2201:22 -p 127.0.0.1:2222:2222 usas-test-srv:latest >/dev/null

# What the installer would have left: the admin's key on the server. The "node" gets root's key.
docker exec -i "$PVE" sh -c 'cat > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys' \
  <"$TEST_TMP/id_ed25519.pub"
docker exec -i "$SRV" sh -c 'cat > /home/sysadmin/.ssh/authorized_keys && chown sysadmin: /home/sysadmin/.ssh/authorized_keys && chmod 600 /home/sysadmin/.ssh/authorized_keys' \
  <"$TEST_TMP/id_ed25519.pub"
# Docker owns /etc/hosts in a container (it can't be replaced, only appended to), so put the line the
# common role wants there up front.
docker exec "$SRV" sh -c 'echo "127.0.1.1 srv-01" >> /etc/hosts'
wait_ssh 2211
wait_ssh 2201
# Only the node is trusted up front; the server's key is added by the provisioning role under test.
ssh-keyscan -p 2211 127.0.0.1 2>/dev/null >"$TEST_TMP/known_hosts"
docker cp "$PVE":/srv/test-signing/keyring.gpg "$TEST_TMP/signing-keyring.gpg"
printf 'installer_signing_keyring: %s\ninstaller_signing_fingerprint: %s\n' "$TEST_TMP/signing-keyring.gpg" \
  "$(docker exec "$PVE" cat /srv/test-signing/fingerprint)" >"$TEST_TMP/signing.yml"

step "build-installer: remaster the ISO, then check a second run changes nothing"
play playbooks/build-installer.yml
play --idempotent playbooks/build-installer.yml
play tests/verify-installer.yml
play_fails 'ubuntu_iso_name may contain only' playbooks/build-installer.yml -e 'ubuntu_iso_name=../../etc/evil.iso'

step "build-installer: a checksum file that isn't signed by the pinned key is refused"
play_fails 'The signature on http://127.0.0.1:8080/tampered/SHA256SUMS is bad' playbooks/build-installer.yml \
  -e ubuntu_iso_base_url=http://127.0.0.1:8080/tampered
play_fails "Couldn't fetch http://127.0.0.1:8080/unsigned/SHA256SUMS.gpg" playbooks/build-installer.yml \
  -e ubuntu_iso_base_url=http://127.0.0.1:8080/unsigned
# A good signature, but by a key other than the pinned one (the test key is in the keyring, Ubuntu's isn't).
play_fails "isn't by the key 843938DF228D22F7B3742BC0D94AA3F0EFE21092" playbooks/build-installer.yml \
  -e installer_signing_fingerprint=843938DF228D22F7B3742BC0D94AA3F0EFE21092
play_fails 'must be the signing key' playbooks/build-installer.yml -e installer_signing_fingerprint=EFE21092

step "provision-vms: a VM the installer powers off, then a rerun that skips it"
play playbooks/provision-vms.yml --limit srv-01
# Changed settings don't reach a VM that exists: it is skipped, not reconfigured.
play --idempotent playbooks/provision-vms.yml --limit srv-01 -e vm_secure_boot=false

step "provision-vms: values that could break the installer's configuration are refused before anything is created"
play_fails 'admin_user must be a lowercase Linux user name' playbooks/provision-vms.yml --limit srv-01 \
  -e 'admin_user="x; touch /pwned"'
play_fails 'admin_user must be a lowercase Linux user name' playbooks/provision-vms.yml --limit srv-01 \
  -e '{"admin_user": "sysadmin\n"}'

step "provision-vms: a node name the cluster doesn't have is refused, with the real names"
play_fails "pve_node 'PVE-Test' isn't a node in this cluster; its nodes are pve-test" playbooks/provision-vms.yml \
  --limit srv-01 -e pve_node=PVE-Test -e pve_ssh_host=pve-test

step "provision-vms: an install that never finishes is cleaned up"
play_fails 'seed ISO removed' playbooks/provision-vms.yml --limit srv-hang
play tests/verify-provision.yml

step "provision-vms: disk encryption needs Secure Boot, the lvm layout and a usable passphrase"
play_fails 'vm_disk_encryption needs vm_secure_boot true' playbooks/provision-vms.yml --limit srv-luks \
  -e vm_secure_boot=false
play_fails 'vm_disk_encryption needs vm_secure_boot true' playbooks/provision-vms.yml --limit srv-luks \
  -e autoinstall_storage_layout=direct
play_fails 'pve_tpmstate_storage must be a Proxmox storage ID' playbooks/provision-vms.yml --limit srv-luks \
  -e 'pve_tpmstate_storage=local-lvm:1,version=v1.2'
play_fails 'vm_disk_passphrase must be 12 to 512' playbooks/provision-vms.yml --limit srv-luks \
  -e vm_disk_passphrase=too-short
play_fails 'vm_disk_passphrase must be 12 to 512' playbooks/provision-vms.yml --limit srv-luks \
  -e '{"vm_disk_passphrase": "long-enough-passphrase\n"}'
play_fails 'vm_disk_passphrase must be 12 to 512' playbooks/provision-vms.yml --limit srv-luks \
  -e '{"vm_disk_passphrase": "long-enough-but-{braces}"}'

step "provision-vms: an encrypted VM gets a vTPM, and the passphrase stays out of the output even while debugging"
# The test server has no encrypted disk: blkid reports one and systemd-cryptenroll logs its arguments, for
# the first-boot tasks.
docker exec "$SRV" sh -c 'printf "#!/bin/sh\necho /dev/sda3\n" >/usr/local/sbin/blkid
  printf "#!/bin/sh\necho \"\$*\" >>/tmp/cryptenroll.calls\n" >/usr/local/sbin/systemd-cryptenroll
  chmod 755 /usr/local/sbin/blkid /usr/local/sbin/systemd-cryptenroll'
play playbooks/provision-vms.yml --limit srv-luks -v --diff -e pve_hide_secrets=false
if grep -lF test-disk-passphrase-4069 "$TEST_TMP"/play.*; then
  echo "ERROR: the disk passphrase is in the output above" >&2
  exit 1
fi
# After the first boot the TPM slot was bound to PCR 7, and the installer's copy of its configuration is gone.
[ "$(docker exec "$SRV" cat /tmp/cryptenroll.calls)" = \
  "--unlock-tpm2-device=auto --tpm2-device=auto --tpm2-pcrs=7 --wipe-slot=tpm2 /dev/sda3" ] \
  || { echo "ERROR: systemd-cryptenroll wasn't run as expected" >&2; docker exec "$SRV" cat /tmp/cryptenroll.calls; exit 1; }
docker exec "$SRV" sh -c '! test -e /var/log/installer/autoinstall-user-data' \
  || { echo "ERROR: the installer's copy of its configuration is still there" >&2; exit 1; }
# Put back what the stand-in server had, for the baseline's own clean-up test.
docker exec "$SRV" sh -c 'rm /usr/local/sbin/blkid /usr/local/sbin/systemd-cryptenroll /tmp/cryptenroll.calls
  echo "password: \"\$6\$test\$hash\"" >/var/log/installer/autoinstall-user-data
  chmod 0600 /var/log/installer/autoinstall-user-data'
play --idempotent playbooks/provision-vms.yml --limit srv-luks
play tests/verify-disk-encryption.yml

step "The installer's TPM enrollment script edits only the root disk's crypttab line, and a second run changes nothing"
docker exec "$PVE" cat /srv/seed-copies/seed-srv-luks/user-data >"$TEST_TMP/user-data-luks"
python3 -c 'import sys, yaml
files = {f["path"]: f["content"] for f in yaml.safe_load(open(sys.argv[1]))["write_files"]}
sys.stdout.write(files["/run/usas/enroll-tpm2.sh"])' "$TEST_TMP/user-data-luks" >"$TEST_TMP/enroll-tpm2.sh"
# blkid, systemd-cryptenroll and dracut are stubs that log their arguments.
docker run --rm -v "$TEST_TMP/enroll-tpm2.sh:/enroll-tpm2.sh:ro" usas-test-pve:latest sh -euc '
  mkdir -p /stubs /etc/dracut.conf.d
  printf "#!/bin/sh\ncase \"\$*\" in *UUID*) echo 1111-2222 ;; *) echo /dev/sda3 ;; esac\n" >/stubs/blkid
  for c in systemd-cryptenroll dracut; do printf "#!/bin/sh\necho %s \"\$*\" >>/calls\n" "$c" >/stubs/$c; done
  chmod +x /stubs/*
  printf "# <name> <device> <key> <options>\ndm_crypt-0 UUID=1111-2222 none luks\nscratch UUID=3333 /dev/urandom swap\n" >/etc/crypttab
  PATH="/stubs:$PATH" sh /enroll-tpm2.sh
  cp /etc/crypttab /crypttab.first
  PATH="/stubs:$PATH" sh /enroll-tpm2.sh
  cmp /etc/crypttab /crypttab.first
  cat /etc/crypttab /calls /etc/dracut.conf.d/90-usas-tpm2.conf' >"$TEST_TMP/enroll.out"
cat >"$TEST_TMP/enroll.expected" <<'EOF'
# <name> <device> <key> <options>
dm_crypt-0 UUID=1111-2222 none luks,tpm2-device=auto
scratch UUID=3333 /dev/urandom swap
systemd-cryptenroll --unlock-key-file=/run/usas/disk-key --tpm2-device=auto --tpm2-pcrs= /dev/sda3
dracut --regenerate-all --force
systemd-cryptenroll --unlock-key-file=/run/usas/disk-key --tpm2-device=auto --tpm2-pcrs= /dev/sda3
dracut --regenerate-all --force
# Unlock the root disk with the TPM at boot (set up by ubuntu-server-auto-setup).
add_dracutmodules+=" tpm2-tss systemd-cryptsetup "
install_items+=" /etc/crypttab "
EOF
diff -u "$TEST_TMP/enroll.expected" "$TEST_TMP/enroll.out"

step "ISO storage other than local: its folder is asked from Proxmox, and the VM's CD-ROMs use it"
play playbooks/build-installer.yml -e pve_iso_storage=synology-nfs
play --idempotent playbooks/build-installer.yml -e pve_iso_storage=synology-nfs
# It also opts out of Secure Boot.
play playbooks/provision-vms.yml --limit srv-nfs -e pve_iso_storage=synology-nfs -e vm_secure_boot=false
play tests/verify-iso-storage.yml
play_fails "Storage 'backup-nfs' doesn't exist on pve-test, isn't active, or doesn't allow ISO images" \
  playbooks/build-installer.yml -e pve_iso_storage=backup-nfs
play_fails 'pve_iso_storage must be a Proxmox storage ID' playbooks/build-installer.yml -e 'pve_iso_storage=local;id'

step "provision-vms: a server on DHCP, its address read from the guest agent, then a rerun that reads it again"
play playbooks/provision-vms.yml --limit srv-dhcp
play --idempotent playbooks/provision-vms.yml --limit srv-dhcp

step "provision-vms: a VLAN on net0 and two more cards, each matched by its MAC address"
play playbooks/provision-vms.yml --limit srv-vlan
play tests/verify-network.yml

step "provision-vms: no DHCP lease, or a guest agent the token may not ask, stops with what to do"
play_fails 'VM 4065 (srv-nolease) reported no DHCP address on net0' playbooks/provision-vms.yml --limit srv-nolease
play_fails 'add VM.GuestAgent.Audit (Proxmox VE 9) or VM.Monitor (Proxmox VE 8)' playbooks/provision-vms.yml \
  --limit srv-forbidden

step "provision-vms: network settings that could add options to a card, or a second default route, are refused"
play_fails 'pve_bridge must be a bridge name' playbooks/provision-vms.yml --limit srv-vlan -e 'pve_bridge=vmbr0,firewall=0'
play_fails 'vm_extra_nics item 1 (net1) may have only the keys' playbooks/provision-vms.yml --limit srv-vlan \
  -e '{"vm_extra_nics": [{"bridge": "vmbr1", "vlan": "20,mtu=1500"}]}'
play_fails 'vm_extra_nics item 2 (net2) may have only the keys' playbooks/provision-vms.yml --limit srv-vlan \
  -e '{"vm_extra_nics": [{"mode": "none"}, {"mode": "dhcp", "address": "10.20.0.5"}]}'
play_fails 'needs exactly one default gateway' playbooks/provision-vms.yml --limit srv-vlan \
  -e '{"vm_extra_nics": [{"address": "10.20.0.5", "prefix": 24, "gateway": "10.20.0.1"}]}'
play_fails 'needs exactly one default gateway' playbooks/provision-vms.yml --limit srv-dhcp \
  -e '{"vm_extra_nics": [{"address": "10.20.0.5", "prefix": 24, "gateway": "10.20.0.1"}]}'
play_fails 'must all differ' playbooks/provision-vms.yml --limit srv-dhcp \
  -e '{"vm_extra_nics": [{"mode": "none", "mac": "02:00:00:00:40:63"}]}'

step "A node user other than root: prepare-node.yml sets it up, then the builds and a VM need no root"
# The fake pvesm, like the real one, refuses anyone but root; the ISO folders are root's (with the ISOs the
# root builds above left there), and so is the work directory.
build_user="$TEST_TMP/build-user.json"
printf '{"pve_ssh_user": "usas-build", "proxmox_node_user_ssh_keys": ["%s"],
  "proxmox_node_user_iso_storages": ["local", "synology-nfs"]}\n' "$(cat "$TEST_TMP/id_ed25519.pub")" >"$build_user"
play_fails 'Set pve_ssh_user to the lowercase Linux user name to create' playbooks/prepare-node.yml
play_fails "These storages don't exist on pve-test, aren't active, or don't allow ISO images: backup-nfs" \
  playbooks/prepare-node.yml -e "@$build_user" -e '{"proxmox_node_user_iso_storages": ["local", "backup-nfs"]}'
play playbooks/prepare-node.yml -e "@$build_user"
play --idempotent playbooks/prepare-node.yml -e "@$build_user"
play_fails 'logs in to pve-test as root, but pve_ssh_user is usas-build' playbooks/build-installer.yml \
  -e pve_ssh_user=usas-build -e ansible_user=root
play_fails 'pve_ssh_user a lowercase Linux user name' playbooks/build-installer.yml -e 'pve_ssh_user=x;id'
play playbooks/build-installer.yml -e pve_ssh_user=usas-build -e installer_iso_rebuild=true
play --idempotent playbooks/build-installer.yml -e pve_ssh_user=usas-build
play playbooks/build-installer.yml -e pve_ssh_user=usas-build -e pve_iso_storage=synology-nfs
play playbooks/provision-vms.yml --limit srv-user -e pve_ssh_user=usas-build
play tests/verify-node-user.yml
play_fails "doesn't name a folder for storage 'backup-nfs'" playbooks/build-installer.yml -e pve_ssh_user=usas-build \
  -e pve_iso_storage=backup-nfs

step "baseline: check mode on the fresh server fails nothing and changes nothing"
# Before the first run UFW and Fail2Ban aren't installed and nothing limits SSH connections.
play playbooks/baseline.yml --limit srv-01 --check --diff
play playbooks/baseline.yml --limit srv-01 --check --diff -e ssh_port=2222
play playbooks/baseline.yml --limit srv-01 --check --diff -e admin_user=newadmin
docker exec "$SRV" sh -c '! test -e /etc/ssh/sshd_config.d/00-hardening.conf && ! command -v ufw && ! id newadmin' \
  >/dev/null || { echo "ERROR: a check run changed the server" >&2; exit 1; }

step "baseline: first run, second run, checks"
play playbooks/baseline.yml --limit srv-01
play --idempotent playbooks/baseline.yml --limit srv-01
play tests/verify-baseline.yml
cool_down

step "baseline: a server on DHCP is reached at the address its guest agent reports"
play --idempotent playbooks/baseline.yml --limit srv-dhcp --tags ssh_keys
cool_down

step "baseline: check mode on a server that has the baseline reports no changes"
play --idempotent playbooks/baseline.yml --limit srv-01 --check --diff
cool_down

step "baseline: a key that is no longer published is removed"
docker exec "$SRV" sh -c 'echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK old@laptop" >> /home/sysadmin/.ssh/authorized_keys'
play playbooks/baseline.yml --limit srv-01 --tags ssh_keys
play tests/verify-baseline.yml
cool_down

step "baseline: a published key that is also an extra key is installed once, and a rerun changes nothing"
dup_keys="$TEST_TMP/dup-keys.json"
printf '{"admin_extra_ssh_keys": ["%s also-extra"]}\n' "$(cut -d' ' -f1-2 "$TEST_TMP/id_ed25519.pub")" >"$dup_keys"
play playbooks/baseline.yml --limit srv-01 --tags ssh_keys -e "@$dup_keys"
play --idempotent playbooks/baseline.yml --limit srv-01 --tags ssh_keys -e "@$dup_keys"
keys_file="$(docker exec "$SRV" cat /home/sysadmin/.ssh/authorized_keys)"
if [ "$(printf '%s\n' "$keys_file" | wc -l)" -ne 1 ] || ! grep -q also-extra <<<"$keys_file"; then
  echo "ERROR: the duplicated key isn't in authorized_keys exactly once" >&2
  exit 1
fi
cool_down

step "baseline: an empty key list is refused instead of wiping authorized_keys"
play_fails 'refusing to touch authorized_keys' playbooks/baseline.yml --limit srv-01 --tags ssh_keys \
  -e github_user= -e '{"admin_extra_ssh_keys": []}'

step "baseline: settings that could inject sshd or Fail2Ban directives are refused"
play_fails "ssh_allow_users entries can't contain spaces or line breaks" playbooks/baseline.yml --limit srv-01 \
  --tags ssh -e '{"ssh_allow_users": ["sysadmin\nPermitRootLogin yes"]}'
play_fails "ssh_allow_users entries can't contain spaces or line breaks" playbooks/baseline.yml --limit srv-01 \
  --tags ssh -e '{"ssh_allow_users": ["sysadmin\n"]}'
play_fails 'fail2ban_ignoreip entries must be addresses' playbooks/baseline.yml --limit srv-01 \
  --tags fail2ban -e '{"fail2ban_ignoreip": ["10.0.0.0/8\nenabled = false"]}'
cool_down

step "baseline: move SSH to port 2222, check, rerun on the new port"
play playbooks/baseline.yml --limit srv-01 -e ssh_port=2222
cool_down
play tests/verify-baseline.yml -e expect_port=2222
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 -e ssh_port=2222 -e ansible_port=2222

step "baseline: keep passwordless sudo when asked to"
cool_down
play playbooks/baseline.yml --limit srv-01 -e ssh_port=2222 -e ansible_port=2222 -e admin_sudo_nopasswd=true
cool_down
play tests/verify-baseline.yml -e expect_port=2222 -e ansible_port=2222 -e expect_nopasswd=true

step "baseline: passwordless sudo isn't removed when no password could replace it"
cool_down
play_fails "couldn't use sudo afterwards" playbooks/baseline.yml --limit srv-01 -e ssh_port=2222 -e ansible_port=2222 \
  -e admin_sudo_nopasswd=false -e admin_password= -e ansible_become_password=
cool_down
play tests/verify-baseline.yml -e expect_port=2222 -e ansible_port=2222 -e expect_nopasswd=true

step "baseline: optional hardening and notifications, all on, then a rerun that changes nothing"
P2222=(-e ssh_port=2222 -e ansible_port=2222 -e admin_sudo_nopasswd=true)
HARDENING=(-e @tests/hardening-vars.yml)
if [ "${CI:-}" != "true" ]; then
  # Kernel-wide keys (unlike net.*, which belong to the container's network namespace) would change the Docker
  # host's own kernel until it reboots. CI's runner is thrown away afterwards, so only CI applies them.
  HARDENING+=(-e '{"hardening_sysctl_extra": {"net.ipv4.conf.all.rp_filter": 2,
    "net.ipv4.icmp_ignore_bogus_error_responses": null, "kernel.kptr_restrict": null, "kernel.dmesg_restrict": null,
    "kernel.unprivileged_bpf_disabled": null, "kernel.yama.ptrace_scope": null, "kernel.randomize_va_space": null,
    "net.core.bpf_jit_harden": null, "dev.tty.ldisc_autoload": null, "fs.protected_hardlinks": null,
    "fs.protected_symlinks": null, "fs.protected_fifos": null, "fs.protected_regular": null, "fs.suid_dumpable": null}}')
fi
cool_down
play playbooks/baseline.yml --limit srv-01 "${P2222[@]}" "${HARDENING[@]}"
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 "${P2222[@]}" "${HARDENING[@]}"
cool_down
play tests/verify-hardening.yml -e ansible_port=2222 "${HARDENING[@]}"
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 --check --diff "${P2222[@]}" "${HARDENING[@]}"

step "baseline: hardening and notification values that could inject settings are refused"
cool_down
play_fails 'fail2ban_webhook_url must be an http(s) URL' playbooks/baseline.yml --limit srv-01 --tags fail2ban \
  "${P2222[@]}" -e '{"fail2ban_webhook_url": "https://ntfy.example.com/x\nenabled = false"}'
play_fails 'fail2ban_webhook_url must be an http(s) URL' playbooks/baseline.yml --limit srv-01 --tags fail2ban \
  "${P2222[@]}" -e '{"fail2ban_webhook_url": "https://ntfy.example.com/x'\''$(touch /pwned)'\''"}'
play_fails 'fail2ban_destemail and fail2ban_sender must be plain mail addresses' playbooks/baseline.yml \
  --limit srv-01 --tags fail2ban "${P2222[@]}" -e '{"fail2ban_destemail": "a@example.com\naction = %(action_)s"}'
cool_down
play_fails 'unattended_upgrades_mail and mail_relay_from must be plain mail addresses' playbooks/baseline.yml \
  --limit srv-01 --tags common "${P2222[@]}" -e '{"unattended_upgrades_mail": "a@example.com\";\nAPT::Periodic::Unattended-Upgrade \"0"}'
play_fails 'mail_relay_host must be a host' playbooks/baseline.yml --limit srv-01 --tags common "${P2222[@]}" \
  "${HARDENING[@]}" -e '{"mail_relay_host": "smtp.example.com\nhost evil.example.com"}'
play_fails 'mail_relay_user and mail_relay_password go together' playbooks/baseline.yml --limit srv-01 --tags common \
  "${P2222[@]}" "${HARDENING[@]}" -e '{"mail_relay_password": "secret\npasswordeval touch /pwned"}'
cool_down
play_fails 'hardening_sysctl and hardening_sysctl_extra keys must be' playbooks/baseline.yml --limit srv-01 \
  --tags hardening "${P2222[@]}" -e hardening_sysctl_enabled=true \
  -e '{"hardening_sysctl_extra": {"kernel.sysrq": "0\nkernel.core_pattern = |/tmp/x"}}'
play_fails 'hardening_sysctl and hardening_sysctl_extra keys must be' playbooks/baseline.yml --limit srv-01 \
  --tags hardening "${P2222[@]}" -e hardening_sysctl_enabled=true -e '{"hardening_sysctl_extra": {"kernel.sysrq = 1": 0}}'
docker exec "$SRV" sh -c '! test -e /pwned' || { echo "ERROR: an injected command ran" >&2; exit 1; }

step "baseline: turning them off again removes their files"
cool_down
play playbooks/baseline.yml --limit srv-01 "${P2222[@]}"
docker exec "$SRV" sh -c '! test -e /etc/sysctl.d/90-usas-hardening.conf && ! test -e /etc/audit/rules.d/60-usas-hardening.rules \
  && ! test -e /etc/fail2ban/action.d/usas-webhook.conf && ! test -e /etc/msmtp-password \
  && ! grep -q Mail /etc/apt/apt.conf.d/52unattended-upgrades-local && ! grep -Eq "^(action|destemail) =" /etc/fail2ban/jail.local' \
  || { echo "ERROR: turning the options off left their files behind" >&2; exit 1; }
cool_down
play tests/verify-baseline.yml -e expect_port=2222 -e ansible_port=2222 -e expect_nopasswd=true

step "baseline: sudo-rs without the classic sudo is refused before anything changes"
cool_down
docker exec "$SRV" mv /usr/bin/sudo.ws /usr/bin/sudo.ws.hidden
play_fails "whose password prompt Ansible doesn't recognise" playbooks/baseline.yml --limit srv-01 \
  -e ssh_port=2222 -e ansible_port=2222 -e admin_sudo_nopasswd=false
docker exec "$SRV" mv /usr/bin/sudo.ws.hidden /usr/bin/sudo.ws

step "Task coverage"
python3 tests/coverage.py "$TASK_COVERAGE_FILE" --min "$MIN_COVERAGE"

step "All integration tests passed"
