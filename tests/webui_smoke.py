#!/usr/bin/env python3
"""Drives the web UI in the smoke test (tests/webui-smoke.sh) the way its app does in a browser, with the
standard library only: the page shell and its built files, cookies, the CSRF token in X-CSRF-Token, posts of
the same form fields to /api/<page>. Fails with a message at the first problem."""

import argparse
import http.cookiejar
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

TOKEN_SECRET = "00000000-0000-4000-8000-000000000000"
PASSWORD = "smoke test passphrase"


class UI:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read()
                status, url, info = resp.status, resp.url, resp.headers
        except urllib.error.HTTPError as err:
            raw, status, url, info = err.read(), err.code, err.url, err.headers
        return status, url, info, raw

    def page(self, path: str):
        """A page as the browser loads it: the HTML shell, with the session's token in it."""
        status, url, info, raw = self.request("GET", path)
        text = raw.decode()
        match = re.search(r'data-csrf="([^"]+)"', text)
        if match:
            self.csrf = match.group(1)
        return status, url, info, text

    def get(self, path: str):
        status, _, _, raw = self.request("GET", "/api" + path, headers={"X-USAS-Page": path})
        return status, json.loads(raw.decode() or "{}")

    def post(self, path: str, data: dict, origin: str | None = None, token: bool = True):
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in data.items()]
        body = ("".join(parts) + f"--{boundary}--\r\n").encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "X-USAS-Page": path}
        if token:
            headers["X-CSRF-Token"] = self.csrf
        if origin is not None:
            headers["Origin"] = origin
        status, _, _, raw = self.request("POST", "/api" + path, body, headers)
        try:
            answer = json.loads(raw.decode())
        except ValueError:
            answer = {"error": raw.decode()}
        if isinstance(answer, dict) and answer.get("csrf"):
            self.csrf = answer["csrf"]
        return status, answer


def check(condition: bool, message: str, detail: object = "") -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        if detail:
            text = detail if isinstance(detail, str) else json.dumps(detail, indent=1)
            print(text[:3000], file=sys.stderr)
        sys.exit(1)
    print(f"ok: {message}")


def mock_call(mock: str, method: str, path: str, data: dict | None = None):
    context = ssl.create_default_context(cafile=ARGS.ca)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(mock + path, data=body, method=method)
    req.add_header("Authorization", f"PVEAPIToken=ansible@pve!test={TOKEN_SECRET}")
    with urllib.request.urlopen(req, context=context, timeout=10) as resp:
        return json.loads(resp.read().decode())


def check_shell(ui: UI) -> None:
    status, url, info, text = ui.page("/runs")
    check(url.endswith("/setup"), "a fresh install sends every page to the first-run setup")
    csp = info["Content-Security-Policy"] or ""
    check("script-src 'self'" in csp and "style-src 'self'" in csp and "font-src 'self'" in csp
          and "unsafe-inline" not in csp, "the page has the strict Content-Security-Policy", csp)
    check('data-theme="dark"' in text, "dark is the default theme", text)
    script = re.search(r'<script type="module" src="(/static/app/assets/[^"]+\.js)"></script>', text)
    styles = re.findall(r'<link rel="stylesheet" href="(/static/app/assets/[^"]+\.css)">', text)
    check(script is not None and len(styles) == 1, "the shell links the app the image built", text)
    check("isn't built" not in text and "<script>" not in text and "style=" not in text,
          "the shell has nothing inline", text)
    status, _, info, raw = ui.request("GET", script.group(1))
    check(status == 200 and "javascript" in (info["Content-Type"] or ""), "the app's script is served")
    status, _, info, raw = ui.request("GET", styles[0])
    css = raw.decode()
    check(status == 200 and "--accent:" in css and "data:" not in css, "the stylesheet is served, without data: URLs")
    fonts = re.findall(r"url\((/static/app/assets/[^)]+\.woff2)\)", css)
    check(len(fonts) >= 5, "the stylesheet uses the bundled fonts", css[:500])
    status, _, info, _ = ui.request("GET", fonts[0])
    check(status == 200 and "font" in (info["Content-Type"] or ""), "the fonts are served from the image")
    icon = re.search(r'<link rel="icon" href="(/static/favicon\.svg\?v=[0-9a-f]{12})"', text)
    check(icon is not None and ui.request("GET", icon.group(1))[0] == 200, "the icon's URL carries a hash")
    with urllib.request.urlopen(ARGS.url + "/favicon.ico", timeout=10) as resp:
        check(resp.url.endswith("/favicon.ico") and resp.headers["Content-Type"].startswith("image/svg+xml"),
              "/favicon.ico answers with the icon, without a sign-in")
    version = re.search(r'data-version="([^"]+)"', text)
    check(version is not None and (not ARGS.version or version.group(1) == ARGS.version),
          f"the page carries the version ({version.group(1) if version else 'none'})", text)


