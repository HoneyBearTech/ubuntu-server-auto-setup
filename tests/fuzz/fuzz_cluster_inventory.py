#!/usr/bin/env python3
"""Fuzz target: the cluster inventory of the updates playbook (plugins/inventory/usas_pve.py, plan()).

Properties, for any cluster listing (as /cluster/resources reports it, through usas_vms.parse_resources), any
configs and guest-agent answers, any static inventory and any filters:
- every host is a static host whose pve_vmid is that VM's ID, or vm-<vmid>; no VM and no name appears twice;
- every group is usas_pve_vms, usas_pve_skipped or a pve_node_/pve_tag_/pve_pool_ group of lowercase letters,
  digits and _ (never a name a VM's owner chose as it is);
- a VM the filters leave out, or exclude, never appears; one tagged usas-no-updates, stopped, locked, a template
  or a container is in usas_pve_skipped with a reason and never in usas_pve_vms;
- a VM outside the static inventory that is updated is pinned: its alias matches ALIAS_PATTERN and names its VM
  ID, its SSH arguments are exactly that HostKeyAlias, it has its own control path, and its address is a static
  ansible_host or one its agent or ipconfig0 reported; a static host never gets an address, alias or SSH
  arguments from the cluster;
- option_problems refuses filter values with spaces, quotes, semicolons or line breaks.
"""

import importlib.util
import pathlib
import re
import sys

import atheris

ROOT = pathlib.Path(__file__).resolve().parents[2]

with atheris.instrument_imports():
    spec = importlib.util.spec_from_file_location("usas_pve_plugin", ROOT / "plugins/inventory/usas_pve.py")
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)

V = P.V
GROUP = re.compile(r"^(usas_pve_vms|usas_pve_skipped|pve_(node|tag|pool)_[a-z0-9_]+)\Z")
NAMES = ["web-01", "db", "Bad Name", "<x>", "vm-4093", "", "a" * 80]
TAGS = ["prod", "web", "usas-no-updates", "USAS-NO-UPDATES", "x;y", "ünï", "a b"]
ADDRESSES = ["10.0.0.5", "127.0.0.1", "169.254.1.1", "999.1.1.1", "192.168.1.20"]
STATUSES = ["running", "stopped", "paused", ""]
FILTER_VALUES = ["pve-1", "prod", "web-*", "4093", "a b", "x;y", "x\n", "'q'", ""]


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    resources = []
    for _ in range(fdp.ConsumeIntInRange(0, 8)):
        resources.append({
            "vmid": fdp.PickValueInList([4091, 4093, 4094, "x", 12, 10_000_000_000]),
            "node": fdp.PickValueInList(["pve-1", "pve-2", "Bad Node", ""]),
            "type": fdp.PickValueInList(["qemu", "qemu", "lxc", "storage"]),
            "status": fdp.PickValueInList(STATUSES), "name": fdp.PickValueInList(NAMES),
            "tags": ";".join(fdp.PickValueInList(TAGS) for _ in range(fdp.ConsumeIntInRange(0, 3))),
            "template": fdp.ConsumeIntInRange(0, 1) if not fdp.ConsumeIntInRange(0, 4) else 0,
            "lock": "" if fdp.ConsumeIntInRange(0, 4) else "backup",
            "pool": fdp.PickValueInList(["", "prod", "Team A"]),
        })
    vms = V.parse_resources(resources)
    configs = {vm.vmid: V.parse_config({"agent": "1", "ostype": fdp.PickValueInList(["l26", "win11", ""]),
                                        "ipconfig0": f"ip={fdp.PickValueInList(ADDRESSES)}/24"})
               for vm in vms if fdp.ConsumeBool()}
    agents = {vm.vmid: ([a for a in [fdp.PickValueInList(ADDRESSES)] if V.usable_ipv4(a)],
                        V.parse_osinfo({"id": "ubuntu", "pretty-name": fdp.ConsumeUnicodeNoSurrogates(20)}))
              for vm in vms if fdp.ConsumeBool()}
    static = {}
    for name in ["upd-usas", "vm-4093", "web-01"]:
        if fdp.ConsumeBool():
            static[name] = {"pve_vmid": fdp.PickValueInList([None, 4091, 4093, "4094"]),
                            "ansible_host": fdp.PickValueInList([None, "127.0.0.1", "10.0.0.9"])}
    opts = {key: [fdp.PickValueInList(FILTER_VALUES) for _ in range(fdp.ConsumeIntInRange(0, 2))]
            for key in ("nodes", "tags_any", "tags_all", "pools", "name_patterns", "exclude")}
    opts["cluster"] = fdp.PickValueInList(["", "homelab", "Bad Cluster"])

    problems = P.option_problems(opts)
    for key, values in opts.items():
        if key != "cluster" and any(any(c in str(v) for c in " ;'\n") for v in values):
            assert any(p.startswith(key) for p in problems), (key, values, problems)
    if problems:
        return
    cluster = opts["cluster"] or V.DEFAULT_CLUSTER
    hosts = P.plan(vms, configs, agents, static, cluster, opts)

    names = [h["name"] for h in hosts]
    assert len(names) == len(set(names)), names
    vmids = [h["vars"]["usas_pve_vmid"] for h in hosts]
    assert len(vmids) == len(set(vmids)), vmids
    by_vmid = {vm.vmid: vm for vm in vms}
    excluded = {str(e).lower() for e in opts["exclude"]}
    for host in hosts:
        vm, variables, groups = by_vmid[host["vars"]["usas_pve_vmid"]], host["vars"], host["groups"]
        assert str(vm.vmid) not in excluded, host
        assert all(GROUP.match(g) for g in groups), groups
        statics = [n for n, s in static.items() if s.get("pve_vmid") and str(s["pve_vmid"]) == str(vm.vmid)]
        if host["static"]:
            assert host["name"] in statics, host
            assert not {"ansible_host", "ansible_ssh_extra_args", "usas_pve_alias"} & set(variables), host
        else:
            assert host["name"] == f"vm-{vm.vmid}" and not statics, host
        tags = {t.lower() for t in vm.tags}
        must_skip = ("usas-no-updates" in tags or vm.type == "lxc" or vm.template or vm.status != "running"
                     or bool(vm.lock))
        if must_skip:
            assert "usas_pve_skipped" in groups and variables["usas_pve_skip_reason"], host
        skipped = "usas_pve_skipped" in groups
        assert skipped == ("usas_pve_skip_reason" in variables), host
        assert not (skipped and "usas_pve_vms" in groups), host
        if not host["static"] and not skipped:
            alias = variables["usas_pve_alias"]
            assert "usas_pve_vms" in groups and V.ALIAS_PATTERN.match(alias) and alias.endswith(f"-{vm.vmid}"), host
            assert variables["ansible_ssh_extra_args"] == f"-o HostKeyAlias={alias}", host
            assert variables["ansible_control_path"] == V.control_path(vm.vmid), host
            address = variables.get("ansible_host") or (static.get(host["name"]) or {}).get("ansible_host")
            reported = agents.get(vm.vmid, ([], None))[0] + [configs[vm.vmid].ipconfig0 if vm.vmid in configs else ""]
            assert address and (address in reported or address == (static.get(host["name"]) or {}).get("ansible_host")), host


atheris.Setup(sys.argv, one_input)
atheris.Fuzz()
