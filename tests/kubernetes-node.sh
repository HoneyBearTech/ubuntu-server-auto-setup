# shellcheck shell=bash
# The Kubernetes node profile (roles/kubernetes_node) on the stand-in server: sourced by tests/baseline.sh (every
# release) and tests/run.sh (Ubuntu 26.04) once SSH is on port 2222, with their INV, SRV, P2222, step, play and
# cool_down. srv-01 is a server of cluster test (tests/inventory/k8s.yml), with a swap line in /etc/fstab to comment
# out. On the RHEL family's release 10 the role is refused (no stable k3s-selinux yet).

# Runs a playbook and fails unless it fails and its output says $1.
k8s_refused() {
  local msg="$1"; shift
  local log; log="$(mktemp "$TEST_TMP/refused.XXXXXX")"
  if ansible-playbook "${INV[@]}" "$@" >"$log" 2>&1; then
    cat "$log"; echo "ERROR: ansible-playbook $* wasn't refused" >&2; return 1
  fi
  grep -qF -- "$msg" "$log" || { cat "$log"; echo "ERROR: the refusal doesn't say: $msg" >&2; return 1; }
  echo "refused as expected: $msg"
}

K8S=(-i tests/inventory/k8s.yml --limit srv-01 --tags "firewall,kubernetes" "${P2222[@]}")
k8s_family="$(docker exec "$SRV" sh -c '. /etc/os-release && case "$ID" in ubuntu|debian) echo debian;; *) echo rhel;; esac')"
release="$(docker exec "$SRV" sh -c '. /etc/os-release && echo "${VERSION_ID%%.*}"')"
step "Kubernetes node settings that would weaken or break the node are refused before anything changes"
cool_down
k8s_refused "which a Kubernetes node needs as net.ipv4.ip_forward = 1" playbooks/baseline.yml "${K8S[@]}" \
  -e hardening_sysctl_enabled=true -e '{"hardening_sysctl_extra": {"net.ipv4.ip_forward": 0}}'
k8s_refused "k8s_api_sources a list of IPv4 addresses or networks" playbooks/baseline.yml "${K8S[@]}" \
  -e '{"k8s_api_sources": ["10.0.0.0/8 port 22"]}'
cool_down
k8s_refused "set k8s_node_ip, or ansible_host to an address, for srv-01, k8s-s2, k8s-a1" playbooks/baseline.yml \
  "${K8S[@]}" -e k8s_node_ip=srv.example.net -e ansible_host=127.0.0.1
k8s_refused "may be in only one k8s_<cluster>_servers or k8s_<cluster>_agents group" playbooks/baseline.yml \
  -i tests/inventory/k8s.yml -i tests/inventory/spread.yml --limit srv-hang --tags kubernetes "${P2222[@]}" \
  -e ansible_host=127.0.0.1
cool_down

if [ "$k8s_family" = rhel ] && [ "$release" != 9 ]; then
step "a Kubernetes node on EL$release is refused: Rancher has no stable k3s-selinux for it yet"
k8s_refused "has k3s-selinux only for release 9" playbooks/baseline.yml "${K8S[@]}"
else
step "a Kubernetes node: k3s' ports from the other members only, kernel settings, swap off, Longhorn, SELinux"
docker exec "$SRV" sh -c 'echo "/swap.img none swap sw 0 0" >> /etc/fstab'
play playbooks/baseline.yml "${K8S[@]}"
play --idempotent playbooks/baseline.yml "${K8S[@]}"
play tests/verify-kubernetes-node.yml -e ansible_port=2222
cool_down

step "a member that left, one that moved and the other flannel backend: their rules go, the owner's own stays"
K8S_MOVED=(-i tests/inventory/k8s-moved.yml --limit srv-01 --tags "firewall,kubernetes" "${P2222[@]}")
play playbooks/baseline.yml "${K8S_MOVED[@]}"
play --idempotent playbooks/baseline.yml "${K8S_MOVED[@]}"
play tests/verify-kubernetes-node.yml -e ansible_port=2222 -e expect_moved=true
cool_down
fi
