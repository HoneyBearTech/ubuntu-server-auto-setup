"""The VMs page's parsing and classification (vms.py): what Proxmox and the guest agents report, including
what a hostile guest could put there."""

import pytest
from usas_web import vms as v


def resource(**kw):
    entry = {"vmid": 101, "name": "web-01", "node": "pve-1", "type": "qemu", "status": "running", "cpu": 0.25,
             "maxcpu": 4, "mem": 1024, "maxmem": 4096, "uptime": 60, "tags": "ubuntu;ansible"}
    entry.update(kw)
    return entry


def test_parse_resources_keeps_vms_and_containers_sorted():
    found = v.parse_resources([resource(vmid=300, type="lxc", name="ct"), resource(),
                               {"type": "node", "node": "pve-1"}, resource(vmid=102, template=1)])
    assert [(x.vmid, x.type, x.template) for x in found] == \
        [(101, "qemu", False), (102, "qemu", True), (300, "lxc", False)]
    first = found[0]
    assert (first.name, first.node, first.cpu, first.maxcpu, first.mem, first.maxmem, first.uptime) == \
        ("web-01", "pve-1", 0.25, 4, 1024, 4096, 60)
    assert first.tags == ["ubuntu", "ansible"]


@pytest.mark.parametrize("entry", [
    resource(vmid="1; rm"), resource(vmid=12), resource(vmid=""), resource(node="../../etc"), resource(node=""),
    resource(type="storage"), "not a dict", None,
])
def test_parse_resources_drops_what_cant_go_into_a_path(entry):
    assert v.parse_resources([entry]) == []


def test_parse_resources_survives_garbage():
    assert v.parse_resources({"data": 1}) == []
    weird = v.parse_resources([resource(cpu="NaN", mem=-5, maxmem="x", uptime=None, maxcpu=1e400, cpu2=1, tags=7,
                                        status=None, name="a\x00b‮c" + "x" * 200)])[0]
    assert (weird.cpu, weird.mem, weird.maxmem, weird.uptime, weird.maxcpu) == (0, 0, 0, 0, 0)
    assert weird.status == "unknown" and weird.tags == ["7"]
    assert "\x00" not in weird.name and "‮" not in weird.name and len(weird.name) == 64
    assert v.parse_resources([resource(cpu=7.5)])[0].cpu == 1.0


def test_text():
    assert v.text(None) == ""
    assert v.text("  a\tb\n\x1b[31m c ") == "a b [31m c"
    assert v.text("x" * 10, 5) == "xxxx…"


@pytest.mark.parametrize("value,tags", [
    ("ubuntu;ansible", ["ubuntu", "ansible"]), ("a, b c;;a", ["a", "b", "c"]), ("", []), (None, []),
    (";".join(str(i) for i in range(40)), [str(i) for i in range(16)]),
])
def test_parse_tags(value, tags):
    assert v.parse_tags(value) == tags


@pytest.mark.parametrize("agent,enabled", [
    ("1", True), ("0", False), ("enabled=1,fstrim_cloned_disks=1", True), ("1,type=virtio", True),
    ("enabled=0", False), ("fstrim_cloned_disks=1", False), (None, False), (1, True),
])
def test_agent_option(agent, enabled):
    assert v.parse_config({"agent": agent}).agent is enabled


@pytest.mark.parametrize("ipconfig,ip", [
    ("ip=10.0.0.5/24,gw=10.0.0.1", "10.0.0.5"), ("gw=10.0.0.1,ip=10.0.0.6/24", "10.0.0.6"), ("ip=dhcp", ""),
    ("ip6=auto", ""), ("ip=127.0.0.1/8", ""), ("ip=10.0.0.300/24", ""), (None, ""),
])
def test_ipconfig(ipconfig, ip):
    assert v.parse_ipconfig(ipconfig) == ip


def test_parse_config():
    config = v.parse_config({"agent": "1", "ostype": "l26", "ipconfig0": "ip=10.0.0.5/24",
                             "description": "Installed by ubuntu-server-auto-setup (Ubuntu 26.04.1)"})
    assert (config.agent, config.ostype, config.ipconfig0, config.ours) == (True, "l26", "10.0.0.5", True)
    assert v.parse_config("nope") == v.VmConfig()
    assert v.parse_config({"description": "not Installed by ubuntu-server-auto-setup"}).ours is False


@pytest.mark.parametrize("address,usable", [
    ("10.0.0.5", True), ("192.168.1.20", True), ("127.0.0.1", False), ("169.254.3.4", False), ("224.0.0.1", False),
    ("0.0.0.0", False), ("0.1.2.3", False), ("255.255.255.255", False), ("240.0.0.1", False), ("10.0.0.300", False),
    ("::1", False), ("10.0.0.5; rm -rf /", False), ("", False),
])
def test_usable_ipv4(address, usable):
    assert v.usable_ipv4(address) is usable


def iface(name, *addresses, kind="ipv4"):
    return {"name": name, "ip-addresses": [{"ip-address": a, "ip-address-type": kind} for a in addresses]}


def test_parse_interfaces_prefers_the_servers_subnet():
    result = [iface("lo", "127.0.0.1"), iface("docker0", "172.17.0.1"), iface("veth12ab", "172.18.0.2"),
              iface("br-1234", "172.19.0.1"), iface("eth1", "192.168.50.21"), iface("eth0", "10.10.10.21"),
              iface("eth0", "fd00::1", kind="ipv6"), iface("eth2", "192.168.50.21")]
    assert v.parse_interfaces(result) == ["192.168.50.21", "10.10.10.21"]
    assert v.parse_interfaces(result, v.subnet("10.10.10.1", 24)) == ["10.10.10.21", "192.168.50.21"]


