#!/usr/bin/env python3
"""Fuzz target: a k3s node's config.yaml and a joining server's port checks (roles/k3s).

Properties, for any members, addresses, names, tokens and cluster settings that the role's patterns accept:
- the config the real template renders is YAML that reads back as exactly the mapping the filter built, with only
  the keys k3s is given, every value one of the inputs (nothing from a name or a certificate name becomes a key);
- the first server starts the cluster and every other node joins through join_url; a server has both tokens and an
  agent only the agent token, so an agent can never join as a server;
- a joining server checks every server before it (and those already started) on 2379, 2380 and 6443, each of those
  reaches its 2380, never itself and never an agent; an agent checks nothing;
- the release `k3s --version` prints reads back as itself;
- an upgrade step (k3s_upgrade_step) is "same" only for the same release, "downgrade" exactly when the target is
  older, "forward" only to a newer release of the same major at most one minor ahead, "skip" otherwise, and ""
  for anything the version pattern refuses (worked out here independently).
"""

import re
import sys

import atheris
import yaml

with atheris.instrument_imports():
    import roles

K3S = roles.filter_plugin("k3s", "k3s")
VARS = roles.role_vars("k3s")
TEMPLATE = roles.template("k3s", "config.yaml.j2")
SERVER_KEYS = {"node-name", "node-ip", "cluster-init", "server", "token", "agent-token", "tls-san", "disable",
               "node-taint", "write-kubeconfig-mode", "secrets-encryption", "flannel-backend", "selinux"}
AGENT_KEYS = {"node-name", "node-ip", "server", "token", "selinux"}


def word(fdp, alphabet, max_len):
    return roles.from_alphabet(fdp, alphabet, max_len)


def address(fdp):
    if fdp.ConsumeIntInRange(0, 9):
        return ".".join(str(fdp.ConsumeIntInRange(0, 255 if fdp.ConsumeIntInRange(0, 9) else 260)) for _ in range(4))
    return word(fdp, "0123456789.:/ -\n#'\"", 18)


# Mostly values the patterns accept, sometimes one with a character they must refuse.
def name(fdp):
    if fdp.ConsumeIntInRange(0, 9):
        return "n" + word(fdp, "abz09-", 10) + "z"
    return word(fdp, "abz09-.: \nA_", 12)


def token_value(fdp):
    if fdp.ConsumeIntInRange(0, 9):
        return word(fdp, "0123456789abcdef", 4) + "a" * 28 + word(fdp, "0123456789abcdefXYZ", 60)
    return word(fdp, "abcXYZ0189 :\n", 40)


def san(fdp):
    if fdp.ConsumeIntInRange(0, 4):
        return name(fdp) + "." + name(fdp)
    return address(fdp) if fdp.ConsumeBool() else word(fdp, "abz09-.:\n *", 20)


def release(fdp):
    if fdp.ConsumeIntInRange(0, 9):
        return "v{}.{}.{}+k3s{}".format(*(fdp.ConsumeIntInRange(0, 3 if i < 2 else 12) for i in range(4)))
    return word(fdp, "v1.2+k3s0 \n-", 16)


