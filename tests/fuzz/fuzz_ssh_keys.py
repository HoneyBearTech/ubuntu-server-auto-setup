#!/usr/bin/env python3
"""Fuzz target: turning the GitHub keys file and admin_extra_ssh_keys into the list of authorized keys.

Property: for any keys file and extra keys, both roles produce the same list, every entry is a single,
trimmed line that starts with a public-key type, and every entry is a line of the input.
"""

import sys

import atheris

with atheris.instrument_imports():
    import roles

PROVISION = roles.set_fact_expression("roles/proxmox_vm/tasks/provision.yml", "provision_ssh_keys")
COMMON = roles.set_fact_expression("roles/common/tasks/ssh_keys.yml", "common_ssh_keys")
PREFIXES = ("ssh-", "ecdsa-", "sk-")


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    content = roles.text(fdp, 300)
    extras = roles.text_list(fdp, 3, 80)

    keys = PROVISION.render(pve_github_keys={"content": content}, admin_extra_ssh_keys=extras)
    assert keys == COMMON.render(common_github_keys={"content": content}, admin_extra_ssh_keys=extras)
    assert isinstance(keys, list), keys
    lines = {line.strip() for line in (content + "\n" + "\n".join(extras)).splitlines()}
    for key in keys:
        assert isinstance(key, str) and key.startswith(PREFIXES), key
        assert key.splitlines() == [key] and key == key.strip(), repr(key)
        assert key in lines, repr(key)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
