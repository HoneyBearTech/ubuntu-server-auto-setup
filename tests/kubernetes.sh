#!/usr/bin/env bash
# k3s on one OS: builds cluster ci (tests/inventory/k3s.yml) from four containers that stand in for freshly
# installed servers of that release (tests/images/ubuntu-target, stage "target"): three servers and an agent on the
# test's own Docker network, each with a volume for k3s' data. The baseline prepares them (firewall and the
# Kubernetes node profile), then playbooks/kubernetes.yml builds the cluster. Checks: the refusals before anything
# changes (an unprepared member, a missing token, an unknown release, a binary that doesn't match the catalog), a
# server that can't reach etcd's peer port stopped with the hosts named, a kube-vip interface one server lacks, then
# the cluster itself (tests/verify-k3s.yml), kube-vip's addresses from the control node, a hard failover of the VIP
# to k3s-s3, whose card is ens18 where the others' are eth0 (as Ubuntu's and Rocky Linux's cloud images differ), the Argo CD bootstrap (its settings checked, then
# Argo CD and a root Application that syncs a test repository from a Git server container, tests/verify-argocd.yml),
# the tokens and Argo CD's initial password in no log, a second run that changes nothing (and leaves the root
# Application alone when its settings change), a cluster with the single kube-vip DaemonSet of before moved to one
# per server with the VIP answering throughout, an updates run (playbooks/updates.yml) that reboots the server holding
# the VIP and hands it over first; then the upgrade to the catalog's next release (--tags upgrade): the
# refusals (without the tag, a minor skipped, another run's lock), every node cordoned, drained, upgraded and
# uncordoned once, servers first, the API answering on the VIP throughout, a second upgrade that changes nothing,
# and going back refused. OS picks the release (ubuntu-26.04 by default; CI also runs debian-13
# and rocky-9). Needs Docker (privileged containers, for systemd and k3s), Python with requirements-ci.txt and the
# Ansible collections, and on Linux the br_netfilter and overlay modules loaded on the host. Run it with
# make test-kubernetes OS=<id>. See docs/testing.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OS="${OS:-ubuntu-26.04}"
case "$OS" in
  ubuntu-22.04|ubuntu-24.04|ubuntu-26.04|debian-12|debian-13|centos-stream-9|rocky-9|alma-9) ;;
  *) echo "OS must be a release whose Kubernetes nodes the baseline supports (see the case above)" >&2; exit 2;;
esac
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/usas-kubernetes.XXXXXX")"
export TEST_TMP
export ANSIBLE_CONFIG="$ROOT/ansible.cfg"
export ANSIBLE_CALLBACK_PLUGINS="$ROOT/tests/callback_plugins"
export ANSIBLE_CALLBACKS_ENABLED=task_coverage
export TASK_COVERAGE_FILE="$TEST_TMP/coverage.txt"
export ANSIBLE_FORCE_COLOR=0
INV=(-i tests/inventory/k3s.yml -e "@$TEST_TMP/tokens.yml")
IMAGE="usas-test-srv:$OS"
NET=usas-k3s-test
NODES=(k3s-s1 k3s-s2 k3s-s3 k3s-a1)
# Each member's address on the test network and its local SSH port (functions, not associative arrays, so macOS's
# bash 3.2 runs this too).
addr() { case "$1" in k3s-s1) echo 10.217.30.11;; k3s-s2) echo 10.217.30.12;; k3s-s3) echo 10.217.30.13;; k3s-a1) echo 10.217.30.21;; esac; }
port() { case "$1" in k3s-s1) echo 2241;; k3s-s2) echo 2242;; k3s-s3) echo 2243;; k3s-a1) echo 2244;; esac; }
# k3s-s3's card is ens18, the others' eth0, so each server's kube-vip needs its own interface.
ifname() { case "$1" in k3s-s3) echo ens18;; *) echo eth0;; esac; }
VIP=10.217.30.100
GIT=10.217.30.50
MIN_COVERAGE="${MIN_COVERAGE:-80}"

probe_pid=""
LAST_LOG=""

# K8S_TEST_KEEP=1 leaves the containers, their volumes and the network after a failure, to look inside
# (docker exec k3s-s1 k3s kubectl get pods -A). A run removes only what it created itself (owned=true): it won't
# start while another run's network or containers exist, so one can't tear down another.
owned=false
cleanup() {
  status=$?
  if [ -n "$probe_pid" ]; then kill "$probe_pid" 2>/dev/null || true; fi
  if [ "$status" -ne 0 ]; then
    for node in "${NODES[@]}"; do
      if docker exec "$node" true 2>/dev/null; then
        echo "--- $node: k3s' journal (last 60 lines)" >&2
        docker exec "$node" sh -c 'journalctl -u k3s -u k3s-agent --no-pager -n 60' >&2 || true
      fi
    done
  fi
  if $owned && { [ "$status" -eq 0 ] || [ "${K8S_TEST_KEEP:-}" != 1 ]; }; then
    for node in "${NODES[@]}"; do
      docker rm -f "$node" >/dev/null 2>&1 || true
      docker volume rm "$node-rancher" >/dev/null 2>&1 || true
    done
    docker rm -f usas-k3s-git >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
  fi
  if [ "$status" -ne 0 ]; then echo "FAILED (logs kept in $TEST_TMP)" >&2; else rm -rf "$TEST_TMP"; fi
}
trap cleanup EXIT

