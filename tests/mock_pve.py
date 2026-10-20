#!/usr/bin/env python3
"""A small stand-in for the Proxmox VE API, for the integration tests.

It implements only the calls that the proxmox_vm role and community.proxmox.proxmox_kvm make, keeps VMs in
memory, and pretends to run the installer: after a VM's first start it reports "running" for a few status
polls, then "stopped" (the installer powering the VM off). VMs whose ID is in HANG_VMIDS never stop, to test
the role's failure path.

It also serves a public-keys file over plain HTTP, standing in for https://github.com/<user>.keys.

Usage: mock_pve.py --cert CERT --key KEY --keys-file FILE [--port 8006] [--keys-port 8081]
The state (VMs, config changes, requests) is at GET /mock/state, for the tests' assertions.
"""

import argparse
import json
import re
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "PVEAPIToken=ansible@pve!test=00000000-0000-4000-8000-000000000000"
NODE = "pve-test"
HANG_VMIDS = {4099}
POLLS_BEFORE_POWEROFF = 2

LOCK = threading.Lock()
STATE = {"vms": {}, "requests": []}


def upid(action, vmid):
    return f"UPID:{NODE}:0000AAAA:00BBBBBB:6700CCCC:{action}:{vmid}:ansible@pve!test:"


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
            return self._dispatch(method, path, query, form)

    def _dispatch(self, method, path, query, form):
        vms = STATE["vms"]
        if method == "GET" and path == "/version":
            return self._reply(200, {"version": "9.1.1", "release": "9.1", "repoid": "mock"})
        if method == "GET" and path == "/nodes":
            return self._reply(200, [{"node": NODE, "status": "online", "type": "node"}])
        if method == "GET" and path == "/cluster/resources":
            data = [
                {"vmid": int(v), "name": vm["config"].get("name", ""), "node": NODE, "type": "qemu",
                 "status": vm["status"], "id": f"qemu/{v}"}
                for v, vm in vms.items()
            ]
            return self._reply(200, data)
        m = re.fullmatch(r"/nodes/([^/]+)/tasks/([^/]+)/(status|log)", path)
        if m and method == "GET":
            if m.group(3) == "log":
                return self._reply(200, [{"n": 1, "t": "TASK OK"}])
            return self._reply(200, {"status": "stopped", "exitstatus": "OK", "upid": m.group(2)})
        if method == "POST" and path == f"/nodes/{NODE}/qemu":
            vmid = str(form.get("vmid"))
            if vmid in vms:
                return self._reply(500, {"vmid": f"VM {vmid} already exists"})
            config = dict(form)
            if "net0" in config:  # Proxmox adds a MAC address to the NIC
                model, _, rest = config["net0"].partition(",")
                config["net0"] = f"{model}=BC:24:11:00:{int(vmid) // 100 % 100:02d}:{int(vmid) % 100:02d},{rest}"
            vms[vmid] = {"config": config, "status": "stopped", "starts": 0, "polls": 0, "config_updates": []}
            return self._reply(200, upid("qmcreate", vmid))
        m = re.fullmatch(rf"/nodes/{NODE}/qemu/(\d+)/(config|status/current|status/start)", path)
        if not m:
            return self._reply(501, {"path": f"{method} {path} is not implemented by the mock"})
        vmid, action = m.group(1), m.group(2)
        vm = vms.get(vmid)
        if vm is None:
            return self._reply(500, {"vmid": f"Configuration file 'nodes/{NODE}/qemu-server/{vmid}.conf' does not exist"})
        if action == "config" and method == "GET":
            return self._reply(200, vm["config"])
        if action == "config" and method == "PUT":
            vm["config_updates"].append(form)
            for key in filter(None, form.get("delete", "").split(",")):
                vm["config"].pop(key.strip(), None)
            vm["config"].update({k: v for k, v in form.items() if k != "delete"})
            return self._reply(200, None)
        if action == "status/start" and method == "POST":
            vm["status"] = "running"
            vm["starts"] += 1
            vm["polls"] = 0
            return self._reply(200, upid("qmstart", vmid))
        if action == "status/current" and method == "GET":
            # First boot is the installer: power off after a few polls, unless this VM is meant to hang.
            if vm["status"] == "running" and vm["starts"] == 1 and int(vmid) not in HANG_VMIDS:
                vm["polls"] += 1
                if vm["polls"] > POLLS_BEFORE_POWEROFF:
                    vm["status"] = "stopped"
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
    args = parser.parse_args()

    with open(args.keys_file, "rb") as fh:
        KeysHandler.keys = fh.read()
    keys_server = ThreadingHTTPServer(("127.0.0.1", args.keys_port), KeysHandler)
    threading.Thread(target=keys_server.serve_forever, daemon=True).start()

    api = ThreadingHTTPServer(("127.0.0.1", args.port), PveHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    api.socket = context.wrap_socket(api.socket, server_side=True)
    print(f"mock Proxmox API on https://127.0.0.1:{args.port}, keys on http://127.0.0.1:{args.keys_port}", flush=True)
    api.serve_forever()


if __name__ == "__main__":
    main()
