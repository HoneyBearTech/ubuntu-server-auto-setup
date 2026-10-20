#!/usr/bin/env python3
"""Fuzz target: the installer configuration (user-data and meta-data) built from inventory values.

Property: for any values the proxmox_vm role accepts, the rendered user-data is valid YAML whose fields hold
exactly those values (nothing can add or change a key), and the two shell commands it runs as root split
into exactly the expected words.
"""

import shlex
import sys

import atheris

with atheris.instrument_imports():
    import yaml

    import roles

USER_DATA = roles.template("proxmox_vm", "user-data.j2")
META_DATA = roles.template("proxmox_vm", "meta-data.j2")
PATTERNS = roles.role_vars("proxmox_vm")


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    user = roles.username(fdp)
    host = roles.hostname(fdp)
    ip = roles.ipv4(fdp)
    gateway = roles.ipv4(fdp)
    prefix = fdp.ConsumeIntInRange(1, 32)
    # The role refuses anything else before rendering (roles/proxmox_vm/tasks/main.yml).
    if not (roles.matches(PATTERNS["provision_username_pattern"], user)
            and roles.matches(PATTERNS["provision_hostname_pattern"], host)
            and roles.matches(PATTERNS["provision_ipv4_pattern"], ip)
            and roles.matches(PATTERNS["provision_ipv4_pattern"], gateway)):
        return

    values = {
        "autoinstall_locale": roles.text(fdp),
        "autoinstall_keyboard_layout": roles.text(fdp),
        "autoinstall_source_id": roles.text(fdp),
        "net_interface_match": roles.text(fdp),
        "net_dns": roles.text_list(fdp),
        "net_search": roles.text_list(fdp),
        "autoinstall_storage_layout": fdp.PickValueInList(["lvm", "direct", "zfs"]) if fdp.ConsumeBool() else roles.text(fdp),
        "admin_realname": roles.text(fdp, 60),
        "provision_password_hash": roles.text(fdp, 120),
        "provision_ssh_keys": roles.text_list(fdp, 3, 80),
        "autoinstall_packages": roles.text_list(fdp),
        "autoinstall_updates": roles.text(fdp),
        "admin_user": user,
        "inventory_hostname": host,
        "inventory_hostname_short": host,
        "ansible_host": ip,
        "net_gateway": gateway,
        "net_prefix": prefix,
        "pve_vmid": fdp.ConsumeIntInRange(100, 999999999),
    }

    doc = yaml.safe_load(USER_DATA.render(values))
    assert list(doc) == ["autoinstall"], doc
    ai = doc["autoinstall"]
    assert ai["locale"] == values["autoinstall_locale"]
    assert ai["keyboard"] == {"layout": values["autoinstall_keyboard_layout"]}
    assert ai["source"] == {"id": values["autoinstall_source_id"]}
    eth = ai["network"]["ethernets"]["primary"]
    assert eth["match"] == {"name": values["net_interface_match"]}
    assert eth["addresses"] == [f"{ip}/{prefix}"]
    assert eth["routes"] == [{"to": "default", "via": gateway}]
    assert eth["nameservers"] == {"addresses": values["net_dns"], "search": values["net_search"]}
    layout = {"name": values["autoinstall_storage_layout"]}
    if values["autoinstall_storage_layout"] == "lvm":
        layout["sizing-policy"] = "all"
    assert ai["storage"] == {"layout": layout}
    assert ai["identity"] == {"hostname": host, "realname": values["admin_realname"], "username": user,
                              "password": values["provision_password_hash"]}
    assert ai["ssh"] == {"install-server": True, "allow-pw": False, "authorized-keys": values["provision_ssh_keys"]}
    assert ai["packages"] == values["autoinstall_packages"]
    assert ai["updates"] == values["autoinstall_updates"]
    assert ai["shutdown"] == "poweroff"
    sudoers = f"/target/etc/sudoers.d/90-ansible-{user}"
    first, second = ai["late-commands"]
    assert shlex.split(first) == ["echo", f"{user} ALL=(ALL:ALL) NOPASSWD: ALL", ">", sudoers], first
    assert shlex.split(second) == ["chmod", "0440", sudoers], second

    meta = yaml.safe_load(META_DATA.render(values))
    assert meta == {"instance-id": f"{host}-{values['pve_vmid']}", "local-hostname": host}, meta


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
