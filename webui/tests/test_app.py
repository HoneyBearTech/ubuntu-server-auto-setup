"""The web UI over HTTP: the page shell, sign-in, the guards, the API behind every page and form, and runs."""

import importlib
import json
import re
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from usas_web import main as m
from usas_web.remote import PveResult, RemoteProblem
from usas_web.sshkeys import HostKey, KeyProblem

from .conftest import DEFAULTS, HARDENING, PASSWORD, PROXMOX, TOKEN_SECRET, configure, csrf, make_client, post, sign_up

PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/run", "/runs", "/account"]
API = ["/api/overview", "/api/proxmox", "/api/ssh", "/api/defaults", "/api/servers", "/api/hardening", "/api/run",
       "/api/runs"]


def get(c, path):
    return c.get(path, headers={"X-USAS-Page": path.removeprefix("/api") or "/"})


# --- First run and sign-in --------------------------------------------------------------------------------


def test_first_run_sends_everything_to_setup(client):
    for path in PAGES + ["/login"]:
        r = client.get(path)
        assert r.url.path == "/setup", path
    for path in API:
        r = client.get(path)
        assert r.status_code == 401 and r.json()["redirect"] == "/setup", path
    assert client.get("/api/session").json()["configured"] is False


def test_setup_needs_the_code_and_a_good_password(client):
    code = client.app_state.auth.setup_code
    r = post(client, "/setup", {"setup_code": "AAAA-BBBB-CCCC", "password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 422 and "setup code is wrong" in r.json()["error"]
    r = post(client, "/setup", {"setup_code": code, "password": "short", "confirm": "short"})
    assert r.status_code == 422 and "at least 12" in r.json()["error"]
    before = csrf(client)
    sign_up(client)
    assert csrf(client) != before  # a new session, with a new token
    assert client.get("/setup").url.path == "/"
    r = post(client, "/setup", {"setup_code": code, "password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 409 and r.json()["redirect"] == "/login"


def test_setup_attempts_are_throttled(client):
    for _ in range(6):
        r = post(client, "/setup", {"setup_code": "WRONG", "password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 429 and "Too many attempts" in r.json()["error"]


def test_login_logout(ui):
    r = post(ui, "/logout")
    assert r.json()["redirect"] == "/login" and "usas_session" not in ui.cookies
    assert ui.get("/servers").url.path == "/login"
    assert "next=/servers" in str(ui.get("/servers").url)
    r = get(ui, "/api/servers")
    assert r.status_code == 401 and r.json()["redirect"] == "/login?next=/servers"
    r = post(ui, "/login", {"password": "wrong password", "next": "/servers"})
    assert r.status_code == 401 and "wrong" in r.json()["error"]
    r = post(ui, "/login", {"password": PASSWORD, "next": "/servers"})
    assert r.status_code == 200 and r.json()["redirect"] == "/servers"
    assert r.json()["csrf"] == csrf(ui)
    assert ui.get("/login").url.path == "/"
    assert ui.get("/login?next=/runs").url.path == "/runs"


def test_login_before_setup(client):
    r = post(client, "/login", {"password": PASSWORD})
    assert r.status_code == 409 and r.json()["redirect"] == "/setup"


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example", "/\\evil", "/nowhere",
                                    "/runs/../etc", "/runs/20200101-000000-abcdef"])
def test_login_never_redirects_elsewhere(ui, target):
    post(ui, "/logout")
    r = post(ui, "/login", {"password": PASSWORD, "next": target})
    assert r.json()["redirect"] == "/"


def test_login_can_return_to_a_run(ui):
    configure(ui)
    run_id = post(ui, "/run", {"playbook": "site"}).json()["run"]["id"]
    wait_done(ui, run_id)
    post(ui, "/logout")
    assert ui.get(f"/runs/{run_id}").url.path == "/login"
    r = post(ui, "/login", {"password": PASSWORD, "next": f"/runs/{run_id}"})
    assert r.json()["redirect"] == f"/runs/{run_id}"


def test_login_is_throttled(ui):
    post(ui, "/logout")
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
    post(other, "/login", {"password": PASSWORD})
    assert other.get("/servers").url.path == "/servers"
    r = post(ui, "/account", {"current": "nope", "password": "a new long password", "confirm": "a new long password"})
    assert r.status_code == 422 and "current password is wrong" in r.json()["error"]
    r = post(ui, "/account", {"current": PASSWORD, "password": "short", "confirm": "short"})
    assert r.status_code == 422
    r = post(ui, "/account", {"current": PASSWORD, "password": "a new long password",
                              "confirm": "a new long password"})
    assert "Password changed" in r.json()["message"]
    assert other.get("/servers").url.path == "/login"
    assert ui.get("/servers").url.path == "/servers"


# --- Guards ------------------------------------------------------------------------------------------------


def test_csrf_token_is_required(ui):
    r = ui.post("/api/proxmox", data={"action": "save", **PROXMOX})
    assert r.status_code == 403 and "form expired" in r.json()["error"]
    r = ui.post("/api/proxmox", data={"action": "save", **PROXMOX}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    # A token in the form, as a page on another site could send it, isn't enough: it must be the header.
    r = ui.post("/api/proxmox", data={"csrf_token": csrf(ui), "action": "save", **PROXMOX})
    assert r.status_code == 403
    assert ui.app_state.store.load().values == {}


def test_cross_site_posts_are_refused(ui):
    headers = {"X-CSRF-Token": csrf(ui)}
    r = ui.post("/api/proxmox", headers={**headers, "Origin": "http://evil.example"})
    assert r.status_code == 403 and "Cross-site" in r.text
    r = ui.post("/api/proxmox", headers={**headers, "Origin": "null"})
    assert r.status_code == 403
    r = ui.post("/api/proxmox", headers={**headers, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    # What a browser sends for the app's own posts.
    r = ui.post("/api/proxmox", data={"action": "remove-ca"},
                headers={**headers, "Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and "was removed" in r.json()["message"]


def test_old_form_posts_are_gone(ui):
    # The pages only answer GET; their forms post to /api/<page>.
    assert ui.post("/proxmox", data={"action": "save"}, headers={"X-CSRF-Token": csrf(ui)}).status_code == 405


def test_unknown_host_names_are_refused(settings):
    c, _ = make_client(settings, base_url="http://attacker.example")
    r = c.get("/")
    assert r.status_code == 400 and "USAS_ALLOWED_HOSTS" in r.text
    assert c.get("/api/session").status_code == 400


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
    cookie = client.get("/setup").headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" not in cookie  # plain HTTP on localhost (Safari drops Secure there)
    for path in ("/setup", "/api/session"):
        r = client.get(path)
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "font-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "same-origin"
    assert client.get("/static/favicon.svg").headers["cache-control"].startswith("private")
    assert "set-cookie" not in client.get("/healthz").headers


def test_api_answers_json_only_to_the_same_site(ui):
    r = get(ui, "/api/overview")
    assert r.headers["content-type"].startswith("application/json")
    assert "access-control-allow-origin" not in r.headers
    r = ui.options("/api/proxmox", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "x-csrf-token"})
    assert "access-control-allow-origin" not in r.headers


def test_favicon_needs_no_sign_in(client):
    for path in ("/static/favicon.svg", "/favicon.ico"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert r.text.startswith("<svg") and "set-cookie" not in r.headers
        assert r.headers["cache-control"].startswith("private") and "default-src 'none'" in r.headers[
            "content-security-policy"]
    page = client.get("/setup").text
    assert f'<link rel="icon" href="{m.asset_urls(m.HERE / "static")["favicon.svg"]}" type="image/svg+xml">' in page


def test_asset_urls_carry_a_hash_of_the_contents(tmp_path):
    urls = m.asset_urls(m.HERE / "static")
    assert re.fullmatch(r"/static/favicon\.svg\?v=[0-9a-f]{12}", urls["favicon.svg"])
    (tmp_path / "favicon.svg").write_text("old")
    before = m.asset_urls(tmp_path)
    (tmp_path / "favicon.svg").write_text("new")
    assert m.asset_urls(tmp_path) != before


def test_the_shell_links_the_built_app(client, tmp_path, monkeypatch):
    app_dir = tmp_path / "static" / "app"
    (app_dir / ".vite").mkdir(parents=True)
    (app_dir / ".vite" / "manifest.json").write_text(json.dumps({
        "src/main.tsx": {"file": "assets/main-abc123.js", "css": ["assets/main-def456.css"], "isEntry": True}}))
    assert m.frontend_assets(app_dir) == {"js": "/static/app/assets/main-abc123.js",
                                          "css": ["/static/app/assets/main-def456.css"]}
    monkeypatch.setattr(m, "HERE", tmp_path)
    (tmp_path / "templates").symlink_to(m.Path(m.__file__).parent / "templates")
    (tmp_path / "static" / "favicon.svg").write_text("<svg/>")
    c, _ = make_client(client.app_state.settings)
    page = c.get("/setup").text
    assert '<script type="module" src="/static/app/assets/main-abc123.js"></script>' in page
    assert '<link rel="stylesheet" href="/static/app/assets/main-def456.css">' in page
    assert "isn't built" not in page


def test_the_shell_without_a_build(tmp_path):
    assert m.frontend_assets(tmp_path) is None
    (tmp_path / ".vite").mkdir()
    (tmp_path / ".vite" / "manifest.json").write_text("{not json")
    assert m.frontend_assets(tmp_path) is None
    (tmp_path / ".vite" / "manifest.json").write_text('{"other.ts": {}}')
    assert m.frontend_assets(tmp_path) is None


def test_the_shell(client, monkeypatch):
    monkeypatch.setattr(m, "frontend_assets", lambda app_dir: None)
    r = client.get("/setup")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    page = r.text
    assert '<html lang="en" data-theme="dark">' in page and "<title>Set up · USAS</title>" in page
    assert f'data-csrf="{csrf(client)}"' in page
    assert "needs JavaScript" in page and "isn't built" in page


def test_version_comes_from_the_environment(client, monkeypatch):
    import usas_web

    assert f'data-version="{usas_web.__version__}"' in client.get("/setup").text
    assert client.get("/api/session").json()["version"] == usas_web.__version__
    try:
        monkeypatch.setenv("USAS_VERSION", " 1.2.3 ")
        assert importlib.reload(usas_web).__version__ == "1.2.3"
        monkeypatch.setenv("USAS_VERSION", "")
        assert importlib.reload(usas_web).__version__ == "dev"
    finally:
        monkeypatch.undo()
        importlib.reload(usas_web)


def test_forced_secure_cookies(settings):
    c, _ = make_client(replace(settings, secure_cookies="true"))
    assert "Secure" in c.get("/setup").headers["set-cookie"]


# --- Theme --------------------------------------------------------------------------------------------------


def test_dark_is_the_default_theme(client):
    assert 'data-theme="dark"' in client.get("/setup").text
    assert '<meta name="color-scheme" content="dark">' in client.get("/setup").text
    assert client.get("/api/session").json()["theme"] == "dark"


@pytest.mark.parametrize("theme, scheme", [("light", "light"), ("system", "dark light"), ("dark", "dark")])
def test_the_theme_is_remembered(client, theme, scheme):
    r = post(client, "/theme", {"theme": theme})
    assert r.status_code == 200 and r.json()["theme"] == theme
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"usas_theme={theme};") and "HttpOnly" in cookie and "Max-Age=" in cookie
    assert "SameSite=strict" in cookie or "SameSite=Strict" in cookie
    page = client.get("/setup").text
    assert f'data-theme="{theme}"' in page and f'<meta name="color-scheme" content="{scheme}">' in page
    assert client.get("/api/session").json()["theme"] == theme


def test_the_theme_needs_a_known_value_and_the_token(client):
    r = post(client, "/theme", {"theme": "neon"})
    assert r.status_code == 422 and "set-cookie" not in r.headers or "usas_theme" not in r.headers.get("set-cookie", "")
    assert client.post("/api/theme", data={"theme": "light"}).status_code == 403
    client.cookies.set("usas_theme", "<script>")
    assert 'data-theme="dark"' in client.get("/setup").text


def test_the_theme_cookie_is_secure_over_https(settings):
    s = replace(settings, allowed_hosts=("usas.example.lan",) + settings.allowed_hosts)
    c, _ = make_client(s, base_url="https://usas.example.lan")
    r = post(c, "/theme", {"theme": "light"})
    assert "Secure" in r.headers["set-cookie"]


# --- Pages and sections ----------------------------------------------------------------------------------


def test_every_page_answers_with_the_shell(ui):
    for path, title in m.PAGES.items():
        r = ui.get(path)
        assert r.status_code == 200 and r.url.path == path, path
        assert f"<title>{title} · USAS</title>" in r.text and f"<h1>{title}</h1>" in r.text


def test_every_api_needs_sign_in(ui):
    for path in API:
        assert get(ui, path).status_code == 200, path
    post(ui, "/logout")
    for path in API:
        r = get(ui, path)
        assert r.status_code == 401 and r.json()["redirect"].startswith("/login"), path
    for path in ["/proxmox", "/defaults", "/hardening", "/servers", "/ssh", "/run", "/account", "/runs/x/stop"]:
        assert post(ui, path, {}).status_code == 401, path


def test_overview_shows_the_steps(ui):
    data = get(ui, "/api/overview").json()
    assert [s["done"] for s in data["steps"]] == [False] * 5 and data["runs"] == []
    assert data["current_run"] is None and data["node"] == ""
    configure(ui)
    data = get(ui, "/api/overview").json()
    assert sum(s["done"] for s in data["steps"]) == 3  # the SSH key and the node's host key are still missing
    assert data["steps"][0] == {"label": "Proxmox API host, token and node", "done": True, "href": "/proxmox"}
    assert data["node"] == "pve-1"


def test_section_fields_describe_the_form(ui):
    data = get(ui, "/api/proxmox").json()
    assert data["title"] == "Proxmox API"
    fields = {f["name"]: f for f in data["fields"]}
    assert list(fields)[:3] == ["pve_api_host", "pve_api_port", "pve_api_user"]
    assert fields["pve_api_port"]["default"] == "8006" and fields["pve_api_port"]["kind"] == "int"
    assert fields["pve_api_token_secret"] == {**fields["pve_api_token_secret"], "kind": "secret", "saved": False,
                                              "variable": "vault_pve_api_token_secret", "default": None}
    assert fields["pve_efidisk_format"]["choices"] == ["raw", "qcow2"]
    assert fields["pve_api_timeout"]["advanced"] is True and fields["pve_api_timeout"]["default"] == "60"
    assert data["values"]["pve_validate_certs"] is True and data["values"]["pve_api_port"] == "8006"
    assert data["cluster"] == {} and data["ca"] == []


def test_proxmox_save_and_secrets(ui, settings):
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX})
    assert r.status_code == 200 and r.json()["message"] == "Saved the Proxmox settings."
    data = get(ui, "/api/proxmox").json()
    assert TOKEN_SECRET not in json.dumps(data)
    assert data["values"]["pve_api_token_secret"] == "" and data["values"]["pve_api_token_id"] == "automation"
    assert {f["name"]: f for f in data["fields"]}["pve_api_token_secret"]["saved"] is True
    # Leaving the secret empty keeps it.
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_token_secret": "", "pve_api_port": "8007"})
    assert r.status_code == 200
    store = ui.app_state.store
    assert store.read_secrets()["vault_pve_api_token_secret"] == TOKEN_SECRET
    assert store.load().values["pve_api_port"] == 8007


def test_proxmox_errors(ui):
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_host": "", "pve_api_token_secret": "",
                              "pve_api_port": "99999", "pve_node": "bad node"})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert set(errors) == {"pve_api_host", "pve_api_token_secret", "pve_api_port", "pve_node"}
    assert ui.app_state.store.load().values == {}


def test_proxmox_connection_test(ui, monkeypatch):
    seen = {}

    def fake(host, port, user, token_id, secret, validate, ca):
        seen.update(host=host, port=port, token_id=token_id, secret=secret, validate=validate)
        return PveResult(version="9.1.1", nodes=["pve-1", "pve-2"])

    monkeypatch.setattr(m, "test_proxmox", fake)
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_node": ""})  # the node isn't needed for a test
    data = r.json()
    assert r.status_code == 200 and "Connected to Proxmox VE 9.1.1." in data["message"]
    assert "Nodes: pve-1, pve-2" in data["message"] and "Nothing was saved yet" in data["message"]
    assert data["test_result"] == {"version": "9.1.1", "cluster": "", "nodes": ["pve-1", "pve-2"], "iso_storages": []}
    assert seen == {"host": "pve.example.lan", "port": 8006, "token_id": "automation", "secret": TOKEN_SECRET,
                    "validate": True}
    assert TOKEN_SECRET not in r.text
    assert ui.app_state.store.load().values == {}  # a test saves nothing
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_node": "pve-9"})
    assert "isn't one of them" in r.json()["message"]

    def failing(*args):
        raise RemoteProblem("Proxmox refused the token (401).")

    monkeypatch.setattr(m, "test_proxmox", failing)
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX})
    assert r.status_code == 200 and "refused the token" in r.json()["test_error"]
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_api_host": "bad host"})
    assert r.status_code == 422 and "pve_api_host" in r.json()["errors"]


def test_proxmox_ca(ui, monkeypatch, settings):
    from .test_sshkeys import make_ca

    r = post(ui, "/proxmox", {"action": "save-ca", "ca_pem": make_ca()})
    assert "Saved the CA certificate CN=Proxmox Virtual Environment" in r.json()["message"]
    assert settings.ca_bundle.exists()
    ca = get(ui, "/api/proxmox").json()["ca"]
    assert len(ca) == 1 and ca[0]["subject"].startswith("CN=Proxmox Virtual Environment")
    r = post(ui, "/proxmox", {"action": "save-ca", "ca_pem": "junk"})
    assert r.status_code == 422 and "PEM certificate" in r.json()["ca_error"]
    monkeypatch.setattr(ui.app_state.keys, "fetch_ca", lambda host: make_ca())
    r = post(ui, "/proxmox", {"action": "fetch-ca"})
    assert "Saved the CA certificate" in r.json()["message"]
    r = post(ui, "/proxmox", {"action": "remove-ca"})
    assert "was removed" in r.json()["message"] and not settings.ca_bundle.exists()


def test_defaults_save_and_preview(ui, monkeypatch):
    r = post(ui, "/defaults", {"action": "save", **DEFAULTS})
    assert r.json()["message"] == "Saved the defaults."
    data = get(ui, "/api/defaults").json()
    assert "admin pass {{ x }}" not in json.dumps(data)
    assert data["values"]["net_dns"] == "10.0.0.1\n1.1.1.1" and data["authorize_ui_key"] is True
    assert "Europe/Berlin" in data["timezones"] and data["ui_key"] == ""
    config = ui.app_state.store.load()
    assert config.values["net_dns"] == ["10.0.0.1", "1.1.1.1"] and config.values["timezone"] == "Europe/Berlin"
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl laptop"
    monkeypatch.setattr(m, "preview_keys", lambda url: [{"type": "ssh-ed25519", "fingerprint": "SHA256:abc",
                                                         "comment": url}])
    r = post(ui, "/defaults", {"action": "preview", "github_user": "octocat", "admin_extra_ssh_keys": key,
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    data = r.json()
    assert data["preview"][0]["comment"] == "https://github.com/octocat.keys" and data["github_user"] == "octocat"
    assert data["extra_keys"][0]["comment"] == "laptop" and data["preview_error"] is None
    monkeypatch.setattr(m, "preview_keys", lambda url: [])
    r = post(ui, "/defaults", {"action": "preview", "github_user": "octocat",
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "has no public keys" in r.json()["preview_error"]
    r = post(ui, "/defaults", {"action": "preview", "github_user": "",
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "Enter a GitHub user first" in r.json()["preview_error"]
    r = post(ui, "/defaults", {"action": "preview", "github_user": "not a user"})
    assert r.status_code == 422 and "github_user" in r.json()["errors"]

    def failing(url):
        raise RemoteProblem("No such GitHub user.")

    monkeypatch.setattr(m, "preview_keys", failing)
    r = post(ui, "/defaults", {"action": "preview", "github_user": "nobody",
                               "github_keys_url": "https://github.com/{{ github_user }}.keys"})
    assert "No such GitHub user" in r.json()["preview_error"]


def test_defaults_errors(ui):
    r = post(ui, "/defaults", {"action": "save", **DEFAULTS, "timezone": "Mars/Olympus", "admin_password": "",
                               "net_gateway": "10.0.0.256", "admin_user": "Admin"})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert "time zone" in errors["timezone"] and "Admin password is required" in errors["admin_password"]
    assert "Gateway isn't" in errors["net_gateway"] and "Admin user isn't" in errors["admin_user"]


def test_hardening(ui):
    r = post(ui, "/hardening", {**HARDENING, "ssh_port": "2222", "ufw_allow": "443/tcp\n9100/tcp from 10.0.0.0/8"})
    assert r.json()["message"] == "Saved the hardening settings."
    values = ui.app_state.store.load().values
    assert values["ssh_port"] == 2222 and len(values["ufw_allow"]) == 2
    assert "9100/tcp from 10.0.0.0/8" in get(ui, "/api/hardening").json()["values"]["ufw_allow"]
    r = post(ui, "/hardening", {**HARDENING, "ssh_allow_users": "deploy"})
    assert r.status_code == 422 and "Add the admin user (sysadmin)" in r.json()["errors"]["ssh_allow_users"]
    r = post(ui, "/hardening", {**HARDENING, "fail2ban_ignoreip": "10.0.0.0/8 enabled=false", "ufw_allow": "x"})
    assert r.status_code == 422 and len(r.json()["errors"]) == 2


def test_servers(ui):
    configure(ui, servers=2)
    data = get(ui, "/api/servers").json()
    assert [c["name"] for c in data["columns"]][:3] == ["name", "ansible_host", "pve_vmid"]
    assert data["rows"][1]["name"] == "web-01" and data["rows"][0]["ufw_ports"] == "443/tcp"
    assert data["rows"][0]["vm_cores"] == ""
    base = {"server_count": "3", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
            "servers-0-pve_vmid": "4000", "servers-1-name": "web-01", "servers-1-ansible_host": "10.0.0.11",
            "servers-1-pve_vmid": "4001"}
    bad = {**base, "servers-1-name": "web-00", "servers-1-pve_vmid": "abc", "servers-2-name": "pve-1",
           "servers-2-ansible_host": "10.0.0.10", "servers-2-pve_vmid": "4002", "servers-2-ufw_ports": "443/sctp"}
    r = post(ui, "/servers", {"action": "save", **bad})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert errors["servers-1-name"] == "Another server already uses this."
    assert "VM ID must be a whole number" in errors["servers-1-pve_vmid"]
    assert errors["servers-2-name"] == "That is the Proxmox node's name."
    assert "'443/sctp'" in errors["servers-2-ufw_ports"]
    # Errors keep the row numbers of the form, also after an empty row.
    r = post(ui, "/servers", {"server_count": "2", "servers-1-name": "bad name", "servers-1-ansible_host": "10.0.0.9",
                              "servers-1-pve_vmid": "4009"})
    assert list(r.json()["errors"]) == ["servers-1-name"]
    r = post(ui, "/servers", {"action": "save", **base, "servers-0-vm_cores": "4"})
    assert r.json()["message"] == "Saved 2 server(s)."
    servers = ui.app_state.store.load().servers
    assert servers[0]["vm_cores"] == 4 and "vm_cores" not in servers[1]
    r = post(ui, "/servers", {"server_count": "lots"})
    assert r.json()["message"] == "Saved 0 server(s)."


# --- SSH key and host key ------------------------------------------------------------------------------------


def test_ssh_key_lifecycle(ui, monkeypatch):
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    data = get(ui, "/api/ssh").json()
    assert data["public_key"] == "" and data["key_info"] is None and data["pve_node"] == "pve-1"
    r = post(ui, "/ssh", {"action": "generate"})
    assert "Created a new Ed25519 key" in r.json()["message"]
    data = get(ui, "/api/ssh").json()
    pub = ui.app_state.keys.public_key()
    assert data["public_key"] == pub and data["key_info"]["type"] == "ssh-ed25519"
    assert pub in ui.app_state.settings.inventory_file.read_text()  # authorized on the servers
    r = ui.get("/ssh/id_ed25519.pub")
    assert r.text.strip() == pub and "attachment" in r.headers["content-disposition"]
    r = post(ui, "/ssh", {"action": "generate"})
    assert r.status_code == 422 and "Tick the box" in r.json()["error"] and r.json()["area"] == "key"
    r = post(ui, "/ssh", {"action": "generate", "replace": "on"})
    assert ui.app_state.keys.public_key() != pub
    r = post(ui, "/ssh", {"action": "upload", "key_text": "garbage"})
    assert r.status_code == 422 and "isn't an OpenSSH" in r.json()["error"] and r.json()["area"] == "key"
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from .test_sshkeys import openssh

    key = openssh(ed25519.Ed25519PrivateKey.generate())
    r = post(ui, "/ssh", {"action": "upload"}, files={"key_file": ("id", key)})
    assert "Saved the uploaded key" in r.json()["message"]
    r = post(ui, "/ssh", {"action": "nonsense"})
    assert r.status_code == 403


def test_no_public_key_yet(ui):
    assert ui.get("/ssh/id_ed25519.pub").status_code == 404


def test_node_host_key(ui, monkeypatch):
    r = post(ui, "/ssh", {"action": "address", "node_address": "10.0.0.2"})
    assert r.status_code == 422 and "node name on the Proxmox page" in r.json()["error"]
    assert r.json()["area"] == "address"
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    r = post(ui, "/ssh", {"action": "address", "node_address": "-oProxyCommand=id"})
    assert r.status_code == 422 and r.json()["area"] == "address"
    r = post(ui, "/ssh", {"action": "address", "node_address": "10.0.0.2"})
    assert "Saved the node's SSH address" in r.json()["message"]
    keys = ui.app_state.keys
    offered = [HostKey("10.0.0.2", "ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMq")]
    monkeypatch.setattr(keys, "scan", lambda host: offered)
    r = post(ui, "/ssh", {"action": "scan"})
    assert r.status_code == 200
    data = get(ui, "/api/ssh").json()
    assert data["pending"] == [{"keytype": "ssh-ed25519", "fingerprint": offered[0].fingerprint}]
    assert data["node_address"] == "10.0.0.2" and data["trusted"] == []
    r = post(ui, "/ssh", {"action": "trust"})
    assert r.status_code == 422 and "tick the box" in r.json()["error"] and r.json()["area"] == "hostkey"
    post(ui, "/ssh", {"action": "scan"})
    r = post(ui, "/ssh", {"action": "trust", "confirm": "on"})
    assert "Trusted 10.0.0.2" in r.json()["message"] and keys.trusted("10.0.0.2") == offered
    assert get(ui, "/api/ssh").json()["trusted"][0]["fingerprint"] == offered[0].fingerprint
    monkeypatch.setattr(keys, "test_login", lambda host: "Logged in to root@10.0.0.2 with the UI's key.")
    assert "Logged in" in post(ui, "/ssh", {"action": "test"}).json()["message"]

    def failing(host):
        raise KeyProblem("SSH to root@10.0.0.2 failed: Permission denied")

    monkeypatch.setattr(keys, "test_login", failing)
    r = post(ui, "/ssh", {"action": "test"})
    assert r.status_code == 422 and "Permission denied" in r.json()["error"] and r.json()["area"] == "hostkey"
    r = post(ui, "/ssh", {"action": "forget"})
    assert "Forgot" in r.json()["message"] and keys.trusted("10.0.0.2") == []
    # A new address drops host keys scanned for the old one.
    post(ui, "/ssh", {"action": "scan"})
    post(ui, "/ssh", {"action": "address", "node_address": "10.0.0.3"})
    assert get(ui, "/api/ssh").json()["pending"] is None


def test_scan_needs_an_address(ui):
    r = post(ui, "/ssh", {"action": "scan"})
    assert r.status_code == 422 and "Save the node's SSH address first" in r.json()["error"]


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
    assert r.status_code == 422 and "Save the settings first" in r.json()["error"]
    r = post(ui, "/run", {"playbook": "nope"})
    assert r.status_code == 422 and "Choose what to run" in r.json()["error"]


def test_run_page_data(ui):
    data = get(ui, "/api/run").json()
    assert [p["key"] for p in data["playbooks"]] == ["build-installer", "provision", "baseline", "site", "keys"]
    assert data["playbooks"][-1] == {"key": "keys", "playbook": "playbooks/baseline.yml",
                                     "description": "Re-sync the admin user's SSH keys only",
                                     "extra": ["--tags", "ssh_keys"]}
    assert "all" in data["names"] and data["current_run"] is None and len(data["steps"]) == 5


def test_run_stream_and_history(ui):
    configure(ui)
    data = get(ui, "/api/run").json()
    assert "web-00" in data["names"] and not all(s["done"] for s in data["steps"])
    for hostile in ("web-00; id", "-e @/etc/passwd", "--become", "web-00\nid", "-web-00"):
        r = post(ui, "/run", {"playbook": "provision", "check": "on", "limit": hostile})
        assert r.status_code == 422 and "isn't a" in r.json()["error"], hostile
    for hostile in ("provision --become", "-e @/etc/passwd", "site;id"):
        r = post(ui, "/run", {"playbook": hostile})
        assert r.status_code == 422 and "Choose what to run" in r.json()["error"], hostile
    assert ui.app_state.runner.history() == []
    r = post(ui, "/run", {"playbook": "provision", "check": "on", "diff": "on", "limit": "web-00"})
    run_id = r.json()["run"]["id"]
    assert r.json()["redirect"] == f"/runs/{run_id}"
    run = wait_done(ui, run_id)
    assert run.status == "succeeded" and run.check and run.limit == "web-00"
    stream = ui.get(f"/runs/{run_id}/stream").text
    assert "data: ARGS -i" in stream and "--check --diff --limit=web-00 playbooks/provision-vms.yml" in stream
    assert TOKEN_SECRET not in stream and "data: SECRET ********" in stream
    assert stream.rstrip().endswith("event: end\ndata: succeeded")
    resumed = ui.get(f"/runs/{run_id}/stream", headers={"Last-Event-ID": "999999"}).text
    assert "ARGS" not in resumed and "event: end" in resumed
    detail = get(ui, f"/api/runs/{run_id}").json()
    assert "PLAY RECAP" in detail["log"] and detail["offset"] == len(detail["log"].encode())
    assert detail["run"]["status"] == "succeeded" and detail["run"]["done"] is True
    assert detail["run"]["playbook"] == "playbooks/provision-vms.yml" and detail["run"]["returncode"] == 0
    assert ui.get(f"/runs/{run_id}").url.path == f"/runs/{run_id}"
    log = ui.get(f"/runs/{run_id}/log")
    assert log.headers["content-type"].startswith("text/plain") and "PLAY RECAP" in log.text
    runs = get(ui, "/api/runs").json()["runs"]
    assert [(r["id"], r["action"], r["check"], r["diff"], r["limit"]) for r in runs] == [
        (run_id, "provision", True, True, "web-00")]
    assert get(ui, "/api/overview").json()["runs"][0]["id"] == run_id
    assert ui.get("/runs/20200101-000000-abcdef").url.path == "/runs"
    r = get(ui, "/api/runs/20200101-000000-abcdef")
    assert r.status_code == 404 and r.json()["redirect"] == "/runs"
    r = ui.get("/runs/..%2F..%2Fetc")
    assert r.url.path == "/runs" or r.status_code == 404
    assert get(ui, "/api/runs/..%2F..%2Fetc").status_code == 404


def test_run_uses_the_ca_bundle(ui):
    from .test_sshkeys import make_ca

    configure(ui)
    post(ui, "/proxmox", {"action": "save-ca", "ca_pem": make_ca()})
    run_id = post(ui, "/run", {"playbook": "build-installer"}).json()["run"]["id"]
    wait_done(ui, run_id)
    assert f"CA {ui.app_state.settings.ca_bundle}" in ui.get(f"/runs/{run_id}/log").text


def test_stop_a_run(ui):
    configure(ui)
    ui.app_state.settings.run_env["FAKE_DELAY"] = "30"
    run_id = post(ui, "/run", {"playbook": "site"}).json()["run"]["id"]
    data = get(ui, "/api/run").json()
    assert data["current_run"]["id"] == run_id and data["current_run"]["status"] == "running"
    assert get(ui, "/api/overview").json()["current_run"]["id"] == run_id
    assert get(ui, f"/api/runs/{run_id}").json()["run"]["done"] is False
    r = post(ui, "/run", {"playbook": "baseline"})
    assert r.status_code == 422 and "Another run" in r.json()["error"]
    r = post(ui, f"/runs/{run_id}/stop")
    assert "Stopping the run" in r.json()["message"] and r.json()["kind"] == "warning"
    assert wait_done(ui, run_id).status == "stopped"
    r = post(ui, f"/runs/{run_id}/stop")
    assert r.json()["message"] == "The run isn't running any more."
    ui.app_state.settings.run_env.pop("FAKE_DELAY")


def test_runs_need_sign_in(ui):
    configure(ui)
    run_id = post(ui, "/run", {"playbook": "site"}).json()["run"]["id"]
    wait_done(ui, run_id)
    post(ui, "/logout")
    for path in (f"/runs/{run_id}", f"/runs/{run_id}/log", f"/runs/{run_id}/stream", "/ssh/id_ed25519.pub"):
        assert ui.get(path).url.path == "/login", path
    assert get(ui, f"/api/runs/{run_id}").status_code == 401


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
    # Before a connection test there is no node list.
    assert get(ui, "/api/proxmox").json()["cluster"] == {}

    monkeypatch.setattr(m, "test_proxmox", lambda *a: PveResult(
        version="9.1.1", nodes=["hephaestus", "zeus"], status={"hephaestus": "online", "zeus": "offline"},
        cluster="homelab"))
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX, "pve_node": ""})
    data = r.json()
    assert "cluster homelab" in data["message"] and "now a list" in data["message"]
    assert data["cluster"] == {"name": "homelab", "nodes": {"hephaestus": "online", "zeus": "offline"},
                               "iso_storages": {}}
    assert ui.app_state.store.load().values == {}  # the test still saves no form values
    assert get(ui, "/api/proxmox").json()["cluster"] == data["cluster"]

    # A name that isn't in the cluster is refused, with a hint when only the case differs.
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "Hephaestus"})
    assert r.status_code == 422
    error = r.json()["errors"]["pve_node"]
    assert "Hephaestus isn't a node in the cluster (hephaestus, zeus)" in error
    assert "case-sensitive: did you mean hephaestus?" in error
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "athena"})
    assert r.status_code == 422 and "did you mean" not in r.json()["errors"]["pve_node"]

    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "hephaestus"})
    assert r.json()["message"] == "Saved the Proxmox settings."
    assert ui.app_state.store.load().values["pve_node"] == "hephaestus"
    assert get(ui, "/api/proxmox").json()["values"]["pve_node"] == "hephaestus"


