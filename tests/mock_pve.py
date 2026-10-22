#!/usr/bin/env python3
"""A small stand-in for the Proxmox VE API, for the integration tests.

It answers for a cluster of two nodes (NODES), each with its own ISO storages, and implements only the calls
that the proxmox_vm role and community.proxmox.proxmox_kvm make. It keeps VMs in memory, on the node each was
created on, and pretends to run the installer: after a VM's first start it reports "running" for a few status
polls, then "stopped" (the installer powering the VM off). VMs whose ID is in HANG_VMIDS never stop, to test
the role's failure path. Like Proxmox, a start request only starts a task: the VM's status stays "stopped"
until that task has been seen running and then finished.

Once an installed VM runs, its guest agent answers network-get-interfaces (after saying it isn't running
yet, as while the system boots): one interface per network card, with the card's MAC address. net0 of a VM
in DHCP_LEASES has that address; the others have only a link-local one, as with no DHCP server.

It also serves a public-keys file over plain HTTP, standing in for https://github.com/<user>.keys.

With --fixture-vms it also lists the fixed VMs in FIXTURE_VMS, for the web UI's VMs page: their configs and
their guest agents (network-get-interfaces, get-osinfo, get-host-name), including an agent that isn't
running, a slow one, one the token may not ask, Debian, Windows, a template, a container, and hostile
strings. Off by default, so the playbook tests see only the VMs they create.

Usage: mock_pve.py --cert CERT --key KEY --keys-file FILE [--port 8006] [--keys-port 8081] [--bind 127.0.0.1]
                   [--fixture-vms]
The state (VMs, config changes, requests) is at GET /mock/state, for the tests' assertions.
"""

import argparse
import json
import re
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "PVEAPIToken=ansible@pve!test=00000000-0000-4000-8000-000000000000"
# A cluster of two nodes. Each has the node-local "local" and the cluster's shared "synology-nfs" for ISO images;
# only the first also has "usb-iso", a node-local storage the second can't use. A VM lives on the node it was
# created on, and its calls must name that node, as in Proxmox.
NODES = ("pve-test", "pve-test-2")
NODE = NODES[0]  # where the fixture VMs live
NODE_STORAGES = {
    "pve-test": ("local", "local-lvm", "synology-nfs", "usb-iso"),
    "pve-test-2": ("local", "local-lvm", "synology-nfs"),
}
STORAGES = {
    "local": {"type": "dir", "content": "iso,vztmpl,backup", "shared": 0},
    "local-lvm": {"type": "lvmthin", "content": "images,rootdir", "shared": 0},
    "synology-nfs": {"type": "nfs", "content": "images,iso", "shared": 1},
    "usb-iso": {"type": "dir", "content": "iso", "shared": 0},
}
HANG_VMIDS = {4099}
POLLS_BEFORE_POWEROFF = 2
# The address DHCP "gives" net0. The test server is a container reached on this machine, hence 127.0.0.1.
DHCP_LEASES = {4063: "127.0.0.1"}
FORBIDDEN_AGENT_VMIDS = {4066}  # the token may not ask these VMs' agents (no VM.GuestAgent.Audit)
AGENT_POLLS_BEFORE_READY = 1

LOCK = threading.Lock()
STATE = {"vms": {}, "requests": []}
FIXTURES = {"enabled": False}
SLOW_AGENT_SECONDS = 7  # longer than the web UI waits for one agent call


def _iface(name, *addresses, kind="ipv4"):
    return {"name": name, "hardware-address": "bc:24:11:00:00:01",
            "ip-addresses": [{"ip-address": a, "ip-address-type": kind, "prefix": 24} for a in addresses]}


def _ubuntu(version, codename):
    return {"id": "ubuntu", "name": "Ubuntu", "version-id": version, "pretty-name": f"Ubuntu {version} LTS",
            "version": f"{version} LTS ({codename})", "kernel-release": "6.17.0-5-generic", "machine": "x86_64"}


