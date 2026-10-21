"""The web UI over HTTP: sign-in, the guards, every page and form, and runs."""

import html
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from usas_web import main as m
from usas_web.remote import PveResult, RemoteProblem
from usas_web.sshkeys import HostKey, KeyProblem

from .conftest import DEFAULTS, HARDENING, PASSWORD, PROXMOX, TOKEN_SECRET, configure, csrf, make_client, post, sign_up


def t(response) -> str:
    """The page's text with HTML character references decoded."""
    return html.unescape(response.text)


PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/run", "/runs", "/account"]


# --- First run and sign-in --------------------------------------------------------------------------------


def test_first_run_sends_everything_to_setup(client):
    for path in PAGES + ["/login"]:
        r = client.get(path)
        assert r.url.path == "/setup", path


def test_setup_needs_the_code_and_a_good_password(client):
    code = client.app_state.auth.setup_code
    r = post(client, "/setup", {"setup_code": "AAAA-BBBB-CCCC", "password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 422 and "setup code is wrong" in t(r)
    r = post(client, "/setup", {"setup_code": code, "password": "short", "confirm": "short"})
    assert r.status_code == 422 and "at least 12" in t(r)
    sign_up(client)
    assert client.get("/setup").url.path == "/login" or client.get("/setup").url.path == "/"
    r = client.post("/setup", data={"csrf_token": "x"})
    assert r.url.path in ("/login", "/")


def test_setup_attempts_are_throttled(client):
    for _ in range(6):
        r = post(client, "/setup", {"setup_code": "WRONG", "password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 429 and "Too many attempts" in t(r)


def test_login_logout(ui):
    r = post(ui, "/logout", {})
    assert r.url.path == "/login"
    assert ui.get("/servers").url.path == "/login"
    assert "next=/servers" in str(ui.get("/servers").url)
    r = ui.get("/login?next=/servers")
    r = ui.post("/login", data={"csrf_token": csrf(r), "password": "wrong password", "next": "/servers"})
    assert r.status_code == 401 and "wrong" in t(r)
    r = ui.post("/login", data={"csrf_token": csrf(r), "password": PASSWORD, "next": "/servers"})
    assert r.url.path == "/servers"
    assert ui.get("/login").url.path == "/"


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example", "/\\evil", "/nowhere"])
def test_login_never_redirects_elsewhere(ui, target):
    post(ui, "/logout", {})
    r = ui.get("/login")
    r = ui.post("/login", data={"csrf_token": csrf(r), "password": PASSWORD, "next": target})
    assert r.url.path == "/" and r.url.host == "localhost"


def test_login_is_throttled(ui):
    post(ui, "/logout", {})
    for _ in range(6):
        r = post(ui, "/login", {"password": "wrong password"})
    assert r.status_code == 429
    r = post(ui, "/login", {"password": PASSWORD})
    assert r.status_code == 429  # even the right password waits


def test_session_id_changes_at_sign_in(client):
    client.get("/setup")
    before = client.cookies.get("usas_session")
    sign_up(client)
    assert client.cookies.get("usas_session") not in (None, before)


def test_change_password(ui, settings):
    other = TestClient(ui.app, base_url="http://localhost")
    r = other.get("/login")
    other.post("/login", data={"csrf_token": csrf(r), "password": PASSWORD})
    assert other.get("/servers").url.path == "/servers"
    r = post(ui, "/account", {"current": "nope", "password": "a new long password", "confirm": "a new long password"})
    assert r.status_code == 422 and "current password is wrong" in t(r)
    r = post(ui, "/account", {"current": PASSWORD, "password": "short", "confirm": "short"})
    assert r.status_code == 422
    r = post(ui, "/account", {"current": PASSWORD, "password": "a new long password",
                              "confirm": "a new long password"})
    assert "Password changed" in t(r)
    assert other.get("/servers").url.path == "/login"
    assert ui.get("/servers").url.path == "/servers"


# --- Guards ------------------------------------------------------------------------------------------------


def test_csrf_token_is_required(ui):
    r = ui.post("/proxmox", data={"action": "save", **PROXMOX})
    assert r.status_code == 403 and "form expired" in t(r)
    r = ui.post("/proxmox", data={"csrf_token": "forged", "action": "save", **PROXMOX})
    assert r.status_code == 403


def test_cross_site_posts_are_refused(ui):
    token = csrf(ui.get("/proxmox"))
    r = ui.post("/proxmox", data={"csrf_token": token}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "Cross-site" in t(r)
    r = ui.post("/proxmox", data={"csrf_token": token}, headers={"Origin": "null"})
    assert r.status_code == 403
    r = ui.post("/proxmox", data={"csrf_token": token}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    # What a browser sends for the UI's own forms.
    r = ui.post("/proxmox", data={"csrf_token": token, "action": "remove-ca"},
                headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and "was removed" in r.text


def test_unknown_host_names_are_refused(settings):
    c, _ = make_client(settings, base_url="http://attacker.example")
    r = c.get("/")
    assert r.status_code == 400 and "USAS_ALLOWED_HOSTS" in t(r)


def test_other_host_names_need_https(settings):
    s = replace(settings, allowed_hosts=("usas.example.lan",) + settings.allowed_hosts)
    c, _ = make_client(s, base_url="http://usas.example.lan")
    assert c.get("/").status_code == 400
    c, _ = make_client(s, base_url="https://usas.example.lan")
    r = c.get("/setup")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-usas_session=") and "Secure" in cookie and "HttpOnly" in cookie
    assert "SameSite=strict" in cookie or "SameSite=Strict" in cookie


def test_ipv6_loopback_is_allowed(settings):
    c, _ = make_client(settings, base_url="http://[::1]:8080")
    assert c.get("/healthz").text == "ok"


def test_security_headers(client):
    r = client.get("/setup")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "same-origin"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" not in cookie  # plain HTTP on localhost (Safari drops Secure there)
    assert client.get("/static/app.css").headers["cache-control"].startswith("private")
    assert "set-cookie" not in client.get("/healthz").headers


def test_forced_secure_cookies(settings):
    c, _ = make_client(replace(settings, secure_cookies="true"))
    assert "Secure" in c.get("/setup").headers["set-cookie"]


# --- Pages and sections ----------------------------------------------------------------------------------


def test_every_page_renders(ui):
    for path in PAGES:
        r = ui.get(path)
        assert r.status_code == 200 and r.url.path == path, path
        assert "<h1>" in t(r)


def test_overview_shows_the_steps(ui):
    r = ui.get("/")
    assert t(r).count("To do") == 5 and "No runs yet" in t(r)
    configure(ui)
    r = ui.get("/")
    assert t(r).count("Done") == 3  # the SSH key and the node's host key are still missing


def test_proxmox_save_and_secrets(ui, settings):
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX})
    assert r.url.path == "/proxmox" and "Saved the Proxmox settings" in t(r)
    assert TOKEN_SECRET not in t(r) and "A value is saved" in t(r)
    # Leaving the secret empty keeps it.
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_token_secret": "", "pve_api_port": "8007"})
    assert r.url.path == "/proxmox"
    store = ui.app_state.store
    assert store.read_secrets()["vault_pve_api_token_secret"] == TOKEN_SECRET
    assert store.load().values["pve_api_port"] == 8007


def test_proxmox_errors(ui):
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_host": "", "pve_api_token_secret": "",
                              "pve_api_port": "99999", "pve_node": "bad node"})
    assert r.status_code == 422
    assert 'id="error-summary"' in t(r) and "There are 4 problems" in t(r)
    assert 'aria-invalid="true"' in t(r) and 'href="#f-pve_api_host"' in t(r)
    assert 'value="bad node"' in t(r)  # what was typed comes back


def test_proxmox_connection_test(ui, monkeypatch):
    seen = {}

    def fake(host, port, user, token_id, secret, validate, ca):
        seen.update(host=host, port=port, token_id=token_id, secret=secret, validate=validate)
        return PveResult(version="9.1.1", nodes=["pve-1", "pve-2"])

    monkeypatch.setattr(m, "test_proxmox", fake)
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_node": ""})  # the node isn't needed for a test
    assert r.status_code == 200 and "Connected to Proxmox VE 9.1.1" in t(r) and "pve-1, pve-2" in t(r)
    assert seen == {"host": "pve.example.lan", "port": 8006, "token_id": "automation", "secret": TOKEN_SECRET,
                    "validate": True}
    assert TOKEN_SECRET not in t(r)
    assert ui.app_state.store.load().values == {}  # a test saves nothing
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_node": "pve-9"})
    assert "isn't one of them" in t(r)

    def failing(*args):
        raise RemoteProblem("Proxmox refused the token (401).")

    monkeypatch.setattr(m, "test_proxmox", failing)
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX})
    assert "refused the token" in t(r)


def test_proxmox_ca(ui, monkeypatch, settings):
    from .test_sshkeys import make_ca

    r = post(ui, "/proxmox", {"action": "save-ca", "ca_pem": make_ca()})
    assert "Saved the CA certificate CN=Proxmox Virtual Environment" in t(r)
    assert settings.ca_bundle.exists()
    r = post(ui, "/proxmox", {"action": "save-ca", "ca_pem": "junk"})
    assert r.status_code == 422 and "PEM certificate" in t(r)
    monkeypatch.setattr(ui.app_state.keys, "fetch_ca", lambda host: make_ca())
    r = post(ui, "/proxmox", {"action": "fetch-ca"})
    assert "Saved the CA certificate" in t(r)
    r = post(ui, "/proxmox", {"action": "remove-ca"})
    assert "was removed" in t(r) and not settings.ca_bundle.exists()


def test_defaults_save_and_preview(ui, monkeypatch):
    r = post(ui, "/defaults", {"action": "save", **DEFAULTS})
    assert r.url.path == "/defaults" and "Saved the defaults" in t(r)
    assert "admin pass {{ x }}" not in t(r)
    config = ui.app_state.store.load()
    assert config.values["net_dns"] == ["10.0.0.1", "1.1.1.1"] and config.values["timezone"] == "Europe/Berlin"
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl laptop"
    monkeypatch.setattr(m, "preview_keys", lambda url: [{"type": "ssh-ed25519", "fingerprint": "SHA256:abc",
                                                         "comment": url}])
    r = post(ui, "/defaults", {"action": "preview", "github_user": "octocat", "admin_extra_ssh_keys": key,
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "https://github.com/octocat.keys" in t(r) and "laptop" in t(r)
    monkeypatch.setattr(m, "preview_keys", lambda url: [])
    r = post(ui, "/defaults", {"action": "preview", "github_user": "octocat",
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "has no public keys" in t(r)
    r = post(ui, "/defaults", {"action": "preview", "github_user": "",
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "Enter a GitHub user first" in t(r)

    def failing(url):
        raise RemoteProblem("No such GitHub user.")

    monkeypatch.setattr(m, "preview_keys", failing)
    r = post(ui, "/defaults", {"action": "preview", "github_user": "nobody",
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "No such GitHub user" in t(r)


def test_defaults_errors(ui):
    r = post(ui, "/defaults", {"action": "save", **DEFAULTS, "timezone": "Mars/Olympus", "admin_password": "",
                               "net_gateway": "10.0.0.256", "admin_user": "Admin"})
    assert r.status_code == 422
    for message in ("time zone", "Admin password is required", "Gateway isn't", "Admin user isn't"):
        assert message in t(r), message


def test_hardening(ui):
    r = post(ui, "/hardening", {**HARDENING, "ssh_port": "2222", "ufw_allow": "443/tcp\n9100/tcp from 10.0.0.0/8"})
    assert "Saved the hardening settings" in t(r)
    values = ui.app_state.store.load().values
    assert values["ssh_port"] == 2222 and len(values["ufw_allow"]) == 2
    assert "9100/tcp from 10.0.0.0/8" in t(ui.get("/hardening"))
    r = post(ui, "/hardening", {**HARDENING, "ssh_allow_users": "deploy"})
    assert r.status_code == 422 and "Add the admin user (sysadmin)" in t(r)
    r = post(ui, "/hardening", {**HARDENING, "fail2ban_ignoreip": "10.0.0.0/8 enabled=false", "ufw_allow": "x"})
    assert r.status_code == 422 and "There are 2 problems" in t(r)


def test_servers(ui):
    configure(ui, servers=2)
    r = ui.get("/servers")
    assert 'value="web-01"' in t(r) and 'value="443/tcp"' in t(r)
    base = {"server_count": "3", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
            "servers-0-pve_vmid": "4000", "servers-1-name": "web-01", "servers-1-ansible_host": "10.0.0.11",
            "servers-1-pve_vmid": "4001"}
    r = post(ui, "/servers", {"action": "add", **base})
    assert t(r).count('name="servers-') >= 3 * 7 and "autofocus" in t(r)
    r = post(ui, "/servers", {"action": "remove-0", **base})
    assert 'value="web-00"' not in t(r) and "Row removed" in t(r)
    r = post(ui, "/servers", {"action": "remove-0", "server_count": "1", "servers-0-name": "x"})
    assert 'name="servers-0-name" value=""' in t(r)
    bad = {**base, "servers-1-name": "web-00", "servers-1-pve_vmid": "abc", "servers-2-name": "pve-1",
           "servers-2-ansible_host": "10.0.0.10", "servers-2-pve_vmid": "4002", "servers-2-ufw_ports": "443/sctp"}
    r = post(ui, "/servers", {"action": "save", **bad})
    assert r.status_code == 422
    text = t(r)
    for message in ("Row 2: Another server already uses this", "Row 2: VM ID must be a whole number",
                    "Row 3: That is the Proxmox node's name", "Row 3: '443/sctp'"):
        assert message in text, message
    r = post(ui, "/servers", {"action": "save", **base, "servers-0-vm_cores": "4"})
    assert "Saved 2 server(s)" in t(r)
    servers = ui.app_state.store.load().servers
    assert servers[0]["vm_cores"] == 4 and "vm_cores" not in servers[1]


# --- SSH key and host key ------------------------------------------------------------------------------------


def test_ssh_key_lifecycle(ui, monkeypatch):
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    r = post(ui, "/ssh", {"action": "generate"})
    assert "Created a new Ed25519 key" in t(r) and "ssh-ed25519 " in t(r)
    pub = ui.app_state.keys.public_key()
    assert pub in ui.app_state.settings.inventory_file.read_text()  # authorized on the servers
    r = ui.get("/ssh/id_ed25519.pub")
    assert t(r).strip() == pub and "attachment" in r.headers["content-disposition"]
    r = post(ui, "/ssh", {"action": "generate"})
    assert r.status_code == 422 and "Tick the box" in t(r)
    r = post(ui, "/ssh", {"action": "generate", "replace": "on"})
    assert ui.app_state.keys.public_key() != pub
    r = post(ui, "/ssh", {"action": "upload", "key_text": "garbage"})
    assert r.status_code == 422 and "isn't an OpenSSH" in t(r)
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from .test_sshkeys import openssh

    r = ui.get("/ssh")
    r = ui.post("/ssh", data={"csrf_token": csrf(r), "action": "upload"},
                files={"key_file": ("id", openssh(ed25519.Ed25519PrivateKey.generate()))})
    assert "Saved the uploaded key" in t(r)
    r = post(ui, "/ssh", {"action": "nonsense"})
    assert r.status_code == 403


def test_no_public_key_yet(ui):
    assert ui.get("/ssh/id_ed25519.pub").status_code == 404


def test_node_host_key(ui, monkeypatch):
    r = post(ui, "/ssh", {"action": "address", "node_address": "10.0.0.2"})
    assert r.status_code == 422 and "node name on the Proxmox page" in t(r)
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    r = post(ui, "/ssh", {"action": "address", "node_address": "-oProxyCommand=id"})
    assert r.status_code == 422
    r = post(ui, "/ssh", {"action": "address", "node_address": "10.0.0.2"})
    assert "Saved the node's SSH address" in t(r)
    keys = ui.app_state.keys
    offered = [HostKey("10.0.0.2", "ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMq")]
    monkeypatch.setattr(keys, "scan", lambda host: offered)
    r = post(ui, "/ssh", {"action": "scan"})
    assert "Compare them" in t(r) and offered[0].fingerprint in t(r)
    r = post(ui, "/ssh", {"action": "trust"})
    assert r.status_code == 422 and "tick the box" in t(r)
    post(ui, "/ssh", {"action": "scan"})
    r = post(ui, "/ssh", {"action": "trust", "confirm": "on"})
    assert "Trusted 10.0.0.2" in t(r) and keys.trusted("10.0.0.2") == offered
    monkeypatch.setattr(keys, "test_login", lambda host: "Logged in to root@10.0.0.2 with the UI's key.")
    assert "Logged in" in t(post(ui, "/ssh", {"action": "test"}))

    def failing(host):
        raise KeyProblem("SSH to root@10.0.0.2 failed: Permission denied")

    monkeypatch.setattr(keys, "test_login", failing)
    r = post(ui, "/ssh", {"action": "test"})
    assert r.status_code == 422 and "Permission denied" in t(r)
    r = post(ui, "/ssh", {"action": "forget"})
    assert "Forgot" in t(r) and keys.trusted("10.0.0.2") == []


def test_scan_needs_an_address(ui):
    r = post(ui, "/ssh", {"action": "scan"})
    assert r.status_code == 422 and "Save the node's SSH address first" in t(r)


# --- Runs ------------------------------------------------------------------------------------------------------


def wait_done(ui, run_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = ui.app_state.runner.get(run_id)
        if run.done:
            return run
        time.sleep(0.05)
    raise AssertionError("run didn't finish")


def test_run_needs_an_inventory(ui):
    r = post(ui, "/run", {"playbook": "site"})
    assert r.status_code == 422 and "Save the settings first" in t(r)
    r = post(ui, "/run", {"playbook": "nope"})
    assert r.status_code == 422 and "Choose what to run" in t(r)


def test_run_stream_and_history(ui):
    configure(ui)
    r = ui.get("/run")
    assert "web-00" in t(r) and "To do" not in t(r)
    for hostile in ("web-00; id", "-e @/etc/passwd", "--become", "web-00\nid", "-web-00"):
        r = post(ui, "/run", {"playbook": "provision", "check": "on", "limit": hostile})
        assert r.status_code == 422 and "isn't a" in t(r), hostile
    for hostile in ("provision --become", "-e @/etc/passwd", "site;id"):
        r = post(ui, "/run", {"playbook": hostile})
        assert r.status_code == 422 and "Choose what to run" in t(r), hostile
    assert ui.app_state.runner.history() == []
    r = post(ui, "/run", {"playbook": "provision", "check": "on", "diff": "on", "limit": "web-00"})
    run_id = r.url.path.rsplit("/", 1)[-1]
    assert r.url.path.startswith("/runs/")
    run = wait_done(ui, run_id)
    assert run.status == "succeeded" and run.check and run.limit == "web-00"
    stream = ui.get(f"/runs/{run_id}/stream").text
    assert "data: ARGS -i" in stream and "--check --diff --limit=web-00 playbooks/provision-vms.yml" in stream
    assert TOKEN_SECRET not in stream and "data: SECRET ********" in stream
    assert stream.rstrip().endswith("event: end\ndata: succeeded")
    resumed = ui.get(f"/runs/{run_id}/stream", headers={"Last-Event-ID": "999999"}).text
    assert "ARGS" not in resumed and "event: end" in resumed
    page = ui.get(f"/runs/{run_id}")
    assert "PLAY RECAP" in t(page) and "Succeeded" in t(page) and "data-stream" not in t(page)
    log = ui.get(f"/runs/{run_id}/log")
    assert log.headers["content-type"].startswith("text/plain") and "PLAY RECAP" in t(log)
    assert "provision" in t(ui.get("/runs")) and "check diff limit web-00" in t(ui.get("/runs"))
    assert run_id in t(ui.get("/"))
    assert ui.get("/runs/20200101-000000-abcdef").url.path == "/runs"
    r = ui.get("/runs/..%2F..%2Fetc")
    assert r.url.path == "/runs" or r.status_code == 404


def test_run_uses_the_ca_bundle(ui):
    from .test_sshkeys import make_ca

    configure(ui)
    post(ui, "/proxmox", {"action": "save-ca", "ca_pem": make_ca()})
    r = post(ui, "/run", {"playbook": "build-installer"})
    run_id = r.url.path.rsplit("/", 1)[-1]
    wait_done(ui, run_id)
    assert f"CA {ui.app_state.settings.ca_bundle}" in ui.get(f"/runs/{run_id}/log").text


def test_stop_a_run(ui):
    configure(ui)
    ui.app_state.settings.run_env["FAKE_DELAY"] = "30"
    r = post(ui, "/run", {"playbook": "site"})
    run_id = r.url.path.rsplit("/", 1)[-1]
    page = ui.get(f"/runs/{run_id}")
    assert "Stop the run" in t(page) and f'data-stream="/runs/{run_id}/stream?offset=' in t(page)
    r = post(ui, "/run", {"playbook": "baseline"})
    assert r.status_code == 422 and "Another run" in t(r)
    r = ui.post(f"/runs/{run_id}/stop", data={"csrf_token": csrf(page)})
    assert "Stopping the run" in t(r)
    assert wait_done(ui, run_id).status == "stopped"
    ui.app_state.settings.run_env.pop("FAKE_DELAY")


def test_runs_need_sign_in(ui):
    configure(ui)
    r = post(ui, "/run", {"playbook": "site"})
    run_id = r.url.path.rsplit("/", 1)[-1]
    wait_done(ui, run_id)
    post(ui, "/logout", {})
    for path in (f"/runs/{run_id}", f"/runs/{run_id}/log", f"/runs/{run_id}/stream", "/ssh/id_ed25519.pub"):
        assert ui.get(path).url.path == "/login", path


def test_module_entry_point(monkeypatch, settings, capsys):
    import usas_web.__main__ as entry

    seen = {}
    monkeypatch.setattr(entry, "from_env", lambda: settings)
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: seen.update(kw))
    entry.main()
    out = capsys.readouterr().out
    assert "Setup code: " in out and seen["port"] == 8080 and seen["server_header"] is False
    assert seen["proxy_headers"] is False
    record = entry.logging.LogRecord("uvicorn.access", 20, "", 0, '"GET /healthz HTTP/1.1" 200', None, None)
    assert not entry._QuietHealthChecks().filter(record)



def test_the_node_is_picked_from_the_cluster(ui, monkeypatch):
    # Before a connection test the node is free text, with a pointer to the test.
    r = ui.get("/proxmox")
    assert 'name="pve_node" value=' in t(r) and "pick the node from a list" in t(r)

    monkeypatch.setattr(m, "test_proxmox", lambda *a: PveResult(
        version="9.1.1", nodes=["hephaestus", "zeus"], status={"hephaestus": "online", "zeus": "offline"},
        cluster="homelab"))
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_node": ""})
    assert "cluster homelab" in t(r) and "now a list" in t(r)
    assert ui.app_state.store.load().values == {}  # the test still saves no form values
    assert ui.app_state.store.cluster() == {"name": "homelab", "nodes": {"hephaestus": "online", "zeus": "offline"},
                                            "iso_storages": {}}

    # From now on the node is a dropdown of the cluster's nodes, with their status.
    page = t(ui.get("/proxmox"))
    assert '<select id="f-pve_node" name="pve_node"' in page
    assert ">hephaestus (online)</option>" in page and ">zeus (offline)</option>" in page
    assert "Choose a node" in page and "The nodes of cluster <strong>homelab</strong>" in page

    # A name that isn't in the cluster is refused, with a hint when only the case differs.
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "Hephaestus"})
    assert r.status_code == 422
    assert "Hephaestus isn&#39;t a node in the cluster (hephaestus, zeus)" in r.text
    assert "case-sensitive: did you mean hephaestus?" in t(r)
    assert ">Hephaestus (not in the cluster)</option>" in t(r)
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "athena"})
    assert r.status_code == 422 and "did you mean" not in t(r)

    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "hephaestus"})
    assert "Saved the Proxmox settings" in t(r)
    assert ui.app_state.store.load().values["pve_node"] == "hephaestus"
    assert '<option value="hephaestus" selected>hephaestus (online)</option>' in t(ui.get("/proxmox"))


def test_a_damaged_settings_file_is_ignored(ui, settings):
    settings.settings_file.write_text("{not json")
    assert ui.app_state.store.cluster() == {}
    settings.settings_file.write_text('{"cluster": {"name": "x", "nodes": ["not", "a", "dict"]}}')
    assert ui.app_state.store.cluster() == {}
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX})  # no node list: any valid name is accepted
    assert "Saved the Proxmox settings" in t(r)


def test_the_api_timeout_is_an_advanced_proxmox_setting(ui):
    page = t(ui.get("/proxmox"))
    assert 'name="pve_api_timeout"' in page and "default <code>60</code>" in page
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_timeout": "120"})
    assert "Saved the Proxmox settings" in t(r)
    assert ui.app_state.store.load().values["pve_api_timeout"] == 120
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_timeout": "1"})
    assert r.status_code == 422


def test_the_iso_storage_is_picked_from_the_storages_that_allow_isos(ui, monkeypatch):
    monkeypatch.setattr(m, "test_proxmox", lambda *a: PveResult(
        version="9.1.1", nodes=["pve-1"], status={"pve-1": "online"},
        iso_storages={"local": "dir", "synology-nfs": "nfs, shared"}))
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX})
    assert "the ISO storage a list of the storages that allow ISO images (local, synology-nfs)" in t(r)
    page = t(ui.get("/proxmox"))
    assert '<select id="f-pve_iso_storage" name="pve_iso_storage"' in page
    assert ">synology-nfs (nfs, shared)</option>" in page and "found automatically" in page

    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": "backup-nfs"})
    assert r.status_code == 422 and "backup-nfs isn&#39;t a storage that allows ISO images" in r.text
    assert ">backup-nfs (doesn't allow ISO images)</option>" in r.text
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": "synology-nfs"})
    assert "Saved the Proxmox settings" in t(r)
    assert ui.app_state.store.load().values["pve_iso_storage"] == "synology-nfs"
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": "bad;id"})
    assert r.status_code == 422