def check_upgrade_step(fdp):
    have, want = release(fdp), release(fdp)
    step = K3S.k3s_upgrade_step(have, want)
    parsed = []
    for value in (have, want):
        found = re.fullmatch(r"v([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})\+k3s([0-9]{1,4})", value)
        parsed.append(tuple(int(p) for p in found.groups()) if found else None)
    a, b = parsed
    if a is None or b is None:
        assert step == "", (have, want, step)
        return
    assert roles.matches(VARS["k3s_version_pattern"], have) and roles.matches(VARS["k3s_version_pattern"], want)
    if a == b:
        expected = "same"
    elif b < a:
        expected = "downgrade"
    elif b[0] == a[0] and b[1] - a[1] <= 1:
        expected = "forward"
    else:
        expected = "skip"
    assert step == expected, (have, want, step, expected)


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    check_upgrade_step(fdp)
    members = [{"host": name(fdp), "role": fdp.PickValueInList(["server", "server", "agent"]),
                "address": address(fdp)} for _ in range(fdp.ConsumeIntInRange(0, 5))]
    names = [m["host"] for m in members]
    if not all(roles.matches(VARS["k3s_node_name_pattern"], n) for n in names) or len(set(names)) != len(names):
        return
    if not all(roles.matches(VARS["k3s_ipv4_pattern"], m["address"]) for m in members):
        return
    servers = [m["host"] for m in members if m["role"] == "server"]
    if not servers:
        return
    token, agent_token = token_value(fdp), token_value(fdp)
    if not (roles.matches(VARS["k3s_token_pattern"], token) and roles.matches(VARS["k3s_token_pattern"], agent_token)
            and token != agent_token):
        return
    sans = [san(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    if not all(roles.matches(VARS["k3s_san_pattern"], s) or roles.matches(VARS["k3s_ipv4_pattern"], s) for s in sans):
        return
    vip = address(fdp) if fdp.ConsumeBool() else ""
    if vip and not roles.matches(VARS["k3s_ipv4_pattern"], vip):
        return
    started = [h for h in servers if fdp.ConsumeBool()]
    seed = K3S.k3s_seed(members, started)
    assert seed == (started[0] if started else servers[0]), (seed, started, servers)
    disable = [d for d in VARS["k3s_disable_choices"] if fdp.ConsumeBool()]
    cluster = {"join_url": f"https://{vip or next(m['address'] for m in members if m['host'] == seed)}:6443",
               "token": token, "agent_token": agent_token, "vip": vip, "tls_san": sans, "disable": disable,
               "taint": fdp.ConsumeBool(), "encryption": fdp.ConsumeBool(),
               "flannel_backend": fdp.PickValueInList(VARS["k3s_flannel_backends"])}

    for m in members:
        node = {"role": m["role"], "name": m["host"], "address": m["address"], "seed": m["host"] == seed,
                "selinux": fdp.ConsumeBool()}
        config = K3S.k3s_config(node, cluster)
        text = TEMPLATE.render(k3s_config_data=config)
        assert yaml.safe_load(text) == config, (text, config)
        assert config["node-name"] == m["host"] and config["node-ip"] == m["address"], config
        if m["role"] == "server":
            assert set(config) <= SERVER_KEYS, config
            assert config["token"] == token and config["agent-token"] == agent_token, config
            assert ("cluster-init" in config) == (m["host"] == seed) != ("server" in config), config
            assert set(config.get("tls-san", [])) == ({vip} if vip else set()) | set(sans), config
            assert config.get("disable", []) == disable, config
        else:
            assert set(config) <= AGENT_KEYS, config
            assert config["token"] == agent_token and token not in text, config
            assert config["server"] == cluster["join_url"], config

        checks = K3S.k3s_join_checks(members, m["host"], started)
        if m["role"] == "agent":
            assert checks == [], checks
            continue
        before = set(servers[: servers.index(m["host"])]) | set(started)
        before.discard(m["host"])
        out = {(c["to"], c["port"]) for c in checks if c["from"] == m["host"]}
        inb = {c["from"] for c in checks if c["to"] == m["host"]}
        assert out == {(h, p) for h in before for p in (2379, 2380, 6443)}, (checks, before)
        assert inb == before and all(c["port"] == 2380 for c in checks if c["to"] == m["host"]), checks
        assert all(c["from"] != c["to"] for c in checks), checks

    version = f"v{fdp.ConsumeIntInRange(0, 99)}.{fdp.ConsumeIntInRange(0, 99)}.{fdp.ConsumeIntInRange(0, 99)}+k3s1"
    assert K3S.k3s_installed_version(f"k3s version {version} (abc)\ngo version go1\n") == version
    found = K3S.k3s_installed_version(word(fdp, "k3s version v1.2+\n ", 40))
    assert found == "" or roles.matches(VARS["k3s_version_pattern"], found), found


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
