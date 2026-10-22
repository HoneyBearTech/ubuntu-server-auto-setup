"""Where build-installer will put the installer ISO, for the Run page.

The plan is worked out by the playbooks' own code (roles/proxmox_vm/filter_plugins/pve_cluster.py), so the page
can't drift from what build-installer does: a storage the cluster shares is built once, by one node that uses
it, and a node-local storage on every node that uses it. The UI knows which storages are shared from the last
connection test (/storage); the playbooks ask each node again when they run.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from .store import Config

PLUGIN = Path("roles/proxmox_vm/filter_plugins/pve_cluster.py")


def load_plan(project_dir: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("usas_pve_cluster", project_dir / PLUGIN)
    if spec is None or spec.loader is None:  # pragma: no cover (a broken checkout)
        raise RuntimeError(f"can't load {project_dir / PLUGIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iso_plan(plan: ModuleType, config: Config, storage: str, cluster: dict[str, Any]) -> dict[str, Any]:
    """Each (node, ISO storage) pair the servers need, and which node builds it, for a run without a limit.
    known is False until a connection test has listed the cluster's ISO storages (then a storage isn't
    known to be shared, and is planned as node-local)."""
    storages = cluster.get("iso_storages") or {}
    shared = "shared" in str(storages.get(storage, "")).split(", ")
    placements = [{"host": s["name"], "node": config.server_node(s), "ssh_host": config.server_node(s),
                   "storage": storage} for s in config.servers if config.server_node(s)]
    node_storages = {p["node"]: {storage: {"shared": shared, "active": True}} for p in placements}
    result = plan.pve_iso_builds(placements, node_storages, [p["node"] for p in placements])
    servers: dict[str, list[str]] = {}
    for p in placements:
        servers.setdefault(p["node"], []).append(p["host"])
    pairs = [{**pair, "servers": sorted(servers.get(pair["node"], [])),
              "address": config.node_addresses.get(pair["node"], "")} for pair in result["pairs"]]
    return {"storage": storage, "shared": shared, "known": bool(storages), "pairs": pairs,
            "builders": [b["node"] for b in result["builds"]]}