def main() -> None:
    ui = UI(ARGS.url)
    check_shell(ui)

    status, data = ui.post("/setup", {"setup_code": "WRONG-CODE-0000", "password": PASSWORD, "confirm": PASSWORD})
    check(status == 422 and "setup code is wrong" in data.get("error", ""), "a wrong setup code is refused", data)
    status, data = ui.post("/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD, "confirm": PASSWORD},
                           token=False)
    check(status == 403, "a post without the CSRF token is refused", data)
    status, data = ui.post("/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD, "confirm": PASSWORD},
                           origin="https://evil.example")
    check(status == 403, "a post from another origin is refused", data)
    status, data = ui.post("/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD, "confirm": PASSWORD})
    check(status == 200 and data.get("redirect") == "/proxmox", "the setup code and a password sign in", data)
    status, url, _, _ = ui.page("/proxmox")
    check(status == 200 and url.endswith("/proxmox"), "the pages open after the sign-in")

    status, data = ui.post("/theme", {"theme": "light"})
    check(status == 200 and 'data-theme="light"' in ui.page("/")[3], "the theme is remembered", data)
    ui.post("/theme", {"theme": "dark"})

    api = {
        "pve_api_host": "pve-mock", "pve_api_port": "8006", "pve_api_user": "ansible@pve",
        "pve_api_token_id": "test", "pve_api_token_secret": TOKEN_SECRET, "pve_validate_certs": "on",
        "pve_node": "pve-test", "pve_storage": "local-lvm", "pve_efidisk_format": "raw", "pve_bridge": "vmbr0",
        "pve_vlan": "", "pve_iso_storage": "local",
    }
    status, data = ui.post("/proxmox", {"action": "test", **api})
    check("couldn't be verified" in data.get("test_error", ""), "the connection test verifies the API's certificate",
          data)
    with open(ARGS.ca) as fh:
        status, data = ui.post("/proxmox", {"action": "save-ca", "ca_pem": fh.read()})
    check("Saved the CA certificate" in data.get("message", ""), "the CA certificate is saved", data)
    status, data = ui.post("/proxmox", {"action": "test", **api})
    check("Connected to Proxmox VE 9.1.1" in data.get("message", "") and "pve-test" in data.get("message", ""),
          "the connection test passes with the CA", data)
    status, page = ui.get("/proxmox")
    cluster = page.get("cluster", {})
    check(cluster.get("nodes", {}).get("pve-test") == "online" and cluster.get("name") == "mock-cluster",
          "the cluster's nodes are kept for the node dropdown", page)
    check(cluster.get("iso_storages", {}).get("synology-nfs") == "nfs, shared",
          "so are the storages that allow ISO images", page)
    check(TOKEN_SECRET not in json.dumps(data), "the test doesn't send the token secret back")
    status, data = ui.post("/proxmox", {"action": "save", **api})
    check(status == 200 and data.get("message") == "Saved the Proxmox settings.", "the Proxmox settings are saved",
          data)
    status, page = ui.get("/proxmox")
    secret = next(f for f in page["fields"] if f["name"] == "pve_api_token_secret")
    check(TOKEN_SECRET not in json.dumps(page) and secret["saved"], "the saved secret isn't sent again", page)

    status, data = ui.post("/ssh", {"action": "generate"})
    status, page = ui.get("/ssh")
    check(page.get("public_key", "").startswith("ssh-ed25519 "), "the UI creates its SSH key", page)

    defaults = {
        "github_user": "test", "github_keys_url": "http://pve-mock:8081/{{ github_user }}.keys",
        "admin_extra_ssh_keys": "", "authorize_ui_key": "on", "admin_user": "sysadmin",
        "admin_password": "admin password {{ not a template }}", "timezone": "America/Chicago",
        "net_gateway": "10.10.10.1", "net_prefix": "24", "net_dns": "10.10.10.1", "net_search": "example.lan",
        "autoinstall_storage_layout": "lvm",
    }
    status, data = ui.post("/defaults", {"action": "preview", **defaults})
    check(any(k.get("comment") == "smoke@ubuntu-server-auto-setup" for k in data.get("preview", [])),
          "the keys preview lists the published key", data)
    status, data = ui.post("/defaults", {"action": "save", **defaults, "net_gateway": "10.10.10.300"})
    check(status == 422 and "net_gateway" in data.get("errors", {}),
          "an invalid gateway is refused (the roles' IPv4 pattern)", data)
    status, data = ui.post("/defaults", {"action": "save", **defaults})
    check(data.get("message") == "Saved the defaults.", "the defaults are saved", data)

    status, data = ui.post("/servers", {"server_count": "1", "servers-0-name": "srv-01",
                                        "servers-0-ansible_host": "10.10.10.61", "servers-0-pve_vmid": "4061",
                                        "servers-0-ufw_ports": "443/tcp"})
    check(data.get("message") == "Saved 1 server(s).", "the servers table is saved", data)
    hardening = {"ssh_port": "22", "ssh_permit_root_login": "no", "fail2ban_sshd_mode": "normal",
                 "fail2ban_maxretry": "5", "fail2ban_findtime": "10m", "fail2ban_bantime": "1h",
                 "fail2ban_bantime_maxtime": "1w", "ufw_ssh_sources": "any"}
    status, data = ui.post("/hardening", {**hardening, "ssh_allow_users": "someone-else"})
    check(status == 422 and "locked out" in data.get("errors", {}).get("ssh_allow_users", ""),
          "ssh_allow_users without the admin user is refused", data)
    status, data = ui.post("/hardening", {**hardening, "ssh_allow_users": "sysadmin",
                                          "fail2ban_bantime_increment": "on", "fail2ban_ignoreip": "10.10.10.0/24",
                                          "ufw_ssh_limit": "on", "admin_sudo_nopasswd": "on",
                                          "ufw_allow": "9100/tcp from 10.10.10.0/24"})
    check(data.get("message") == "Saved the hardening settings.", "the hardening settings are saved", data)

    # The VM exists already, so provisioning only checks the settings, asks the API and skips it: a real
    # playbook run against the mock that needs no SSH.
    mock_call(ARGS.mock, "POST", "/api2/json/nodes/pve-test/qemu", {"vmid": "4061", "name": "srv-01"})
    status, data = ui.post("/run", {"playbook": "provision", "limit": "srv-01; rm -rf /"})
    check(status == 422 and "isn't a host or group" in data.get("error", ""),
          "a limit that isn't an inventory name is refused", data)
    status, data = ui.post("/run", {"playbook": "provision", "limit": "srv-01"})
    run_path = data.get("redirect", "")
    check(re.fullmatch(r"/runs/[0-9-]+[a-f0-9]{6}", run_path) is not None, "the run starts", data)
    check(ui.page(run_path)[0] == 200, "the run's page opens")

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
    status, data = ui.get("/runs")
    check(data["runs"][0]["status"] == "succeeded" and data["runs"][0]["action"] == "provision",
          "the run is in the history", data)
    status, data = ui.get(run_path)
    check("PLAY RECAP" in data.get("log", "") and data["run"]["done"], "the run's log is kept", data)

    state = mock_call(ARGS.mock, "GET", "/mock/state")
    calls = [r for r in state["data"]["requests"] if r["path"] == "/cluster/resources"]
    check(len(calls) >= 1, "the playbook called the API with the token from the Vault, over verified TLS")

    status, data = ui.post("/logout", {})
    check(data.get("redirect") == "/login", "signing out ends the session", data)
    status, url, _, _ = ui.page("/runs")
    check("/login" in url, "pages need a sign-in again")
    status, data = ui.get("/runs")
    check(status == 401 and data.get("redirect", "").startswith("/login"), "and so does the API", data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--setup-code", required=True)
    parser.add_argument("--mock", required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--keys", required=True)
    parser.add_argument("--version", default="", help="the version the image must show (default: any)")
    ARGS = parser.parse_args()
    main()