step() { printf '\n=== %s: %s\n' "$OS" "$*"; }

# Runs a playbook and fails unless it succeeds; with --idempotent also fails if anything changed. Every log is kept
# in $TEST_TMP, where the tokens must never appear; LAST_LOG is the last one.
play() {
  local idempotent=false
  if [ "$1" = "--idempotent" ]; then idempotent=true; shift; fi
  local log; log="$(mktemp "$TEST_TMP/play.XXXXXX")"
  LAST_LOG="$log"
  if ! ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: ansible-playbook $* failed" >&2; return 1
  fi
  sed -n '/PLAY RECAP/,$p' "$log"
  if $idempotent && grep -Eq 'changed=[1-9]' <(sed -n '/PLAY RECAP/,$p' "$log"); then
    grep -B3 -E '^changed:' "$log" || true
    echo "ERROR: second run of $* changed something" >&2; return 1
  fi
}

# Runs a playbook and fails unless it fails and its output says $1.
refused() {
  local msg="$1"; shift
  local log; log="$(mktemp "$TEST_TMP/refused.XXXXXX")"
  if ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: ansible-playbook $* wasn't refused" >&2; return 1
  fi
  grep -qF -- "$msg" "$log" || { cat "$log"; echo "ERROR: the refusal doesn't say: $msg" >&2; return 1; }
  echo "refused as expected: $msg"
}

# UFW limits SSH to 6 new connections in 30 s per source, and every connection comes from the network's gateway.
cool_down() { sleep 31; }

# From outside the cluster (Linux only: elsewhere the Docker network isn't reachable), every half second, between
# start_probe and stop_probe: the API on the VIP, which must keep answering while kube-vip moves or servers restart.
probe_vip() {
  while :; do
    if curl -fsSk --max-time 1 "https://$VIP:6443/ping" >/dev/null 2>&1; then echo "$(date +%s.%N) up"
    else echo "$(date +%s.%N) down"; fi
    sleep 0.5
  done
}
start_probe() {
  rm -f "$TEST_TMP/vip.log"
  if [ "$(uname -s)" = Linux ]; then probe_vip >"$TEST_TMP/vip.log" & probe_pid=$!; fi
}
stop_probe() { if [ -n "$probe_pid" ]; then kill "$probe_pid"; probe_pid=""; fi; }

# Fails if the API on the VIP didn't answer for more than $1 seconds at a time while the probe ran, during $2.
vip_gaps() {
  [ -s "$TEST_TMP/vip.log" ] || return 0
  python3 - "$TEST_TMP/vip.log" "$1" "$2" <<'EOF'
import sys
probes = [(float(t), state) for t, state in (line.split() for line in open(sys.argv[1]) if line.strip())]
longest, down_since = 0.0, None
for moment, state in probes:
    if state == "down" and down_since is None:
        down_since = moment
    elif state == "up" and down_since is not None:
        longest, down_since = max(longest, moment - down_since), None
if down_since is not None:
    longest = max(longest, probes[-1][0] - down_since)
assert probes and longest <= float(sys.argv[2]), f"the API on the VIP didn't answer for {longest:.1f} s during {sys.argv[3]}"
print(f"the VIP answered throughout {sys.argv[3]} ({len(probes)} probes, longest gap {longest:.1f} s)")
EOF
}

# The node that holds kube-vip's lease, so the VIP.
vip_holder() {
  docker exec k3s-s1 k3s kubectl --namespace=kube-system get lease plndr-cp-lock -o 'jsonpath={.spec.holderIdentity}'
}

# Fails if any member has $1.
none_has() {
  for node in "${NODES[@]}"; do
    if docker exec "$node" test -e "$1"; then echo "ERROR: $node has $1" >&2; return 1; fi
  done
}