HOSTILE = "<img src=x onerror=alert(1)>\u202e\x1b[31mUbuntu\x00 " + "A" * 400
# vmid -> the resource entry's extra fields, the config, and the agent: (behaviour, interfaces, osinfo, name).
FIXTURE_VMS = {
    101: {"name": "web-2604", "tags": "ansible;ubuntu", "cpu": 0.12, "maxcpu": 4, "mem": 1_610_612_736,
          "maxmem": 4_294_967_296, "uptime": 86_400,
          "config": {"agent": "1", "ostype": "l26", "description": "Installed by ubuntu-server-auto-setup (Ubuntu 26.04.1)"},
          "agent": ("ok", [_iface("lo", "127.0.0.1"), _iface("docker0", "172.17.0.1"), _iface("eth1", "192.168.50.21"),
                           _iface("eth0", "10.10.10.21", "fe80::1", kind="ipv4"), _iface("eth0", "fd00::21", kind="ipv6")],
                    _ubuntu("26.04", "Resolute Raccoon"), "web-2604")},
    102: {"name": "db-2404", "cpu": 0.53, "maxcpu": 2, "mem": 3_221_225_472, "maxmem": 4_294_967_296, "uptime": 3600,
          "config": {"agent": "enabled=1,fstrim_cloned_disks=1", "ostype": "l26"},
          "agent": ("ok", [_iface("ens18", "10.10.10.22")], _ubuntu("24.04", "Noble Numbat"), "db-2404")},
    103: {"name": "agent-stopped", "maxcpu": 2, "mem": 1_073_741_824, "maxmem": 2_147_483_648, "uptime": 120,
          "config": {"agent": "1", "ostype": "l26"}, "agent": ("not-running",)},
    104: {"name": "no-agent", "maxcpu": 1, "mem": 536_870_912, "maxmem": 1_073_741_824, "uptime": 5000,
          "config": {"ostype": "l26", "ipconfig0": "ip=10.10.10.24/24,gw=10.10.10.1"}},
    105: {"name": "debian-13", "maxcpu": 2, "mem": 805_306_368, "maxmem": 2_147_483_648, "uptime": 7200,
          "config": {"agent": "1", "ostype": "l26"},
          "agent": ("ok", [_iface("ens18", "10.10.10.25")],
                    {"id": "debian", "version-id": "13", "pretty-name": "Debian GNU/Linux 13 (trixie)"}, "debian-13")},
    106: {"name": "windows-11", "maxcpu": 4, "mem": 6_442_450_944, "maxmem": 8_589_934_592, "uptime": 600,
          "config": {"agent": "1", "ostype": "win11"},
          "agent": ("ok", [_iface("Ethernet", "10.10.10.26")],
                    {"id": "mswindows", "version-id": "11", "pretty-name": "Windows 11 Pro"}, "WIN-11")},
    107: {"name": "srv-stopped", "status": "stopped", "tags": "ubuntu;ansible", "maxcpu": 2, "maxmem": 2_147_483_648,
          "config": {"agent": "1", "ostype": "l26", "description": "Installed by ubuntu-server-auto-setup (Ubuntu 26.04.1)"}},
    108: {"name": "ubuntu-template", "status": "stopped", "template": 1, "maxcpu": 2, "maxmem": 2_147_483_648,
          "config": {"agent": "1", "ostype": "l26", "template": 1}},
    109: {"name": "Bad_Name<b>", "tags": "<script>;ok", "maxcpu": 1, "mem": 268_435_456, "maxmem": 536_870_912,
          "uptime": 10, "config": {"agent": "1", "ostype": "l26"},
          "agent": ("ok", [_iface("eth0\u202e", "999.1.1.1", "10.10.10.29; rm -rf /", "169.254.1.1", "224.0.0.1",
                                  "10.10.10.29")],
                    {"id": "ubuntu", "version-id": "26.04", "pretty-name": HOSTILE}, "bad_host!")},
    110: {"name": "slow-agent", "maxcpu": 1, "mem": 268_435_456, "maxmem": 1_073_741_824, "uptime": 50,
          "config": {"agent": "1", "ostype": "l26"}, "agent": ("slow", [_iface("ens18", "10.10.10.30")],
                                                               _ubuntu("26.04", "Resolute Raccoon"), "slow-agent")},
    111: {"name": "agent-forbidden", "maxcpu": 1, "mem": 268_435_456, "maxmem": 1_073_741_824, "uptime": 50,
          "config": {"agent": "1", "ostype": "l26"}, "agent": ("forbidden",)},
    112: {"name": "jammy", "maxcpu": 1, "mem": 268_435_456, "maxmem": 1_073_741_824, "uptime": 50,
          "config": {"agent": "1", "ostype": "l26"},
          "agent": ("ok", [_iface("ens18", "10.10.10.32")], _ubuntu("22.04", "Jammy Jellyfish"), "jammy")},
    200: {"name": "ct-dns", "type": "lxc", "maxcpu": 1, "mem": 67_108_864, "maxmem": 536_870_912, "uptime": 99_999},
}


