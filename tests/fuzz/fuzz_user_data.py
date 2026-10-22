#!/usr/bin/env python3
"""Fuzz target: the installer configuration (user-data and meta-data) and the VM's network cards, built from
inventory values.

Property: for any values the proxmox_vm role's checks accept (its own assert conditions, read from
roles/proxmox_vm/tasks/main.yml), the rendered user-data is valid YAML whose fields hold exactly those values
(nothing can add or change a key), the two shell commands it runs as root split into exactly the expected
words, each network card's Proxmox setting holds exactly the given options (a value can't add another), the
installed system matches every card by its MAC address, and exactly one card has the default route.
"""

import re
import shlex
import sys

import atheris

with atheris.instrument_imports():
    import yaml

    import roles

TASKS = "roles/proxmox_vm/tasks/main.yml"
USER_DATA = roles.template("proxmox_vm", "user-data.j2")
META_DATA = roles.template("proxmox_vm", "meta-data.j2")
PATTERNS = roles.role_vars("proxmox_vm")
REQUIRED = roles.assert_conditions(TASKS, "Check required variables")
FORMATS = roles.assert_conditions(TASKS, "Check the formats of values the installer uses")
NIC_SETTINGS = roles.assert_conditions(TASKS, "Check the network card settings")
EXTRA_NIC = roles.assert_conditions(TASKS, "Check the extra network cards")
ONE_GATEWAY = roles.assert_conditions(TASKS, "Check that exactly one network card has the default route")
UNIQUE_MACS = roles.assert_conditions(TASKS, "Check that no two network cards share a MAC address")
EXTRA_NICS = roles.set_fact_expression(TASKS, "provision_extra_nics")
EXTRA_NICS_VARS = roles.set_fact_raw_vars(TASKS, "provision_extra_nics")
NICS = roles.set_fact_expression(TASKS, "provision_nics", last=True)
NICS_VARS = roles.set_fact_raw_vars(TASKS, "provision_nics", last=True)
BRIDGE_CHARS = "vmbr019._-,= \n"
NUMBER_CHARS = "0123456789,= \n-"
NIC_KEYS = PATTERNS["provision_nic_keys"]


def bridge(fdp):
    if fdp.ConsumeIntInRange(0, 4) == 0:
        return roles.from_alphabet(fdp, BRIDGE_CHARS, 18)
    return "vmbr" + str(fdp.ConsumeIntInRange(0, 9999))


def number(fdp, low, high):
    """Mostly whole numbers around a range (as ints or strings), sometimes text, sometimes empty."""
    choice = fdp.ConsumeIntInRange(0, 9)
    if choice == 0:
        return ""
    if choice == 1:
        return roles.from_alphabet(fdp, NUMBER_CHARS, 6)
    value = fdp.ConsumeIntInRange(low - 2, high + 2)
    return value if fdp.ConsumeBool() else str(value)


def extra_nic(fdp):
    """Mostly cards the role accepts: the keys their mode needs, values mostly in range, now and then a wrong one."""
    nic = {"mode": fdp.PickValueInList(PATTERNS["provision_nic_modes"] * 3 + ["", "Static"])}
    if fdp.ConsumeIntInRange(0, 9) == 0:
        del nic["mode"]  # static by default
    static = nic.get("mode", "static") == "static"
    optional = {
        "bridge": (7, lambda: bridge(fdp)),
        "vlan": (5, lambda: number(fdp, 1, 4094)),
        "model": (3, lambda: fdp.PickValueInList(PATTERNS["provision_nic_models"] + ["virtio", "virtio,bridge=x"])),
        "mac": (3, lambda: roles.mac(fdp)),
        "mtu": (3, lambda: number(fdp, 576, 9216)),
        "address": (9 if static else 0, lambda: roles.ipv4(fdp)),
        "prefix": (9 if static else 0, lambda: number(fdp, 1, 32)),
        "gateway": (2 if static else 0, lambda: roles.ipv4(fdp)),
    }
    for key, (in_ten, make) in optional.items():
        if fdp.ConsumeIntInRange(0, 9) < max(in_ten, 1 if fdp.ConsumeIntInRange(0, 9) == 0 else 0):
            nic[key] = make()
    if fdp.ConsumeIntInRange(0, 15) == 0:
        nic[roles.from_alphabet(fdp, "vlan_tg", 8)] = "1"
    return nic


