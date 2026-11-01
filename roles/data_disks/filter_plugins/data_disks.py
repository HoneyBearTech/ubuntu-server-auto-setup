"""Filters for the baseline's extra disks: what blkid found on each, and so whether it may be formatted.

tests/fuzz/fuzz_data_disks.py checks that a disk is only ever "blank" when blkid found nothing on it.
"""

DOCUMENTATION = """
name: data_disks
short_description: Read blkid's findings on the extra disks
description: See the module docstring and roles/data_disks/tasks/main.yml.
"""


def _text(value):
    return "" if value is None else str(value)


def data_disks_state(probes, disks):
    """Each disk with what `blkid -p -o export` found on it: {"label", "mount", "fs", "device", "state", "found"}.

    probes are the loop results of that command (one per disk, in the order of disks, "item" being the stat result
    whose "item" is the disk); disks the checked vm_extra_disks. state is "blank" when blkid found nothing (exit 2
    and no output), "ready" when it found the disk's own file system with its label, "other" for anything else,
    which is then described in found. A probe that didn't run (check mode, a skipped item) counts as "other".
    """
    states = []
    for probe, disk in zip(probes, disks, strict=False):
        stat = (probe.get("item") or {}).get("stat") or {}
        device = _text(stat.get("lnk_source") or stat.get("path"))
        fields = {}
        # One field per line, split at newlines only: splitlines() would also split a value at characters such
        # as \x1d or \x85, and read what follows as another field.
        for line in _text(probe.get("stdout")).split("\n"):
            key, sep, value = line.partition("=")
            if sep:
                fields[key.strip()] = value.strip()
        rc = probe.get("rc")
        label, fs = _text(disk.get("label")), _text(disk.get("fs"))
        if rc == 2 and not fields:
            state, found = "blank", "nothing"
        elif rc == 0 and fields.get("TYPE") == fs and fields.get("LABEL") == label and "PTTYPE" not in fields:
            state, found = "ready", f"its own file system (type {fs}, label {label})"
        else:
            state = "other"
            if "PTTYPE" in fields:
                found = f"a partition table (type {fields['PTTYPE']})"
            elif "TYPE" in fields:
                found = f"a file system (type {fields['TYPE']}, " + (f"label {fields['LABEL']})" if fields.get("LABEL")
                                                                     else "no label)")
            else:
                found = "something blkid couldn't read" if rc not in (0, 2) else "an unknown signature"
        states.append({"label": label, "mount": _text(disk.get("mount")), "fs": fs, "device": device,
                       "state": state, "found": found})
    return states


class FilterModule:
    def filters(self):
        return {"data_disks_state": data_disks_state}