step "Setting up keys, tokens, the network and the containers"
# The four containers run as the Docker host's root, so their systemd, kubelets and containerd count against one
# per-user inotify limit, fs.inotify.max_user_instances. When it is used up, a kubelet can't start ("inotify_init:
# too many open files"), k3s restarts in a loop and its node never registers. The setting is the Docker host's
# own kernel's, so only CI (CI=true) raises it; elsewhere on Linux the test says so and goes on. Off Linux the
# containers run in Docker's own VM, whose setting this script can't see, so there it does nothing.
INOTIFY_INSTANCES=8192
if [ "$(uname -s)" = Linux ]; then
  inotify_now="$(cat /proc/sys/fs/inotify/max_user_instances)"
  if [ "$inotify_now" -ge "$INOTIFY_INSTANCES" ]; then
    echo "fs.inotify.max_user_instances is $inotify_now"
  elif [ "${CI:-}" = "true" ]; then
    sudo -n sysctl -q -w "fs.inotify.max_user_instances=$INOTIFY_INSTANCES" \
      || { echo "ERROR: couldn't raise fs.inotify.max_user_instances from $inotify_now to $INOTIFY_INSTANCES" >&2; exit 1; }
    echo "fs.inotify.max_user_instances raised from $inotify_now to $INOTIFY_INSTANCES"
  else
    echo "WARNING: fs.inotify.max_user_instances is $inotify_now, below $INOTIFY_INSTANCES. If a node never becomes" \
      "Ready and k3s' journal says \"too many open files\", raise it:" \
      "sudo sysctl -w fs.inotify.max_user_instances=$INOTIFY_INSTANCES" >&2
  fi
fi
ssh-keygen -q -t ed25519 -N '' -C test@ubuntu-server-auto-setup -f "$TEST_TMP/id_ed25519"
printf 'vault_k8s_token_ci: %s\nvault_k8s_agent_token_ci: %s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" \
  >"$TEST_TMP/tokens.yml"
docker build -q -t "$IMAGE" --build-arg OS="$OS" --target target tests/images/ubuntu-target >/dev/null
for node in "${NODES[@]}"; do
  if docker container inspect "$node" >/dev/null 2>&1 || docker volume inspect "$node-rancher" >/dev/null 2>&1; then
    echo "ERROR: $node (or its volume) exists: another run is using it, or one kept it (K8S_TEST_KEEP). Remove it with" \
      "docker rm -f k3s-s1 k3s-s2 k3s-s3 k3s-a1; docker volume rm k3s-s1-rancher k3s-s2-rancher k3s-s3-rancher" \
      "k3s-a1-rancher; docker network rm $NET" >&2
    exit 1
  fi
done
docker network create --subnet 10.217.30.0/24 --gateway 10.217.30.1 "$NET" >/dev/null \
  || { echo "ERROR: couldn't create the network $NET: another run may be using it (docker network rm $NET)" >&2; exit 1; }
owned=true
: >"$TEST_TMP/known_hosts"
for node in "${NODES[@]}"; do
  # k3s' overlayfs snapshotter needs its data on a volume, not on the container's own overlay (K0). Each node gets a
  # cgroup namespace of its own, as k3d does: with the host's (--cgroupns=host, as the baseline tests use), the four
  # kubelets share one cgroup tree and kill each other's pods ("Pod sandbox changed") whenever another node starts.
  docker run -d --name "$node" --hostname "$node" --privileged \
    --network "name=$NET,ip=$(addr "$node"),driver-opt=com.docker.network.endpoint.ifname=$(ifname "$node")" \
    --cgroupns=private -v "$node-rancher:/var/lib/rancher" \
    --tmpfs /run --tmpfs /run/lock -p "127.0.0.1:$(port "$node"):22" "$IMAGE" >/dev/null
  docker exec -i "$node" sh -c 'cat > /home/sysadmin/.ssh/authorized_keys && chown sysadmin: /home/sysadmin/.ssh/authorized_keys && chmod 600 /home/sysadmin/.ssh/authorized_keys' \
    <"$TEST_TMP/id_ed25519.pub"
  docker exec "$node" sh -c "echo '127.0.1.1 $node' >> /etc/hosts"
done
for node in "${NODES[@]}"; do
  for _ in $(seq 1 60); do
    ssh-keyscan -T 2 -p "$(port "$node")" 127.0.0.1 >"$TEST_TMP/scan" 2>/dev/null && [ -s "$TEST_TMP/scan" ] && break
    sleep 1
  done
  [ -s "$TEST_TMP/scan" ] || { echo "ERROR: no SSH on port $(port "$node")" >&2; exit 1; }
  sed "s/^127.0.0.1/[127.0.0.1]:$(port "$node")/" "$TEST_TMP/scan" >>"$TEST_TMP/known_hosts"
  for _ in $(seq 1 120); do docker exec "$node" test ! -e /run/nologin && break; sleep 1; done
done
for node in "${NODES[@]}"; do
  docker exec "$node" ip -o link show "$(ifname "$node")" >/dev/null \
    || { echo "ERROR: $node has no interface $(ifname "$node"): Docker didn't name it (needs Docker 28 or later)" >&2; exit 1; }
done
family="$(docker exec k3s-s1 sh -c '. /etc/os-release && case "$ID" in ubuntu|debian) echo debian;; *) echo rhel;; esac')"