def fixture_resources():
    data = []
    for vmid, vm in FIXTURE_VMS.items():
        kind = vm.get("type", "qemu")
        entry = {"vmid": vmid, "id": f"{kind}/{vmid}", "node": NODE, "type": kind,
                 "status": vm.get("status", "running")}
        entry.update({k: v for k, v in vm.items() if k not in ("config", "agent", "type", "status")})
        data.append(entry)
    return data


def _parse_bool(value):
    """A Proxmox boolean (it accepts 1/0, yes/no, on/off and true/false in any case)."""
    lowered = str(value).lower()
    if lowered in ("1", "yes", "on", "true"):
        return True
    if lowered in ("0", "no", "off", "false"):
        return False
    raise ValueError(f"invalid boolean value '{value}'")


def _special_disks(config, vmid):
    """Checks efidisk0 and tpmstate0 as Proxmox does and stores them the way it does: the allocated volume, then
    the options (booleans as 1 or 0). Returns an error message, or None."""
    disk_number = 0
    for key in ("efidisk0", "tpmstate0"):
        if key not in config:
            continue
        storage_size, *options = config[key].split(",")
        storage, _, size = storage_size.partition(":")
        opts = dict(option.partition("=")[::2] for option in options)
        opts.pop("format", None)
        if not storage or size != "1":
            return f"{key}: expected <storage>:1 for a new volume, got '{storage_size}'"
        if key == "efidisk0":
            if config.get("bios") != "ovmf":
                return "efidisk0: only for bios=ovmf"
            try:
                keys = _parse_bool(opts.get("pre-enrolled-keys", "0"))
            except ValueError as err:
                return f"efidisk0: pre-enrolled-keys: {err}"
            if keys and opts.get("efitype") != "4m":
                return "efidisk0: pre-enrolled-keys needs efitype=4m"
            opts["pre-enrolled-keys"] = "1" if keys else "0"
        elif opts.get("version") not in ("v1.2", "v2.0"):
            return f"tpmstate0: version must be v1.2 or v2.0, got '{opts.get('version')}'"
        volume = f"{storage}:vm-{vmid}-disk-{disk_number}"
        disk_number += 1
        opts["size"] = "4M"
        config[key] = ",".join([volume, *(f"{k}={v}" for k, v in sorted(opts.items()))])
    return None


def storage_entry(name):
    """A storage as /storage lists it: "nodes" names the nodes that have it, unless every node does."""
    entry = {"storage": name, **STORAGES[name]}
    nodes = [n for n in NODES if name in NODE_STORAGES[n]]
    if len(nodes) < len(NODES):
        entry["nodes"] = ",".join(nodes)
    return entry


def upid(action, vmid, node=NODE):
    return f"UPID:{node}:0000AAAA:00BBBBBB:6700CCCC:{action}:{vmid}:ansible@pve!test:"


