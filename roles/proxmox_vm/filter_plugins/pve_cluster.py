"""Filters for placing VMs and installer ISOs on the nodes of one Proxmox VE cluster.

A placement says where one inventory host needs the installer ISO: {"host", "node", "ssh_host", "storage"}.
The node is the Proxmox node name (a VM's pve_node), ssh_host the inventory host Ansible reaches that node
through (pve_ssh_host), and storage the ISO storage (pve_iso_storage). "cloud_image": true marks a host that installs
from a cloud image, which goes in the same storage's Import content. tests/fuzz/fuzz_node_plan.py checks the
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
    {node: {storage: {"shared": bool, "active": bool, "type": str, "import": bool}}}, "import" when it allows
    Import content too. A storage the token can't see, or that is disabled, isn't listed."""
    found = {}
    for result in results:
        node = _text(result.get("item"))
        entries = ((result.get("json") or {}).get("data")) or []
        storages = {}
        for entry in entries:
            content = _text(entry.get("content")).split(",") if isinstance(entry, dict) else []
            if "iso" not in content:
                continue
            if _text(entry.get("enabled", 1)) in ("0", "False", "false"):
                continue
            storages[_text(entry.get("storage"))] = {
                "shared": _text(entry.get("shared", 0)) in ("1", "True", "true"),
                "active": _text(entry.get("active", 1)) in ("1", "True", "true"),
                "type": _text(entry.get("type")),
                "import": "import" in content,
            }
        found[node] = storages
    return found


def pve_storage_problems(placements, node_storages):
    """The placements whose ISO storage doesn't allow ISO images on their node, or isn't active there, or (for a
    placement marked "cloud_image") doesn't allow Import content, each with the storages that would do."""
    bad = []
    for p in placements:
        node, storage = _text(p.get("node")), _text(p.get("storage"))
        needs_import = _text(p.get("cloud_image")) in ("1", "True", "true")
        offered = node_storages.get(node, {})
        info = offered.get(storage, {})
        if info.get("active") and (info.get("import") or not needs_import):
            continue
        bad.append({"host": _text(p.get("host")), "node": node, "storage": storage, "cloud_image": needs_import,
                    "available": sorted(s for s, i in offered.items()
                                        if i.get("active") and (i.get("import") or not needs_import))})
    return bad


def pve_iso_builds(placements, node_storages, in_run):
    """Where each installer ISO is built, given every placement of the run.

    Each placement names the ISOs its host needs ("isos": [{"name", "release", "point_release", "base_url"}], or
    for a cloud-image install the image, [{"name": its file, "url", "checksum"}]; without the key, one unnamed
    installer ISO, as the web UI plans it). An image goes in the same way as an ISO, into the storage's Import
    folder: "url" tells them apart. Each
    (node, storage) pair needs each of those ISOs once. A storage the cluster shares (NFS, CIFS, Ceph...) is the
    same folder on every node, so an ISO is built there once, by the first node that uses it (by node name) and
    whose SSH host is in the run. A node-local storage gets it on each node that uses it. Pairs whose SSH host
    --limit leaves out are skipped, not built.

    Returns {"builds": [{"host", "node", "storages", "isos": [{"storage", "name", "release", "point_release",
    "base_url", "url", "checksum"}]}], "skipped": [{"node", "storage", "shared", "iso"}], "pairs": [{"node",
    "storage", "shared", "iso", "image", "built_by"}], "report": [one line per pair and ISO]}, sorted by host,
    node, storage and ISO.
    """
    run = {_text(h) for h in in_run}
    pairs = {}
    for p in placements:
        node, storage = _text(p.get("node")), _text(p.get("storage"))
        ssh_host = _text(p.get("ssh_host")) or node
        entry = pairs.setdefault((node, storage), {"ssh_host": ssh_host, "isos": {}})
        for iso in p.get("isos", [{}]) or []:
            iso = {k: _text(iso.get(k)) for k in ("name", "release", "point_release", "base_url", "url", "checksum")}
            entry["isos"].setdefault((iso["name"], iso["base_url"], iso["url"]), iso)

    def shared(node, storage):
        return bool(node_storages.get(node, {}).get(storage, {}).get("shared"))

    builds, skipped, summary = {}, [], []

    def build(ssh_host, node, storage, iso):
        entry = builds.setdefault(ssh_host, {"host": ssh_host, "node": node, "storages": [], "isos": []})
        if storage not in entry["storages"]:
            entry["storages"].append(storage)
        item = {"storage": storage, **iso}
        if item not in entry["isos"]:
            entry["isos"].append(item)

    def note(node, storage, is_shared, iso, built_by):
        summary.append({"node": node, "storage": storage, "shared": is_shared, "iso": iso["name"],
                        "image": bool(iso["url"]), "built_by": built_by})
        if not built_by:
            skipped.append({"node": node, "storage": storage, "shared": is_shared, "iso": iso["name"]})

    shared_users = {}
    for (node, storage), entry in sorted(pairs.items()):
        for _, iso in sorted(entry["isos"].items()):
            if shared(node, storage):
                shared_users.setdefault((storage, iso["name"], iso["base_url"], iso["url"]), (iso, []))[1].append(
                    (node, entry["ssh_host"]))
            elif entry["ssh_host"] in run:
                build(entry["ssh_host"], node, storage, iso)
                note(node, storage, False, iso, node)
            else:
                note(node, storage, False, iso, "")
    for (storage, _, _, _), (iso, users) in sorted(shared_users.items(), key=lambda kv: kv[0]):
        chosen = next(((node, ssh_host) for node, ssh_host in users if ssh_host in run), None)
        if chosen:
            build(chosen[1], chosen[0], storage, iso)
        for node, _ in users:
            summary.append({"node": node, "storage": storage, "shared": True, "iso": iso["name"],
                            "image": bool(iso["url"]), "built_by": chosen[0] if chosen else ""})
        if not chosen:
            skipped.append({"node": users[0][0], "storage": storage, "shared": True, "iso": iso["name"]})
    for entry in builds.values():
        entry["storages"].sort()
        entry["isos"].sort(key=lambda i: (i["storage"], i["name"], i["base_url"]))
    pairs_out = sorted(summary, key=lambda s: (s["node"], s["storage"], s["iso"]))
    report = [f"{p['storage']} on {p['node']}{' (shared)' if p['shared'] else ''}: "
              + (f"{'downloaded' if p['image'] else 'built'} by {p['built_by']}" if p["built_by"]
                 else "skipped, --limit leaves its node out")
              + (f" ({p['iso']})" if p["iso"] else "")
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
