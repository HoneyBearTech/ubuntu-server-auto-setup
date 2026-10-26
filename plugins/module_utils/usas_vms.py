"""The cluster's VMs: parsing what Proxmox and the guest agents report, picking each VM's IP address and
saying whether the playbooks support it.

One parser for everything that reads the cluster's VMs: the web UI's VMs page loads this file
(webui/usas_web/vms.py), and so can the playbooks' own plugins, so the two can't drift apart. Everything here is
pure Python from the standard library (no network, no FastAPI, no Ansible), so the tests and the fuzzer can call
it directly. What a guest agent reports comes from inside the guest, which may be hostile: it is display-only,
shortened, and never decides anything on its own (docs/security.md).
"""

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any

# What Proxmox accepts as a VM ID, and node names as the roles check them (provision_hostname_pattern). Both
# go into API paths, so anything else is dropped rather than sent back to the API.
VMID_PATTERN = re.compile(r"^[0-9]{3,9}\Z")
NODE_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
HOSTNAME_PATTERN = NODE_PATTERN
# Interfaces whose addresses never reach the VM from outside: loopback and container or VM bridges.
SKIP_INTERFACES = re.compile(r"^(lo|docker\d*|veth.*|br-.*|virbr\d*.*|cni\d*|flannel\..*|cali.*|vxlan\..*|tailscale\d*|"
                             r"wg\d*|lxcbr\d*|lxdbr\d*|podman\d*|kube-.*)\Z")
OURS_DESCRIPTION = "Installed by ubuntu-server-auto-setup"
SUPPORTED_UBUNTU = ("26.04", "24.04")
MAX_TEXT = 120  # longest guest-reported string kept
MAX_ADDRESSES = 16  # addresses kept per VM
MAX_TAGS = 16
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")

# Where an address came from, in priority order (design: guest agent, inventory, cloud-init).
SOURCES = ("agent", "inventory", "ipconfig0")
THIS_NETWORK = ipaddress.IPv4Network("0.0.0.0/8")
BROADCAST = ipaddress.IPv4Address("255.255.255.255")


