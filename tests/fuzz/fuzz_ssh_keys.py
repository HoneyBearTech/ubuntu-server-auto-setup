#!/usr/bin/env python3
"""Fuzz target: turning the GitHub keys file and admin_extra_ssh_keys into the list of authorized keys, and
proxmox_node_user_ssh_keys into the node user's.

Property: for any keys file and extra keys, all three roles produce the same list, every entry is a single,
trimmed line that starts with a public-key type, every entry is a line of the input, no key (type and key
material) is in the list twice, and no key of the input is left out.
"""

import sys

import atheris

with atheris.instrument_imports():
    import roles

PROVISION = roles.set_fact_expression("roles/proxmox_vm/tasks/provision.yml", "provision_ssh_keys")
PROVISION_VARS = roles.set_fact_vars("roles/proxmox_vm/tasks/provision.yml", "provision_ssh_keys")
PROVISION_LISTED = PROVISION_VARS["provision_ssh_keys_listed"]
COMMON = roles.set_fact_expression("roles/common/tasks/ssh_keys.yml", "common_ssh_keys")
COMMON_LISTED = roles.set_fact_vars("roles/common/tasks/ssh_keys.yml", "common_ssh_keys")["common_ssh_keys_listed"]
NODE = roles.set_fact_expression("roles/proxmox_node_user/tasks/main.yml", "proxmox_node_user_keys")
NODE_LISTED = roles.set_fact_vars("roles/proxmox_node_user/tasks/main.yml",
                                  "proxmox_node_user_keys")["proxmox_node_user_keys_listed"]
PREFIXES = ("ssh-", "ecdsa-", "sk-")


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    content = roles.text(fdp, 300)
    extras = roles.text_list(fdp, 3, 80)

    listed = PROVISION_LISTED.render(pve_github_keys={"content": content}, admin_extra_ssh_keys=extras)
    keys = PROVISION.render(provision_ssh_keys_listed=listed)
    common_listed = COMMON_LISTED.render(common_github_keys={"content": content}, admin_extra_ssh_keys=extras)
    assert keys == COMMON.render(common_ssh_keys_listed=common_listed)
    node_listed = NODE_LISTED.render(proxmox_node_user_ssh_keys=[content, *extras])
    assert keys == NODE.render(proxmox_node_user_keys_listed=node_listed)
    assert isinstance(keys, list), keys
    lines = {line.strip() for line in (content + "\n" + "\n".join(extras)).splitlines()}
    for key in keys:
        assert isinstance(key, str) and key.startswith(PREFIXES), key
        assert key.splitlines() == [key] and key == key.strip(), repr(key)
        assert key in lines, repr(key)
    material = [" ".join(key.split()[:2]) for key in keys]
    assert len(material) == len(set(material)), keys
    wanted = {" ".join(line.split()[:2]) for line in lines if line.startswith(PREFIXES)}
    assert set(material) == wanted, (keys, wanted)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