step "a member the baseline hasn't prepared is refused before anything changes"
refused "isn't prepared for Kubernetes yet: run the baseline" playbooks/kubernetes.yml
none_has /usr/local/bin/k3s

step "the baseline prepares the members: the firewall and the Kubernetes node profile"
play playbooks/baseline.yml --tags firewall,kubernetes
cool_down

step "a missing token, an unknown release and a member outside the run are refused before anything changes"
refused "needs two different join tokens in the Vault, vault_k8s_token_ci" playbooks/kubernetes.yml \
  -e vault_k8s_agent_token_ci=short
refused "k8s_version must be one of the catalog's releases (v1.36.5+k3s1, v1.37.1+k3s1)" playbooks/kubernetes.yml \
  -e k8s_version=v1.30.0+k3s1
refused "Run it for every server of the cluster: k3s-s3 isn't in this run." playbooks/kubernetes.yml --limit '!k3s-s3'
refused "k3s-s3 has no interface eth0 for kube-vip to put 10.217.30.100 on" playbooks/kubernetes.yml \
  -e k8s_api_vip_interface=eth0
none_has /usr/local/bin/k3s
cool_down

step "a binary that doesn't match the catalog is never installed"
python3 - "$TEST_TMP/bad-catalog.json" <<'EOF'
import json, sys, yaml
catalog = yaml.safe_load(open("roles/k3s/vars/catalog.yml"))
for release in catalog["k3s_catalog"]:
    for binary in release["binaries"].values():
        binary["sha256"] = "0" * 64
json.dump({"k3s_catalog": catalog["k3s_catalog"]}, open(sys.argv[1], "w"))
EOF
refused "did not match 0000000000000000" playbooks/kubernetes.yml -e "@$TEST_TMP/bad-catalog.json"
none_has /usr/local/bin/k3s
none_has /etc/rancher/k3s/config.yaml
cool_down

step "a check run on the new cluster passes and changes nothing"
# What the guide and the web UI do first: no member has k3s' unit yet, so check mode can't ask systemd to start it.
play playbooks/kubernetes.yml --check --diff
none_has /usr/local/bin/k3s
none_has /etc/systemd/system/k3s.service
none_has /etc/systemd/system/k3s-agent.service
none_has /etc/rancher/k3s/config.yaml
cool_down

step "a server that the others can't reach on etcd's peer port is stopped before it joins, with the hosts named"
if [ "$family" = rhel ]; then
  docker exec k3s-s2 sh -c "firewall-cmd -q --remove-rich-rule='rule family=\"ipv4\" source address=\"$(addr k3s-s1)\" port port=\"2379-2380\" protocol=\"tcp\" accept'"
else
  docker exec k3s-s2 ufw delete allow from "$(addr k3s-s1)" to any port 2379:2380 proto tcp >/dev/null
fi
refused "nothing answered from k3s-s1 to k3s-s2 ($(addr k3s-s2)) port 2380" playbooks/kubernetes.yml
docker exec k3s-s2 test ! -e /var/lib/rancher/k3s/server/db/etcd \
  || { echo "ERROR: k3s-s2 joined although the check failed" >&2; exit 1; }
cool_down
# The baseline puts the rule back (on firewalld, reloading the permanent rules is enough).
if [ "$family" = rhel ]; then docker exec k3s-s2 firewall-cmd -q --reload
else play playbooks/baseline.yml --tags kubernetes --limit k3s-s2; cool_down; fi

step "the cluster: three servers one at a time through the VIP, then the agent"
play playbooks/kubernetes.yml
cool_down
play tests/verify-k3s.yml -e "tokens_file=$TEST_TMP/tokens.yml"

step "kube-vip answers for the API and a LoadBalancer address from outside the cluster"
if [ "$(uname -s)" = Linux ]; then
  curl -fsSk --max-time 10 "https://$VIP:6443/ping" | grep -qx pong \
    || { echo "ERROR: the API doesn't answer on the VIP $VIP" >&2; exit 1; }
  lb="$(docker exec k3s-s1 k3s kubectl -n kube-system get service traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
  # The address can be handed out before Traefik serves on it.
  for _ in $(seq 1 60); do
    code="$(curl -s -o /dev/null --max-time 5 -w '%{http_code}' "http://$lb/" || true)"
    [ "$code" = 404 ] && break
    sleep 5
  done
  if [ "$code" != 404 ]; then
    echo "--- pods, Traefik's service and endpoints, and who holds the LoadBalancer address" >&2
    docker exec k3s-s1 k3s kubectl get pods -A -o wide >&2 || true
    docker exec k3s-s1 k3s kubectl -n kube-system get service,endpointslices -o wide >&2 || true
    echo "--- the last lines of each restarted pod's previous run" >&2
    docker exec k3s-s1 sh -c 'k3s kubectl -n kube-system get pods --no-headers \
      | awk "\$4 > 0 {print \$1}" | while read -r pod; do echo "# $pod"; \
      k3s kubectl -n kube-system logs --previous --tail=8 "$pod" 2>&1; done' >&2 || true
    for node in "${NODES[@]}"; do
      docker exec "$node" sh -c "ip -4 -o addr show | grep -F '$lb' && echo 'on $node'" >&2 || true
      if [ "$family" = rhel ]; then
        echo "--- $node: firewalld" >&2
        docker exec "$node" sh -c 'firewall-cmd --get-active-zones; firewall-cmd --list-all; nft list chain inet firewalld filter_FORWARD' >&2 || true
      fi
    done
    echo "ERROR: Traefik doesn't answer on its LoadBalancer address $lb (HTTP $code)" >&2; exit 1
  fi