class PveHandler(BaseHTTPRequestHandler):
    server_version = "pve-api-daemon/3.0"

    def log_message(self, fmt, *args):  # keep test output readable
        pass

    def _reply(self, status, data=None):
        body = json.dumps({"data": data} if status < 400 else {"errors": data}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        if self.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(raw or "{}")
        return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _route(self, method):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path == "/mock/state" and method == "GET":
            with LOCK:
                return self._reply(200, STATE)
        if not path.startswith("/api2/json/"):
            return self._reply(404, {"path": "not found"})
        if self.headers.get("Authorization") != TOKEN:
            return self._reply(401, {"auth": "invalid token"})
        path = path[len("/api2/json") :]
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        form = self._form() if method in ("POST", "PUT") else {}
        with LOCK:
            STATE["requests"].append({"method": method, "path": path, "query": query, "form": form})
        fixture = re.fullmatch(rf"/nodes/{NODE}/qemu/(\d+)/(config|agent/[a-z-]+)", path)
        if FIXTURES["enabled"] and method == "GET" and fixture and int(fixture.group(1)) in FIXTURE_VMS:
            return self._fixture(FIXTURE_VMS[int(fixture.group(1))], fixture.group(2))
        with LOCK:
            return self._dispatch(method, path, query, form)

    def _fixture(self, vm, what):
        """A fixture VM's config or one of its agent's answers, as Proxmox gives them."""
        if what == "config":
            return self._reply(200, {"name": vm["name"], **vm.get("config", {})})
        agent = vm.get("agent")
        if not vm.get("config", {}).get("agent") or agent is None and vm.get("status") == "stopped":
            return self._message(500, "No QEMU guest agent configured" if not vm.get("config", {}).get("agent")
                                 else f"VM {vm['name']} is not running")
        behaviour = agent[0] if agent else "not-running"
        if behaviour == "not-running":
            return self._message(500, "QEMU guest agent is not running")
        if behaviour == "forbidden":
            return self._message(403, "Permission check failed (/vms/111, VM.GuestAgent.Audit|VM.GuestAgent.Unrestricted)")
        if behaviour == "slow":
            time.sleep(SLOW_AGENT_SECONDS)
        _, interfaces, osinfo, host = agent
        results = {"agent/network-get-interfaces": interfaces, "agent/get-osinfo": osinfo,
                   "agent/get-host-name": {"host-name": host}}
        if what not in results:
            return self._reply(501, {"path": f"{what} is not implemented by the mock"})
        return self._reply(200, {"result": results[what]})

    def _agent_interfaces(self, vmid, vm):
        """What the guest agent of a VM the tests created reports: its cards, net0 with the DHCP lease if any."""
        if vm["status"] != "running":
            return self._message(500, f"VM {vmid} is not running")
        if vmid in FORBIDDEN_AGENT_VMIDS:
            return self._message(403, f"Permission check failed (/vms/{vmid}, VM.GuestAgent.Audit|VM.GuestAgent.Unrestricted)")
        vm["agent_polls"] += 1
        if not vm["installed"] or vm["agent_polls"] <= AGENT_POLLS_BEFORE_READY:
            return self._message(500, "QEMU guest agent is not running")
        interfaces = [{"name": "lo", "hardware-address": "00:00:00:00:00:00",
                       "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4", "prefix": 8}]}]
        for key in sorted((k for k in vm["config"] if re.fullmatch(r"net[0-9]+", k)), key=lambda k: int(k[3:])):
            mac = vm["config"][key].split(",")[0].partition("=")[2].lower()
            index = int(key[3:])
            lease = DHCP_LEASES.get(vmid) if index == 0 else None
            addresses = [{"ip-address": lease, "ip-address-type": "ipv4", "prefix": 24}] if lease else [
                {"ip-address": f"169.254.{index}.{vmid % 250}", "ip-address-type": "ipv4", "prefix": 16}]
            addresses.append({"ip-address": f"fe80::{index}", "ip-address-type": "ipv6", "prefix": 64})
            interfaces.append({"name": f"ens{18 + index}", "hardware-address": mac, "ip-addresses": addresses})
        return self._reply(200, {"result": interfaces})

    def _message(self, status, message):
        body = json.dumps({"data": None, "message": message + "\n"}).encode()
        self.send_response(status, message)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method, path, query, form):
        vms = STATE["vms"]
        if method == "GET" and path == "/version":
            return self._reply(200, {"version": "9.1.1", "release": "9.1", "repoid": "mock"})
        if method == "GET" and path == "/nodes":
            return self._reply(200, [{"node": n, "status": "online", "type": "node"} for n in NODES])
        if method == "GET" and path == "/storage":
            return self._reply(200, [storage_entry(name) for name in STORAGES])
        m = re.fullmatch(r"/nodes/([^/]+)/storage", path)
        if m and method == "GET":
            if m.group(1) not in NODES:
                return self._reply(500, {"node": f"no such node '{m.group(1)}'"})
            content = query.get("content")
            return self._reply(200, [
                {"storage": name, "active": 1, "enabled": 1, **STORAGES[name]}
                for name in NODE_STORAGES[m.group(1)] if not content or content in STORAGES[name]["content"].split(",")
            ])
        if method == "GET" and path == "/cluster/status":
            return self._reply(200, [{"type": "cluster", "name": "mock-cluster", "nodes": len(NODES), "quorate": 1}]
                               + [{"type": "node", "name": n, "online": 1, "local": int(n == NODE)} for n in NODES])
        if method == "GET" and path == "/cluster/resources":
            if query.get("type", "vm") != "vm":  # only VMs here: no nodes, storages or pools
                return self._reply(200, [])
            data = [
                {"vmid": int(v), "name": vm["config"].get("name", ""), "node": vm["node"], "type": "qemu",
                 "status": vm["status"], "id": f"qemu/{v}"}
                for v, vm in vms.items()
            ]
            if FIXTURES["enabled"]:
                data += fixture_resources()
            return self._reply(200, data)
        m = re.fullmatch(r"/nodes/([^/]+)/tasks/([^/]+)/(status|log)", path)
        if m and method == "GET":
            if m.group(3) == "log":
                return self._reply(200, [{"n": 1, "t": "TASK OK"}])
            task = urllib.parse.unquote(m.group(2))
            _, _, _, _, _, action, vmid, *_ = task.split(":")
            vm = vms.get(vmid)
            if action == "qmstart" and vm and "starting" in vm:
                # The first poll finds the start task still running; the next one finds the VM started.
                if vm["starting"] > 0:
                    vm["starting"] -= 1
                    return self._reply(200, {"status": "running", "upid": task})
                del vm["starting"]
                vm["status"] = "running"
            return self._reply(200, {"status": "stopped", "exitstatus": "OK", "upid": task})
        m = re.fullmatch(r"/nodes/([^/]+)/qemu", path)
        if method == "POST" and m and m.group(1) in NODES:
            node = m.group(1)
            vmid = str(form.get("vmid"))
            if vmid in vms:
                return self._reply(500, {"vmid": f"VM {vmid} already exists"})
            config = dict(form)
            error = _special_disks(config, vmid)
            if error:
                return self._reply(400, {"config": error})
            for key in [k for k in config if re.fullmatch(r"net[0-9]+", k)]:
                model, _, rest = config[key].partition(",")
                if "=" not in model:  # Proxmox adds a MAC address to a card that has none
                    config[key] = f"{model}=BC:24:11:{int(key[3:]):02d}:{int(vmid) // 100 % 100:02d}:{int(vmid) % 100:02d},{rest}"
            vms[vmid] = {"node": node, "config": config, "status": "stopped", "starts": 0, "polls": 0,
                         "installed": False, "config_updates": [], "agent_polls": 0}
            return self._reply(200, upid("qmcreate", vmid, node))
        m = re.fullmatch(r"/nodes/([^/]+)/qemu/(\d+)/(config|status/current|status/start|agent/network-get-interfaces)", path)
        if not m:
            return self._reply(501, {"path": f"{method} {path} is not implemented by the mock"})
        node, vmid, action = m.group(1), m.group(2), m.group(3)
        vm = vms.get(vmid)
        if vm is None or vm["node"] != node:  # Proxmox looks for the VM's config on the node the path names
            return self._reply(500, {"vmid": f"Configuration file 'nodes/{node}/qemu-server/{vmid}.conf' does not exist"})
        if action == "config" and method == "GET":
            return self._reply(200, vm["config"])
        if action == "config" and method == "PUT":
            vm["config_updates"].append(form)
            for key in filter(None, form.get("delete", "").split(",")):
                vm["config"].pop(key.strip(), None)
            vm["config"].update({k: v for k, v in form.items() if k != "delete"})
            return self._reply(200, None)
        if action == "agent/network-get-interfaces" and method == "GET":
            return self._agent_interfaces(int(vmid), vm)
        if action == "status/start" and method == "POST":
            vm["starting"] = 1
            vm["starts"] += 1
            vm["polls"] = 0
            return self._reply(200, upid("qmstart", vmid, node))
        if action == "status/current" and method == "GET":
            # First boot is the installer: power off after a few polls, unless this VM is meant to hang.
            if vm["status"] == "running" and vm["starts"] == 1 and int(vmid) not in HANG_VMIDS:
                vm["polls"] += 1
                if vm["polls"] > POLLS_BEFORE_POWEROFF:
                    vm["status"] = "stopped"
                    vm["installed"] = True
            return self._reply(200, {"status": vm["status"], "vmid": int(vmid), "qmpstatus": vm["status"]})
        return self._reply(501, {"path": f"{method} {path} is not implemented by the mock"})

    def do_GET(self):  # noqa: N802 (http.server naming)
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PUT(self):  # noqa: N802
        self._route("PUT")


class KeysHandler(BaseHTTPRequestHandler):
    keys = b""

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(self.keys)))
        self.end_headers()
        self.wfile.write(self.keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--keys-file", required=True)
    parser.add_argument("--port", type=int, default=8006)
    parser.add_argument("--keys-port", type=int, default=8081)
    # The web UI smoke test runs the mock in its own container, reached by name (tests/webui-smoke.sh).
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--fixture-vms", action="store_true", help="also list the fixed VMs of FIXTURE_VMS")
    args = parser.parse_args()
    FIXTURES["enabled"] = args.fixture_vms

    with open(args.keys_file, "rb") as fh:
        KeysHandler.keys = fh.read()
    keys_server = ThreadingHTTPServer((args.bind, args.keys_port), KeysHandler)
    threading.Thread(target=keys_server.serve_forever, daemon=True).start()

    api = ThreadingHTTPServer((args.bind, args.port), PveHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    api.socket = context.wrap_socket(api.socket, server_side=True)
    print(f"mock Proxmox API on https://{args.bind}:{args.port}, keys on http://{args.bind}:{args.keys_port}", flush=True)
    api.serve_forever()


if __name__ == "__main__":
    main()
