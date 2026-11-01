"""Filters for k3s on a cluster's members: who the members are, which server starts the cluster, what each node's
config.yaml says, which connections a joining server needs, whether a member may move to another release, and what
the optional Argo CD bootstrap installs.

A member is {"host", "role", "address"}: role "server" or "agent", address the IPv4 address the others reach it on
(as roles/kubernetes_node's k8s_node_addresses). tests/fuzz/fuzz_k3s_config.py checks the properties described
below.
"""

import json
import re

DOCUMENTATION = """
name: k3s
short_description: A k3s cluster's members, its first server and each node's configuration
description: See the module docstring and roles/k3s/tasks.
"""

# What `k3s --version` prints first: "k3s version v1.36.5+k3s1 (d73847bc)".
_VERSION = re.compile(r"^k3s version (v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+)(?: |$)")
# A release, as k8s_version and the catalog name it (roles/k3s/vars/main.yml, k3s_version_pattern).
_RELEASE = re.compile(r"^v([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})\+k3s([0-9]{1,4})\Z")


def _text(value):
    return "" if value is None else str(value).strip()


def k3s_members(cluster, groups, hostvars):
    """The members of a cluster, servers first, each in its group's order: the address is the host's k8s_node_ip,
    else its ansible_host, else its inventory name."""
    found = []
    for role in ("server", "agent"):
        for host in groups.get(f"k8s_{_text(cluster)}_{role}s", []) or []:
            values = hostvars.get(host, {})
            address = _text(values.get("k8s_node_ip")) or _text(values.get("ansible_host")) or _text(host)
            found.append({"host": _text(host), "role": role, "address": address})
    return found


def k3s_seed(members, started):
    """The server that starts the cluster (cluster-init): the first server that already has the cluster's data
    (started, the hosts that do), else the first server. Empty if there's no server."""
    servers = [m["host"] for m in members if m.get("role") == "server"]
    have = {_text(h) for h in started or []}
    for host in servers:
        if host in have:
            return host
    return servers[0] if servers else ""


def k3s_join_checks(members, me, started):
    """The connections a server needs before it joins: to each server already in the cluster on etcd's client and
    peer ports and the API, and from each of them to its own etcd peer port. Already in the cluster: the servers
    that had its data when the run began (started), and every server before this one (the playbook joins them in
    order). Each is {"from", "to", "address", "port"}: from and to are hosts, address is the to host's, sorted."""
    by_host = {m["host"]: m for m in members}
    mine = by_host.get(_text(me))
    if mine is None or mine.get("role") != "server":
        return []
    servers = [m["host"] for m in members if m.get("role") == "server"]
    joined = {_text(h) for h in started or []} | set(servers[: servers.index(mine["host"])])
    checks = []
    for host in sorted(joined):
        other = by_host.get(host)
        if other is None or other.get("role") != "server" or host == mine["host"]:
            continue
        for port in (2379, 2380, 6443):
            checks.append({"from": mine["host"], "to": host, "address": other["address"], "port": port})
        checks.append({"from": host, "to": mine["host"], "address": mine["address"], "port": 2380})
    return sorted(checks, key=lambda c: (c["from"], c["to"], c["port"]))


def k3s_config(node, cluster):
    """A node's /etc/rancher/k3s/config.yaml, as a mapping. node: {"role", "name", "address", "seed", "selinux"}.
    cluster: {"join_url", "token", "agent_token", "vip", "tls_san", "disable", "taint", "encryption",
    "flannel_backend"}. The first server starts the cluster (cluster-init); every other node joins through join_url.
    An agent gets only the agent token, so it can't join as a server."""
    server = _text(node.get("role")) == "server"
    config = {"node-name": _text(node.get("name")), "node-ip": _text(node.get("address"))}
    if server and node.get("seed"):
        config["cluster-init"] = True
    else:
        config["server"] = _text(cluster.get("join_url"))
    if server:
        config["token"] = _text(cluster.get("token"))
        config["agent-token"] = _text(cluster.get("agent_token"))
        sans = [_text(cluster.get("vip"))] if _text(cluster.get("vip")) else []
        sans += [_text(s) for s in cluster.get("tls_san") or [] if _text(s) and _text(s) not in sans]
        if sans:
            config["tls-san"] = sans
        if cluster.get("disable"):
            config["disable"] = [_text(d) for d in cluster["disable"]]
        if cluster.get("taint"):
            config["node-taint"] = ["CriticalAddonsOnly=true:NoExecute"]
        config["write-kubeconfig-mode"] = "0600"
        config["secrets-encryption"] = bool(cluster.get("encryption"))
        config["flannel-backend"] = _text(cluster.get("flannel_backend"))
    else:
        config["token"] = _text(cluster.get("agent_token"))
    if node.get("selinux"):
        config["selinux"] = True
    return config


