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
# below) points the installer role at that key instead of Ubuntu's. catalog.yml (also written below) is the real
# OS catalog with the stand-in ISOs pinned as Ubuntu 26.04's and 24.04's installers.
INV=(-i tests/inventory/hosts.yml -e "@$TEST_TMP/signing.yml" -e "@$TEST_TMP/catalog.yml")
# For the steps that check an ISO the catalog doesn't pin against the signed SHA256SUMS: a catalog that pins
# 26.04.9 instead (written below), and pve-test only, whose own release is 26.04.
UNPINNED=(--limit pve-test -e "@$TEST_TMP/catalog-unpinned.yml" -e ubuntu_point_release=26.04.1)
PVE=usas-test-pve
PVE2=usas-test-pve2
SRV=usas-test-srv
# The cluster's shared ISO storage (synology-nfs): one volume both node containers mount at its folder.
NFS=usas-test-nfs
MIN_COVERAGE="${MIN_COVERAGE:-80}"
mock_pid=""
last_log=""

cleanup() {
  status=$?
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; wait "$mock_pid" 2>/dev/null || true; fi
  # The extra disks' loop devices are the Docker host's: detach the ones the disk step attached, if it stopped early.
  docker exec "$SRV" sh -c 'umount /srv/data /var/log/app 2>/dev/null; for d in $(cat /srv/test-disks/loops 2>/dev/null); do
    losetup -d "$d"; done' >/dev/null 2>&1 || true
  docker rm -f "$PVE" "$PVE2" "$SRV" >/dev/null 2>&1 || true
  docker volume rm "$NFS" >/dev/null 2>&1 || true
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
  last_log="$log"
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

# Runs a playbook that must succeed, and checks the output for an expected message.
play_says() {
  local expect="$1"; shift
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  last_log="$log"
  if ! ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"
    echo "ERROR: ansible-playbook $* failed" >&2
    return 1
  fi
  if ! grep -q -- "$expect" "$log"; then
    cat "$log"
    echo "ERROR: expected '$expect' in the output" >&2
    return 1
  fi
  echo "said as expected: $expect"
}

# Makes the mock API drop the next call to METHOD PATH (a regex), before or after carrying it out.
drop() {
  curl -fsSk -X POST -H 'Content-Type: application/json' \
    -d "{\"method\": \"$1\", \"path\": \"$2\", \"when\": \"$3\"}" https://127.0.0.1:8006/mock/drop >/dev/null
}

# Fails unless the mock API has none of the given VM IDs.
no_vms() {
  python3 - "$@" <<'PY'
import json, ssl, sys, urllib.request
state = json.load(urllib.request.urlopen("https://127.0.0.1:8006/mock/state", context=ssl._create_unverified_context()))
made = sorted(set(sys.argv[1:]) & set(state["data"]["vms"]))
if made:
    sys.exit(f"ERROR: VMs {made} were created")
PY
}

# How many of the node containers have the given path.
nodes_with() {
  local count=0 node
  for node in "$PVE" "$PVE2"; do
    if docker exec "$node" test -e "$1"; then count=$((count + 1)); fi
  done
  echo "$count"
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

step "ansible.cfg waits 30 seconds for a connection and for sudo's prompt (servers just after a reboot)"
ansible-config dump --only-changed | grep -Eq '^DEFAULT_TIMEOUT\(.*\) = 30$' || {
  echo "ERROR: ansible.cfg doesn't set [defaults] timeout = 30" >&2
  exit 1
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
docker build -q --target target -t usas-test-srv:latest tests/images/ubuntu-target >/dev/null
docker rm -f "$PVE" "$PVE2" "$SRV" >/dev/null 2>&1 || true
docker volume rm "$NFS" >/dev/null 2>&1 || true
docker volume create "$NFS" >/dev/null
docker run -d --name "$PVE" -p 127.0.0.1:2211:22 -v "$NFS":/mnt/pve/synology-nfs usas-test-pve:latest >/dev/null
docker run -d --name "$PVE2" --hostname pve-test-2 -p 127.0.0.1:2212:22 -v "$NFS":/mnt/pve/synology-nfs \
  usas-test-pve:latest >/dev/null
docker run -d --name "$SRV" --hostname srv-01 --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
  -p 127.0.0.1:2201:22 -p 127.0.0.1:2222:2222 -p 127.0.0.1:2280:8080 usas-test-srv:latest >/dev/null

# What the installer would have left: the admin's key on the server. The "nodes" get root's key.
for node in "$PVE" "$PVE2"; do
  docker exec -i "$node" sh -c 'cat > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys' \
    <"$TEST_TMP/id_ed25519.pub"
done
docker exec -i "$SRV" sh -c 'cat > /home/sysadmin/.ssh/authorized_keys && chown sysadmin: /home/sysadmin/.ssh/authorized_keys && chmod 600 /home/sysadmin/.ssh/authorized_keys' \
  <"$TEST_TMP/id_ed25519.pub"
# Docker owns /etc/hosts in a container (it can't be replaced, only appended to), so put the line the
# common role wants there up front.
docker exec "$SRV" sh -c 'echo "127.0.1.1 srv-01" >> /etc/hosts'
wait_ssh 2211
wait_ssh 2212
wait_ssh 2201
# Only the nodes are trusted up front; the server's key is added by the provisioning role under test.
ssh-keyscan -p 2211 127.0.0.1 2>/dev/null >"$TEST_TMP/known_hosts"
ssh-keyscan -p 2212 127.0.0.1 2>/dev/null >>"$TEST_TMP/known_hosts"
# The same, without the second node's key: for the steps where it isn't trusted.
grep -v '^\[127.0.0.1\]:2212 ' "$TEST_TMP/known_hosts" >"$TEST_TMP/known_hosts.one"
printf '{"ansible_ssh_common_args": "-o UserKnownHostsFile=%s/known_hosts.one -o StrictHostKeyChecking=yes"}\n' \
  "$TEST_TMP" >"$TEST_TMP/untrusted.json"
docker cp "$PVE":/srv/test-signing/keyring.gpg "$TEST_TMP/signing-keyring.gpg"
printf 'installer_signing_keyring: %s\ninstaller_signing_fingerprint: %s\n' "$TEST_TMP/signing-keyring.gpg" \
  "$(docker exec "$PVE" cat /srv/test-signing/fingerprint)" >"$TEST_TMP/signing.yml"
docker exec "$PVE" sh -c 'cd /srv/ubuntu && sha256sum ubuntu-*-live-server-amd64.iso' >"$TEST_TMP/stand-in.sums"
python3 - "$TEST_TMP" <<'PY'
import hashlib, os, sys, yaml
catalog = yaml.safe_load(open("roles/os_catalog/vars/catalog.yml"))
entries = {e["id"]: e for e in catalog["os_catalog"]}
for line in open(f"{sys.argv[1]}/stand-in.sums"):
    digest, name = line.split()
    point = name.split("-")[1]
    release = ".".join(point.split(".")[:2])
    entries[f"ubuntu-{release}"]["methods"]["autoinstall"].update(
        url=f"https://releases.ubuntu.com/{release}/{name}", checksum=f"sha256:{digest}", file=name, build=point)
# Stand-ins for the cloud images, served by the nodes like the ISOs, each checked with its vendor's algorithm.
os.makedirs(f"{sys.argv[1]}/cloud", exist_ok=True)
for entry in entries.values():
    image = entry["methods"]["cloud_image"]
    content = f"stand-in for {image['file']}\n".encode()
    open(f"{sys.argv[1]}/cloud/{image['file']}", "wb").write(content)
    algorithm = image["checksum"].split(":")[0]
    image.update(url=f"http://127.0.0.1:8080/cloud/{image['file']}",
                 checksum=f"{algorithm}:{hashlib.new(algorithm, content).hexdigest()}")
yaml.safe_dump(catalog, open(f"{sys.argv[1]}/catalog.yml", "w"))
entries["ubuntu-26.04"]["methods"]["autoinstall"].update(file="ubuntu-26.04.9-live-server-amd64.iso", build="26.04.9")
yaml.safe_dump(catalog, open(f"{sys.argv[1]}/catalog-unpinned.yml", "w"))
entries["debian-13"]["methods"]["cloud_image"]["checksum"] = "sha512:" + "0" * 128
yaml.safe_dump(catalog, open(f"{sys.argv[1]}/catalog-bad-image.yml", "w"))
PY
for node in "$PVE" "$PVE2"; do
  docker exec "$node" mkdir -p /srv/ubuntu/cloud
  docker cp "$TEST_TMP/cloud/." "$node":/srv/ubuntu/cloud/
done

step "build-installer: local ISO storage, so each server's ISO is remastered on its node (26.04 and 24.04)"
play_says 'local on pve-test-2: built by pve-test-2 (ubuntu-24.04.5-live-server-amd64.iso)' playbooks/build-installer.yml
grep -q 'local on pve-test: built by pve-test (ubuntu-26.04.1-live-server-amd64.iso)' "$last_log"
play --idempotent playbooks/build-installer.yml
play tests/verify-installer.yml

step "build-installer: --limit leaves a node out, and that node is skipped (and said so), not built"
docker exec "$PVE2" rm -f /var/lib/vz/template/iso/ubuntu-24.04.5-live-server-amd64-autoinstall.iso
play_says 'local on pve-test-2: skipped, --limit leaves its node out' playbooks/build-installer.yml --limit srv-two
[ "$(docker exec "$PVE2" ls /var/lib/vz/template/iso)" = ubuntu-24.04.5-live-server-amd64.iso ] \
  || { echo "ERROR: a node outside --limit was built" >&2; exit 1; }
play_says 'local on pve-test-2: built by pve-test-2' playbooks/build-installer.yml --limit srv-two,pve-test-2
play tests/verify-installer.yml

step "build-installer: a node whose SSH host key isn't trusted is named, and nothing is built"
play_fails "Node pve-test-2 (127.0.0.1): its SSH host key isn't in known_hosts" playbooks/build-installer.yml \
  -e "@$TEST_TMP/untrusted.json" -e installer_work_dir=/var/tmp/usas-untrusted
[ "$(nodes_with /var/tmp/usas-untrusted)" = 0 ] || { echo "ERROR: a node built before every node answered" >&2; exit 1; }

step "Nodes the cluster doesn't have are refused for every host at once, with the real names"
BAD_NODES="pve_node isn't a node in this cluster for 2 host(s): srv-case ('PVE-Test', did you mean pve-test); \
srv-gone ('pve-gone'). Its nodes are pve-test, pve-test-2 (node names are case-sensitive)"
play_fails "$BAD_NODES" playbooks/build-installer.yml -i tests/inventory/bad-nodes.yml
# Those two are in ubuntu_servers, the group's old name: read, and said so.
grep -q 'DEPRECATED (still read until 1.0): The group ubuntu_servers is now called servers' "$last_log"
play_fails "$BAD_NODES" playbooks/provision-vms.yml -i tests/inventory/bad-nodes.yml
no_vms 4061 4062 4081 4083 4084 4099
play_fails 'ubuntu_iso_name may contain only' playbooks/build-installer.yml -e 'ubuntu_iso_name=../../etc/evil.iso'

step "build-installer: an ISO that doesn't have the checksum the OS catalog pins is refused, the copy kept"
# The real catalog pins the real ISO's checksum, which the stand-in doesn't have.
play_fails "with the checksum the OS catalog pins" playbooks/build-installer.yml --limit pve-test \
  -e @roles/os_catalog/vars/catalog.yml
grep -q "update ubuntu-server-auto-setup, whose" "$last_log"
docker exec "$PVE" cmp /srv/ubuntu/ubuntu-26.04.1-live-server-amd64.iso /var/lib/vz/template/iso/ubuntu-26.04.1-live-server-amd64.iso

step "build-installer: a point release the catalog doesn't pin is checked against the signed SHA256SUMS"
play_says 'Pick the checksum for ubuntu-26.04.1-live-server-amd64.iso' playbooks/build-installer.yml "${UNPINNED[@]}"
play --idempotent playbooks/build-installer.yml "${UNPINNED[@]}"

step "build-installer: a checksum file that isn't signed by the pinned key is refused"
play_fails 'The signature on http://127.0.0.1:8080/tampered/SHA256SUMS is bad' playbooks/build-installer.yml \
  "${UNPINNED[@]}" -e ubuntu_iso_base_url=http://127.0.0.1:8080/tampered
play_fails "Couldn't fetch http://127.0.0.1:8080/unsigned/SHA256SUMS.gpg" playbooks/build-installer.yml \
  "${UNPINNED[@]}" -e ubuntu_iso_base_url=http://127.0.0.1:8080/unsigned
# A good signature, but by a key other than the pinned one (the test key is in the keyring, Ubuntu's isn't).
play_fails "isn't by the key 843938DF228D22F7B3742BC0D94AA3F0EFE21092" playbooks/build-installer.yml \
  "${UNPINNED[@]}" -e installer_signing_fingerprint=843938DF228D22F7B3742BC0D94AA3F0EFE21092
play_fails 'must be the signing key' playbooks/build-installer.yml -e installer_signing_fingerprint=EFE21092
# What tests/real-iso.sh shows of a failed play: the failed task and its message, not the tasks that passed.
failures="$(tests/show-failures.sh "$last_log")"
grep -q "^TASK \[" <<<"$failures"
grep -q "must be the signing key" <<<"$failures"
if grep -Eq "^PLAY RECAP|^TASK \[Gathering Facts\]" <<<"$failures" || [ "$(wc -l <<<"$failures")" -ge "$(wc -l <"$last_log")" ]; then
  printf '%s\n' "$failures"
  echo "ERROR: tests/show-failures.sh showed more than the failed task" >&2
  exit 1
fi

step "provision-vms: a VM the installer powers off, then a rerun that skips it"
# With connections dropped as pveproxy restarting a worker drops them: two reads, which are retried, and the
# creation, which Proxmox carries out without answering, so the next try finds the VM and the run carries on.
drop GET /nodes before
drop GET /cluster/resources before
drop POST /nodes/pve-test/qemu after
play_says 'Create VM 4061 (3 retries left)' playbooks/provision-vms.yml --limit srv-01
python3 - <<'PY'
import json, ssl, sys, urllib.request
state = json.load(urllib.request.urlopen("https://127.0.0.1:8006/mock/state", context=ssl._create_unverified_context()))["data"]
creates = [r for r in state["requests"] if r["method"] == "POST" and r["path"] == "/nodes/pve-test/qemu"]
if len(state["dropped"]) != 3 or any(d["times"] for d in state["drops"]):
    sys.exit(f"ERROR: not every dropped call was made: {state['drops']}")
if [c["form"].get("vmid") for c in creates] != ["4061"] or "4061" not in state["vms"]:
    sys.exit(f"ERROR: VM 4061 should have been created by one request, not {len(creates)}")
PY
# Changed settings don't reach a VM that exists: it is skipped, not reconfigured.
play --idempotent playbooks/provision-vms.yml --limit srv-01 -e vm_secure_boot=false

step "provision-vms: a VM on the second node, created through that node's API path, its seed ISO on that node"
play playbooks/provision-vms.yml --limit srv-two
play --idempotent playbooks/provision-vms.yml --limit srv-two

step "provision-vms: the node of a new VM must answer over SSH with a trusted host key first"
play_fails "Node pve-test-2 (127.0.0.1): its SSH host key isn't in known_hosts" playbooks/provision-vms.yml \
  --limit srv-two -e pve_vmid=4082 -e "@$TEST_TMP/untrusted.json"
no_vms 4082

step "provision-vms: values that could break the installer's configuration are refused before anything is created"
play_fails 'admin_user must be a lowercase Linux user name' playbooks/provision-vms.yml --limit srv-01 \
  -e 'admin_user="x; touch /pwned"'
play_fails 'admin_user must be a lowercase Linux user name' playbooks/provision-vms.yml --limit srv-01 \
  -e '{"admin_user": "sysadmin\n"}'

step "provision-vms: an OS the catalog doesn't have, or that can't be installed that way, is refused before anything is created"
play_fails "vm_os 'debian-99' isn't in the OS catalog. The catalog has ubuntu-22.04" playbooks/provision-vms.yml \
  --limit srv-luks -e vm_os=debian-99
play_fails "vm_os 'my-os-1' isn't in the OS catalog and vm_os_family isn't set" playbooks/provision-vms.yml \
  --limit srv-luks -e vm_os=my-os-1 -e vm_install_method=cloud_image -e vm_os_image_url=https://example.com/my.qcow2 \
  -e vm_os_image_sha256=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
play_fails "Debian 13 can't be installed with vm_install_method autoinstall" playbooks/provision-vms.yml \
  --limit srv-luks -e vm_os=debian-13
play_fails "vm_disk_encryption needs vm_install_method autoinstall" \
  playbooks/provision-vms.yml --limit srv-luks -e vm_os=debian-13 -e vm_install_method=cloud_image
play_fails "ubuntu_release 24.04 disagrees with vm_os ubuntu-26.04" playbooks/provision-vms.yml --limit srv-luks \
  -e ubuntu_release=24.04 -e vm_os=ubuntu-26.04
for bad in '-e vm_os=ubuntu-26.04;x' '-e vm_install_method=kickstart' '-e vm_os_family=arch' \
    '-e vm_os_image_url=http://example.com/x.qcow2 -e vm_os_image_sha256=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' \
    '-e vm_os_image_url=https://example.com/x.qcow2'; do
  # shellcheck disable=SC2086 # each case is a list of -e arguments
  play_fails 'vm_os must be an OS catalog id' playbooks/provision-vms.yml --limit srv-luks $bad
done
no_vms 4069

step "The old variable names (before 0.7.0) still set the new ones, and the run says so"
play_says 'ufw_allow is now firewall_allow' tests/verify-aliases.yml -e '{"ufw_allow": [{"port": "8443", "proto": "tcp"}],
  "ufw_ssh_sources": ["10.0.0.0/8"], "ufw_ssh_limit": false, "ufw_default_incoming": "reject", "ufw_logging": "medium",
  "apt_upgrade": "safe", "ubuntu_release": "24.04"}'
grep -q 'apt_upgrade is now pkg_upgrade. ubuntu_release is now vm_os' "$last_log"

step "provision-vms: an install that never finishes is cleaned up"
play_fails 'seed ISO removed' playbooks/provision-vms.yml --limit srv-hang
play tests/verify-provision.yml

step "provision-vms: disk encryption needs Secure Boot, the lvm layout and a usable passphrase"
play_fails 'unencrypted), vm_secure_boot true' playbooks/provision-vms.yml --limit srv-luks \
  -e vm_secure_boot=false
play_fails 'unencrypted), vm_secure_boot true' playbooks/provision-vms.yml --limit srv-luks \
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
dm_crypt-0 UUID=1111-2222 none luks,tpm2-device=auto,tries=5
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

step "ISO storage the cluster shares (NFS): built once, by one node, and the VMs' CD-ROMs use it"
# Every server on 26.04 here, so one ISO is built.
SHARED=(-e pve_iso_storage=synology-nfs -e installer_work_dir=/var/tmp/usas-shared -e vm_os=ubuntu-26.04)
play_says 'synology-nfs on pve-test-2 (shared): built by pve-test' playbooks/build-installer.yml "${SHARED[@]}"
play --idempotent playbooks/build-installer.yml "${SHARED[@]}"
# Only the node that built it has the build's work directory.
[ "$(nodes_with /var/tmp/usas-shared)" = 1 ] && docker exec "$PVE" test -d /var/tmp/usas-shared \
  || { echo "ERROR: the shared storage's ISO wasn't built exactly once, by pve-test" >&2; exit 1; }
# It also opts out of Secure Boot.
play playbooks/provision-vms.yml --limit srv-nfs -e pve_iso_storage=synology-nfs -e vm_secure_boot=false
play tests/verify-iso-storage.yml

step "ISO storages that don't fit a node are refused"
play_fails "srv-two: storage 'usb-iso' doesn't allow ISO images on node pve-test-2, or isn't active there (ISO storages \
there: local, synology-nfs)" playbooks/build-installer.yml -e pve_iso_storage=usb-iso
# The API lists usb-iso on pve-test, but the node itself (pvesm) has no such storage.
play_fails "Storage 'usb-iso' doesn't exist on pve-test, isn't active, or doesn't allow ISO images" \
  playbooks/build-installer.yml --limit srv-01,pve-test -e pve_iso_storage=usb-iso
play_fails "pve_iso_storage 'local;id' a storage ID" playbooks/build-installer.yml -e 'pve_iso_storage=local;id'

step "build-installer: a cloud image goes into its storage's Import folder, once for a shared one, checked"
DEBIAN_IMAGE=$(python3 -c 'import sys, yaml; print({e["id"]: e for e in yaml.safe_load(open(sys.argv[1]))["os_catalog"]}
  ["debian-13"]["methods"]["cloud_image"]["file"])' "$TEST_TMP/catalog.yml")
play_says "synology-nfs on pve-test (shared): downloaded by pve-test ($DEBIAN_IMAGE)" playbooks/build-installer.yml \
  --limit srv-cloud,pve-test,pve-test-2
play --idempotent playbooks/build-installer.yml --limit srv-cloud,pve-test,pve-test-2
docker exec "$PVE2" cmp -s "/mnt/pve/synology-nfs/import/$DEBIAN_IMAGE" "/srv/ubuntu/cloud/$DEBIAN_IMAGE" \
  || { echo "ERROR: the cloud image isn't in the shared storage's Import folder" >&2; exit 1; }
play_fails "Couldn't download http://127.0.0.1:8080/cloud/$DEBIAN_IMAGE with the checksum sha512:000" \
  playbooks/build-installer.yml --limit srv-cloud,pve-test -e "@$TEST_TMP/catalog-bad-image.yml"
docker exec "$PVE" cmp -s "/mnt/pve/synology-nfs/import/$DEBIAN_IMAGE" "/srv/ubuntu/cloud/$DEBIAN_IMAGE" \
  || { echo "ERROR: a download with the wrong checksum replaced the good copy" >&2; exit 1; }
play_fails "srv-cloud: storage 'local' doesn't allow ISO images and Import content (for the cloud image) on node \
pve-test" playbooks/build-installer.yml --limit srv-cloud,pve-test -e pve_iso_storage=local

step "provision-vms: Rocky Linux 10 needs an x86-64-v3 CPU, in the VM and on the node"
play_fails "vm_cpu_type x86-64-v2-AES doesn't offer it" playbooks/provision-vms.yml --limit srv-cloud \
  -e vm_os=rocky-10 -e vm_cpu_type=x86-64-v2-AES
play_fails "pve-test's (Intel(R) Xeon(R) CPU E5-2470 v2 @ 2.40GHz) lacks avx2, bmi2, fma, movbe" \
  playbooks/provision-vms.yml --limit srv-cloud -e vm_os=rocky-10 -e provision_cpuinfo_file=/srv/cpuinfo/cpuinfo-ivybridge
# A newer CPU passes the check; the run stops next because build-installer hasn't fetched Rocky's image.
play_fails "Rocky-10-GenericCloud-Base-" playbooks/provision-vms.yml --limit srv-cloud -e vm_os=rocky-10 \
  -e provision_cpuinfo_file=/srv/cpuinfo/cpuinfo-haswell
grep -q 'is not on pve-test. Run playbooks/build-installer.yml first' "$last_log"
# AlmaLinux 10 (built for x86-64-v2 too) isn't checked.
play_fails "AlmaLinux-10-GenericCloud-" playbooks/provision-vms.yml --limit srv-cloud -e vm_os=alma-10 \
  -e provision_cpuinfo_file=/srv/cpuinfo/cpuinfo-ivybridge
no_vms 4070

step "provision-vms: a VM from a cloud image: its disk imported and grown, a cloud-init seed, powered off once set up"
play playbooks/provision-vms.yml --limit srv-cloud
play --idempotent playbooks/provision-vms.yml --limit srv-cloud
play tests/verify-cloud-image.yml

step "provision-vms: extra disks added after the install, and the tag of a Kubernetes agent"
play playbooks/provision-vms.yml --limit srv-disks
play --idempotent playbooks/provision-vms.yml --limit srv-disks
play tests/verify-disks.yml

step "provision-vms: two VMs of one spread group on one node are refused, counting hosts outside the run"
# srv-01's VM runs on pve-test, with srv-disks's, though spread.yml moves srv-01's pve_node to pve-test-2.
play_fails "vm_spread_group lab puts srv-01 and srv-disks on node pve-test (srv-01's VM 4061 running there now)" \
  playbooks/provision-vms.yml -i tests/inventory/spread.yml --limit srv-dhcp
no_vms 4063
play_fails 'may be in only one k8s_<cluster>_servers or _agents group, not k8s_lab_servers and k8s_other_agents' \
  playbooks/provision-vms.yml -i tests/inventory/spread.yml --limit srv-hang
play_fails 'vm_spread_group must be empty or a name' playbooks/provision-vms.yml --limit srv-dhcp \
  -e 'vm_spread_group=lab;x'

step "provision-vms: extra disk settings that could add options to a disk are refused"
for bad in '{"size_gb": "8,cache=unsafe", "label": "data", "mount": "/srv/data"}' \
    '{"size_gb": 8, "label": "data,serial=x", "mount": "/srv/data"}' \
    '{"size_gb": 8, "label": "data", "mount": "/etc"}' '{"size_gb": 8, "label": "data", "mount": "/srv/../etc"}' \
    '{"size_gb": 8, "storage": "local-lvm,iothread=0", "label": "data", "mount": "/srv/data"}' \
    '{"size_gb": 8, "label": "data", "mount": "/srv/data", "fs": "btrfs"}' \
    '{"size_gb": 8, "label": "data", "mount": "/srv/data", "cache": "unsafe"}'; do
  play_fails 'vm_extra_disks item 1 (scsi1) may have only the keys' playbooks/provision-vms.yml --limit srv-dhcp \
    -e "{\"vm_extra_disks\": [$bad]}"
done
play_fails 'the label and the mount of each disk in vm_extra_disks must all differ' playbooks/provision-vms.yml \
  --limit srv-dhcp -e '{"vm_extra_disks": [{"size_gb": 8, "label": "a", "mount": "/srv/a"}, {"size_gb": 8, "label": "a", "mount": "/srv/b"}]}'
no_vms 4063

step "provision-vms: a server on DHCP, its address read from the guest agent, then a rerun that reads it again"
play playbooks/provision-vms.yml --limit srv-dhcp
play --idempotent playbooks/provision-vms.yml --limit srv-dhcp

step "provision-vms: a VLAN on net0 and two more cards, each matched by its MAC address"
play playbooks/provision-vms.yml --limit srv-vlan
play tests/verify-network.yml

step "provision-vms: no DHCP lease, or a guest agent the token may not ask, stops with what to do"
# A VM installed but never reached says how to recover: later runs skip it, and its host key isn't trusted.
play_fails 'VM 4065 (srv-nolease) reported no DHCP address on net0.*its SSH host key was never trusted' \
  playbooks/provision-vms.yml --limit srv-nolease
# A token without the privilege is refused before the VM is created, not after its install.
play_fails "role, as docs/installing.md describes. VM 4066 wasn't created." playbooks/provision-vms.yml \
  --limit srv-forbidden
no_vms 4066

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
# An NFS export that lets only root in: the ACL entry goes on, but the user can't reach the folder.
docker exec "$PVE" sh -c 'mkdir -p /mnt/pve/squash-nfs/template/iso && chmod 0700 /mnt/pve/squash-nfs'
play_fails "usas-build can't write to /mnt/pve/squash-nfs/template/iso (mktemp: " playbooks/prepare-node.yml \
  -e "@$build_user" -e '{"proxmox_node_user_iso_storages": ["local", "squash-nfs"]}'
play playbooks/prepare-node.yml -e "@$build_user"
play --idempotent playbooks/prepare-node.yml -e "@$build_user"
# A work directory whose owner has no name (as after usermod -u usas-build): refused with the reason, not a crash.
docker exec "$PVE" chown 4242 /var/tmp/ubuntu-server-auto-setup
play_fails "isn't a folder that root or usas-build owns (owner: UID 4242)" playbooks/prepare-node.yml -e "@$build_user"
docker exec "$PVE" chown usas-build:usas-build /var/tmp/ubuntu-server-auto-setup
play_fails 'logs in to pve-test as root, but pve_ssh_user is usas-build' playbooks/build-installer.yml \
  -e pve_ssh_user=usas-build -e ansible_user=root
play_fails 'pve_ssh_user must be a lowercase Linux user name' playbooks/build-installer.yml -e 'pve_ssh_user=x;id'
# Every server on 26.04 here, so each node rebuilds the same ISO as the user.
play playbooks/build-installer.yml -e pve_ssh_user=usas-build -e installer_iso_rebuild=true -e vm_os=ubuntu-26.04
play --idempotent playbooks/build-installer.yml -e pve_ssh_user=usas-build -e vm_os=ubuntu-26.04
play playbooks/build-installer.yml -e pve_ssh_user=usas-build -e pve_iso_storage=synology-nfs -e vm_os=ubuntu-26.04
# If the export stops letting the user in, the build says so instead of "Permission denied".
template_mode="$(docker exec "$PVE" stat -c %a /mnt/pve/synology-nfs/template)"
docker exec "$PVE" chmod 0700 /mnt/pve/synology-nfs/template
play_fails "/mnt/pve/synology-nfs/template/iso on pve-test can't be reached by usas-build" \
  playbooks/build-installer.yml --limit srv-01,pve-test -e pve_ssh_user=usas-build -e pve_iso_storage=synology-nfs
docker exec "$PVE" chmod "$template_mode" /mnt/pve/synology-nfs/template
play playbooks/provision-vms.yml --limit srv-user -e pve_ssh_user=usas-build
play tests/verify-node-user.yml
# usb-iso allows ISO images on pve-test (so the API check passes), but prepare-node.yml didn't record it.
play_fails "doesn't name a folder for storage 'usb-iso'" playbooks/build-installer.yml --limit srv-01,pve-test \
  -e pve_ssh_user=usas-build -e pve_iso_storage=usb-iso

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

step "baseline: extra disks formatted while blank and mounted by label, then a rerun that changes nothing"
# Loop devices stand in for the VM's disks, under the names QEMU gives them by serial.
DISKS=(--limit srv-disks --tags data_disks -e data_disks_by_id_dir=/srv/test-disks)
docker exec "$SRV" sh -euc 'mkdir -p /srv/test-disks; for label in data app-logs other; do
    truncate -s 320M /srv/test-disks/$label.img; d=$(losetup -f --show /srv/test-disks/$label.img)
    echo "$d" >>/srv/test-disks/loops; ln -s "$d" /srv/test-disks/scsi-0QEMU_QEMU_HARDDISK_usas-$label; done
  mkfs.ext4 -q -L other /srv/test-disks/scsi-0QEMU_QEMU_HARDDISK_usas-other'
play playbooks/baseline.yml "${DISKS[@]}"
play --idempotent playbooks/baseline.yml "${DISKS[@]}"
play --idempotent playbooks/baseline.yml "${DISKS[@]}" --check
[ "$(docker exec "$SRV" findmnt -n -o FSTYPE,LABEL /srv/data | tr -s ' ')" = "ext4 data" ] \
  && [ "$(docker exec "$SRV" findmnt -n -o FSTYPE,LABEL /var/log/app | tr -s ' ')" = "xfs app-logs" ] \
  && docker exec "$SRV" grep -qx 'LABEL=data /srv/data ext4 defaults,nofail 0 0' /etc/fstab \
  || { echo "ERROR: the extra disks aren't mounted by label with nofail" >&2; docker exec "$SRV" sh -c 'findmnt; cat /etc/fstab'; exit 1; }

step "baseline: a disk that holds another file system is left alone, and a missing disk is named"
OTHER=(-e '{"vm_extra_disks": [{"size_gb": 1, "label": "other", "mount": "/srv/other", "fs": "xfs"}]}')
play_fails "the disk usas-other (/srv/test-disks/scsi-0QEMU_QEMU_HARDDISK_usas-other) holds a file system (type ext4, \
label other), not nothing or an xfs file system" playbooks/baseline.yml "${DISKS[@]}" "${OTHER[@]}"
[ "$(docker exec "$SRV" blkid -p -s TYPE -o value /srv/test-disks/scsi-0QEMU_QEMU_HARDDISK_usas-other)" = ext4 ] \
  || { echo "ERROR: a disk holding a file system was formatted" >&2; exit 1; }
play_fails 'srv-disks has no disk with the serial usas-gone' playbooks/baseline.yml "${DISKS[@]}" \
  -e '{"vm_extra_disks": [{"size_gb": 1, "label": "gone", "mount": "/srv/gone"}]}'
docker exec "$SRV" sh -euc 'umount /srv/data /var/log/app; sed -i "/,nofail /d" /etc/fstab
  for d in $(cat /srv/test-disks/loops); do losetup -d "$d"; done; rm -r /srv/test-disks'
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

step "baseline: optional hardening, notifications and time sources, all on, then a rerun that changes nothing"
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
play tests/verify-time-sync.yml -e ansible_port=2222
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 --check --diff "${P2222[@]}" "${HARDENING[@]}"

step "baseline: hardening, notification and time source values that could inject settings are refused"
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
cool_down
play_fails 'time_sync_servers and time_sync_pools must be lists of host names' playbooks/baseline.yml --limit srv-01 \
  --tags common "${P2222[@]}" -e '{"time_sync_servers": ["ntp.example.com iburst\nserver 203.0.113.9"]}'
play_fails 'time_sync_servers and time_sync_pools must be lists of host names' playbooks/baseline.yml --limit srv-01 \
  --tags common "${P2222[@]}" -e '{"time_sync_pools": "pool.example.net"}'
docker exec "$SRV" sh -c '! test -e /pwned' || { echo "ERROR: an injected command ran" >&2; exit 1; }

step "baseline: turning them off again removes their files"
cool_down
play playbooks/baseline.yml --limit srv-01 "${P2222[@]}"
docker exec "$SRV" sh -c '! test -e /etc/sysctl.d/90-usas-hardening.conf && ! test -e /etc/audit/rules.d/60-usas-hardening.rules \
  && ! test -e /etc/fail2ban/action.d/usas-webhook.conf && ! test -e /etc/msmtp-password \
  && ! grep -q Mail /etc/apt/apt.conf.d/52unattended-upgrades-local && ! grep -Eq "^(action|destemail) =" /etc/fail2ban/jail.local \
  && ! test -e /etc/chrony/sources.d/usas.sources && ! grep -rq "^#usas-time-sync# " /etc/chrony' \
  || { echo "ERROR: turning the options off left their files behind" >&2; exit 1; }
cool_down
play tests/verify-time-sync.yml -e ansible_port=2222 -e expect_time_sync=false
cool_down
play tests/verify-baseline.yml -e expect_port=2222 -e ansible_port=2222 -e expect_nopasswd=true

step "baseline: Docker-published ports: skipped without Docker, then blocked unless allowed"
# At most three plays between pauses: UFW limits SSH to 6 new connections in 30 seconds.
# The stand-in publishes the server's port 8080 (the runner's 127.0.0.1:2280) as Docker does, past UFW.
published() { curl -fsS --max-time 3 -o /dev/null http://127.0.0.1:2280/; }
DOCKER_ON=(--tags firewall "${P2222[@]}" -e firewall_docker_enabled=true)
cool_down
play_says "Docker isn't installed on srv-01: no DOCKER-USER rules were written" playbooks/baseline.yml \
  --limit srv-01 "${DOCKER_ON[@]}"
docker exec "$SRV" sh -c '! grep -q "USAS: Docker" /etc/ufw/after.rules' \
  || { echo "ERROR: DOCKER-USER rules were written without Docker" >&2; exit 1; }
docker cp tests/fixtures/docker-stand-in/docker.service "$SRV":/etc/systemd/system/docker.service
docker cp tests/fixtures/docker-stand-in/usas-docker-stand-in "$SRV":/usr/local/sbin/usas-docker-stand-in
docker exec "$SRV" sh -c 'systemctl daemon-reload && systemctl start docker'
for _ in $(seq 20); do published && break; sleep 0.5; done
published || { echo "ERROR: the Docker stand-in's published port doesn't answer" >&2; exit 1; }
play playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}"
! published || { echo "ERROR: a published port answers with firewall_docker_enabled and no allowance" >&2; exit 1; }
play --idempotent playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}"
cool_down
play tests/verify-docker-ports.yml -e ansible_port=2222
play playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}" -e '{"firewall_docker_allow": [{"port": 8080}]}'
published || { echo "ERROR: the published port allowed in firewall_docker_allow doesn't answer" >&2; exit 1; }
play tests/verify-docker-ports.yml -e ansible_port=2222 -e '{"firewall_docker_allow": [{"port": 8080}]}'
# The address the "container" saw the last request from: the runner, as the server sees it.
client="$(docker exec "$SRV" sh -c "tail -n 1 /var/log/usas-ctr.log | cut -d' ' -f1")"
cool_down
play playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}" -e "{\"firewall_docker_sources\": [\"192.0.2.0/24\", \"fd00::/8\"]}"
! published || { echo "ERROR: a published port answers a source firewall_docker_sources doesn't list" >&2; exit 1; }
play playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}" -e "{\"firewall_docker_sources\": [\"$client\"]}"
published || { echo "ERROR: the published port doesn't answer $client, listed in firewall_docker_sources" >&2; exit 1; }
cool_down
play_fails 'firewall_docker_sources must be a list of IPv4 or IPv6 addresses' playbooks/baseline.yml --limit srv-01 \
  "${DOCKER_ON[@]}" -e '{"firewall_docker_sources": ["0.0.0.0/0 -j ACCEPT"]}'
play_fails 'Each firewall_docker_allow entry is' playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}" \
  -e '{"firewall_docker_allow": [{"port": "8080\n-A DOCKER-USER -j ACCEPT"}]}'
