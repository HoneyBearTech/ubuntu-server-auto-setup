#!/usr/bin/env python3
"""Drives the web UI in the smoke test (tests/webui-smoke.sh) the way a browser would, with the standard
library only: cookies, CSRF tokens from the forms, redirects. Fails with a message at the first problem."""

import argparse
import html
import http.cookiejar
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_SECRET = "00000000-0000-4000-8000-000000000000"
PASSWORD = "smoke test passphrase"


class UI:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def request(self, method: str, path: str, data: dict | None = None, origin: str | None = None):
        body = urllib.parse.urlencode(data or {}, doseq=True).encode() if method == "POST" else None
        req = urllib.request.Request(self.base + path, data=body, method=method)
        if origin is not None:
            req.add_header("Origin", origin)
        try:
            with self.opener.open(req, timeout=30) as resp:
                text = resp.read().decode()
                status, url = resp.status, resp.url
        except urllib.error.HTTPError as err:
            text, status, url = err.read().decode(), err.code, err.url
        match = re.search(r'name="csrf_token" value="([^"]+)"', text)
        if match:
            self.csrf = match.group(1)
        return status, url, html.unescape(text)

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, data: dict, **kw):
        return self.request("POST", path, {"csrf_token": self.csrf, **data}, **kw)


def check(condition: bool, message: str, text: str = "") -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        if text:
            errors = re.findall(r'class="(?:error|error-summary|flash flash-error)"[^>]*>(.*?)</', text, re.S)
            print("\n".join(e.strip() for e in errors) or text[:3000], file=sys.stderr)
        sys.exit(1)
    print(f"ok: {message}")


