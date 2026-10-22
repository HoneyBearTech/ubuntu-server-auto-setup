#!/usr/bin/env python3
"""Fuzz target: placing VMs and installer ISOs on the nodes of a cluster (roles/proxmox_vm/filter_plugins).

Properties, for any placements (hosts, the nodes they name, the hosts that reach those nodes over SSH, their ISO
storages), any storages per node and any set of hosts in the run:
- the node check reports exactly the placements whose node the cluster doesn't have, and suggests a name only
  when it differs from a real one by case alone;
- the storage check reports exactly the placements whose storage isn't active on their node;
- every (node, node-local storage) pair is built by that node's SSH host if it is in the run, and skipped
  otherwise; every shared storage is built exactly once, by the SSH host of a node that uses it and is in the
  run, or skipped if there is none; nothing else is built, and every builder is in the run;
- a node name the roles accept (provision_hostname_pattern) is safe in an API path as it is.
"""

import sys
import urllib.parse

import atheris

with atheris.instrument_imports():
    import roles

PLAN = roles.filter_plugin("proxmox_vm", "pve_cluster")
NODE_PATTERN = roles.role_vars("proxmox_vm")["provision_hostname_pattern"]
NAMES = ["pve-a", "pve-b", "PVE-A", "pve-c", ""]
STORAGES = ["local", "nfs", "cephfs", "usb"]


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    cluster = [n for n in NAMES[:4] if fdp.ConsumeBool()]
    shared = {s: fdp.ConsumeBool() for s in STORAGES}  # the cluster's view: a storage is shared or it isn't
    node_storages = {n: {s: {"shared": shared[s], "active": fdp.ConsumeBool()} for s in STORAGES if fdp.ConsumeBool()}
                     for n in cluster}
    placements = []
    for i in range(fdp.ConsumeIntInRange(0, 8)):
        node = fdp.PickValueInList(NAMES)
        placements.append({"host": f"srv-{i}", "node": node, "storage": fdp.PickValueInList(STORAGES),
                           "ssh_host": node if fdp.ConsumeIntInRange(0, 3) else f"ssh-{node}"})
    hosts = sorted({p["ssh_host"] for p in placements})
    in_run = [h for h in hosts if fdp.ConsumeIntInRange(0, 3)] + [f"srv-{i}" for i in range(len(placements))]

    unknown = PLAN.pve_unknown_nodes(placements, cluster)
    assert [u["host"] for u in unknown] == [p["host"] for p in placements if p["node"] not in cluster]
    for u in unknown:
        same = [n for n in cluster if n.lower() == u["node"].lower()]
        assert u["did_you_mean"] == (same[0] if same and u["node"] else ""), u

    known = [p for p in placements if p["node"] in cluster]
    problems = PLAN.pve_storage_problems(known, node_storages)
    assert [b["host"] for b in problems] == [
        p["host"] for p in known if not node_storages[p["node"]].get(p["storage"], {}).get("active")]

    usable = [p for p in known if node_storages[p["node"]].get(p["storage"], {}).get("active")]
    plan = PLAN.pve_iso_builds(usable, node_storages, in_run)
    built = {(b["host"], s) for b in plan["builds"] for s in b["storages"]}
    assert all(b["host"] in in_run for b in plan["builds"]), plan
    assert len(built) == sum(len(b["storages"]) for b in plan["builds"]), plan  # no storage twice on a host
    pairs = {}
    for p in usable:
        pairs.setdefault((p["node"], p["storage"]), p["ssh_host"])
    expected = set()
    skipped = []
    for (node, storage), ssh_host in pairs.items():
        if shared[storage]:
            continue
        if ssh_host in in_run:
            expected.add((ssh_host, storage))
        else:
            skipped.append((node, storage))
    for storage in {s for (_, s) in pairs if shared[s]}:
        users = {h for (n, s), h in pairs.items() if s == storage}
        builders = {h for (h, s) in built if s == storage}
        if users & set(in_run):
            assert len(builders) == 1 and builders <= users, (storage, builders, users)
            expected |= {(h, storage) for h in builders}
        else:
            assert not builders, (storage, builders)
            skipped.append(None)
    assert built == expected, (built, expected)
    assert len(plan["skipped"]) == len(skipped), (plan["skipped"], skipped)

    name = roles.from_alphabet(fdp, "abzAZ09-./?#% \n", 20)
    if roles.matches(NODE_PATTERN, name):
        assert urllib.parse.quote(name, safe="") == name, name


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
