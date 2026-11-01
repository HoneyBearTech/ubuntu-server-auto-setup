"""The Kubernetes clusters of the Defaults page, the Servers page's cluster and role columns, and what the VMs page
and the runs need to know about them.

A cluster's settings are the fields of schema.CLUSTER_FIELDS, written as the group_vars of k8s_<cluster>
(store.Cluster); its members are the servers whose k8s_cluster and k8s_role name it. The checks here are those of
roles/k3s/tasks/check.yml that the forms can make before a run: the run checks again. The join tokens are
write-only: they go into the Vault and are never sent back, and their values never reach the audit log.
"""

import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from .schema import Field, Invalid, Schema, parse_value, token_vault_name
from .store import Cluster, Config

MAX_CLUSTERS = 16
# The Proxmox tag proxmox_vm gives a member's VM (K1): usas-k8s-<cluster>-<role>.
TAG = re.compile(r"^usas-k8s-([a-z][a-z0-9-]{0,30})-(server|agent)\Z")
# The fleet updates' drain of cluster members (roles/updates): the Updates page's setting, or a cluster's own.
DRAIN_VAR = "updates_k8s_drain"


@dataclass
class Parsed:
    clusters: list[Cluster]
    errors: dict[str, str] = field(default_factory=dict)
    renamed: dict[str, str] = field(default_factory=dict)  # old name -> new name
    removed: list[str] = field(default_factory=list)
    tokens: dict[str, str] = field(default_factory=dict)  # Vault name -> the new value (never logged)


def members(config: Config, name: str) -> list[dict[str, Any]]:
    """The Servers page's servers in cluster name (as a server or an agent)."""
    return [s for s in config.servers if s.get("k8s_cluster") == name and s.get("k8s_role") in ("server", "agent")]


def member_names(config: Config, cluster: Cluster) -> list[str]:
    """Every member's inventory name: the listed servers', then those set by hand."""
    names = [str(s["name"]) for s in members(config, cluster.name)]
    return names + [h for hosts in cluster.other_members.values() for h in hosts if h not in names]


def _network(text: str) -> tuple[int, int] | None:
    """A VIP, a pool range a-b or a network as (first, last), or None."""
    try:
        if "/" in text:
            net = ipaddress.IPv4Network(text, strict=False)
            return int(net.network_address), int(net.broadcast_address)
        if "-" in text:
            first, last = (int(ipaddress.IPv4Address(p)) for p in text.split("-", 1))
            return (first, last) if first <= last else None
        return (int(ipaddress.IPv4Address(text)),) * 2 if text else None
    except ValueError:
        return None


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def reserved_addresses(config: Config) -> list[tuple[str, str, tuple[int, int]]]:
    """(cluster, what, range) for each cluster's API address and LoadBalancer pool."""
    found = []
    for cluster in config.clusters:
        for key, what in (("k8s_api_vip", "API address"), ("k8s_lb_pool", "LoadBalancer pool")):
            value = cluster.values.get(key) or cluster.extra_vars.get(key)
            span = _network(str(value or "")) if isinstance(value, str) else None
            if span:
                found.append((cluster.name, what, span))
    return found


def address_problem(address: str, config: Config) -> str:
    """Why a server can't have address: it is a cluster's API address or in its LoadBalancer pool."""
    span = _network(address)
    if not address or span is None or "/" in address or "-" in address:
        return ""
    for name, what, reserved in reserved_addresses(config):
        if _overlap(span, reserved):
            return f"This is cluster {name}'s {what}: kube-vip moves it between the members, it's no server's own."
    return ""