def mock_call(mock: str, method: str, path: str, data: dict | None = None):
    context = ssl.create_default_context(cafile=ARGS.ca)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(mock + path, data=body, method=method)
    req.add_header("Authorization", f"PVEAPIToken=ansible@pve!test={TOKEN_SECRET}")
    with urllib.request.urlopen(req, context=context, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    ui = UI(ARGS.url)

    status, url, text = ui.get("/runs")
    check(url.endswith("/setup"), "a fresh install sends every page to the first-run setup")
    status, url, text = ui.post("/setup", {"setup_code": "WRONG-CODE-0000", "password": PASSWORD, "confirm": PASSWORD})
    check(status == 422 and "setup code is wrong" in text, "a wrong setup code is refused", text)
    status, url, text = ui.request("POST", "/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD,
                                                      "confirm": PASSWORD})
    check(status == 403, "a form without the CSRF token is refused")
    status, url, text = ui.post("/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD, "confirm": PASSWORD},
                                origin="https://evil.example")
    check(status == 403, "a form posted from another origin is refused")
    status, url, text = ui.post("/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD, "confirm": PASSWORD})
    check(status == 200 and url.endswith("/proxmox"), "the setup code and a password sign in", text)

    api = {
        "pve_api_host": "pve-mock", "pve_api_port": "8006", "pve_api_user": "ansible@pve",
        "pve_api_token_id": "test", "pve_api_token_secret": TOKEN_SECRET, "pve_validate_certs": "on",
        "pve_node": "pve-test", "pve_storage": "local-lvm", "pve_efidisk_format": "raw", "pve_bridge": "vmbr0",
        "pve_vlan": "", "pve_iso_storage": "local",
    }
    status, url, text = ui.post("/proxmox", {"action": "test", **api})
    check("couldn't be verified" in text, "the connection test verifies the API's certificate", text)
    with open(ARGS.ca) as fh:
        status, url, text = ui.post("/proxmox", {"action": "save-ca", "ca_pem": fh.read()})
    check("Saved the CA certificate" in text, "the CA certificate is saved", text)
    status, url, text = ui.post("/proxmox", {"action": "test", **api})
    check("Connected to Proxmox VE 9.1.1" in text and "pve-test" in text, "the connection test passes with the CA",
          text)
    _, _, page = ui.get("/proxmox")
    check('<select id="f-pve_node" name="pve_node"' in page and ">pve-test (online)</option>" in page
          and "mock-cluster" in page, "the node is a dropdown of the cluster's nodes after the test")
    check('<select id="f-pve_iso_storage" name="pve_iso_storage"' in page
          and ">synology-nfs (nfs, shared)</option>" in page,
          "the ISO storage is a dropdown of the storages that allow ISO images")
    check(TOKEN_SECRET not in text, "the page doesn't show the token secret")
    status, url, text = ui.post("/proxmox", {"action": "save", **api})
    check(url.endswith("/proxmox") and "Saved the Proxmox settings" in text, "the Proxmox settings are saved", text)
    check(TOKEN_SECRET not in text and "A value is saved" in text, "the saved secret isn't shown again", text)

    status, url, text = ui.post("/ssh", {"action": "generate"})
    check("ssh-ed25519 " in text, "the UI creates its SSH key", text)

    defaults = {
        "github_user": "test", "github_keys_url": "http://pve-mock:8081/{{ github_user }}.keys",
        "admin_extra_ssh_keys": "", "authorize_ui_key": "on", "admin_user": "sysadmin",
        "admin_password": "admin password {{ not a template }}", "timezone": "America/Chicago",
        "net_gateway": "10.10.10.1", "net_prefix": "24", "net_dns": "10.10.10.1", "net_search": "example.lan",
        "autoinstall_storage_layout": "lvm",
    }
    status, url, text = ui.post("/defaults", {"action": "preview", **defaults})
    check("smoke@ubuntu-server-auto-setup" in text, "the keys preview lists the published key", text)
    status, url, text = ui.post("/defaults", {"action": "save", **defaults, "net_gateway": "10.10.10.300"})
    check(status == 422 and "Gateway" in text, "an invalid gateway is refused (the roles' IPv4 pattern)", text)
    status, url, text = ui.post("/defaults", {"action": "save", **defaults})
    check("Saved the defaults" in text, "the defaults are saved", text)

    status, url, text = ui.post("/servers", {"action": "save", "server_count": "1", "servers-0-name": "srv-01",
                                             "servers-0-ansible_host": "10.10.10.61", "servers-0-pve_vmid": "4061",
                                             "servers-0-ufw_ports": "443/tcp"})
    check("Saved 1 server" in text, "the servers table is saved", text)
    status, url, text = ui.post("/hardening", {"ssh_port": "22", "ssh_allow_users": "someone-else",
                                               "ssh_permit_root_login": "no", "fail2ban_sshd_mode": "normal",
                                               "fail2ban_maxretry": "5", "fail2ban_findtime": "10m",
                                               "fail2ban_bantime": "1h", "fail2ban_bantime_maxtime": "1w",
                                               "ufw_ssh_sources": "any"})
    check(status == 422 and "locked out" in text, "ssh_allow_users without the admin user is refused", text)
    status, url, text = ui.post("/hardening", {"ssh_port": "22", "ssh_allow_users": "sysadmin",
                                               "ssh_permit_root_login": "no", "fail2ban_sshd_mode": "normal",
                                               "fail2ban_maxretry": "5", "fail2ban_findtime": "10m",
                                               "fail2ban_bantime": "1h", "fail2ban_bantime_maxtime": "1w",
                                               "fail2ban_bantime_increment": "on", "fail2ban_ignoreip": "10.10.10.0/24",
                                               "ufw_ssh_sources": "any", "ufw_ssh_limit": "on",
                                               "admin_sudo_nopasswd": "on", "ufw_allow": "9100/tcp from 10.10.10.0/24"})
    check("Saved the hardening settings" in text, "the hardening settings are saved", text)

    # The VM exists already, so provisioning only checks the settings, asks the API and skips it: a real
    # playbook run against the mock that needs no SSH.
    mock_call(ARGS.mock, "POST", "/api2/json/nodes/pve-test/qemu", {"vmid": "4061", "name": "srv-01"})
    status, url, text = ui.post("/run", {"playbook": "provision", "limit": "srv-01; rm -rf /"})
    check(status == 422 and "isn't a host or group" in text, "a limit that isn't an inventory name is refused", text)
    status, url, text = ui.post("/run", {"playbook": "provision", "limit": "srv-01"})
    run_path = urllib.parse.urlsplit(url).path
    check(re.fullmatch(r"/runs/[0-9-]+[a-f0-9]{6}", run_path) is not None, "the run starts", text)

    deadline, stream = time.time() + 180, ""
    while time.time() < deadline:
        with ui.opener.open(ARGS.url + run_path + "/stream", timeout=200) as resp:
            stream = resp.read().decode()
        if "event: end" in stream:
            break
        time.sleep(1)
    lines = [line[6:] for line in stream.splitlines() if line.startswith("data: ")]
    log = "\n".join(lines)
    check("event: end\ndata: succeeded" in stream, "the run succeeds and the stream reports it", log)
    check("VM 4061 already exists; skipping install for srv-01" in log, "the playbook skipped the existing VM", log)
    check("ok=" in log and "failed=0" in log, "the log streams the play recap", log)
    check(TOKEN_SECRET not in log, "the log doesn't contain the token secret", log)
    status, url, text = ui.get("/runs")
    check("Succeeded" in text and "provision" in text, "the run is in the history", text)

    state = mock_call(ARGS.mock, "GET", "/mock/state")
    calls = [r for r in state["data"]["requests"] if r["path"] == "/cluster/resources"]
    check(len(calls) >= 1, "the playbook called the API with the token from the Vault, over verified TLS")

    status, url, text = ui.post("/logout", {})
    check(url.endswith("/login"), "signing out ends the session")
    status, url, text = ui.get("/runs")
    check("/login" in url, "pages need a sign-in again")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--setup-code", required=True)
    parser.add_argument("--mock", required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--keys", required=True)
    ARGS = parser.parse_args()
    main()