play_fails 'Each firewall_docker_allow entry is' playbooks/baseline.yml --limit srv-01 "${DOCKER_ON[@]}" \
  -e '{"firewall_docker_allow": [{"port": "8080", "proto": "tcp -j ACCEPT"}]}'
cool_down
play playbooks/baseline.yml --limit srv-01 --tags firewall "${P2222[@]}"
published || { echo "ERROR: turning firewall_docker_enabled off left the published port blocked" >&2; exit 1; }
play tests/verify-docker-ports.yml -e ansible_port=2222 -e expect_docker_rules=false
docker exec "$SRV" sh -c 'systemctl stop docker && rm /etc/systemd/system/docker.service && systemctl daemon-reload'

# The Kubernetes node profile (roles/kubernetes_node), shared with tests/baseline.sh.
# shellcheck source=tests/kubernetes-node.sh
. tests/kubernetes-node.sh

step "baseline: sudo-rs without the classic sudo is refused before anything changes"
cool_down
docker exec "$SRV" mv /usr/bin/sudo.ws /usr/bin/sudo.ws.hidden
play_fails "whose password prompt Ansible doesn't recognise" playbooks/baseline.yml --limit srv-01 \
  -e ssh_port=2222 -e ansible_port=2222 -e admin_sudo_nopasswd=false
docker exec "$SRV" mv /usr/bin/sudo.ws.hidden /usr/bin/sudo.ws

step "Task coverage"
# The updates and k3s roles have their own suites and figures (tests/updates.sh, tests/kubernetes.sh).
# Ubuntu only here: the RHEL family's task files are tests/baseline.sh's.
python3 tests/coverage.py "$TASK_COVERAGE_FILE" --min "$MIN_COVERAGE" --skip-roles updates,k3s \
  --skip-files '*/RedHat.yml,roles/common/tasks/epel.yml,roles/kubernetes_node/tasks/selinux.yml'

step "All integration tests passed"