else
  echo "skipped: the Docker network isn't reachable from this host"
fi

step "the VIP moves to k3s-s3 and onto its own interface, ens18"
# The other servers' kube-vip DaemonSets kept off their nodes for a while: their pods stop and let the lease go.
for node in k3s-s1 k3s-s2; do
  docker exec k3s-s1 k3s kubectl --namespace=kube-system patch daemonset "kube-vip-ds-$node" --type=json \
    -p '[{"op": "add", "path": "/spec/template/spec/nodeSelector", "value": {"usas-test/nowhere": "true"}}]' >/dev/null
done
for _ in $(seq 1 60); do
  [ "$(vip_holder)" = k3s-s3 ] && docker exec k3s-s3 ip -o -4 addr show dev ens18 | grep -qF " $VIP/32" && break
  sleep 2
done
[ "$(vip_holder)" = k3s-s3 ] && docker exec k3s-s3 ip -o -4 addr show dev ens18 | grep -qF " $VIP/32" \
  || { echo "ERROR: k3s-s3 didn't take the VIP on ens18 (holder: $(vip_holder))" >&2; exit 1; }
if [ "$(uname -s)" = Linux ]; then
  for _ in $(seq 1 15); do curl -fsSk --max-time 2 "https://$VIP:6443/ping" | grep -qx pong && break; sleep 2; done
  curl -fsSk --max-time 5 "https://$VIP:6443/ping" | grep -qx pong \
    || { echo "ERROR: the API doesn't answer on the VIP $VIP held by k3s-s3" >&2; exit 1; }
fi
for node in k3s-s1 k3s-s2; do
  docker exec k3s-s1 k3s kubectl --namespace=kube-system patch daemonset "kube-vip-ds-$node" --type=json \
    -p '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]' >/dev/null
  docker exec k3s-s1 k3s kubectl --namespace=kube-system rollout status daemonset "kube-vip-ds-$node" --timeout=120s >/dev/null
done
echo "k3s-s3 holds the VIP on ens18"

step "the Argo CD bootstrap's settings are checked before anything changes"
# The root Application's repository: a ConfigMap in apps/, served over HTTPS by a Git server container with a
# certificate of its own, which the bootstrap tells Argo CD to trust for that server.
mkdir -p "$TEST_TMP/repo/apps" "$TEST_TMP/git" "$TEST_TMP/tls"
cat >"$TEST_TMP/repo/apps/handoff.yaml" <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: usas-handoff
  namespace: argocd
data:
  from: git
EOF
git -C "$TEST_TMP/repo" init -q -b main
git -C "$TEST_TMP/repo" add apps
git -C "$TEST_TMP/repo" -c user.name=test -c user.email=test@invalid -c commit.gpgsign=false commit -q -m "The test's apps"
git clone -q --bare "$TEST_TMP/repo" "$TEST_TMP/git/handoff.git"
git -C "$TEST_TMP/git/handoff.git" update-server-info
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=usas-k3s-git -addext "subjectAltName=IP:$GIT" \
  -keyout "$TEST_TMP/tls/server.key" -out "$TEST_TMP/tls/server.crt" 2>/dev/null
docker build -q -t usas-test-git --target git-server tests/images/ubuntu-target >/dev/null
docker rm -f usas-k3s-git >/dev/null 2>&1 || true
docker run -d --name usas-k3s-git --network "$NET" --ip "$GIT" -v "$TEST_TMP/git:/srv/git:ro" \
  -v "$TEST_TMP/tls:/srv/tls:ro" usas-test-git >/dev/null
python3 - "$TEST_TMP/argocd.json" "$TEST_TMP/tls/server.crt" "https://$GIT/handoff.git" <<'EOF'
import json, sys
json.dump({"k8s_bootstrap_argocd": True, "k8s_bootstrap_repo_url": sys.argv[3], "k8s_bootstrap_path": "apps",
           "k8s_bootstrap_revision": "main", "k8s_bootstrap_repo_ca": open(sys.argv[2]).read()}, open(sys.argv[1], "w"))
