"""Proxmox VMs are created one at a time. Proxmox copies a cloud image into a new VM's disk under the storage's
cluster-wide lock, so five VMs created at once on one shared storage left one failing on the lock, and proxmox_kvm
then waited out its 900 s timeout. Run with `make catalog-test`."""

import pathlib

import yaml
from test_known_hosts_writes import tasks

ROOT = pathlib.Path(__file__).resolve().parents[2]
CREATORS = {"community.proxmox.proxmox_kvm", "proxmox_kvm"}


def vm_creates():
    for path in sorted((ROOT / "roles").glob("*/tasks/*.yml")):
        data = yaml.safe_load(path.read_text())
        for task, throttle in tasks(data if isinstance(data, list) else []):
            module = next(iter(CREATORS & task.keys()), None)
            if module and task[module].get("state") == "present":
                yield path.relative_to(ROOT), task.get("name"), throttle


def test_the_create_is_found():
    assert any(name == "Create VM {{ pve_vmid }}" for _, name, _ in vm_creates())


def test_each_create_runs_one_host_at_a_time():
    unthrottled = [(str(path), name) for path, name, throttle in vm_creates() if throttle != 1]
    assert not unthrottled, f"VM creates without throttle: 1: {unthrottled}"
