#!/usr/bin/env python3
"""Fuzz target: what the updates playbook plans (roles/updates/filter_plugins/usas_updates.py): the waves the
servers are updated in (tasks/plan.yml) and the Proxmox snapshots (tasks/snapshot.yml and snapshot_prune.yml).

Properties, for any list of servers with any groups, nodes and settings:
- every server with a plain name is planned exactly once, and nothing else is (no entry that isn't one);
- no wave is empty or larger than updates_serial allows (worked out here independently);
- no wave holds more than updates_group_max_parallel servers of a group, or more than updates_node_max_parallel
  servers that may reboot on one node (0: no limit);
- a server is in a later wave only because every earlier wave was full for it;
- update groups are plain lower-case names, from updates_group and usas-group-<name> tags only.

For any snapshot list, VM config and storage answers Proxmox could send:
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
GROUP = re.compile(V["updates_group_pattern"])
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
NOW = datetime.datetime(2026, 10, 30, 12, tzinfo=datetime.UTC)
MARKERS = ["USAS updates run 20261029T020000Z-0a1b; safe to delete after 2026-11-05",
           "USAS updates run 20261029T020000Z-0A1B; safe", "USAS updates run x;", "usas updates run", "mine", "",
           " USAS updates run 20261029T020000Z-0a1b;", "USAS updates run 20261029T020000Z-0a1b;\n"]
CONFIG_VALUES = ["local-lvm:vm-100-disk-0,size=32G", "none,media=cdrom", "local:iso/x.iso,media=cdrom",
                 "/dev/disk/by-id/ata-X,size=1T", "ceph_pool.1:vm-100-disk-1", "bad store:x", "-x:y", "nfs:", ""]

assert U.SNAPSHOT_NAME.pattern == V["updates_snapshot_name_pattern"]
assert U.SERIAL.pattern == V["updates_serial_pattern"]
assert U.PARALLEL.pattern == V["updates_parallel_pattern"]
assert U.GROUP.pattern == V["updates_group_pattern"]
assert U.SNAPSHOT_MARKER.pattern == V["updates_snapshot_marker_pattern"]
for name in ("updates_snapshot_name_pattern", "updates_snapshot_keep_pattern", "updates_snapshot_age_pattern",
             "updates_serial_pattern", "updates_parallel_pattern", "updates_group_pattern"):
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


GROUP_NAMES = ["web", "Web", "db", " db ", "a.b-c_d", "", "-x", "x" * 64, "usas-group-web", 7, True, None]
TAGS = ["usas-group-web", "USAS-GROUP-db", "usas-group-", "usas-group-<b>", "prod", "usas-groupweb", 3]


def server(fdp):
    if fdp.ConsumeIntInRange(0, 15) == 0:
        return fdp.PickValueInList([None, "x", 7, [], {}, {"name": None}, {"name": ""}, {"name": "a\nb"}])
    group = (fdp.PickValueInList(GROUP_NAMES) if fdp.ConsumeBool()
             else [fdp.PickValueInList(GROUP_NAMES) for _ in range(fdp.ConsumeIntInRange(0, 3))])
    tags = [fdp.PickValueInList(TAGS) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(20)
            for _ in range(fdp.ConsumeIntInRange(0, 3))]
    groups = U.usas_update_groups(group, tags)
    assert groups == sorted(set(groups)) and len(groups) <= 16 and all(GROUP.match(g) for g in groups), groups
    named = {str(n).strip().lower() for n in (group if isinstance(group, list) else [group])
             if isinstance(n, (str, int)) and not isinstance(n, bool)}
    tagged = {t[len("usas-group-"):].strip().lower() for t in tags if isinstance(t, str)
              and t.lower().startswith("usas-group-")}
    assert set(groups) <= named | tagged, (groups, group, tags)
    return {"name": fdp.PickValueInList(["s1", "s2", "s3", "s4", "s5", "s6", "vm-4093"])
            if fdp.ConsumeIntInRange(0, 3) else fdp.ConsumeUnicodeNoSurrogates(10),
            "groups": groups if fdp.ConsumeIntInRange(0, 5) else fdp.PickValueInList(["web", None, ["Web", 1]]),
            "node": fdp.PickValueInList(["", "pve1", "pve2", None, 3]),
            "reboot": fdp.PickValueInList([True, False, "true", None])}


def check_plan(fdp):
    hosts = [server(fdp) for _ in range(fdp.ConsumeIntInRange(0, 30))]
    serial = fdp.PickValueInList(["1", "2", "5", "25%", "50%", "100%", "1%", 3, "0", "x", "101%", " 2 "])
    group_max = fdp.PickValueInList([0, 1, 2, "1", "x", -1, 100])
    node_max = fdp.PickValueInList([0, 1, 2, "2", None, 99])
    waves = U.usas_update_plan(hosts, serial, group_max, node_max)

    wanted = []
    for h in hosts:
        name = h.get("name") if isinstance(h, dict) else None
        if isinstance(name, str) and name and len(name) <= 255 and not CONTROL.search(name) and name not in wanted:
            wanted.append(name)
    planned = [n for wave in waves for n in wave]
    assert sorted(planned) == sorted(wanted), (planned, wanted)
    first = {}
    for h in hosts:
        if isinstance(h, dict) and h.get("name") in wanted:
            first.setdefault(h["name"], h)

    text = str(serial).strip()
    if re.fullmatch(r"[1-9][0-9]{0,3}", text):
        size = int(text)
    else:
        percent = int(text[:-1]) if re.fullmatch(r"([1-9][0-9]?|100)%", text) else 25
        size = max(1, len(wanted) * percent // 100)
    gmax = int(str(group_max)) if re.fullmatch(r"[0-9]{1,2}", str(group_max).strip()) else 1
    nmax = int(str(node_max)) if re.fullmatch(r"[0-9]{1,2}", str(node_max).strip()) else 2

    def groups_of(name):
        return U.usas_update_groups(first[name].get("groups") or [])

    def rebooting_node(name):
        node = first[name].get("node")
        return node if isinstance(node, str) and node and first[name].get("reboot") is True else ""

    def fits(wave, name):
        if len(wave) >= size:
            return False
        if gmax and any(sum(g in groups_of(o) for o in wave) >= gmax for g in groups_of(name)):
            return False
        node = rebooting_node(name)
        return not (nmax and node and sum(rebooting_node(o) == node for o in wave) >= nmax)

    for number, wave in enumerate(waves):
        assert wave and len(wave) <= size, (wave, size)
        if gmax:
            for g in {g for n in wave for g in groups_of(n)}:
                assert sum(g in groups_of(n) for n in wave) <= gmax, (g, wave)
        if nmax:
            for node in {rebooting_node(n) for n in wave} - {""}:
                assert sum(rebooting_node(n) == node for n in wave) <= nmax, (node, wave)
        for name in wave:
            assert U.usas_wave_number(waves, name) == number + 1
            # More servers in an earlier wave only take room: if it didn't fit then, it doesn't fit now.
            assert not any(fits(earlier, name) for earlier in waves[:number]), (name, waves)
    assert U.usas_wave_number(waves, "not-planned\x00") == 0


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    check_plan(fdp)
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