EOF
ARGOCD=(-e "@$TEST_TMP/argocd.json")
refused "k8s_bootstrap_repo_url must be the Git repository's" playbooks/kubernetes.yml "${ARGOCD[@]}" \
  -e "k8s_bootstrap_repo_url=http://$GIT/handoff.git"
refused "k8s_bootstrap_repo_url must be the Git repository's" playbooks/kubernetes.yml "${ARGOCD[@]}" \
  -e "k8s_bootstrap_repo_url=https://user:secret@$GIT/handoff.git"
refused "k8s_bootstrap_path a folder in it" playbooks/kubernetes.yml "${ARGOCD[@]}" -e k8s_bootstrap_path=../etc
none_has /usr/local/bin/helm
cool_down

step "the Argo CD bootstrap: Helm, Argo CD and the root Application, which syncs the test repository"
play playbooks/kubernetes.yml "${ARGOCD[@]}"
cool_down
play tests/verify-argocd.yml -e "@$TEST_TMP/argocd.json"
for node in k3s-s2 k3s-s3 k3s-a1; do
  docker exec "$node" test ! -e /usr/local/bin/helm || { echo "ERROR: $node has Helm, not only the first server" >&2; exit 1; }
done
cool_down

step "Argo CD's initial password appears in no log"
password="$(docker exec k3s-s1 k3s kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
[ -n "$password" ] || { echo "ERROR: argocd-initial-admin-secret has no password" >&2; exit 1; }
if grep -rlF -- "$password" "$TEST_TMP"/play.* "$TEST_TMP"/refused.* 2>/dev/null; then
  echo "ERROR: Argo CD's initial password is in the logs above" >&2; exit 1
fi

step "the tokens appear in no log, and the read-only kubeconfig is 0600"
while read -r _ token; do
  if grep -rlF -- "$token" "$TEST_TMP"/play.* "$TEST_TMP"/refused.* 2>/dev/null; then
    echo "ERROR: a join token is in the logs above" >&2; exit 1
  fi
done <"$TEST_TMP/tokens.yml"
[ "$(stat -c %a "$TEST_TMP/kube/ci-readonly.yaml" 2>/dev/null || stat -f %Lp "$TEST_TMP/kube/ci-readonly.yaml")" = 600 ] \
  || { echo "ERROR: the read-only kubeconfig isn't 0600" >&2; exit 1; }
cool_down

step "a second run changes nothing, and leaves Argo CD and the root Application as they are"
play --idempotent playbooks/kubernetes.yml "${ARGOCD[@]}"
cool_down
play --idempotent playbooks/kubernetes.yml "${ARGOCD[@]}" -e k8s_bootstrap_revision=other
grep -qF "not k8s_bootstrap_repo_url, _path and _revision: left as it is" "$LAST_LOG" \
  || { echo "ERROR: the run didn't say it left the root Application alone" >&2; exit 1; }
docker exec k3s-s1 k3s kubectl -n argocd get secret argocd-initial-admin-secret -o name >/dev/null \
  || { echo "ERROR: argocd-initial-admin-secret is gone" >&2; exit 1; }
cool_down

step "a cluster with the single kube-vip DaemonSet of before moves to one per server, the VIP answering"
# The layout of before, as the role wrote it: kube-vip-ds, with one interface name for every server (eth0). k3s-s3
# has no eth0, which the role refused then, so here the DaemonSet runs on k3s-s1 and k3s-s2.
manifest=/var/lib/rancher/k3s/server/manifests/usas-kube-vip.yaml
docker exec k3s-s1 cat "$manifest" >"$TEST_TMP/kube-vip-now.yaml"
python3 - "$TEST_TMP/kube-vip-now.yaml" "$TEST_TMP/kube-vip-before.yaml" <<'EOF'
import sys, yaml
ds = next(d for d in yaml.safe_load_all(open(sys.argv[1])) if d["metadata"]["name"] == "kube-vip-ds-k3s-s1")
ds["metadata"]["name"] = "kube-vip-ds"
for labels in (ds["metadata"]["labels"], ds["spec"]["selector"]["matchLabels"], ds["spec"]["template"]["metadata"]["labels"]):
    del labels["app.kubernetes.io/instance"]
    labels["app.kubernetes.io/name"] = "kube-vip-ds"
ds["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"] = {
    "nodeSelectorTerms": [{"matchFields": [{"key": "metadata.name", "operator": "In", "values": [node]}]}
                          for node in ("k3s-s1", "k3s-s2")]}
