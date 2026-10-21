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
INV=(-i tests/inventory/hosts.yml)
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

step "build-installer: remaster the ISO, then check a second run changes nothing"
play playbooks/build-installer.yml
play --idempotent playbooks/build-installer.yml
play tests/verify-installer.yml
play_fails 'ubuntu_iso_name may contain only' playbooks/build-installer.yml -e 'ubuntu_iso_name=../../etc/evil.iso'

step "provision-vms: a VM the installer powers off, then a rerun that skips it"
play playbooks/provision-vms.yml --limit srv-01
play --idempotent playbooks/provision-vms.yml --limit srv-01

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

step "ISO storage other than local: its folder is asked from Proxmox, and the VM's CD-ROMs use it"
play playbooks/build-installer.yml -e pve_iso_storage=synology-nfs
play --idempotent playbooks/build-installer.yml -e pve_iso_storage=synology-nfs
play playbooks/provision-vms.yml --limit srv-nfs -e pve_iso_storage=synology-nfs
play tests/verify-iso-storage.yml
play_fails "Storage 'backup-nfs' doesn't exist on pve-test, isn't active, or doesn't allow ISO images" \
  playbooks/build-installer.yml -e pve_iso_storage=backup-nfs
play_fails 'pve_iso_storage must be a Proxmox storage ID' playbooks/build-installer.yml -e 'pve_iso_storage=local;id'

step "baseline: first run, second run, checks"
play playbooks/baseline.yml --limit srv-01
play --idempotent playbooks/baseline.yml --limit srv-01
play tests/verify-baseline.yml
cool_down

step "baseline: a key that is no longer published is removed"
docker exec "$SRV" sh -c 'echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK old@laptop" >> /home/sysadmin/.ssh/authorized_keys'
play playbooks/baseline.yml --limit srv-01 --tags ssh_keys
play tests/verify-baseline.yml
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

step "Task coverage"
python3 tests/coverage.py "$TASK_COVERAGE_FILE" --min "$MIN_COVERAGE"

step "All integration tests passed"
