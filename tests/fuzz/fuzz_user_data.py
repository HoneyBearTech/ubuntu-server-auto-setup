#!/usr/bin/env python3
"""Fuzz target: the installer configuration (user-data and meta-data) built from inventory values.

Property: for any values the proxmox_vm role accepts, the rendered user-data is valid YAML whose fields hold
exactly those values (nothing can add or change a key), and the two shell commands it runs as root split
into exactly the expected words. With disk encryption, the passphrase is exactly the storage layout's password
and the content of the installer's key file, and appears nowhere else; the passphrases the role accepts are
printable ASCII without braces.
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


# Characters that could break YAML, the shell or a template, besides ordinary ones.
PASSPHRASE_ALPHABET = "aZ09 '\"#:-&*!%@`|>\\{}[],?\n\t\r\x00\x7f\u00e9"


# Every character the role accepts: printable ASCII but the braces.
PASSPHRASE_PRINTABLE = "".join(c for c in map(chr, range(0x20, 0x7F)) if c not in "{}")


def passphrase(fdp):
    """Mostly passphrases the role accepts (YAML, shell and template characters included), sometimes not."""
    choice = fdp.ConsumeIntInRange(0, 3)
    if choice == 0:
        return roles.text(fdp, 60)
    if choice == 1:
        return roles.from_alphabet(fdp, PASSPHRASE_ALPHABET, 40)
    alphabet = PASSPHRASE_PRINTABLE if choice == 2 else PASSPHRASE_ALPHABET[:22]
    return "".join(alphabet[fdp.ConsumeIntInRange(0, len(alphabet) - 1)] for _ in range(fdp.ConsumeIntInRange(10, 60)))


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    encrypt = fdp.ConsumeBool()
    disk_passphrase = passphrase(fdp)
    if encrypt:
        if not roles.matches(PATTERNS["provision_disk_passphrase_pattern"], disk_passphrase):
            return
        assert 12 <= len(disk_passphrase) <= 512, disk_passphrase
        assert all(" " <= c <= "~" and c not in "{}" for c in disk_passphrase), disk_passphrase
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
        "vm_disk_encryption": encrypt,
        "vm_disk_passphrase": disk_passphrase,
    }
    # The role refuses encryption with any other layout (roles/proxmox_vm/tasks/main.yml).
    if encrypt:
        values["autoinstall_storage_layout"] = "lvm"

    doc = yaml.safe_load(USER_DATA.render(values))
    assert sorted(doc) == (["autoinstall", "write_files"] if encrypt else ["autoinstall"]), doc
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
    if encrypt:
        layout["password"] = disk_passphrase
    assert ai["storage"] == {"layout": layout}
    assert ai["identity"] == {"hostname": host, "realname": values["admin_realname"], "username": user,
                              "password": values["provision_password_hash"]}
    assert ai["ssh"] == {"install-server": True, "allow-pw": False, "authorized-keys": values["provision_ssh_keys"]}
    # dracut's tpm2-tss module needs the tpm2 tool.
    missing = encrypt and "tpm2-tools" not in values["autoinstall_packages"]
    assert ai["packages"] == values["autoinstall_packages"] + (["tpm2-tools"] if missing else [])
    assert ai["updates"] == values["autoinstall_updates"]
    assert ai["shutdown"] == "poweroff"
    sudoers = f"/target/etc/sudoers.d/90-ansible-{user}"
    first, second, *rest = ai["late-commands"]
    assert shlex.split(first) == ["echo", f"{user} ALL=(ALL:ALL) NOPASSWD: ALL", ">", sudoers], first
    assert shlex.split(second) == ["chmod", "0440", sudoers], second
    assert rest == (["curtin in-target -- sh /run/usas/enroll-tpm2.sh", "rm -rf /run/usas"] if encrypt else []), rest
    if encrypt:
        key_file, script = doc["write_files"]
        assert key_file == {"path": "/run/usas/disk-key", "permissions": "0600", "content": disk_passphrase}
        assert script["path"] == "/run/usas/enroll-tpm2.sh" and script["permissions"] == "0700", script
        assert script["content"] == ENROLL_SCRIPT, script

    meta = yaml.safe_load(META_DATA.render(values))
    assert meta == {"instance-id": f"{host}-{values['pve_vmid']}", "local-hostname": host}, meta


# The enrollment script holds no inventory value: it is the same for every input.
ENROLL_SCRIPT = yaml.safe_load(USER_DATA.render({
    "admin_user": "a", "inventory_hostname_short": "h", "ansible_host": "10.0.0.2", "net_gateway": "10.0.0.1",
    "net_prefix": 24, "net_dns": [], "net_search": [], "net_interface_match": "en*", "autoinstall_locale": "",
    "autoinstall_keyboard_layout": "", "autoinstall_source_id": "", "autoinstall_storage_layout": "lvm",
    "admin_realname": "", "provision_password_hash": "", "provision_ssh_keys": [], "autoinstall_packages": [],
    "autoinstall_updates": "", "vm_disk_encryption": True, "vm_disk_passphrase": "p" * 12}))["write_files"][1]["content"]


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
