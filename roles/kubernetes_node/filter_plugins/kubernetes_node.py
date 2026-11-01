"""Filters for a Kubernetes node's firewall: the rules k3s needs between a cluster's members, and the rules a
member still has from an earlier run that the cluster no longer needs (a member that left, a changed address).

A rule is {"port", "proto", "from", "comment"}: port a number or a range "a:b", proto tcp or udp, from an IPv4
address or network, or "any". tests/fuzz/fuzz_kubernetes_node.py checks the properties described below.
"""

import re

DOCUMENTATION = """
name: kubernetes_node
short_description: The firewall rules between the members of a k3s cluster
description: See the module docstring and roles/kubernetes_node/tasks/firewall.
"""

# What `ufw show added` prints for a rule this role added: "ufw allow from <addr> to any port <port> proto <proto>
# comment '<comment>'", or "ufw allow <port>/<proto> comment '<comment>'" for one from anywhere.
_UFW_RULE = re.compile(
    r"^ufw allow (?:from (?P<from>\S+) to any port (?P<port>[0-9]+(?::[0-9]+)?) proto (?P<proto>tcp|udp)"
    r"|(?P<any_port>[0-9]+(?::[0-9]+)?)/(?P<any_proto>tcp|udp)) comment '(?P<comment>[^']*)'$"
)
# What `firewall-cmd --list-rich-rules` prints for one: the rich rule k8s_firewalld_rule renders.
_RICH_RULE = re.compile(
    r'^rule (?:family="ipv4" source address="(?P<from>[^"]+)" )?port port="(?P<port>[0-9]+(?:-[0-9]+)?)"'
    r' protocol="(?P<proto>tcp|udp)" accept$'
)


def _text(value):
    return "" if value is None else str(value).strip()


def k8s_node_addresses(members, hostvars):
    """Each member ({"host", "role"}) with its "address": the host's k8s_node_ip, else its ansible_host, else its
    inventory name."""
    found = []
    for m in members:
        host = _text(m.get("host"))
        values = hostvars.get(host, {})
        address = _text(values.get("k8s_node_ip")) or _text(values.get("ansible_host")) or host
        found.append({**m, "address": address})
    return found


def k8s_sysctl_conflicts(wanted, others):
    """The keys of wanted that others (another role's sysctl settings) set to a different value; a key others sets to
    None is left out there, so it's no conflict."""
    return sorted(k for k, v in wanted.items() if others.get(k) is not None and _text(others[k]) != _text(v))


def k8s_firewall_rules(members, me, api_sources, flannel_port, comment):
    """The rules one member needs, from the cluster's members ([{"host", "address", "role"}], role "server" or
    "agent") and this member's host name: the flannel port (udp) and the kubelet's 10250/tcp from every other
    member; on a server also 6443/tcp from every other member and each of api_sources, and etcd's 2379:2380/tcp
    from the other servers. Never from this member itself, never twice, sorted."""
    mine = [m for m in members if _text(m.get("host")) == _text(me)]
    server = bool(mine) and _text(mine[0].get("role")) == "server"
    others = [m for m in members if _text(m.get("host")) != _text(me)]
    wanted = set()
    for m in others:
        address = _text(m.get("address"))
        wanted.add((_text(flannel_port), "udp", address))
        wanted.add(("10250", "tcp", address))
        if server:
            wanted.add(("6443", "tcp", address))
            if _text(m.get("role")) == "server":
                wanted.add(("2379:2380", "tcp", address))
    if server:
        for source in api_sources or []:
            wanted.add(("6443", "tcp", _text(source)))
    return [{"port": p, "proto": pr, "from": f, "comment": comment} for p, pr, f in sorted(wanted)]


def k8s_ufw_stale(lines, rules, comment):
    """The rules in `ufw show added` (its lines) that carry this role's comment but aren't in rules: each as the
    arguments of the `ufw delete` that removes it, which names the rule as it was added."""
    wanted = {(_text(r.get("port")), _text(r.get("proto")), _text(r.get("from"))) for r in rules}
    stale = []
    for line in lines or []:
        found = _UFW_RULE.match(_text(line))
        if not found or found["comment"] != comment:
            continue
        if found["any_port"]:
            if (found["any_port"], found["any_proto"], "any") not in wanted:
                stale.append(["delete", "allow", f'{found["any_port"]}/{found["any_proto"]}'])
        elif (found["port"], found["proto"], found["from"]) not in wanted:
            stale.append(["delete", "allow", "from", found["from"], "to", "any", "port", found["port"],
                          "proto", found["proto"]])
    return stale


def k8s_firewalld_rule(rule):
    """The firewalld rich rule for one rule, as firewall-cmd --list-rich-rules prints it ("any" has no source)."""
    port = _text(rule.get("port")).replace(":", "-")
    source = _text(rule.get("from"))
    head = "rule " if source == "any" else f'rule family="ipv4" source address="{source}" '
    return f'{head}port port="{port}" protocol="{_text(rule.get("proto"))}" accept'


def k8s_firewalld_stale(rich_rules, rules, ports, keep):
    """The rich rules in firewall-cmd --list-rich-rules (rich_rules) on one of the cluster's ports (ports, as
    "port/proto" with "a-b" ranges) from a source address that rules don't allow: rich rules carry no comment, so the
    role owns the k3s ports on a member. A rich rule in keep (firewall_allow's) stays."""
    wanted = {k8s_firewalld_rule(r) for r in rules} | {_text(k) for k in keep or []}
    owned = {_text(p) for p in ports or []}
    stale = []
    for rich in rich_rules or []:
        rich = _text(rich)
        found = _RICH_RULE.match(rich)
        if not found or f'{found["port"]}/{found["proto"]}' not in owned or rich in wanted:
            continue
        stale.append(rich)
    return stale


class FilterModule:
    def filters(self):
        return {
            "k8s_node_addresses": k8s_node_addresses,
            "k8s_sysctl_conflicts": k8s_sysctl_conflicts,
            "k8s_firewall_rules": k8s_firewall_rules,
            "k8s_ufw_stale": k8s_ufw_stale,
            "k8s_firewalld_rule": k8s_firewalld_rule,
            "k8s_firewalld_stale": k8s_firewalld_stale,
        }
