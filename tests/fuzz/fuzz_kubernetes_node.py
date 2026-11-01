#!/usr/bin/env python3
"""Fuzz target: a Kubernetes node's firewall rules (roles/kubernetes_node).

Properties, for any cluster members, addresses, API sources and existing firewall rules:
- with addresses and sources the role's patterns accept, a member's rules open only the flannel port and the
  kubelet's 10250/tcp to the other members, and on a server also 6443/tcp to the other members and the API sources
  and etcd's 2379:2380/tcp to the other servers; never anything from the member itself, an agent never 6443 or etcd;
- every rule renders as a firewalld rich rule the role's own parser reads back, with nothing in it but the address,
  port and protocol;
- the role's rules as UFW and firewalld list them are never stale; a rule with the role's comment (UFW) or on a k3s
  port (firewalld) that the cluster doesn't need is, exactly once; a rule with another comment, on another port, or
  one firewall_allow adds, never is.
"""

import sys

import atheris

with atheris.instrument_imports():
    import roles

K8S = roles.filter_plugin("kubernetes_node", "kubernetes_node")
VARS = roles.role_vars("kubernetes_node")
PORTS = ["8472", "51820"]


def address(fdp):
    if fdp.ConsumeIntInRange(0, 5):
        return ".".join(str(fdp.ConsumeIntInRange(0, 260)) for _ in range(4))
    return roles.from_alphabet(fdp, "0123456789./ \"'\nany", 18)


def ufw_line(rule):
    if rule["from"] == "any":
        return f"ufw allow {rule['port']}/{rule['proto']} comment '{rule['comment']}'"
    return f"ufw allow from {rule['from']} to any port {rule['port']} proto {rule['proto']} comment '{rule['comment']}'"


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    members = [{"host": f"h{i}", "role": fdp.PickValueInList(["server", "agent"]), "address": address(fdp)}
               for i in range(fdp.ConsumeIntInRange(0, 6))]
    sources = [address(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    me = fdp.PickValueInList([m["host"] for m in members] + ["nobody"])
    port = fdp.PickValueInList(PORTS)
    comment = "usas-k8s " + roles.from_alphabet(fdp, "abz09-", 8)
    if not all(roles.matches(VARS["k8s_node_ipv4_pattern"], m["address"]) for m in members):
        return
    if not all(roles.matches(VARS["k8s_node_source_pattern"], s) for s in sources):
        return
    if len({m["address"] for m in members}) != len(members):
        return

    rules = K8S.k8s_firewall_rules(members, me, sources, port, comment)
    mine = next((m for m in members if m["host"] == me), None)
    server = mine is not None and mine["role"] == "server"
    others = {m["address"]: m["role"] for m in members if m["host"] != me}
    for r in rules:
        assert r["comment"] == comment, r
        if r["from"] in others and not (r["port"] == "6443" and r["from"] in sources):
            allowed = {(port, "udp"), ("10250", "tcp")}
            if server:
                allowed.add(("6443", "tcp"))
                if others[r["from"]] == "server":
                    allowed.add(("2379:2380", "tcp"))
            assert (r["port"], r["proto"]) in allowed, (r, members, me)
        else:
            assert server and (r["port"], r["proto"]) == ("6443", "tcp") and r["from"] in sources, (r, members, me)
        assert mine is None or r["from"] != mine["address"] or r["from"] in sources, (r, me)
    assert len({(r["port"], r["proto"], r["from"]) for r in rules}) == len(rules), rules
    for a, role in others.items():
        assert {"port": port, "proto": "udp", "from": a, "comment": comment} in rules, (a, rules)
        assert ({"port": "2379:2380", "proto": "tcp", "from": a, "comment": comment} in rules) == \
            (server and role == "server"), (a, rules)

    rich = [K8S.k8s_firewalld_rule(r) for r in rules]
    for text, r in zip(rich, rules, strict=True):
        found = K8S._RICH_RULE.match(text)
        assert found, text
        assert (found["from"] or "any", found["port"], found["proto"]) == (r["from"], r["port"].replace(":", "-"),
                                                                            r["proto"]), (text, r)

    # What an earlier run may have left: a rule the cluster no longer needs, one with another comment, one on a
    # port the role doesn't own, and the owner's firewall_allow rule on the kubelet's port.
    gone = {"port": fdp.PickValueInList(PORTS + ["10250", "6443", "2379:2380"]), "proto": "tcp",
            "from": address(fdp), "comment": comment}
    gone["proto"] = "udp" if gone["port"] in PORTS else "tcp"
    if not roles.matches(VARS["k8s_node_ipv4_pattern"], gone["from"]):
        return
    needed = any((r["port"], r["proto"], r["from"]) == (gone["port"], gone["proto"], gone["from"]) for r in rules)
    theirs = dict(gone, comment="kubelet-scraper")
    other_port = dict(gone, port="22", proto="tcp")
    lines = [ufw_line(r) for r in rules] + [ufw_line(gone), ufw_line(theirs), ufw_line(other_port)]
    stale = K8S.k8s_ufw_stale(lines, rules, comment)
    assert len(stale) == (0 if needed else 1), (stale, gone, rules)
    if stale:
        assert stale[0] == ["delete", "allow", "from", gone["from"], "to", "any", "port", gone["port"], "proto",
                            gone["proto"]], stale

    keep = [K8S.k8s_firewalld_rule(theirs)] if fdp.ConsumeBool() else []
    current = rich + [K8S.k8s_firewalld_rule(gone), K8S.k8s_firewalld_rule(other_port)] + keep
    stale = K8S.k8s_firewalld_stale(current, rules, VARS["k8s_node_owned_ports"], keep)
    expected = [] if needed else [K8S.k8s_firewalld_rule(gone)]
    if keep and keep[0] == K8S.k8s_firewalld_rule(gone):
        expected = []
    assert sorted(set(stale)) == sorted(set(expected)), (stale, expected)


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
