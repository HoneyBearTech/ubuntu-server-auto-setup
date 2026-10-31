#!/usr/bin/env bash
# Integration tests of the updates playbook: runs playbooks/updates.yml against two containers that have
# updates waiting in a signed test repository (tests/images/ubuntu-target, stage "updates"), checks what was
# installed, the reports, reboots and health checks, that a second run changes nothing, and the role's task
# coverage. OS picks the Ubuntu release (ubuntu-26.04 by default; CI runs each). Needs Docker (privileged
# containers, for systemd), Python with requirements-ci.txt and the Ansible collections. Run it with
# make test-updates. With USAS_LOG_DIR set, a failed run keeps its logs there. See docs/testing.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OS="${OS:-ubuntu-26.04}"
case "$OS" in ubuntu-22.04|ubuntu-24.04|ubuntu-26.04) ;; *) echo "OS must be ubuntu-22.04, ubuntu-24.04 or ubuntu-26.04" >&2; exit 2;; esac
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-updates.XXXXXX")"
export TEST_TMP
export ANSIBLE_CONFIG="$ROOT/ansible.cfg"
export ANSIBLE_CALLBACK_PLUGINS="$ROOT/tests/callback_plugins"
export ANSIBLE_CALLBACKS_ENABLED=task_coverage
export TASK_COVERAGE_FILE="$TEST_TMP/coverage.txt"
export ANSIBLE_FORCE_COLOR=0
INV=(-i tests/inventory/updates.yml)
IMAGE="usas-test-upd:$OS"
UPD1=usas-test-upd1
UPD2=usas-test-upd2
MIN_COVERAGE="${MIN_COVERAGE:-80}"
# A window that is never open: one minute, twelve hours from now.
NEVER="{\"days\": [\"mon\", \"tue\", \"wed\", \"thu\", \"fri\", \"sat\", \"sun\"], \"start\": \"$(date -u -d '+12 hours' +%H:%M 2>/dev/null || date -u -v+12H +%H:%M)\", \"duration\": \"1m\", \"timezone\": \"UTC\"}"

