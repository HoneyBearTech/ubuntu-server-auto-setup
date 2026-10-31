#!/usr/bin/env python3
"""Fuzz target: what the updates playbook decides about Proxmox snapshots
(roles/updates/filter_plugins/usas_updates.py; tasks/snapshot.yml and snapshot_prune.yml use the results).

Properties, for any snapshot list, VM config and storage answers Proxmox could send:
- the clean-up only ever names snapshots that have both this playbook's name (usas_upd_YYYYMMDD_HHMMSS, a real
  time) and its description marker: never the operator's own, never "current", never one named like ours without
  the marker; each name at most once;
- it names exactly the snapshots of ours older than the age limit and, when a number to keep is given, all but
  the newest that many (worked out here independently);
- the storages read from a VM's config, and the ones reported nearly full, are plain storage names;
- the explanation of a failed API call never raises and has no control or direction characters;
- the filter's patterns are the role's (roles/updates/vars/main.yml).
"""

import datetime
import re
import sys

import atheris

with atheris.instrument_imports():
    import roles

U = roles.filter_plugin("updates", "usas_updates")
V = roles.role_vars("updates")
NAME = re.compile(V["updates_snapshot_name_pattern"])
MARKER = re.compile(V["updates_snapshot_marker_pattern"])
STORAGE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,99}\Z")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
NOW = datetime.datetime(2026, 10, 30, 12, tzinfo=datetime.UTC)
MARKERS = ["USAS updates run 20261029T020000Z-0a1b; safe to delete after 2026-11-05",
           "USAS updates run 20261029T020000Z-0A1B; safe", "USAS updates run x;", "usas updates run", "mine", "",
           " USAS updates run 20261029T020000Z-0a1b;", "USAS updates run 20261029T020000Z-0a1b;\n"]
CONFIG_VALUES = ["local-lvm:vm-100-disk-0,size=32G", "none,media=cdrom", "local:iso/x.iso,media=cdrom",
                 "/dev/disk/by-id/ata-X,size=1T", "ceph_pool.1:vm-100-disk-1", "bad store:x", "-x:y", "nfs:", ""]

assert U.SNAPSHOT_NAME.pattern == V["updates_snapshot_name_pattern"]
assert U.SNAPSHOT_MARKER.pattern == V["updates_snapshot_marker_pattern"]
for name in ("updates_snapshot_name_pattern", "updates_snapshot_keep_pattern", "updates_snapshot_age_pattern"):
    assert V[name].endswith(r"\Z"), name


def snapshot_name(fdp):
    kind = fdp.ConsumeIntInRange(0, 4)
    if kind == 0:
        return "current"
    if kind == 1:
        return fdp.ConsumeUnicodeNoSurrogates(30)
    moment = NOW - datetime.timedelta(minutes=fdp.ConsumeIntInRange(-60, 40 * 24 * 60))
    name = moment.strftime("usas_upd_%Y%m%d_%H%M%S")
    if kind == 3:  # not a real time, or not quite our pattern
        name = fdp.PickValueInList(["usas_upd_20261399_250000", "usas_upd_20261030_120000x", "USAS_upd_20261030_120000",
                                    "usas_upd_2026103_120000", name + "\n"])
    return name


def snapshot(fdp):
    if fdp.ConsumeIntInRange(0, 20) == 0:
        return fdp.PickValueInList([None, "x", 7, [], {"name": None}])
    entry = {"name": snapshot_name(fdp)}
    if fdp.ConsumeBool():
        entry["description"] = (fdp.PickValueInList(MARKERS) if fdp.ConsumeBool()
                                else fdp.ConsumeUnicodeNoSurrogates(40))
    return entry


def ours(entry):
    """The time one of the playbook's snapshots was taken, worked out here; None for any other snapshot."""
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        return None
    match = re.fullmatch(r"usas_upd_([0-9]{8})_([0-9]{6})", entry["name"])
    description = entry.get("description")
    if not match or not isinstance(description, str) or not description.startswith("USAS updates run "):
        return None
    if not re.match(r"USAS updates run [0-9]{8}T[0-9]{6}Z-[0-9a-f]{4};", description):
        return None
    try:
        return datetime.datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=datetime.UTC)
    except ValueError:
        return None


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    snapshots = [snapshot(fdp) for _ in range(fdp.ConsumeIntInRange(0, 12))]
    keep = fdp.PickValueInList([None, "", 0, 1, 2, 5, "1"])
    max_days = fdp.ConsumeIntInRange(1, 365)
    deleted = U.usas_snapshot_cleanup(snapshots, keep, max_days, NOW.isoformat())

    assert len(deleted) == len(set(deleted)), deleted
    taken = {}
    for entry in snapshots:
        moment = ours(entry)
        if moment is not None:
            taken.setdefault(entry["name"], moment)
    for name in deleted:
        assert name in taken, (name, snapshots)
        assert NAME.match(name) and name != "current"
        assert any(isinstance(e, dict) and e.get("name") == name and isinstance(e.get("description"), str)
                   and MARKER.match(e["description"]) for e in snapshots)
    newest_first = sorted(taken, key=lambda n: taken[n], reverse=True)
    limit = len(newest_first) if keep in (None, "") else int(keep)
    expected = {n for i, n in enumerate(newest_first)
                if i >= limit or taken[n] < NOW - datetime.timedelta(days=max_days)}
    assert set(deleted) == expected, (deleted, expected, snapshots, keep, max_days)

    config = {fdp.PickValueInList(["scsi0", "virtio1", "ide2", "efidisk0", "tpmstate0", "unused0", "net0",
                                   "scsi100", fdp.ConsumeUnicodeNoSurrogates(8)]):
              fdp.PickValueInList(CONFIG_VALUES) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(30)
              for _ in range(fdp.ConsumeIntInRange(0, 6))}
    storages = U.usas_disk_storages(config if fdp.ConsumeIntInRange(0, 10) else fdp.PickValueInList([None, [], "x"]))
    assert storages == sorted(set(storages)) and len(storages) <= 16
    assert all(STORAGE.match(s) for s in storages), storages

    results = [{"item": fdp.PickValueInList(storages + ["x\ny", "", "ok-store"]),
                "status": fdp.PickValueInList([200, 403, -1, "200"]),
                "json": {"data": {"used": fdp.PickValueInList([0, 90, "90", None, -1, 1e300]),
                                  "total": fdp.PickValueInList([0, 100, "100", None, "x"])}}}
               for _ in range(fdp.ConsumeIntInRange(0, 4))]
    full = U.usas_full_storages(results, fdp.ConsumeIntInRange(0, 100))
    assert all(STORAGE.match(s) and any(r["item"] == s for r in results) for s in full), full

    result = {"status": fdp.PickValueInList([403, 500, -1, None, "x"]),
              "msg": fdp.ConsumeUnicodeNoSurrogates(300)} if fdp.ConsumeBool() else fdp.PickValueInList([None, "x"])
    message = U.usas_pve_error(result, "take a snapshot of VM 100", "VM.Snapshot")
    assert not CONTROL.search(message) and len(message) < 600, message


atheris.Setup(sys.argv, one_input)
atheris.Fuzz()
