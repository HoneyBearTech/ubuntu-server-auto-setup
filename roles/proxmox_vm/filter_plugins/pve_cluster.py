"""Filters for placing VMs and installer ISOs on the nodes of one Proxmox VE cluster.

A placement says where one inventory host needs the installer ISO: {"host", "node", "ssh_host", "storage"}.
The node is the Proxmox node name (a VM's pve_node), ssh_host the inventory host Ansible reaches that node
through (pve_ssh_host), and storage the ISO storage (pve_iso_storage). tests/fuzz/fuzz_node_plan.py checks the
properties described below.
"""

DOCUMENTATION = """
name: pve_cluster
short_description: Place VMs and installer ISOs on the nodes of a Proxmox VE cluster
description: See the module docstring and roles/proxmox_vm/tasks/iso_plan.yml.
"""


def _text(value):
    return "" if value is None else str(value)


def pve_unknown_nodes(placements, node_names):
    """The placements whose node isn't one of the cluster's nodes, each with a hint when only the case differs
    (Proxmox node names are case-sensitive)."""
    names = [_text(n) for n in node_names]
    lower = {n.lower(): n for n in names}
    bad = []
    for p in placements:
        node = _text(p.get("node"))
        if node in names:
            continue
        bad.append({"host": _text(p.get("host")), "node": node,
                    "did_you_mean": lower.get(node.lower(), "") if node else ""})
    return bad


def pve_storages_by_node(results):
    """The ISO storages each node offers, from the loop results of GET /nodes/<node>/storage?content=iso:
    {node: {storage: {"shared": bool, "active": bool, "type": str}}}. A storage the token can't see, or that
    is disabled, isn't listed."""
    found = {}
    for result in results:
        node = _text(result.get("item"))
        entries = ((result.get("json") or {}).get("data")) or []
        storages = {}
        for entry in entries:
            if not isinstance(entry, dict) or "iso" not in _text(entry.get("content")).split(","):
                continue
            if _text(entry.get("enabled", 1)) in ("0", "False", "false"):
                continue
            storages[_text(entry.get("storage"))] = {
                "shared": _text(entry.get("shared", 0)) in ("1", "True", "true"),
                "active": _text(entry.get("active", 1)) in ("1", "True", "true"),
                "type": _text(entry.get("type")),
            }
        found[node] = storages
    return found


def pve_storage_problems(placements, node_storages):
    """The placements whose ISO storage doesn't allow ISO images on their node, or isn't active there, each
    with the storages that would do."""
    bad = []
    for p in placements:
        node, storage = _text(p.get("node")), _text(p.get("storage"))
        offered = node_storages.get(node, {})
        if offered.get(storage, {}).get("active"):
            continue
        bad.append({"host": _text(p.get("host")), "node": node, "storage": storage,
                    "available": sorted(s for s, info in offered.items() if info.get("active"))})
    return bad


def pve_iso_builds(placements, node_storages, in_run):
    """Where the installer ISO is built, given every placement of the run.

    Each (node, storage) pair needs the ISO once. A storage the cluster shares (NFS, CIFS, Ceph...) is the same
    folder on every node, so it is built there once, by the first node that uses it (by node name) and whose
    SSH host is in the run. A node-local storage is built on each node that uses it. Pairs whose SSH host
    --limit leaves out are skipped, not built.

    Returns {"builds": [{"host", "node", "storages"}], "skipped": [{"node", "storage", "shared"}],
    "pairs": [{"node", "storage", "shared", "built_by"}], "report": [one line per pair]}, sorted by host and node.
    """
    run = {_text(h) for h in in_run}
    pairs = {}
    for p in placements:
        node, storage = _text(p.get("node")), _text(p.get("storage"))
        ssh_host = _text(p.get("ssh_host")) or node
        pairs.setdefault((node, storage), ssh_host)

    def shared(node, storage):
        return bool(node_storages.get(node, {}).get(storage, {}).get("shared"))

    builds, skipped, summary = {}, [], []

    def build(ssh_host, node, storage):
        entry = builds.setdefault(ssh_host, {"host": ssh_host, "node": node, "storages": []})
        if storage not in entry["storages"]:
            entry["storages"].append(storage)

    shared_users = {}
    for (node, storage), ssh_host in sorted(pairs.items()):
        if shared(node, storage):
            shared_users.setdefault(storage, []).append((node, ssh_host))
            continue
        if ssh_host in run:
            build(ssh_host, node, storage)
            summary.append({"node": node, "storage": storage, "shared": False, "built_by": node})
        else:
            skipped.append({"node": node, "storage": storage, "shared": False})
            summary.append({"node": node, "storage": storage, "shared": False, "built_by": ""})
    for storage, users in sorted(shared_users.items()):
        chosen = next(((node, ssh_host) for node, ssh_host in users if ssh_host in run), None)
        if chosen:
            build(chosen[1], chosen[0], storage)
        else:
            skipped.append({"node": users[0][0], "storage": storage, "shared": True})
        for node, _ in users:
            summary.append({"node": node, "storage": storage, "shared": True,
                            "built_by": chosen[0] if chosen else ""})
    for entry in builds.values():
        entry["storages"].sort()
    pairs_out = sorted(summary, key=lambda s: (s["node"], s["storage"]))
    report = [f"{p['storage']} on {p['node']}{' (shared)' if p['shared'] else ''}: "
              + (f"built by {p['built_by']}" if p["built_by"] else "skipped, --limit leaves its node out")
              for p in pairs_out]
    return {"builds": [builds[h] for h in sorted(builds)], "skipped": skipped, "pairs": pairs_out,
            "report": report}


class FilterModule:
    def filters(self):
        return {
            "pve_unknown_nodes": pve_unknown_nodes,
            "pve_storages_by_node": pve_storages_by_node,
            "pve_storage_problems": pve_storage_problems,
            "pve_iso_builds": pve_iso_builds,
        }