def proxmox_options(nic, setting):
    """A card's Proxmox setting, split into its options; each option must be there once."""
    first, *rest = setting.split(",")
    model, _, mac = first.partition("=")
    options = dict(option.split("=", 1) for option in rest)
    assert len(options) == len(rest), setting
    assert (model, mac) == (nic["model"], nic["mac"]), setting
    expected = {"bridge": nic["bridge"]}
    if str(nic["vlan"]):
        expected["tag"] = str(nic["vlan"])
    if str(nic["mtu"]):
        expected["mtu"] = str(nic["mtu"])
    assert options == expected, (setting, expected)


def expected_ethernet(nic, first, values):
    eth = {"match": {"macaddress": nic["mac"].lower()}, "dhcp4": nic["mode"] == "dhcp", "dhcp6": False}
    if nic["mode"] == "dhcp":
        eth["dhcp-identifier"] = "mac"
        if not first:
            eth["dhcp4-overrides"] = {"use-routes": False, "use-dns": False}
    elif nic["mode"] == "static":
        eth["addresses"] = [f"{nic['address']}/{nic['prefix']}"]
        if str(nic["gateway"]):
            eth["routes"] = [{"to": "default", "via": str(nic["gateway"])}]
    if first and (nic["mode"] == "static" or values["net_dns"] or values["net_search"]):
        eth["nameservers"] = {"addresses": values["net_dns"], "search": values["net_search"]}
    if str(nic["mtu"]):
        eth["mtu"] = int(nic["mtu"])
    if not first:
        eth["optional"] = True
    return eth