def text(value: Any, limit: int = MAX_TEXT) -> str:
    """A string from Proxmox or a guest, made safe to show: control and direction characters removed,
    whitespace collapsed, and shortened to limit characters."""
    if value is None:
        return ""
    cleaned = " ".join(CONTROL.sub(" ", str(value)).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _number(value: Any, kind: type = float) -> float | int:
    try:
        number = kind(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number == number and number >= 0 else 0  # NaN and negative numbers count as 0


@dataclass
class Vm:
    """One entry of /cluster/resources?type=vm (a QEMU VM or an LXC container)."""

    vmid: int
    name: str
    node: str
    type: str  # "qemu" or "lxc"
    status: str  # "running", "stopped", "paused", ... (shown, not trusted)
    template: bool = False
    lock: str = ""
    tags: list[str] = field(default_factory=list)
    cpu: float = 0.0  # share of maxcpu in use, 0 to 1
    maxcpu: int = 0
    mem: int = 0
    maxmem: int = 0
    uptime: int = 0


def parse_tags(value: Any) -> list[str]:
    """Proxmox tags ("ubuntu;ansible", or separated by commas or spaces)."""
    if not value:
        return []
    found = [text(t, 64) for t in re.split(r"[;, ]+", str(value)) if t.strip()]
    return [t for t in dict.fromkeys(found) if t][:MAX_TAGS]


def parse_resources(data: Any) -> list[Vm]:
    """The VMs and containers of /cluster/resources?type=vm, sorted by VM ID. Entries with an ID or a node
    name that isn't valid, or another type, are left out."""
    vms: dict[int, Vm] = {}
    if not isinstance(data, list):
        return []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        vmid, node = str(entry.get("vmid", "")), str(entry.get("node", ""))
        if kind not in ("qemu", "lxc") or not VMID_PATTERN.match(vmid) or not NODE_PATTERN.match(node):
            continue
        cpu = min(float(_number(entry.get("cpu"))), 1.0)
        vms[int(vmid)] = Vm(
            vmid=int(vmid), name=text(entry.get("name"), 64), node=node, type=str(kind),
            status=text(entry.get("status"), 20) or "unknown", template=bool(_number(entry.get("template"), int)),
            lock=text(entry.get("lock"), 32), tags=parse_tags(entry.get("tags")), cpu=cpu,
            maxcpu=int(_number(entry.get("maxcpu"), int)), mem=int(_number(entry.get("mem"), int)),
            maxmem=int(_number(entry.get("maxmem"), int)), uptime=int(_number(entry.get("uptime"), int)))
    return [vms[k] for k in sorted(vms)]


@dataclass
class VmConfig:
    """What the page needs from /nodes/<node>/qemu/<vmid>/config."""

    agent: bool = False
    ostype: str = ""
    ipconfig0: str = ""  # the static IPv4 address cloud-init sets, if any
    ours: bool = False  # created by this project's provisioning


def _agent_enabled(value: Any) -> bool:
    """Proxmox's agent option: "1", "0", or "enabled=1,fstrim_cloned_disks=1" (the first item may be bare)."""
    if value is None:
        return False
    for i, item in enumerate(str(value).split(",")):
        key, sep, val = item.partition("=")
        if not sep and i == 0:
            return key.strip() == "1"
        if key.strip() == "enabled":
            return val.strip() == "1"
    return False


def parse_ipconfig(value: Any) -> str:
    """The IPv4 address of a cloud-init ipconfig0 ("ip=10.0.0.5/24,gw=10.0.0.1"); "" for dhcp or none."""
    for item in str(value or "").split(","):
        key, _, val = item.partition("=")
        if key.strip() == "ip":
            address = val.strip().split("/", 1)[0]
            return address if usable_ipv4(address) else ""
    return ""


def parse_config(data: Any) -> VmConfig:
    if not isinstance(data, dict):
        return VmConfig()
    description = str(data.get("description") or "")
    return VmConfig(agent=_agent_enabled(data.get("agent")), ostype=text(data.get("ostype"), 16),
                    ipconfig0=parse_ipconfig(data.get("ipconfig0")), ours=description.startswith(OURS_DESCRIPTION))


def usable_ipv4(value: str) -> bool:
    """An IPv4 address a server could be reached on: not loopback, link-local, multicast, unspecified or
    reserved (the special-use ranges)."""
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return not (address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved
                or address in THIS_NETWORK or address == BROADCAST)


def subnet(gateway: Any, prefix: Any) -> ipaddress.IPv4Network | None:
    """The servers' subnet from net_gateway and net_prefix, if both are set."""
    try:
        return ipaddress.IPv4Network(f"{gateway}/{int(prefix)}", strict=False)
    except (TypeError, ValueError):
        return None


def parse_interfaces(result: Any, network: ipaddress.IPv4Network | None = None) -> list[str]:
    """The usable IPv4 addresses of an agent's network-get-interfaces result, those in network first; container
    and loopback interfaces are skipped."""
    found: list[str] = []
    if not isinstance(result, list):
        return []
    for iface in result:
        if len(found) >= MAX_ADDRESSES:
            break
        if not isinstance(iface, dict) or SKIP_INTERFACES.match(str(iface.get("name", ""))):
            continue
        addresses = iface.get("ip-addresses")
        for entry in addresses if isinstance(addresses, list) else []:
            if not isinstance(entry, dict) or entry.get("ip-address-type") != "ipv4":
                continue
            address = str(entry.get("ip-address", ""))
            if usable_ipv4(address) and address not in found:
                found.append(address)
            if len(found) >= MAX_ADDRESSES:
                break
    if network is not None:
        found.sort(key=lambda a: ipaddress.IPv4Address(a) not in network)
    return found


@dataclass
class OsInfo:
    """An agent's get-osinfo result: the guest's own claim about its OS."""

    id: str = ""  # "ubuntu", "debian", "mswindows", ...
    version_id: str = ""  # "26.04"
    pretty: str = ""  # "Ubuntu 26.04 LTS"


def parse_osinfo(result: Any) -> OsInfo | None:
    if not isinstance(result, dict):
        return None
    info = OsInfo(id=text(result.get("id"), 32).lower(), version_id=text(result.get("version-id"), 16),
                  pretty=text(result.get("pretty-name") or result.get("name")))
    return info if info.id or info.pretty else None


def parse_hostname(result: Any) -> str:
    name = text(result.get("host-name"), 64) if isinstance(result, dict) else ""
    return name if HOSTNAME_PATTERN.match(name) else ""


def pick_ip(agent: list[str], inventory: str = "", ipconfig0: str = "") -> tuple[str, str | None]:
    """The address to show and where it came from: the guest agent's first, then the inventory's, then
    cloud-init's; ("", None) when none is known."""
    for address, source in ((agent[0] if agent else "", "agent"), (inventory, "inventory"), (ipconfig0, "ipconfig0")):
        if address and usable_ipv4(address):
            return address, source
    return "", None


# How the playbooks would treat a VM. Nothing can be selected yet; later this gates the selection.
SUPPORTED, NEEDS_OVERRIDE, UNKNOWN, UNSUPPORTED = "supported", "override", "unknown", "unsupported"


def classify(vm: Vm, config: VmConfig | None, os: OsInfo | None) -> tuple[str, str]:
    """(support, reason). The guest reports its own OS, so "supported" is advice, not proof."""
    if vm.type == "lxc":
        return UNSUPPORTED, "A container (LXC), not a VM: the playbooks set up whole servers."
    if vm.template:
        return UNSUPPORTED, "A template: clone it into a VM first."
    ostype = config.ostype if config else ""
    if ostype.startswith("win") or (os and os.id == "mswindows"):
        return UNSUPPORTED, "Windows: the playbooks set up Ubuntu Server only."
    if os is None:
        if ostype in ("solaris", "other"):
            return UNSUPPORTED, f"Proxmox says the OS type is {ostype}: the playbooks set up Ubuntu Server only."
        if vm.status != "running":
            return UNKNOWN, "Not running, so its guest agent can't report the OS: confirm it is Ubuntu."
        return UNKNOWN,"The guest agent didn't report the OS: confirm it is Ubuntu before running a playbook."
    name = os.pretty or os.id
    if os.id != "ubuntu":
        return UNSUPPORTED, f"{name} (reported by the guest): the playbooks set up Ubuntu Server only."
    if os.version_id in SUPPORTED_UBUNTU:
        return SUPPORTED, f"{name} (reported by the guest)."
    return NEEDS_OVERRIDE, f"{name} (reported by the guest): only {' and '.join(SUPPORTED_UBUNTU)} are tested."


# --- What a run calls a VM, and the name its host keys are pinned under -----------------------------------------
# A run reaches a VM either as the static inventory's host with the same pve_vmid (its own name, address and
# credentials) or as vm-<vmid>: never by its Proxmox name, which whoever may edit the VM chooses and which could
# match a host_vars file. Its SSH host keys are pinned in known_hosts as usas-<cluster>-<vmid>, passed to ssh as
# HostKeyAlias, so a guest that reports another VM's address gets a host-key mismatch. The web UI's VMs page and
# the fleet updates' inventory plugin use these, so both name and pin a VM the same way.

FOREIGN_NAME = "vm-{vmid}"
CLUSTER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")
ALIAS_PATTERN = re.compile(r"^usas-[a-z0-9][a-z0-9-]{0,31}-[0-9]{3,9}\Z")
DEFAULT_CLUSTER = "pve"  # when the cluster's name isn't known (a single node, or a token without Sys.Audit)


def vm_alias(cluster: str, vmid: int) -> str:
    """The known_hosts name of a VM's pinned host keys: usas-<cluster>-<vmid>. cluster is the cluster's name
    (lowercased; "pve" when it is empty or not a plain name). Raises ValueError for a VM ID Proxmox wouldn't
    give."""
    name = str(cluster).strip().lower()
    if not CLUSTER_PATTERN.match(name):
        name = DEFAULT_CLUSTER
    alias = f"usas-{name}-{int(vmid)}"
    if not ALIAS_PATTERN.match(alias):
        raise ValueError(f"{vmid} isn't a VM ID")
    return alias


def host_name(vmid: int, servers: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    """The inventory name a run reaches a VM as, and the static host it is (by pve_vmid), if any."""
    for server in servers:
        if str(server.get("pve_vmid") or "") == str(int(vmid)):
            return str(server["name"]), server
    return FOREIGN_NAME.format(vmid=int(vmid)), None


def control_path(vmid: int) -> str:
    """ansible_control_path for a VM: its own SSH master connection. Without it, a master opened for another VM at
    the same address, port and user would be reused, and this VM's host key never checked. %C already hashes the
    address, port and user; the VM ID keeps the socket path short."""
    return f"%(directory)s/v{int(vmid)}-%%C"
