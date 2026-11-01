#!/usr/bin/env python3
"""Fuzz target: a VM's extra disks (vm_extra_disks) and spread groups (roles/data_disks, roles/proxmox_vm).

Properties, for any disk settings, any blkid output and any hosts, groups, nodes and running VMs:
- a disk that the role's patterns accept becomes exactly one configuration option of the form
  "<storage>:<size>,discard=on,iothread=1,ssd=1,serial=usas-<label>": its values can't add or change an option,
  and the serial fits Proxmox's 20 characters;
- a disk is "blank" (and so formatted) only when blkid exited 2 and printed nothing, and "ready" only when blkid
  found its own file system type and label and no partition table: anything else is "other", left alone;
- the spread check reports exactly the (group, node) pairs that more than one host would share, counting an
  existing VM on the node it runs on.
"""

import re
import sys

import atheris

with atheris.instrument_imports():
    import roles

DISKS = roles.filter_plugin("data_disks", "data_disks")
PLAN = roles.filter_plugin("proxmox_vm", "pve_cluster")
VARS = roles.role_vars("data_disks")
FIELDS = ["TYPE", "LABEL", "PTTYPE", "UUID", "USAGE", "DEVNAME", "TYPE "]
FS = ["ext4", "xfs", "btrfs", "swap", ""]
NODES = ["pve-a", "pve-b", "pve-c", ""]


def accepted(disk):
    return (roles.matches(VARS["data_disks_size_pattern"], disk["size_gb"]) and int(disk["size_gb"]) <= 65536
            and roles.matches(VARS["data_disks_storage_pattern"], disk["storage"])
            and roles.matches(VARS["data_disks_label_pattern"], disk["label"]))


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)

    # Mostly from the patterns' own characters plus a few that would split an option, so many inputs pass.
    disk = {"size_gb": roles.from_alphabet(fdp, "0123456789,", 7),
            "storage": roles.from_alphabet(fdp, "abzLZ09._-,=:", 20),
            "label": roles.from_alphabet(fdp, "abz09_-,= ", 16)} if fdp.ConsumeBool() else \
        {"size_gb": roles.text(fdp, 8), "storage": roles.text(fdp, 20), "label": roles.text(fdp, 16)}
    if accepted(disk):
        options = PLAN.pve_extra_disks([disk])
        assert list(options) == ["scsi1"], options
        target, *rest = options["scsi1"].split(",")
        assert target == f"{disk['storage']}:{disk['size_gb']}", options
        assert rest == ["discard=on", "iothread=1", "ssd=1", f"serial=usas-{disk['label']}"], options
        assert len(rest[-1].removeprefix("serial=")) <= 20, options
        assert re.fullmatch(r"[A-Za-z0-9_-]+", rest[-1].removeprefix("serial=")), options

    wanted = {"label": fdp.PickValueInList(["data", "logs", ""]), "fs": fdp.PickValueInList(["ext4", "xfs"]),
              "mount": "/srv/data"}
    lines = []
    for _ in range(fdp.ConsumeIntInRange(0, 4)):
        key = fdp.PickValueInList(FIELDS)
        value = fdp.PickValueInList(FS + ["data", "logs", "gpt", roles.text(fdp, 8)])
        lines.append(f"{key}={value}" if fdp.ConsumeIntInRange(0, 5) else roles.text(fdp, 12))
    rc = fdp.PickValueInList([0, 2, 4, None])
    probe = {"rc": rc, "stdout": "\n".join(lines), "item": {"stat": {"path": "/dev/disk/by-id/x"}}}
    state = DISKS.data_disks_state([probe], [wanted])[0]
    fields = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    if state["state"] == "blank":
        assert rc == 2 and not fields, (probe, state)
    elif state["state"] == "ready":
        assert rc == 0 and fields.get("TYPE") == wanted["fs"] and fields.get("LABEL") == wanted["label"], (probe, state)
        assert "PTTYPE" not in fields, (probe, state)
    else:
        assert state["state"] == "other" and state["found"], (probe, state)

    hosts = [{"host": f"h{i}", "group": fdp.PickValueInList(["g1", "g2", ""]), "node": fdp.PickValueInList(NODES),
              "vmid": fdp.PickValueInList(["", "1", "2", "3"])} for i in range(fdp.ConsumeIntInRange(0, 6))]
    vms = [{"vmid": v, "node": fdp.PickValueInList(NODES)} for v in (1, 2) if fdp.ConsumeBool()]
    running = {str(v["vmid"]): v["node"] for v in vms}
    where = {}
    for h in hosts:
        node = running.get(h["vmid"], h["node"]) if h["vmid"] else h["node"]
        if h["group"] and node:
            where.setdefault((h["group"], node), []).append(h["host"])
    expected = sorted((g, n, sorted(hs)) for (g, n), hs in where.items() if len(hs) > 1)
    found = [(b["group"], b["node"], b["hosts"]) for b in PLAN.pve_spread_conflicts(hosts, vms)]
    assert found == expected, (hosts, vms, found, expected)


def fixed_cases():
    """Inputs the fuzzer found once, checked on every run."""
    # A label holding \x1d, which str.splitlines() treats as a line break: it isn't the empty label.
    probe = {"rc": 0, "stdout": "\nTYPE=xfs\nLABEL=\x1d!!\x00\x00\x08", "item": {"stat": {"path": "/dev/disk/by-id/x"}}}
    state = DISKS.data_disks_state([probe], [{"label": "", "fs": "xfs", "mount": "/srv/data"}])[0]
    assert state["state"] == "other", state


if __name__ == "__main__":
    fixed_cases()
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
