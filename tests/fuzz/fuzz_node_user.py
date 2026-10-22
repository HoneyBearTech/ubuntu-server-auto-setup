#!/usr/bin/env python3
"""Fuzz target: the Proxmox node's SSH user name, and the ISO folder a non-root user reads from the file
prepare-node.yml writes.

Properties: a user name passes the roles' pattern exactly when it passes the admin user's (common's), and one
that passes is a plain lowercase name that can't be read as an option, a path or a second ACL entry. Whatever
the recorded file holds, the folder taken is either that storage's entry, an absolute path without line breaks
or other control characters, or nothing (and the role then stops).
"""

import base64
import json
import re
import sys

import atheris

with atheris.instrument_imports():
    import roles

RELEASE = roles.role_vars("ubuntu_release")
COMMON = roles.role_vars("common")
ISO_DIR = "roles/ubuntu_release/tasks/iso_dir.yml"
TASK = "Use the folder recorded for the ISO storage"
PICK = roles.set_fact_expression(ISO_DIR, "pve_iso_dir", TASK)
PICK_VARS = roles.set_fact_vars(ISO_DIR, "pve_iso_dir", TASK)
PATH_CHARS = "/az09._- \n\t\x00\x7f:"


def user(fdp):
    name = roles.username(fdp)
    accepted = roles.matches(RELEASE["pve_ssh_user_pattern"], name)
    assert accepted == roles.matches(COMMON["common_username_pattern"], name), repr(name)
    if accepted:
        assert re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", name), repr(name)


def value(fdp):
    kind = fdp.ConsumeIntInRange(0, 4)
    if kind == 0:
        return fdp.ConsumeInt(4)
    if kind == 1:
        return None
    if kind == 2:
        return [roles.text(fdp, 10)]
    path = roles.from_alphabet(fdp, PATH_CHARS, 30)
    return "/" + path if kind == 4 else path  # mostly absolute, as pvesm gives them


def iso_dir(fdp):
    recorded = {roles.from_alphabet(fdp, "ab-", 4): value(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))}
    names = sorted(recorded)
    # Mostly a storage the file has an entry for.
    storage = names[fdp.ConsumeIntInRange(0, len(names) - 1)] if names and fdp.ConsumeBool() else "local"
    shape = fdp.ConsumeIntInRange(0, 3)
    if shape == 0:
        slurp = {}  # the file doesn't exist
    elif shape == 1:
        slurp = {"content": base64.b64encode(json.dumps([recorded]).encode()).decode()}  # not an object
    else:
        slurp = {"content": base64.b64encode(json.dumps(recorded).encode()).decode()}
    dirs = PICK_VARS["ubuntu_release_dirs"].render(ubuntu_release_iso_dirs=slurp)
    chosen = PICK_VARS["ubuntu_release_recorded"].render(ubuntu_release_dirs=dirs, pve_iso_storage=storage)
    picked = PICK.render(ubuntu_release_recorded=chosen, pve_iso_dir_pattern=RELEASE["pve_iso_dir_pattern"])
    assert isinstance(picked, str), repr(picked)
    if picked:
        assert shape >= 2 and picked == recorded.get(storage), (picked, recorded, storage)
        assert picked.startswith("/") and not re.search(r"[\x00-\x1f\x7f]", picked), repr(picked)
    else:
        good = isinstance(recorded.get(storage), str) and re.fullmatch(r"/[^\x00-\x1f\x7f]*", recorded[storage])
        assert shape < 2 or not good, (recorded, storage)


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    user(fdp)
    iso_dir(fdp)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