def network(fdp, values):
    """Fills in the network values; returns False if the role would refuse them."""
    values.update({
        "net_mode": fdp.PickValueInList(["static", "static", "dhcp", "DHCP"]),
        "pve_bridge": bridge(fdp),
        "pve_vlan": number(fdp, 1, 4094) if fdp.ConsumeBool() else "",
        "net_mac": roles.mac(fdp),
        "vm_nic_model": fdp.PickValueInList(PATTERNS["provision_nic_models"]),
        "net_dhcp_timeout": 300,
        "vm_extra_nics": [extra_nic(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))],
    })
    if values["net_mode"] == "dhcp" and fdp.ConsumeBool():
        del values["ansible_host"]
    if values["vm_extra_nics"] and fdp.ConsumeBool() or fdp.ConsumeIntInRange(0, 9) == 0:
        values["net_gateway"] = ""  # an extra card may have the default route instead
    context = {**PATTERNS, **values}
    if not (roles.accepted(REQUIRED, context) and roles.accepted(FORMATS, context)
            and roles.accepted(NIC_SETTINGS, context)):
        return False
    context["provision_extra_nics"] = EXTRA_NICS.render({**context, **roles.render(EXTRA_NICS_VARS, context)})
    for index, item in enumerate(context["provision_extra_nics"]):
        if not roles.accepted(EXTRA_NIC, {**context, "item": item, "provision_nic_index": index}):
            return False
    if not roles.accepted(ONE_GATEWAY, context):
        return False
    context["provision_nics"] = []
    primary = None
    for index in range(1 + len(context["provision_extra_nics"])):
        # Ansible renders a loop's vars for each item, in order, from the variables so far.
        step = {**context, "provision_nic_index": index}
        for name, raw in NICS_VARS.items():
            step[name] = roles.render(raw, step)
            if name == "provision_primary_nic":
                primary = step[name]
                step["item"] = ([primary] + context["provision_extra_nics"])[index]
        context["provision_nics"] = NICS.render(step)
    if not roles.accepted(UNIQUE_MACS, context):
        return False
    values["provision_nics"] = context["provision_nics"]
    values["_primary"] = primary
    return True


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    values = {
        "autoinstall_locale": roles.text(fdp),
        "autoinstall_keyboard_layout": roles.text(fdp),
        "autoinstall_source_id": roles.text(fdp),
        "net_dns": roles.text_list(fdp),
        "net_search": roles.text_list(fdp),
        "autoinstall_storage_layout": fdp.PickValueInList(["lvm", "direct", "zfs"]) if fdp.ConsumeBool() else roles.text(fdp),
        "admin_realname": roles.text(fdp, 60),
        "provision_password_hash": roles.text(fdp, 120),
        "provision_ssh_keys": roles.text_list(fdp, 3, 80),
        "autoinstall_packages": roles.text_list(fdp),
        "autoinstall_updates": roles.text(fdp),
        "admin_user": roles.username(fdp),
        "inventory_hostname": (host := roles.hostname(fdp)),
        "inventory_hostname_short": host,
        "ansible_host": roles.ipv4(fdp),
        "net_gateway": roles.ipv4(fdp),
        "net_prefix": fdp.ConsumeIntInRange(1, 32),
        "pve_vmid": fdp.ConsumeIntInRange(100, 999999999),
        # The values the required-variables check also looks at.
        "pve_api_host": "pve", "pve_api_token_id": "t", "pve_api_token_secret": "s", "pve_node": "pve",
        "github_user": "u", "admin_extra_ssh_keys": [], "admin_password": "p",
    }
    # The role refuses anything else before rendering (roles/proxmox_vm/tasks/main.yml).
    if not network(fdp, values):
        return
    user, nics = values["admin_user"], values["provision_nics"]

    # Every card: its Proxmox setting holds exactly its options, and the MAC addresses are fixed and distinct.
    assert [nic["key"] for nic in nics] == [f"net{i}" for i in range(len(nics))]
    for nic in nics:
        assert re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", nic["mac"]), nic
        proxmox_options(nic, nic["proxmox"])
    assert nics[0]["mac"].startswith("BC:24:11:") or nics[0]["mac"] == values["net_mac"].upper()

    doc = yaml.safe_load(USER_DATA.render(values))
    assert list(doc) == ["autoinstall"], doc
    ai = doc["autoinstall"]
    assert ai["locale"] == values["autoinstall_locale"]
    assert ai["keyboard"] == {"layout": values["autoinstall_keyboard_layout"]}
    assert ai["source"] == {"id": values["autoinstall_source_id"]}
    assert ai["network"]["version"] == 2
    ethernets = ai["network"]["ethernets"]
    names = ["primary"] + [nic["key"] for nic in nics[1:]]
    assert sorted(ethernets) == sorted(names), ethernets
    for i, (nic, name) in enumerate(zip(nics, names, strict=True)):
        assert ethernets[name] == expected_ethernet(nic, i == 0, values), (ethernets[name], nic)
    if values["net_mode"] == "static":
        assert ethernets["primary"]["addresses"] == [f"{values['ansible_host']}/{values['net_prefix']}"]
    routes = [r for eth in ethernets.values() for r in eth.get("routes", []) if r["to"] == "default"]
    assert len(routes) + (values["net_mode"] == "dhcp") == 1, ethernets
    layout = {"name": values["autoinstall_storage_layout"]}
    if values["autoinstall_storage_layout"] == "lvm":
        layout["sizing-policy"] = "all"
    assert ai["storage"] == {"layout": layout}
    assert ai["identity"] == {"hostname": host, "realname": values["admin_realname"], "username": user,
                              "password": values["provision_password_hash"]}
    assert ai["ssh"] == {"install-server": True, "allow-pw": False, "authorized-keys": values["provision_ssh_keys"]}
    packages = values["autoinstall_packages"]
    if values["net_mode"] == "dhcp":  # the address is read from the guest agent
        packages = list(dict.fromkeys(packages + ["qemu-guest-agent"]))
    assert ai["packages"] == packages
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