def test_parse_interfaces_survives_a_hostile_guest():
    result = [iface("eth0", "999.1.1.1", "10.0.0.1; rm -rf /", "169.254.1.1", "10.0.0.9"), "x", {"name": "a"},
              {"name": "b", "ip-addresses": "nope"}, {"name": "c", "ip-addresses": [None, {"ip-address": 5}]},
              iface("eth1", *[f"10.1.0.{i}" for i in range(1, 60)])]
    found = v.parse_interfaces(result)
    assert found == ["10.0.0.9", *[f"10.1.0.{i}" for i in range(1, v.MAX_ADDRESSES)]]
    assert v.parse_interfaces(None) == [] and v.parse_interfaces({"x": 1}) == []


@pytest.mark.parametrize("gateway,prefix,network", [
    ("10.0.0.1", 24, "10.0.0.0/24"), ("10.0.0.1", "16", "10.0.0.0/16"), (None, 24, None), ("10.0.0.1", "x", None),
    ("", 24, None),
])
def test_subnet(gateway, prefix, network):
    found = v.subnet(gateway, prefix)
    assert (str(found) if found else None) == network


def test_parse_osinfo_and_hostname():
    info = v.parse_osinfo({"id": "Ubuntu", "version-id": "26.04", "pretty-name": "Ubuntu 26.04 LTS"})
    assert (info.id, info.version_id, info.pretty) == ("ubuntu", "26.04", "Ubuntu 26.04 LTS")
    assert v.parse_osinfo({"name": "Something"}).pretty == "Something"
    assert v.parse_osinfo({}) is None and v.parse_osinfo([1]) is None
    hostile = v.parse_osinfo({"id": "ubuntu", "pretty-name": "<script>‮\x1b" + "A" * 500})
    assert len(hostile.pretty) == v.MAX_TEXT and "‮" not in hostile.pretty
    assert v.parse_hostname({"host-name": "web-01"}) == "web-01"
    assert v.parse_hostname({"host-name": "bad_host!"}) == "" and v.parse_hostname("x") == ""


@pytest.mark.parametrize("agent,inventory,ipconfig,expected", [
    (["10.0.0.5", "10.0.0.6"], "10.0.0.7", "10.0.0.8", ("10.0.0.5", "agent")),
    ([], "10.0.0.7", "10.0.0.8", ("10.0.0.7", "inventory")),
    ([], "", "10.0.0.8", ("10.0.0.8", "ipconfig0")),
    ([], "not an ip", "", ("", None)),
    ([], "", "", ("", None)),
])
def test_pick_ip(agent, inventory, ipconfig, expected):
    assert v.pick_ip(agent, inventory, ipconfig) == expected


def vm(**kw):
    return v.parse_resources([resource(**kw)])[0]


UBUNTU = v.OsInfo("ubuntu", "26.04", "Ubuntu 26.04 LTS")


@pytest.mark.parametrize("machine,config,os,support,words", [
    (vm(type="lxc"), None, None, v.UNSUPPORTED, "container"),
    (vm(template=1), v.VmConfig(), UBUNTU, v.UNSUPPORTED, "template"),
    (vm(), v.VmConfig(ostype="win11"), None, v.UNSUPPORTED, "Windows"),
    (vm(), v.VmConfig(), v.OsInfo("mswindows", "11", "Windows 11"), v.UNSUPPORTED, "Windows"),
    (vm(), v.VmConfig(ostype="other"), None, v.UNSUPPORTED, "OS type is other"),
    (vm(), v.VmConfig(ostype="l26"), None, v.UNKNOWN, "confirm"),
    (vm(status="stopped"), v.VmConfig(ostype="l26"), None, v.UNKNOWN, "Not running"),
    (vm(), None, None, v.UNKNOWN, "confirm"),
    (vm(), v.VmConfig(), v.OsInfo("debian", "13", "Debian GNU/Linux 13"), v.UNSUPPORTED, "Debian"),
    (vm(), v.VmConfig(), UBUNTU, v.SUPPORTED, "Ubuntu 26.04"),
    (vm(), v.VmConfig(), v.OsInfo("ubuntu", "24.04", ""), v.SUPPORTED, "ubuntu"),
    (vm(), v.VmConfig(), v.OsInfo("ubuntu", "22.04", "Ubuntu 22.04.5 LTS"), v.NEEDS_OVERRIDE, "only 26.04 and 24.04"),
])
def test_classify(machine, config, os, support, words):
    found, reason = v.classify(machine, config, os)
    assert found == support and words in reason


def test_the_page_uses_the_playbooks_parser():
    """vms.py offers the names of plugins/module_utils/usas_vms.py itself, not a copy, and nothing it imported."""
    import sys

    shared = sys.modules["usas_vms"]
    assert v.SHARED.name == "usas_vms.py" and v.SHARED.parent.name == "module_utils"
    assert v.parse_resources is shared.parse_resources and v.Vm is shared.Vm and v.VMID_PATTERN is shared.VMID_PATTERN
    assert "parse_resources" in v.__all__
    assert not {"re", "ipaddress", "dataclass", "field", "Any", "_number"} & set(v.__all__)


def test_names_and_pins_for_runs():
    assert v.vm_alias("Home-Lab", 101) == "usas-home-lab-101" and v.ALIAS_PATTERN.match("usas-home-lab-101")
    assert v.vm_alias("", 4061) == v.vm_alias("not a name", 4061) == "usas-pve-4061"
    with pytest.raises(ValueError):
        v.vm_alias("lab", 12)
    servers = [{"name": "web-00", "pve_vmid": 4000}, {"name": "db-01", "pve_vmid": None}]
    assert v.host_name(4000, servers) == ("web-00", servers[0])
    assert v.host_name(101, servers) == ("vm-101", None)
    assert v.control_path(101) == "%(directory)s/v101-%%C"