yaml.safe_dump(ds, open(sys.argv[2], "w"))
EOF
for node in k3s-s1 k3s-s2 k3s-s3; do docker exec -i "$node" sh -c "cat > $manifest" <"$TEST_TMP/kube-vip-before.yaml"; done
for _ in $(seq 1 60); do
  [ "$(docker exec k3s-s1 k3s kubectl --namespace=kube-system get daemonset kube-vip-ds -o 'jsonpath={.status.numberReady}' 2>/dev/null)" = 2 ] && break
  sleep 2
done
# k3s may keep what a manifest no longer has: the per-server DaemonSets go by hand if so.
docker exec k3s-s1 k3s kubectl --namespace=kube-system delete daemonsets --selector=app.kubernetes.io/name=kube-vip \
  --ignore-not-found --wait >/dev/null
for _ in $(seq 1 60); do
  [ -z "$(docker exec k3s-s1 k3s kubectl --namespace=kube-system get pods --selector=app.kubernetes.io/name=kube-vip -o name)" ] \
    && case "$(vip_holder)" in k3s-s1|k3s-s2) true;; *) false;; esac && break
  sleep 2
done
[ "$(docker exec k3s-s1 k3s kubectl --namespace=kube-system get daemonsets -o name | grep -c kube-vip)" = 1 ] \
  && case "$(vip_holder)" in k3s-s1|k3s-s2) true;; *) false;; esac \
  || { echo "ERROR: couldn't put the earlier kube-vip layout in place (holder: $(vip_holder))" >&2; exit 1; }
cool_down
start_probe
play playbooks/kubernetes.yml "${ARGOCD[@]}"
stop_probe
vip_gaps 30 "the move to one kube-vip DaemonSet per server"
daemonsets="$(docker exec k3s-s1 k3s kubectl --namespace=kube-system get daemonsets -o name | grep kube-vip | sort | tr '\n' ' ')"
[ "$daemonsets" = "daemonset.apps/kube-vip-ds-k3s-s1 daemonset.apps/kube-vip-ds-k3s-s2 daemonset.apps/kube-vip-ds-k3s-s3 " ] \
  || { echo "ERROR: kube-vip's DaemonSets after the move: $daemonsets" >&2; exit 1; }
case "$(vip_holder)" in k3s-s1|k3s-s2|k3s-s3) ;; *) echo "ERROR: no server holds the VIP after the move" >&2; exit 1;; esac
cool_down

step "an updates run that reboots the server holding the VIP hands the VIP to another server first"
# A kernel in /boot newer than the running one asks for the reboot, which restarts the container (Docker starts it
# again, as a server comes back); every package update is left out, so the reboot is all the run does.
holder="$(vip_holder)"
docker update --restart=always "$holder" >/dev/null
docker exec "$holder" touch /boot/vmlinuz-999.0.0-usas-test
# The container shares the Docker host's kernel and boot_id: its PID 1's start time says it restarted.
boot="$(docker exec "$holder" ps -o lstart= -p 1)"
start_probe
play playbooks/updates.yml --limit "$holder" -e updates_reboot=if_required -e updates_os_reboot_check=kernel \
  -e '{"updates_exclude_packages": ["*"]}' -e "updates_report_dir=$TEST_TMP/reports" \
  -e 'updates_boot_time_command="ps -o lstart= -p 1"' -e updates_reboot_timeout=300
stop_probe
docker exec "$holder" rm /boot/vmlinuz-999.0.0-usas-test
docker update --restart=no "$holder" >/dev/null
[ "$(docker exec "$holder" ps -o lstart= -p 1)" != "$boot" ] || { echo "ERROR: $holder didn't reboot" >&2; exit 1; }
python3 - "$LAST_LOG" "$holder" <<'EOF2'
import re, sys
holder, task, done = sys.argv[2], "", []
for line in open(sys.argv[1]):
    found = re.match(r"^TASK \[(?:[a-z0-9_]+ : )?(.*)\] \*", line)
    if found:
        task = found[1]
    found = re.match(r"^(changed|ok): \[([a-z0-9-]+)", line)
    if found and found[2] == holder:
        done.append((found[1], task))
order = [task for state, task in done if state == "changed"]
for name in ("Drain the node before the reboot", "Stop kube-vip here until another server holds the API's address",
             "Reboot to finish the updates", "Uncordon the node"):
    assert name in order, (name, order)
assert order.index("Stop kube-vip here until another server holds the API's address") < order.index("Reboot to finish the updates"), order
assert ("ok", "Wait until the API answers on its address") in done, done
print(f"{holder} was drained, handed the VIP over, rebooted and uncordoned, in that order")
EOF2
case "$(vip_holder)" in "$holder") echo "ERROR: $holder still holds the VIP after its update" >&2; exit 1;; k3s-s*) ;;
  *) echo "ERROR: no server holds the VIP after the update" >&2; exit 1;; esac
vip_gaps 30 "the reboot of the server that held the VIP"
cool_down

