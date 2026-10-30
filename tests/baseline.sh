#!/usr/bin/env bash
# The baseline on one OS: runs playbooks/baseline.yml against a container that stands in for a freshly installed
# server of that release (tests/images/ubuntu-target, stage "target"), in check mode first, then twice (the second
# run must change nothing), checks the result with tests/verify-baseline.yml, the server's own update settings and
# its reboot check, then moves SSH to port 2222 and checks again. OS picks the release (debian-13 by default; CI
# runs each release tests/run.sh doesn't). Uses the same container name and ports as tests/run.sh, so only one of
# them runs at a time. Needs Docker (privileged containers, for systemd), Python with requirements-ci.txt and the
# Ansible collections. Run it with make test-baseline OS=<id>. See docs/testing.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OS="${OS:-debian-13}"
case "$OS" in
  debian-12|debian-13|ubuntu-22.04|ubuntu-24.04|ubuntu-26.04) ;;
  *) echo "OS must be debian-12, debian-13, ubuntu-22.04, ubuntu-24.04 or ubuntu-26.04" >&2; exit 2;;
esac
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-baseline.XXXXXX")"
export TEST_TMP
export ANSIBLE_CONFIG="$ROOT/ansible.cfg"
export ANSIBLE_CALLBACK_PLUGINS="$ROOT/tests/callback_plugins"
export ANSIBLE_CALLBACKS_ENABLED=task_coverage
export TASK_COVERAGE_FILE="$TEST_TMP/coverage.txt"
export ANSIBLE_FORCE_COLOR=0
INV=(-i tests/inventory/hosts.yml)
SRV=usas-test-srv
IMAGE="usas-test-srv:$OS"
MIN_COVERAGE="${MIN_COVERAGE:-80}"
mock_pid=""

cleanup() {
  status=$?
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; wait "$mock_pid" 2>/dev/null || true; fi
  docker rm -f "$SRV" >/dev/null 2>&1 || true
  if [ "$status" -ne 0 ]; then echo "FAILED (logs kept in $TEST_TMP)" >&2; else rm -rf "$TEST_TMP"; fi
}
trap cleanup EXIT

step() { printf '\n=== %s: %s\n' "$OS" "$*"; }

# Runs a playbook and fails unless it succeeds; with --idempotent also fails if anything changed.
play() {
  local idempotent=false
  if [ "$1" = "--idempotent" ]; then idempotent=true; shift; fi
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  last_log="$log"
  if ! ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: ansible-playbook $* failed" >&2; return 1
  fi
  sed -n '/PLAY RECAP/,$p' "$log"
  if $idempotent && grep -Eq 'changed=[1-9]' <(sed -n '/PLAY RECAP/,$p' "$log"); then
    grep -B3 -E '^changed:' "$log" || true
    echo "ERROR: second run of $* changed something" >&2; return 1
  fi
}

# UFW limits SSH to 6 new connections in 30 s per source, and every connection comes from Docker's one address.
cool_down() { sleep 31; }

step "Setting up keys, the keys server and the container"
ssh-keygen -q -t ed25519 -N '' -C test@ubuntu-server-auto-setup -f "$TEST_TMP/id_ed25519"
cp "$TEST_TMP/id_ed25519.pub" "$TEST_TMP/test.keys"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 \
  -keyout "$TEST_TMP/api.key" -out "$TEST_TMP/api.crt" >/dev/null 2>&1
# Serves the public keys file that stands in for https://github.com/<user>.keys (and an API the baseline doesn't use).
python3 tests/mock_pve.py --cert "$TEST_TMP/api.crt" --key "$TEST_TMP/api.key" \
  --keys-file "$TEST_TMP/test.keys" >"$TEST_TMP/mock.log" 2>&1 &
mock_pid=$!
docker build -q -t "$IMAGE" --build-arg OS="$OS" --target target tests/images/ubuntu-target >/dev/null
docker rm -f "$SRV" >/dev/null 2>&1 || true
docker run -d --name "$SRV" --hostname srv-01 --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
  -p 127.0.0.1:2201:22 -p 127.0.0.1:2222:2222 "$IMAGE" >/dev/null
docker exec -i "$SRV" sh -c 'cat > /home/sysadmin/.ssh/authorized_keys && chown sysadmin: /home/sysadmin/.ssh/authorized_keys && chmod 600 /home/sysadmin/.ssh/authorized_keys' \
  <"$TEST_TMP/id_ed25519.pub"
