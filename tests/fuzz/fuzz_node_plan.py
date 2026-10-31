#!/usr/bin/env python3
"""Fuzz target: placing VMs and installer ISOs on the nodes of a cluster (roles/proxmox_vm/filter_plugins).

Properties, for any placements (hosts, the nodes they name, the hosts that reach those nodes over SSH, their ISO
storages), any storages per node and any set of hosts in the run:
- the node check reports exactly the placements whose node the cluster doesn't have, and suggests a name only
  when it differs from a real one by case alone;
- the storage check reports exactly the placements whose storage isn't active on their node;
- every installer ISO a placement needs on a (node, node-local storage) pair is built by that node's SSH host if
  it is in the run, and skipped otherwise; every ISO needed on a shared storage is built there exactly once, by
  the SSH host of a node that needs it there and is in the run, or skipped if there is none; nothing else is
  built, every builder is in the run, and a build's storages are exactly its ISOs' storages;
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
ISOS = [{"name": n, "release": r, "point_release": "", "base_url": f"https://releases.ubuntu.com/{r}"}
        for n, r in (("ubuntu-26.04.1-live-server-amd64.iso", "26.04"), ("ubuntu-24.04.5-live-server-amd64.iso", "24.04"))]
# A cloud image goes in like an ISO, into the storage's Import folder.
ISOS.append({"name": "debian-13-genericcloud-amd64-20261021-2638.qcow2", "checksum": "sha512:" + "0" * 128,
             "url": "https://cloud.debian.org/images/cloud/trixie/20261021-2638/debian-13-genericcloud-amd64-20261021-2638.qcow2"})


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
                           "ssh_host": node if fdp.ConsumeIntInRange(0, 3) else f"ssh-{node}",
                           "isos": [iso for iso in ISOS if fdp.ConsumeBool()]})
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
    built = {(b["host"], i["storage"], i["name"]) for b in plan["builds"] for i in b["isos"]}
    assert all(b["host"] in in_run for b in plan["builds"]), plan
    assert len(built) == sum(len(b["isos"]) for b in plan["builds"]), plan  # no ISO twice in a storage on a host
    assert all(b["storages"] == sorted({i["storage"] for i in b["isos"]}) for b in plan["builds"]), plan
    pairs = {}
    for p in usable:
        pair = pairs.setdefault((p["node"], p["storage"]), (p["ssh_host"], set()))
        pair[1].update(iso["name"] for iso in p["isos"])
    expected = set()
    skipped = []
    for (node, storage), (ssh_host, isos) in pairs.items():
        if shared[storage]:
            continue
        for iso in isos:
            if ssh_host in in_run:
                expected.add((ssh_host, storage, iso))
            else:
                skipped.append((node, storage, iso))
    for storage, iso in {(s, i) for (_, s), (_, isos) in pairs.items() if shared[s] for i in isos}:
        users = {h for (_, s), (h, isos) in pairs.items() if s == storage and iso in isos}
        builders = {h for (h, s, i) in built if s == storage and i == iso}
        if users & set(in_run):
            assert len(builders) == 1 and builders <= users, (storage, iso, builders, users)
            expected |= {(h, storage, iso) for h in builders}
        else:
            assert not builders, (storage, iso, builders)
            skipped.append(None)
    assert built == expected, (built, expected)
    assert len(plan["skipped"]) == len(skipped), (plan["skipped"], skipped)
    assert len(plan["report"]) == len(plan["pairs"]), plan

    name = roles.from_alphabet(fdp, "abzAZ09-./?#% \n", 20)
    if roles.matches(NODE_PATTERN, name):
        assert urllib.parse.quote(name, safe="") == name, name


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