step "another release is refused without --tags upgrade, and --tags upgrade only on its own"
refused "runs k3s v1.36.5+k3s1, not v1.37.1+k3s1. Moving a cluster to another release is an upgrade" \
  playbooks/kubernetes.yml -e k8s_version=v1.37.1+k3s1
refused "--tags upgrade runs on its own" playbooks/kubernetes.yml --tags upgrade,install -e k8s_version=v1.37.1+k3s1
cool_down

step "an upgrade skipping a minor release is refused, and so is one while another run holds the cluster's lock"
python3 - "$TEST_TMP/later-catalog.json" <<'EOF2'
import json, sys, yaml
catalog = yaml.safe_load(open("roles/k3s/vars/catalog.yml"))["k3s_catalog"]
later = json.loads(json.dumps(catalog[-1]).replace(catalog[-1]["version"], "v1.38.0+k3s1"))
json.dump({"k3s_catalog": catalog + [later]}, open(sys.argv[1], "w"))
EOF2
refused "and v1.38.0+k3s1 is more than one minor release ahead" playbooks/kubernetes.yml --tags upgrade \
  -e "@$TEST_TMP/later-catalog.json" -e k8s_version=v1.38.0+k3s1
docker exec k3s-s1 k3s kubectl --namespace=kube-system create configmap usas-upgrade-lock \
  --from-literal=run=another --from-literal=release=v1.37.1+k3s1 --from-literal=started=2026-11-01T02:00:00Z >/dev/null
refused "Another upgrade of cluster ci holds its lock (kube-system/usas-upgrade-lock: to v1.37.1+k3s1, started 2026-11-01T02:00:00Z)" \
  playbooks/kubernetes.yml --tags upgrade -e k8s_version=v1.37.1+k3s1
# The other run's lock is still there, and no node was touched.
docker exec k3s-s1 k3s kubectl --namespace=kube-system get configmap usas-upgrade-lock -o 'jsonpath={.data.run}' \
  | grep -qx another || { echo "ERROR: the refused run removed another run's lock" >&2; exit 1; }
docker exec k3s-s1 k3s kubectl --namespace=kube-system delete configmap usas-upgrade-lock >/dev/null
docker exec k3s-s1 k3s kubectl get nodes -o 'jsonpath={.items[*].spec.unschedulable}' | grep -q true \
  && { echo "ERROR: the refused run cordoned a node" >&2; exit 1; }
cool_down

step "the upgrade to v1.37.1+k3s1: servers then the agent, one at a time, each cordoned and uncordoned once"
start_probe
play playbooks/kubernetes.yml --tags upgrade -e k8s_version=v1.37.1+k3s1
stop_probe
python3 - "$LAST_LOG" <<'EOF2'
import collections, re, sys
task, done = "", collections.defaultdict(list)
for line in open(sys.argv[1]):
    found = re.match(r"^TASK \[k3s : (.*)\] \*", line)
    if found:
        task = found[1]
    found = re.match(r"^(changed|ok): \[([a-z0-9-]+)", line)
    if found and found[1] == "changed":
        done[task].append(found[2])
nodes = ["k3s-s1", "k3s-s2", "k3s-s3", "k3s-a1"]
for name in ("Cordon the node", "Drain the node", "Uncordon the node", "Install the new k3s binary, checked against the catalog"):
    assert done[name] == nodes, (name, done[name])
assert 1 <= len(done["Stop kube-vip here until another server holds the API's address"]) <= 3, dict(done)
assert all(h.startswith("k3s-s") for h in done["Stop kube-vip here until another server holds the API's address"])
print("each node was cordoned, drained, upgraded and uncordoned once, servers first; kube-vip handed over",
      len(done["Stop kube-vip here until another server holds the API's address"]), "time(s)")
EOF2
vip_gaps 30 "the upgrade"
docker exec k3s-s1 k3s kubectl --namespace=kube-system get configmap usas-upgrade-lock >/dev/null 2>&1 \
  && { echo "ERROR: the upgrade left its lock behind" >&2; exit 1; }
cool_down
play tests/verify-k3s.yml -e "tokens_file=$TEST_TMP/tokens.yml" -e verify_version=v1.37.1+k3s1
cool_down

step "a second upgrade, and a run without tags on the new release, change nothing; going back is refused"
play --idempotent playbooks/kubernetes.yml --tags upgrade -e k8s_version=v1.37.1+k3s1
cool_down
play --idempotent playbooks/kubernetes.yml -e k8s_version=v1.37.1+k3s1
cool_down
refused "runs k3s v1.37.1+k3s1, newer than v1.36.5+k3s1: k3s can't move to an older release" \
  playbooks/kubernetes.yml --tags upgrade

step "Task coverage of the k3s role on $OS"
python3 tests/coverage.py "$TASK_COVERAGE_FILE" --min "$MIN_COVERAGE" --roles k3s
echo "=== $OS: the Kubernetes tests passed"