# Docker owns /etc/hosts in a container, so put the line the common role wants there up front.
docker exec "$SRV" sh -c 'echo "127.0.1.1 srv-01" >> /etc/hosts'
for _ in $(seq 1 60); do ssh-keyscan -T 2 -p 2201 127.0.0.1 >"$TEST_TMP/known_hosts" 2>/dev/null && [ -s "$TEST_TMP/known_hosts" ] && break; sleep 1; done
[ -s "$TEST_TMP/known_hosts" ] || { echo "ERROR: no SSH on port 2201" >&2; exit 1; }
family="$(docker exec "$SRV" sh -c '. /etc/os-release && echo "$ID"')"

step "check mode on the fresh server fails nothing and changes nothing"
play playbooks/baseline.yml --limit srv-01 --check --diff
docker exec "$SRV" sh -c '! test -e /etc/ssh/sshd_config.d/00-hardening.conf && ! command -v ufw' >/dev/null \
  || { echo "ERROR: a check run changed the server" >&2; exit 1; }

step "first run, second run, checks"
play playbooks/baseline.yml --limit srv-01
play --idempotent playbooks/baseline.yml --limit srv-01
play tests/verify-baseline.yml
cool_down

step "check mode on a server that has the baseline reports no changes"
play --idempotent playbooks/baseline.yml --limit srv-01 --check --diff
cool_down

step "unattended-upgrades installs the distribution's own security updates"
origins="$(docker exec "$SRV" cat /etc/apt/apt.conf.d/52unattended-upgrades-local)"
# shellcheck disable=SC2016 # ${distro_id} is unattended-upgrades' own placeholder
grep -q '"${distro_id}:${distro_codename}-security";' <<<"$origins"
if [ "$family" = debian ]; then
  ! grep -q ESM <<<"$origins" || { echo "ERROR: Debian has no ESM origins" >&2; exit 1; }
else
  # shellcheck disable=SC2016 # as above
  grep -q '"${distro_id}ESM:${distro_codename}-infra-security";' <<<"$origins"
fi

step "a waiting reboot is reported (Ubuntu: reboot-required; Debian: a newer kernel in /boot)"
if [ "$family" = debian ]; then
  docker exec "$SRV" sh -c 'mkdir -p /boot && touch /boot/vmlinuz-99.0.0-1-amd64'
else
  docker exec "$SRV" sh -c 'echo "*** System restart required ***" > /var/run/reboot-required'
fi
play playbooks/baseline.yml --limit srv-01 --tags common
grep -q 'srv-01 needs a reboot to finish upgrades' "$last_log" \
  || { echo "ERROR: the waiting reboot wasn't reported" >&2; exit 1; }
docker exec "$SRV" rm -f /boot/vmlinuz-99.0.0-1-amd64 /var/run/reboot-required
play playbooks/baseline.yml --limit srv-01 --tags common
if grep -q 'needs a reboot' "$last_log"; then echo "ERROR: a reboot was reported with none waiting" >&2; exit 1; fi
cool_down

step "move SSH to port 2222, check, rerun on the new port"
play playbooks/baseline.yml --limit srv-01 -e ssh_port=2222
cool_down
play tests/verify-baseline.yml -e expect_port=2222
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 -e ssh_port=2222 -e ansible_port=2222

step "notifications, a mail relay and passwordless sudo on, a rerun that changes nothing, then off again"
# tests/hardening-vars.yml without the kernel, audit and AppArmor parts (tests/run.sh covers those on Ubuntu).
python3 -c 'import sys, yaml; d = yaml.safe_load(open(sys.argv[1]))
yaml.safe_dump({k: v for k, v in d.items() if not k.startswith("hardening_")}, open(sys.argv[2], "w"))' \
  tests/hardening-vars.yml "$TEST_TMP/notify.yml"
P2222=(-e ssh_port=2222 -e ansible_port=2222)
cool_down
play playbooks/baseline.yml --limit srv-01 "${P2222[@]}" -e "@$TEST_TMP/notify.yml" -e admin_sudo_nopasswd=true
cool_down
play --idempotent playbooks/baseline.yml --limit srv-01 "${P2222[@]}" -e "@$TEST_TMP/notify.yml" -e admin_sudo_nopasswd=true
docker exec "$SRV" sh -c 'test -s /etc/msmtprc && test -e /etc/sudoers.d/90-ansible-sysadmin' \
  || { echo "ERROR: the mail relay or passwordless sudo wasn't set up" >&2; exit 1; }
cool_down
play playbooks/baseline.yml --limit srv-01 "${P2222[@]}"
docker exec "$SRV" sh -c '! test -e /etc/sudoers.d/90-ansible-sysadmin' \
  || { echo "ERROR: passwordless sudo wasn't removed again" >&2; exit 1; }

step "Task coverage of the baseline roles on $OS"
python3 tests/coverage.py "$TASK_COVERAGE_FILE" --min "$MIN_COVERAGE" --roles common,firewall,ssh_hardening,fail2ban
echo "=== $OS: the baseline passed"