def parse(form: Any, schema: Schema, config: Config) -> Parsed:
    """The clusters of the Defaults page's form (clusters-<row>-<field>, cluster_count rows, a row's "original"
    being the name it was loaded with) and the errors by field name."""
    text = str(form.get("cluster_count", "0"))
    count = min(int(text) if text.isdigit() else 0, MAX_CLUSTERS)
    old = {c.name: c for c in config.clusters}
    result = Parsed(clusters=[])
    seen: dict[str, int] = {}
    for i in range(count):
        prefix = f"clusters-{i}-"
        original = str(form.get(prefix + "original") or "")
        cluster = Cluster(name="")
        if original in old:  # kept as it was where the form has no field for it
            kept = old[original]
            cluster = Cluster(name=original, extra_vars=dict(kept.extra_vars), member_vars=dict(kept.member_vars),
                              other_children=dict(kept.other_children), other_members=dict(kept.other_members),
                              member_host_vars=dict(kept.member_host_vars), under_servers=kept.under_servers)
        for f in schema.cluster_fields:
            raw: Any = form.get(prefix + f.name)
            if f.kind == "list" and f.choices is not None:
                raw = "\n".join(str(v) for v in form.getlist(prefix + f.name))
            try:
                value = parse_value(f, raw)
            except Invalid as exc:
                result.errors[prefix + f.name] = exc.message
                continue
            if f.name == "name":
                cluster.name = value
                if value in seen:
                    result.errors[prefix + "name"] = "Another cluster already has this name."
                seen[value] = i
                continue
            if f.name in cluster.extra_vars and value in (None, "", [], f.default):
                continue  # left empty: the value set by hand stays
            cluster.values[f.name] = value
            cluster.extra_vars.pop(f.name, None)  # the form's value replaces one set by hand
        _check_cluster(cluster, prefix, result.errors)
        for t in schema.cluster_tokens:
            _token(form, prefix, t, cluster, original, config, result)
        if original in old and cluster.name and cluster.name != original:
            result.renamed[original] = cluster.name
        result.clusters.append(cluster)
    submitted = {str(form.get(f"clusters-{i}-original") or "") for i in range(count)}
    for name in old:
        if name not in submitted:
            result.removed.append(name)
            if member_names(config, old[name]):
                result.errors.setdefault("clusters", (
                    f"Cluster {name} still has members ({', '.join(member_names(config, old[name]))}): set their "
                    "Kubernetes cluster to none on the Servers page before removing it."))
    _check_addresses(result.clusters, config, result.errors)
    return result


def _check_cluster(cluster: Cluster, prefix: str, errors: dict[str, str]) -> None:
    """What roles/k3s/tasks/check.yml refuses about one cluster's settings."""
    values = cluster.values
    vip, pool = str(values.get("k8s_api_vip") or ""), str(values.get("k8s_lb_pool") or "")
    if pool and not vip and prefix + "k8s_api_vip" not in errors:
        errors.setdefault(prefix + "k8s_lb_pool", "A LoadBalancer pool needs the API address: kube-vip announces both.")
    if pool and "servicelb" not in (values.get("k8s_disable") or []) and prefix + "k8s_disable" not in errors:
        errors.setdefault(prefix + "k8s_lb_pool", "A LoadBalancer pool needs servicelb left out: k3s' own load "
                                                  "balancer would take the services' addresses too.")
    if vip and pool:
        span, vip_span = _network(pool), _network(vip)
        if span and vip_span and _overlap(span, vip_span):
            errors.setdefault(prefix + "k8s_lb_pool", "The API address can't be in the LoadBalancer pool.")


def _token(form: Any, prefix: str, t: Field, cluster: Cluster, original: str, config: Config,
           result: Parsed) -> None:
    """A token typed into the form: checked, and only replacing a saved one when the box says so."""
    key = prefix + t.name
    try:
        value = parse_value(t, form.get(key))
    except Invalid as exc:
        result.errors[key] = exc.message
        return
    if not value or not cluster.name:
        return
    vault = token_vault_name(t, cluster.name)
    saved = config.secret_is_set(token_vault_name(t, original or cluster.name))
    if saved and form.get(key + "__replace") not in ("on", "true"):
        result.errors[key] = ("A token is saved already. Replacing it on a running cluster needs k3s' own token "
                              "rotation first: tick the box to replace it.")
        return
    result.tokens[vault] = value