# On failure, with USAS_LOG_DIR set (CI uploads it): each play's log, the reports, the notification stand-ins'
# log, and each container's failed units and recent warnings. No keys: they stay in TEST_TMP.
keep_logs() {
  [ -n "${USAS_LOG_DIR:-}" ] || return 0
  mkdir -p "$USAS_LOG_DIR"
  for log in "$TEST_TMP"/play.*; do [ -e "$log" ] && cp "$log" "$USAS_LOG_DIR/$(basename "$log").log"; done
  cp "$TEST_TMP"/reports/*.json "$TEST_TMP"/sink.log "$TEST_TMP"/notified.jsonl "$USAS_LOG_DIR/" 2>/dev/null || true
  for c in "$UPD1" "$UPD2"; do
    docker exec "$c" sh -c 'systemctl list-units --failed --no-pager; journalctl -b -p warning --no-pager | tail -n 300' \
      >"$USAS_LOG_DIR/$c-units.log" 2>&1 || true
  done
}

mock_pid=""
web_pid=""

cleanup() {
  status=$?
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; fi
  if [ -n "$web_pid" ]; then kill "$web_pid" 2>/dev/null || true; fi
  if [ "$status" -ne 0 ]; then keep_logs; fi
  docker rm -f "$UPD1" "$UPD2" >/dev/null 2>&1 || true
  if [ "$status" -ne 0 ]; then echo "FAILED (logs kept in $TEST_TMP)" >&2; else rm -rf "$TEST_TMP"; fi
}
trap cleanup EXIT

step() { printf '\n=== %s\n' "$*"; }

# Runs the playbook and fails unless it succeeds; with --idempotent also fails if anything changed.
play() {
  local idempotent=false
  if [ "$1" = "--idempotent" ]; then idempotent=true; shift; fi
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  if ! ansible-playbook "${INV[@]}" playbooks/updates.yml "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: ansible-playbook updates.yml $* failed" >&2; return 1
  fi
  sed -n '/PLAY RECAP/,$p' "$log"
  if $idempotent && grep -Eq 'changed=[1-9]' <(sed -n '/PLAY RECAP/,$p' "$log"); then
    grep -B3 -E '^changed:' "$log" || true
    echo "ERROR: a second run of updates.yml $* changed something" >&2; return 1
  fi
}

# Runs the playbook, which must fail with the expected message in its output.
play_fails() {
  local expect="$1"; shift
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  if ansible-playbook "${INV[@]}" playbooks/updates.yml "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: updates.yml $* should have failed" >&2; return 1
  fi
  grep -q -- "$expect" "$log" || { cat "$log"; echo "ERROR: expected '$expect' in the output" >&2; return 1; }
  echo "failed as expected: $expect"
}

# Checks the newest report: each argument is a Python expression over h (the results by host) and run.
report() {
  python3 - "$TEST_TMP/reports" "$@" <<'PY'
import json, pathlib, sys
newest = max(pathlib.Path(sys.argv[1]).glob("*.json"), key=lambda p: p.stat().st_mtime)
data = json.loads(newest.read_text())
h = {r["host"]: r for r in data["hosts"]}
run = data["run"]
for check in sys.argv[2:]:
    if not eval(check, {"h": h, "run": run}):  # noqa: S307 (the test's own expressions)
        print(json.dumps(data, indent=1))
        sys.exit(f"ERROR: in {newest.name}, not true: {check}")
print(f"report {newest.name}: {len(sys.argv) - 2} checks passed")
PY
}

# The installed version of each test package, as "name=version ...".
versions() {
  docker exec "$1" dpkg-query -W -f='${Package}=${Version} ' usas-test-tool usas-test-extra usas-test-kernel \
    usas-test-pcr7 usas-test-held usas-test-phased usas-test-newdep 2>/dev/null || true
}

expect_versions() {
  local got; got="$(versions "$1")"
  for want in "${@:2}"; do
    [[ " $got " == *" $want "* ]] || { echo "ERROR: $1 has $got, expected $want" >&2; exit 1; }
  done
}

boot_id() { docker exec "$1" cat /run/usas-boot-id; }

wait_ssh() {
  for _ in $(seq 1 60); do
    if ssh-keyscan -T 2 -p "$1" 127.0.0.1 >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "ERROR: no SSH on port $1" >&2
  return 1
}

# A new container with updates waiting: NAME PORT USER. USER sysadmin is the image's admin (sudo with the
# password test-password); any other user is made with passwordless sudo, like a server someone else set up.
fresh() {
  local name="$1" port="$2" user="$3"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --hostname "$name" --restart=always --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock -p "127.0.0.1:$port:22" "$IMAGE" >/dev/null
  if [ "$user" != sysadmin ]; then
    docker exec "$name" sh -c "useradd -m -s /bin/bash $user && echo '$user ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-$user \
      && chmod 0440 /etc/sudoers.d/90-$user && install -d -m 0700 -o $user -g $user /home/$user/.ssh"
  fi
  docker exec -i "$name" sh -c "cat > /home/$user/.ssh/authorized_keys && chown $user: /home/$user/.ssh/authorized_keys \
    && chmod 600 /home/$user/.ssh/authorized_keys" <"$TEST_TMP/id_ed25519.pub"
  wait_ssh "$port"
  # Host keys of its own (the image's are shared by every container), so a host key pin tells them apart.
  docker exec "$name" sh -c 'rm -f /etc/ssh/ssh_host_*_key* && ssh-keygen -A >/dev/null && systemctl restart ssh.service'
  wait_ssh "$port"
  ssh-keygen -q -R "[127.0.0.1]:$port" -f "$TEST_TMP/known_hosts" >/dev/null 2>&1 || true
  ssh-keyscan -p "$port" 127.0.0.1 2>/dev/null >>"$TEST_TMP/known_hosts"
}

step "The role never turns off package signature checks"
if grep -rnEi 'allow_unauthenticated|disable_gpg_check|nogpgcheck|AllowUnauthenticated|AllowInsecureRepositories|trusted=yes|gpgcheck *[:=] *(0|false|no)' \
  roles/updates playbooks/updates.yml; then
  echo "ERROR: the updates role must not turn off signature checks" >&2
  exit 1
fi

step "Setting up keys and two $OS servers with updates waiting"
ssh-keygen -q -t ed25519 -N '' -C test@ubuntu-server-auto-setup -f "$TEST_TMP/id_ed25519"
: >"$TEST_TMP/known_hosts"
docker build -q --build-arg "OS=$OS" --target updates -t "$IMAGE" tests/images/ubuntu-target >/dev/null
fresh "$UPD1" 2341 sysadmin
fresh "$UPD2" 2342 ops

step "Check mode reports what each server would install, and changes nothing"
play --idempotent --check
expect_versions "$UPD1" usas-test-tool=1.0 usas-test-extra=1.0 usas-test-kernel=1.0
expect_versions "$UPD2" usas-test-tool=1.0 usas-test-pcr7=1.0
report "run['mode'] == 'check'" \
  "all(r['status'] == 'pending' and r['checked'] for r in h.values())" \
  "sorted(h['upd-usas']['planned']) == ['usas-test-extra', 'usas-test-kernel', 'usas-test-newdep', 'usas-test-pcr7', 'usas-test-tool']" \
  "h['upd-usas']['security_count'] == 2 and h['upd-usas']['held'] == ['usas-test-held']" \
  "h['upd-usas']['deferred'] == ['usas-test-phased']" \
  "h['upd-usas']['reboot']['expected'] and not h['upd-usas']['reboot']['required']" \
  "h['upd-usas']['auto_updates'] == 'Automatic: security daily, no reboot'" \
  "h['upd-usas']['installed_by_usas'] and not h['upd-other']['installed_by_usas']" \
  "h['upd-usas']['os']['facts'] == 'Ubuntu ${OS#ubuntu-}'"

step "Security updates only, on a server this project installed"
play --limit upd-usas -e updates_scope=security
expect_versions "$UPD1" usas-test-tool=1.2 usas-test-pcr7=1.1 usas-test-newdep=1.0 usas-test-extra=1.0 \
  usas-test-kernel=1.0 usas-test-held=1.0 usas-test-phased=1.0
report "h['upd-usas']['status'] == 'updated'" \
  "sorted(u['name'] for u in h['upd-usas']['upgraded']) == ['usas-test-newdep', 'usas-test-pcr7', 'usas-test-tool']"

step "Everything but an excluded package, on a server this project didn't install: the reboot it needs waits"
boot="$(boot_id "$UPD2")"
play --limit upd-other -e '{"updates_exclude_packages": ["usas-test-extra"]}'
expect_versions "$UPD2" usas-test-tool=1.2 usas-test-kernel=1.1 usas-test-extra=1.0 usas-test-held=1.0
[ "$(boot_id "$UPD2")" = "$boot" ] || { echo "ERROR: upd-other rebooted without updates_reboot" >&2; exit 1; }
report "h['upd-other']['status'] == 'updated' and h['upd-other']['excluded'] == ['usas-test-extra']" \
  "h['upd-other']['reboot']['required'] and not h['upd-other']['reboot']['done']" \
  "h['upd-other']['reboot']['packages'] == ['usas-test-kernel']"

step "A second run with the same settings changes nothing and finds nothing to do"
play --idempotent -e updates_scope=security -e '{"updates_exclude_packages": ["usas-test-extra"]}'
report "all(r['status'] == 'nothing' for r in h.values())"

step "Outside its reboot window a server that needs a reboot waits for it"
play --idempotent --limit upd-other -e updates_reboot=if_required -e "{\"updates_reboot_window\": $NEVER}" \
  -e '{"updates_exclude_packages": ["usas-test-extra"]}'
[ "$(boot_id "$UPD2")" = "$boot" ] || { echo "ERROR: upd-other rebooted outside its reboot window" >&2; exit 1; }
report "h['upd-other']['reboot']['held'] and 'reboot window' in h['upd-other']['reboot']['why']"

step "Everything, with reboots: the server restarts, its health is checked, and a second run changes nothing"
fresh "$UPD1" 2341 sysadmin
boot="$(boot_id "$UPD1")"
python3 -m http.server --bind 127.0.0.1 --directory "$TEST_TMP" 2349 >/dev/null 2>&1 &
web_pid=$!
play --limit upd-usas -e updates_reboot=if_required -e '{"updates_health_services": ["ssh.service"],
  "updates_health_urls": [{"url": "http://127.0.0.1:2349/known_hosts", "status": 200}]}'
kill "$web_pid"
web_pid=""
[ "$(boot_id "$UPD1")" != "$boot" ] || { echo "ERROR: upd-usas didn't reboot" >&2; exit 1; }
expect_versions "$UPD1" usas-test-tool=1.2 usas-test-extra=1.1 usas-test-kernel=1.1 usas-test-held=1.0
report "h['upd-usas']['status'] == 'updated' and h['upd-usas']['reboot']['done']" \
  "h['upd-usas']['health'] == {'status': 'ok', 'problems': []}"
play --idempotent --limit upd-usas -e updates_reboot=if_required
report "h['upd-usas']['status'] == 'nothing'"

step "A disk that unlocks with the TPM: a Secure Boot (PCR 7) update holds the reboot"
fresh "$UPD1" 2341 sysadmin
boot="$(boot_id "$UPD1")"
play --limit upd-usas -e updates_reboot=if_required -e vm_disk_encryption=true
[ "$(boot_id "$UPD1")" = "$boot" ] || { echo "ERROR: upd-usas rebooted after a PCR 7 update" >&2; exit 1; }
report "h['upd-usas']['reboot']['held'] and 'PCR 7' in h['upd-usas']['reboot']['why']" \
  "'usas-test-pcr7' in h['upd-usas']['reboot']['why']"

step "Windows, opt-outs and unsupported releases skip servers without failing the run"
fresh "$UPD1" 2341 sysadmin
play --idempotent -e "{\"updates_window\": $NEVER}"
report "all(r['status'] == 'skipped' and 'outside its update window' in r['reason'] for r in h.values())"
play --idempotent --check -e "{\"updates_window\": $NEVER}"
report "'an apply would skip it' in h['upd-usas']['reason'] and h['upd-usas']['status'] == 'pending'"
play --idempotent -e updates_enabled=false
report "all(r['status'] == 'skipped' and 'opted out' in r['reason'] for r in h.values())"
play --idempotent --limit upd-usas -e '{"updates_supported_releases": []}'
report "h['upd-usas']['status'] == 'unsupported'"
play --idempotent --check --limit upd-usas \
  -e "{\"updates_supported_releases\": [{\"distribution\": \"Ubuntu\", \"version\": \"${OS#ubuntu-}\", \"eol\": \"2020-04-30\"}]}"
report "'past the end of its standard support (2020-04-30)' in h['upd-usas']['os']['warning']"
expect_versions "$UPD1" usas-test-tool=1.0

step "Settings that can't be honoured safely are refused before anything changes"
play_fails "can't be combined with" -e updates_apt_upgrade=dist -e '{"updates_exclude_packages": ["usas-test-extra"]}'
play_fails 'root on it could read it' --limit upd-other -e ansible_become_password=test-password
play_fails 'updates_scope must be one of' -e updates_scope=everything
play_fails 'updates_scope must be one of' -e '{"updates_exclude_packages": ["usas-test-extra; touch /pwned"]}'
play_fails 'updates_scope must be one of' -e '{"updates_health_urls": [{"url": "https://x.example/\nHost: evil"}]}'
play_fails 'updates_window.start must be a time' -e '{"updates_window": {"start": "25:00", "duration": "1h"}}'
expect_versions "$UPD1" usas-test-tool=1.0

step "A busy package manager: the server is skipped, then updated once the lock is free"
docker exec -d "$UPD1" python3 -c 'import fcntl, time
f = open("/var/lib/dpkg/lock-frontend", "r+")
fcntl.lockf(f, fcntl.LOCK_EX)
time.sleep(45)'
sleep 2
play --limit upd-usas
report "h['upd-usas']['status'] == 'busy' and '/var/lib/dpkg/lock-frontend' in h['upd-usas']['reason']"
expect_versions "$UPD1" usas-test-tool=1.0
for _ in $(seq 1 60); do docker exec "$UPD1" pgrep -f fcntl.lockf >/dev/null || break; sleep 1; done
play --limit upd-usas
report "h['upd-usas']['status'] == 'updated'"

step "No Python, or no answer: skipped or reported, and an unreachable server fails the run"
docker exec "$UPD2" mv /usr/bin/python3 /usr/bin/python3.hidden
play --limit upd-other
docker exec "$UPD2" mv /usr/bin/python3.hidden /usr/bin/python3
report "h['upd-other']['status'] == 'skipped' and 'needs python3' in h['upd-other']['reason']"
docker stop "$UPD2" >/dev/null
play_fails "couldn't be reached" --limit upd-other
docker start "$UPD2" >/dev/null
wait_ssh 2342
report "h['upd-other']['status'] == 'unreachable'"

step "A service the update breaks fails its server's health check and stops the rollout"
fresh "$UPD1" 2341 sysadmin
fresh "$UPD2" 2342 ops
for c in "$UPD1" "$UPD2"; do docker exec "$c" systemctl enable --now usas-test-fragile.service >/dev/null 2>&1; done
play_fails 'Unhealthy after the update' -e updates_reboot=if_required -e updates_serial=1
report "sorted(r['status'] for r in h.values()) == ['failed', 'skipped']" \
  "any(r['status'] == 'skipped' and r['reason'].startswith('not started: 1 of 1') for r in h.values())" \
  "any('not running: usas-test-fragile.service' in r['health']['problems'] for r in h.values())"

step "A dist upgrade installs every update but the held one"
fresh "$UPD2" 2342 ops
play --limit upd-other -e updates_apt_upgrade=dist
expect_versions "$UPD2" usas-test-tool=1.2 usas-test-extra=1.1 usas-test-kernel=1.1 usas-test-pcr7=1.1 usas-test-held=1.0
report "h['upd-other']['status'] == 'updated' and h['upd-other']['removals'] == []"

step "Notifications: a summary by mail (STARTTLS, certificate checked) and to a webhook, when something changed"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 -addext "subjectAltName=IP:127.0.0.1" \
  -keyout "$TEST_TMP/relay.key" -out "$TEST_TMP/relay.crt" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 -addext "subjectAltName=IP:127.0.0.1" \
  -keyout "$TEST_TMP/other.key" -out "$TEST_TMP/other.crt" >/dev/null 2>&1
python3 tests/notify_sink.py --cert "$TEST_TMP/relay.crt" --key "$TEST_TMP/relay.key" --smtp-port 2345 \
  --cert2 "$TEST_TMP/other.crt" --key2 "$TEST_TMP/other.key" --smtp-port2 2346 --http-port 2347 \
  --out "$TEST_TMP/notified.jsonl" >"$TEST_TMP/sink.log" 2>&1 &
sink_pid=$!
# The relay's certificate is trusted the way an operator's CA would be; the one on port 2346 isn't.
export SSL_CERT_FILE="$TEST_TMP/relay.crt"
NOTIFY=(-e updates_notify_mail=ops@example.com -e mail_relay_host=127.0.0.1 -e mail_relay_port=2345
  -e mail_relay_from=usas@example.com -e mail_relay_user=usas -e mail_relay_password=relay-secret
  -e updates_webhook_url=http://127.0.0.1:2347/hook/tok-secret -e updates_webhook_format=json)
notified() {
  python3 - "$TEST_TMP/notified.jsonl" "$@" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
n = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
mails, hooks = [m for m in n if m["kind"] == "mail"], [m for m in n if m["kind"] == "webhook"]
for check in sys.argv[2:]:
    if not eval(check, {"mails": mails, "hooks": hooks, "json": json}):  # noqa: S307 (the test's own expressions)
        sys.exit(f"ERROR: not true: {check}\n{json.dumps(n, indent=1)}")
print(f"notifications: {len(sys.argv) - 2} checks passed")
PY
}
fresh "$UPD1" 2341 sysadmin
play --limit upd-usas "${NOTIFY[@]}"
notified "len(mails) == 1 and len(hooks) == 1" \
  "mails[0]['tls'] and mails[0]['user'] == 'usas' and mails[0]['password_ok'] and mails[0]['to'] == ['ops@example.com']" \
  "'Subject: USAS updates apply: 1 updated' in mails[0]['data'] and 'upd-usas: updated' in mails[0]['data']" \
  "json.loads(hooks[0]['body'])['title'].startswith('USAS updates apply: 1 updated') and hooks[0]['path'] == '/hook/tok-secret'"
play --idempotent --limit upd-usas "${NOTIFY[@]}"
notified "len(mails) == 1 and len(hooks) == 1"
play_fails "Couldn't send the mail: Couldn't send the mail through 127.0.0.1:2346" --limit upd-usas "${NOTIFY[@]}" \
  -e mail_relay_port=2346 -e updates_notify=always
notified "len(mails) == 1 and len(hooks) == 2"
play_fails 'updates_webhook_url must be an http(s) URL' --limit upd-usas "${NOTIFY[@]}" \
  -e '{"updates_webhook_url": "https://ntfy.example.com/x\nHost: evil"}'
play_fails 'updates_notify must be always' --limit upd-usas "${NOTIFY[@]}" -e updates_notify=sometimes
unset SSL_CERT_FILE
kill "$sink_pid"
if grep -lE 'relay-secret|tok-secret' "$TEST_TMP"/play.*; then
  echo "ERROR: the relay password or the webhook's token is in the output above" >&2
  exit 1
fi

step "Cluster discovery: every VM on the cluster, pinned by its alias; the ones to leave alone reported, not contacted"
# The mock Proxmox API lists the cluster of tests/mock_pve.py's UPDATE_FIXTURE_VMS: VM 4091 is upd-usas (by its
# pve_vmid); 4093 and 4094 are VMs nobody put in the inventory, reached at the addresses
# tests/inventory/updates-cluster.yml gives vm-4093 and vm-4094 (upd2 and upd1). VM 4094's pinned key is upd2's,
# as if its guest reported the address of another VM: the pin must refuse it.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 -addext "subjectAltName=IP:127.0.0.1" \
  -keyout "$TEST_TMP/api.key" -out "$TEST_TMP/api.crt" >/dev/null 2>&1
python3 tests/mock_pve.py --cert "$TEST_TMP/api.crt" --key "$TEST_TMP/api.key" --keys-file "$TEST_TMP/id_ed25519.pub" \
  --port 2348 --keys-port 2350 --fixture-updates >"$TEST_TMP/mock.log" 2>&1 &
mock_pid=$!
printf 'plugin: usas_pve\nexclude: ["4087"]\n' >"$TEST_TMP/cluster.usas_pve.yml"
CLUSTER=(-i tests/inventory/updates-cluster.yml -i "$TEST_TMP/cluster.usas_pve.yml")
# The alias pins of VMs 4093 and 4094: both upd2's key (right for 4093, wrong for 4094).
pin_cluster() {
  for vmid in 4093 4094; do
    ssh-keygen -q -R "usas-mock-cluster-$vmid" -f "$TEST_TMP/known_hosts" >/dev/null 2>&1 || true
    ssh-keyscan -t ed25519 -p 2342 127.0.0.1 2>/dev/null | sed "s/^\[127.0.0.1\]:2342 /usas-mock-cluster-$vmid /" >>"$TEST_TMP/known_hosts"
  done
}
fresh "$UPD1" 2341 sysadmin
fresh "$UPD2" 2342 ops
pin_cluster
ansible-inventory "${INV[@]}" "${CLUSTER[@]}" --graph >"$TEST_TMP/cluster-graph.txt"
grep -q "vm-4087" "$TEST_TMP/cluster-graph.txt" && { echo "ERROR: the excluded VM 4087 is in the inventory" >&2; exit 1; }
play --idempotent --check "${CLUSTER[@]}" --limit 'all:!upd-other:!vm-4094'
report "h['upd-usas']['pve']['node'] == 'pve-test-2' and h['upd-usas']['installed_by_usas']" \
  "h['vm-4093']['status'] == 'pending' and not h['vm-4093']['installed_by_usas']" \
  "h['vm-4093']['pve']['alias'] == 'usas-mock-cluster-4093' and h['vm-4093']['os']['hint'] == 'Ubuntu 24.04 LTS'" \
  "h['vm-4085']['status'] == 'unsupported' and h['vm-4096']['status'] == 'busy'" \
  "'no address' in h['vm-4086']['reason'] and 'container' in h['vm-4092']['reason']" \
  "'stopped' in h['vm-4095']['reason'] and 'usas-no-updates' in h['vm-4097']['reason'] and 'template' in h['vm-4098']['reason']" \
  "'vm-4087' not in h"
play_fails "couldn't be reached" "${CLUSTER[@]}" --limit 'all:!upd-other' -e updates_scope=security
report "h['vm-4094']['status'] == 'unreachable' and \"isn't trusted as usas-mock-cluster-4094\" in h['vm-4094']['reason']" \
  "h['vm-4093']['status'] == 'updated' and h['upd-usas']['status'] == 'updated'"
expect_versions "$UPD1" usas-test-tool=1.2 usas-test-extra=1.0
expect_versions "$UPD2" usas-test-tool=1.2 usas-test-extra=1.0
# A cluster inventory that can't be read stops the run (any_unparsed_is_failed), rather than leaving its VMs out.
printf 'plugin: usas_pve\nexclude: ["4087; rm -rf /"]\n' >"$TEST_TMP/bad.usas_pve.yml"
play_fails "exclude has values that aren't plain names" -i tests/inventory/updates-cluster.yml \
  -i "$TEST_TMP/bad.usas_pve.yml" --check

step "Snapshots before updating: on each VM's live node, refused or skipped without one, only the playbook's own deleted"
# Changes a fixture VM of the mock: {"feature_snapshot": false} (disks without snapshots), {"forbid_snapshot":
# true} (no VM.Snapshot).
mock_set() {
  curl -sfk -X POST -H 'Content-Type: application/json' -d "$1" https://127.0.0.1:2348/mock/fixture >/dev/null
}
# Checks the mock's state: each argument is a Python expression over snaps (VM ID -> snapshot names, newest first),
# desc (VM ID -> name -> description) and reqs (the API calls).
mock_state() {
  python3 - "$@" <<'PY'
import json, ssl, sys, urllib.request
state = json.load(urllib.request.urlopen("https://127.0.0.1:2348/mock/state", context=ssl._create_unverified_context()))["data"]
snaps = {int(v): [s["name"] for s in l] for v, l in state["snapshots"].items()}
desc = {int(v): {s["name"]: s.get("description", "") for s in l} for v, l in state["snapshots"].items()}
reqs = state["requests"]
for check in sys.argv[1:]:
    if not eval(check, {"snaps": snaps, "desc": desc, "reqs": reqs}):  # noqa: S307 (the test's own expressions)
        sys.exit(f"ERROR: not true: {check}\n{json.dumps(state['snapshots'], indent=1)}")
print(f"mock state: {len(sys.argv) - 1} checks passed")
PY
}
SNAP=("${CLUSTER[@]}" -e updates_snapshot=true)
mock_set '{"vmid": 4093, "set": {"feature_snapshot": false}}'
play --idempotent "${SNAP[@]}" --limit vm-4093
report "h['vm-4093']['status'] == 'skipped'" \
  "h['vm-4093']['reason'] == \"no snapshot: Proxmox says VM 4093's disks don't support snapshots\""
mock_set '{"vmid": 4093, "set": {"feature_snapshot": true, "forbid_snapshot": true}}'
play_fails 'the API token.s role needs VM.Snapshot' "${SNAP[@]}" --limit vm-4093
report "h['vm-4093']['status'] == 'failed' and 'Permission check failed (/vms/4093, VM.Snapshot)' in h['vm-4093']['reason']"
expect_versions "$UPD2" usas-test-extra=1.0
mock_set '{"vmid": 4093, "set": {"forbid_snapshot": false}}'
play "${SNAP[@]}" --limit upd-usas,vm-4093
report "h['upd-usas']['status'] == 'updated' and h['vm-4093']['status'] == 'updated'" \
  "h['upd-usas']['snapshot']['status'] == 'taken' and h['upd-usas']['snapshot']['node'] == 'pve-test-2'" \
  "h['vm-4093']['snapshot']['deleted'] == ['usas_upd_20200101_000000']" \
  "h['vm-4093']['snapshot']['warning'] == 'storage local-lvm is over 85 % used' and not h['upd-usas']['snapshot']['warning']"
mock_state "snaps[4093][1:] == ['before-upgrade', 'usas_upd_20200102_000000'] and snaps[4093][0].startswith('usas_upd_2')" \
  "desc[4093][snaps[4093][0]].startswith('USAS updates run ')" \
  "len(snaps[4091]) == 1 and any(r['method'] == 'POST' and r['path'] == '/nodes/pve-test-2/qemu/4091/snapshot'
   and r['form']['vmstate'] == '0' for r in reqs)"
expect_versions "$UPD2" usas-test-extra=1.1
# Without updates_snapshot_required a VM whose disks can't have one is updated; an unhealthy VM keeps its snapshot
# (updates_snapshot_keep: 0 would delete it after a healthy update).
fresh "$UPD1" 2341 sysadmin
fresh "$UPD2" 2342 ops
pin_cluster
docker exec "$UPD2" systemctl enable --now usas-test-fragile.service >/dev/null 2>&1
mock_set '{"vmid": 4091, "set": {"feature_snapshot": false}}'
play_fails 'Unhealthy after the update' "${SNAP[@]}" --limit upd-usas,vm-4093 -e updates_snapshot_required=false \
  -e updates_snapshot_keep=0 -e updates_reboot=if_required -e updates_serial=2
report "h['upd-usas']['status'] == 'updated' and h['upd-usas']['snapshot']['status'] == 'none'" \
  "\"don't support snapshots\" in h['upd-usas']['snapshot']['reason']" \
  "h['vm-4093']['status'] == 'failed' and h['vm-4093']['snapshot']['status'] == 'taken'"
mock_state "len([n for n in snaps[4093] if desc[4093][n].startswith('USAS updates run ')]) == 2" \
  "snaps[4091] == [snaps[4091][0]] and snaps[4091][0].startswith('usas_upd_2')"
if grep -l '00000000-0000-4000-8000-000000000000' "$TEST_TMP"/play.*; then
  echo "ERROR: the Proxmox API token is in the output above" >&2
  exit 1
fi
kill "$mock_pid"
mock_pid=""

step "Task coverage of the updates role"
python3 tests/coverage.py "$TASK_COVERAGE_FILE" --min "$MIN_COVERAGE" --roles updates

step "All updates tests passed on $OS"