def k3s_unreached(results):
    """What a joining server couldn't reach, from the results of its looped port checks (each item a join check),
    as "from <host> to <host> (<address>) port <port>"."""
    return [
        f'from {r["item"]["from"]} to {r["item"]["to"]} ({r["item"]["address"]}) port {r["item"]["port"]}'
        for r in results or []
        if r.get("failed") and isinstance(r.get("item"), dict)
    ]


def k3s_installed_version(output):
    """The release `k3s --version` reports (its output), or "" if it doesn't say."""
    for line in _text(output).splitlines():
        found = _VERSION.match(line.strip())
        if found:
            return found[1]
    return ""


def _release(value):
    found = _RELEASE.match(_text(value))
    return tuple(int(part) for part in found.groups()) if found else None


def k3s_upgrade_step(installed, target):
    """How a member on release installed gets to release target: "same"; "forward" to a newer patch or k3s build
    of its minor release, or to the next minor; "skip" for more than one minor (or another major) ahead, which
    Kubernetes doesn't support; "downgrade" to an older release, which k3s can't do; "" when either isn't a
    release."""
    have, want = _release(installed), _release(target)
    if have is None or want is None:
        return ""
    if want == have:
        return "same"
    if want < have:
        return "downgrade"
    if want[0] != have[0] or want[1] - have[1] > 1:
        return "skip"
    return "forward"


def k3s_argocd_values(settings):
    """The values Argo CD's chart is installed with: those homelab-kube's apps/argocd.yaml adopts the release with
    (the UI on a LoadBalancer address, no Dex, RBAC by email with nothing by default), so its first sync changes
    nothing. settings: {"load_balancer", "repo_url", "repo_ca"}. Without a LoadBalancer pool the UI's service is a
    ClusterIP one. A repo_ca is trusted for the repository's server only (argocd-tls-certs-cm)."""
    values = {
        "server": {"service": {"type": "LoadBalancer" if settings.get("load_balancer") else "ClusterIP"}},
        "dex": {"enabled": False},
        "configs": {"rbac": {"scopes": "[email]", "policy.default": ""}},
    }
    if _text(settings.get("repo_ca")):
        host = _repo_host(settings.get("repo_url"))
        values["configs"]["tls"] = {"certificates": {host: _text(settings["repo_ca"]) + "\n"}}
    return values


def k3s_root_application(settings):
    """The root Application, as homelab-kube's bootstrap/root.yaml: it syncs every Application in the repository's
    folder from the revision, with automated sync, pruning and self-heal. settings: {"name", "namespace",
    "repo_url", "path", "revision"}."""
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": _text(settings.get("name")), "namespace": _text(settings.get("namespace"))},
        "spec": {
            "project": "default",
            "source": {"repoURL": _text(settings.get("repo_url")), "targetRevision": _text(settings.get("revision")),
                       "path": _text(settings.get("path"))},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": _text(settings.get("namespace"))},
            "syncPolicy": {"automated": {"prune": True, "selfHeal": True}},
        },
    }


def k3s_argocd_installed(status, crd):
    """What's there of Argo CD: "deployed" (USAS' Helm release, installed), "retry" (the release is there but its
    install didn't finish, so it's installed again), "other" (Argo CD's Application type without that release:
    installed some other way, left alone) or "none". status: `helm status --output json`'s result; crd: `kubectl get
    crd applications.argoproj.io --ignore-not-found -o name`'s."""
    if (status or {}).get("rc") == 0:
        try:
            state = json.loads(status.get("stdout") or "{}").get("info", {}).get("status")
        except (ValueError, AttributeError):
            state = None
        return "deployed" if state == "deployed" else "retry"
    return "other" if _text((crd or {}).get("stdout")) else "none"


def _repo_host(url):
    """The host of an https:// URL the role's pattern has accepted."""
    return _text(url).split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


class FilterModule:
    def filters(self):
        return {
            "k3s_members": k3s_members,
            "k3s_seed": k3s_seed,
            "k3s_join_checks": k3s_join_checks,
            "k3s_config": k3s_config,
            "k3s_unreached": k3s_unreached,
            "k3s_installed_version": k3s_installed_version,
            "k3s_upgrade_step": k3s_upgrade_step,


            "k3s_argocd_values": k3s_argocd_values,
            "k3s_root_application": k3s_root_application,
            "k3s_argocd_installed": k3s_argocd_installed,
        }