def test_a_damaged_settings_file_is_ignored(ui, settings):
    settings.settings_file.write_text("{not json")
    assert ui.app_state.store.cluster() == {}
    settings.settings_file.write_text('{"cluster": {"name": "x", "nodes": ["not", "a", "dict"]}}')
    assert ui.app_state.store.cluster() == {}
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX})  # no node list: any valid name is accepted
    assert r.json()["message"] == "Saved the Proxmox settings."


def test_the_api_timeout_is_an_advanced_proxmox_setting(ui):
    fields = {f["name"]: f for f in get(ui, "/api/proxmox").json()["fields"]}
    assert fields["pve_api_timeout"]["advanced"] and fields["pve_api_timeout"]["default"] == "60"
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_timeout": "120"})
    assert r.status_code == 200
    assert ui.app_state.store.load().values["pve_api_timeout"] == 120
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_timeout": "1"})
    assert r.status_code == 422


def test_the_iso_storage_is_picked_from_the_storages_that_allow_isos(ui, monkeypatch):
    monkeypatch.setattr(m, "test_proxmox", lambda *a: PveResult(
        version="9.1.1", nodes=["pve-1"], status={"pve-1": "online"},
        iso_storages={"local": "dir", "synology-nfs": "nfs, shared"}))
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX})
    data = r.json()
    assert "the ISO storage a list of the storages that allow ISO images (local, synology-nfs)" in data["message"]
    assert data["test_result"]["iso_storages"] == ["local", "synology-nfs"]
    assert get(ui, "/api/proxmox").json()["cluster"]["iso_storages"] == {"local": "dir", "synology-nfs": "nfs, shared"}

    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": "backup-nfs"})
    assert r.status_code == 422
    assert "backup-nfs isn't a storage that allows ISO images" in r.json()["errors"]["pve_iso_storage"]
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": "synology-nfs"})
    assert r.status_code == 200
    assert ui.app_state.store.load().values["pve_iso_storage"] == "synology-nfs"
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": "bad;id"})
    assert r.status_code == 422