def _check_addresses(clusters: list[Cluster], config: Config, errors: dict[str, str]) -> None:
    """Two clusters' API addresses and pools can't overlap, and no server may have one as its address."""
    spans: list[tuple[str, str, tuple[int, int]]] = []
    for i, cluster in enumerate(clusters):
        for key in ("k8s_api_vip", "k8s_lb_pool"):
            span = _network(str(cluster.values.get(key) or ""))
            if span is None:
                continue
            field_name = f"clusters-{i}-{key}"
            other = next((n for n, _, s in spans if _overlap(s, span)), None)
            if other is not None and field_name not in errors:
                errors[field_name] = f"This overlaps cluster {other}'s API address or LoadBalancer pool."
            servers = [str(s["name"]) for s in config.servers if s.get("ansible_host")
                       and (a := _network(str(s["ansible_host"]))) and _overlap(a, span)]
            if servers and field_name not in errors:
                errors[field_name] = f"{', '.join(servers)} already use this address: it must be free."
            spans.append((cluster.name, key, span))


def generate_token() -> str:
    """A join token, as openssl rand -hex 32 makes one."""
    return secrets.token_hex(32)


def apply_renames(config: Config, parsed: Parsed) -> None:
    """Moves the servers and the saved tokens of a renamed cluster to its new name."""
    for old, new in parsed.renamed.items():
        for server in config.servers:
            if server.get("k8s_cluster") == old:
                server["k8s_cluster"] = new
        for template in ("vault_k8s_token_{}", "vault_k8s_agent_token_{}"):
            before, after = template.format(old.replace("-", "_")), template.format(new.replace("-", "_"))
            if before in config.secrets and after not in parsed.tokens:
                config.secrets[after] = config.secrets.pop(before)


def drain(config: Config, cluster: Cluster | None, default: Any) -> bool:
    """Whether a fleet update drains a member of cluster first (updates_k8s_drain, set for the cluster by hand or
    the role's default)."""
    if cluster is not None:
        for values in (cluster.values, cluster.extra_vars, *cluster.member_vars.values()):
            if isinstance(values, dict) and DRAIN_VAR in values:
                return values[DRAIN_VAR] is True or str(values[DRAIN_VAR]).lower() == "true"
    return default is True


def membership(config: Config, vmid: int | None, tags: list[str]) -> dict[str, Any] | None:
    """A VM's cluster and role: from the inventory if it is a member there, else from its usas-k8s tag (a VM the
    inventory doesn't make a member, so no run treats it as one)."""
    for server in config.servers:
        if vmid is not None and server.get("pve_vmid") == vmid and server.get("k8s_role") in ("server", "agent"):
            return {"cluster": str(server["k8s_cluster"]), "role": str(server["k8s_role"]), "inventory": True}
    for tag in tags:
        match = TAG.match(str(tag))
        if match:
            return {"cluster": match.group(1), "role": match.group(2), "inventory": False}
    return None


def drain_warnings(config: Config, targets: list[tuple[int, str]], tags: dict[int, list[str]],
                   default: Any) -> list[str]:
    """For an updates plan: the cluster members it would update (and maybe reboot) without draining them first."""
    found = []
    by_name = {c.name: c for c in config.clusters}
    for vmid, name in targets:
        member = membership(config, vmid, tags.get(vmid, []))
        if member is None:
            continue
        cluster = by_name.get(member["cluster"])
        if not member["inventory"] or cluster is None:
            found.append(f"{name} is tagged as a member of Kubernetes cluster {member['cluster']}, which the "
                         "inventory doesn't have: it is updated without being drained first.")
        elif not drain(config, cluster, default):
            found.append(f"{name} is a {member['role']} of Kubernetes cluster {member['cluster']}, and draining "
                         f"is off ({DRAIN_VAR}): its pods aren't moved before the update, and a reboot stops them.")
    return found
