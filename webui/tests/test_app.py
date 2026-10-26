"""The web UI over HTTP: the page shell, sign-in, the guards, the API behind every page and form, and runs."""

import importlib
import json
import re
import time
from dataclasses import replace

import pytest
import requests
from fastapi.testclient import TestClient
from usas_web import main as m
from usas_web import remote
from usas_web.remote import PveResult, RemoteProblem
from usas_web.sshkeys import HostKey, KeyProblem
from usas_web.store import load_yaml

from .conftest import DEFAULTS, HARDENING, PASSWORD, PROXMOX, TOKEN_SECRET, configure, csrf, make_client, post, sign_up

PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/vms", "/run", "/runs", "/account"]
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


def test_problems_send_only_their_message(ui, monkeypatch):
    from usas_web.problems import Problem

    class Wrapped(RemoteProblem):
        pass

    def failing(*args):
        try:
            raise OSError("/internal/path: permission denied")
        except OSError as inner:
            raise Wrapped("Couldn't connect to pve.example.lan:8006.") from inner

    monkeypatch.setattr(m, "test_proxmox", failing)
    r = post(ui, "/proxmox", {"action": "test", **PROXMOX})
    assert r.json() == {"test_error": "Couldn't connect to pve.example.lan:8006."}
    assert issubclass(KeyProblem, Problem) and issubclass(RemoteProblem, Problem)


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
    users = []
    monkeypatch.setattr(ui.app_state.keys, "fetch_ca", lambda host, user: users.append(user) or make_ca())
    r = post(ui, "/proxmox", {"action": "fetch-ca"})
    assert "Saved the CA certificate" in r.json()["message"] and users == ["root"]
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


ENCRYPTION = {**DEFAULTS, "vm_secure_boot": "on", "vm_disk_encryption": "on",
              "vm_disk_passphrase": "correct horse battery staple"}


def test_disk_encryption(ui, settings):
    r = post(ui, "/defaults", {"action": "save", **ENCRYPTION})
    assert r.json()["message"] == "Saved the defaults."
    store = ui.app_state.store
    assert store.load().values["vm_disk_encryption"] is True
    assert store.read_secrets()["vault_disk_passphrase"] == ENCRYPTION["vm_disk_passphrase"]
    text = settings.inventory_file.read_text()
    assert "vm_disk_encryption: true" in text and "vm_disk_passphrase: '{{ vault_disk_passphrase }}'" in text
    assert ENCRYPTION["vm_disk_passphrase"] not in text
    # Never sent back: the page only says a value is saved.
    data = get(ui, "/api/defaults").json()
    assert ENCRYPTION["vm_disk_passphrase"] not in json.dumps(data) and data["values"]["vm_disk_passphrase"] == ""
    fields = {f["name"]: f for f in data["fields"]}
    assert fields["vm_disk_passphrase"]["saved"] is True and fields["vm_disk_encryption"]["advanced"] is False
    # Leaving it empty keeps it; removing it while encryption is on is refused.
    assert post(ui, "/defaults", {"action": "save", **ENCRYPTION, "vm_disk_passphrase": ""}).status_code == 200
    assert store.read_secrets()["vault_disk_passphrase"] == ENCRYPTION["vm_disk_passphrase"]
    r = post(ui, "/defaults", {"action": "save", **ENCRYPTION, "vm_disk_passphrase": "",
                               "vm_disk_passphrase__clear": "on"})
    assert r.status_code == 422 and "Set a disk passphrase" in r.json()["errors"]["vm_disk_passphrase"]
    # Turned off, the passphrase can go, and the inventory no longer refers to it.
    off = {k: v for k, v in ENCRYPTION.items() if k != "vm_disk_encryption"}
    r = post(ui, "/defaults", {"action": "save", **off, "vm_disk_passphrase": "", "vm_disk_passphrase__clear": "on"})
    assert r.status_code == 200 and "vault_disk_passphrase" not in store.read_secrets()
    assert "vm_disk_encryption" not in settings.inventory_file.read_text()
    assert "vm_disk_passphrase" not in settings.inventory_file.read_text()


def test_disk_encryption_needs_secure_boot_lvm_and_a_passphrase(ui):
    form = {**ENCRYPTION, "autoinstall_storage_layout": "direct", "vm_disk_passphrase": ""}
    form.pop("vm_secure_boot")
    r = post(ui, "/defaults", {"action": "save", **form})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert errors["vm_disk_encryption"] == ("Encrypting the root disk needs Secure Boot keys (under Installation, "
                                            "More settings) and the lvm disk layout.")
    assert "Set a disk passphrase" in errors["vm_disk_passphrase"]
    assert "vm_disk_encryption" not in ui.app_state.store.load().values  # nothing was saved


@pytest.mark.parametrize("passphrase", ["too short", "has {{ braces }} in it", "tab\there is not ok",
                                        "ünïcödé passphrase", "x" * 513])
def test_disk_passphrases_the_role_would_refuse_are_refused(ui, passphrase):
    r = post(ui, "/defaults", {"action": "save", **ENCRYPTION, "vm_disk_passphrase": passphrase})
    assert r.status_code == 422 and set(r.json()["errors"]) == {"vm_disk_passphrase"}
    assert "12 to 512 printable ASCII" in r.json()["errors"]["vm_disk_passphrase"]
    assert "vault_disk_passphrase" not in ui.app_state.store.read_secrets()


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


EXTRAS = {
    "hardening_sysctl_enabled": "on", "hardening_auditd_enabled": "on", "hardening_apparmor_enabled": "on",
    "unattended_upgrades_mail": "updates@example.com", "unattended_upgrades_mail_report": "only-on-error",
    "fail2ban_destemail": "security@example.com", "fail2ban_webhook_url": "https://ntfy.example.com/bans?auth=tk_1",
    "fail2ban_webhook_format": "text", "mail_relay_enabled": "on", "mail_relay_host": "smtp.example.com",
    "mail_relay_port": "465", "mail_relay_tls": "tls", "mail_relay_from": "srv@example.com",
    "mail_relay_user": "relay", "mail_relay_password": 'p "w" \\ x',
}


def test_hardening_extras_and_notifications(ui, settings):
    r = post(ui, "/hardening", {**HARDENING, **EXTRAS})
    assert r.json()["message"] == "Saved the hardening settings."
    store = ui.app_state.store
    values = store.load().values
    assert values["hardening_sysctl_enabled"] is True and "hardening_auditd_immutable" not in values  # the default
    assert values["mail_relay_port"] == 465 and values["unattended_upgrades_mail_report"] == "only-on-error"
    secrets = store.read_secrets()
    assert secrets["vault_fail2ban_webhook_url"] == EXTRAS["fail2ban_webhook_url"]
    assert secrets["vault_mail_relay_password"] == EXTRAS["mail_relay_password"]
    text = settings.inventory_file.read_text()
    assert "fail2ban_webhook_url: '{{ vault_fail2ban_webhook_url }}'" in text and "tk_1" not in text
    assert "mail_relay_password: '{{ vault_mail_relay_password }}'" in text
    data = get(ui, "/api/hardening").json()
    fields = {f["name"]: f for f in data["fields"]}
    assert fields["fail2ban_webhook_url"]["saved"] is True and data["values"]["fail2ban_webhook_url"] == ""

    # Leaving a saved secret empty keeps it; ticking "Remove the saved value" removes it from the Vault and
    # the inventory.
    post(ui, "/hardening", {**HARDENING, **EXTRAS, "fail2ban_webhook_url": "", "mail_relay_password": ""})
    assert store.read_secrets()["vault_fail2ban_webhook_url"] == EXTRAS["fail2ban_webhook_url"]
    post(ui, "/hardening", {**HARDENING, **EXTRAS, "fail2ban_webhook_url": "", "fail2ban_webhook_url__clear": "on"})
    assert "vault_fail2ban_webhook_url" not in store.read_secrets()
    assert "fail2ban_webhook_url" not in settings.inventory_file.read_text()


@pytest.mark.parametrize("name,raw", [
    ("fail2ban_webhook_url", "https://ntfy.example.com/x'$(id)'"),
    ("fail2ban_webhook_url", "https://ntfy.example.com/%(action_)s"),
    ("fail2ban_destemail", "a@example.com\naction = x"),
    ("unattended_upgrades_mail", 'a@example.com";'),
    ("mail_relay_host", "smtp.example.com host"),
    ("mail_relay_user", 'relay"'),
    ("mail_relay_password", "two\nlines"),
    ("mail_relay_tls", "maybe"),
])
def test_notification_values_that_could_inject_settings_are_refused(ui, name, raw):
    r = post(ui, "/hardening", {**HARDENING, **EXTRAS, name: raw})
    assert r.status_code == 422 and set(r.json()["errors"]) == {name}


def test_servers(ui):
    configure(ui, servers=2)
    data = get(ui, "/api/servers").json()
    assert [c["name"] for c in data["columns"]][:4] == ["name", "ansible_host", "net_mode", "net_mac"]
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
    assert errors["servers-2-name"] == "That is the name of a Proxmox node."
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


def test_servers_networks(ui):
    configure(ui)
    row = {"server_count": "1", "servers-0-name": "app-01", "servers-0-pve_vmid": "4100"}

    def errors(**fields):
        r = post(ui, "/servers", {**row, **{f"servers-0-{k}": v for k, v in fields.items()}})
        assert r.status_code == 422
        return r.json()["errors"]

    assert "static IP address, or choose dhcp" in errors(net_mode="static")["servers-0-ansible_host"]
    assert "Address must be one of: static, dhcp" in errors(net_mode="bootp")["servers-0-net_mode"]
    assert "isn't in the accepted format" in errors(net_mode="dhcp", net_mac="01:00:00:00:00:01")["servers-0-net_mac"]
    assert "remove via" in errors(net_mode="dhcp", vm_extra_nics="vmbr1 10.2.0.5/24 via 10.2.0.1")[
        "servers-0-vm_extra_nics"]
    assert "Only one card" in errors(ansible_host="10.0.0.9", vm_extra_nics="vmbr1 10.2.0.5/24 via 10.2.0.1, "
                                     "vmbr2 10.3.0.5/24 via 10.3.0.1")["servers-0-vm_extra_nics"]
    assert "its own MAC address" in errors(net_mode="dhcp", net_mac="bc:24:11:00:00:01",
                                           vm_extra_nics="vmbr1 dhcp mac BC:24:11:00:00:01")["servers-0-vm_extra_nics"]
    assert "vlan takes a VLAN tag" in errors(net_mode="dhcp", vm_extra_nics="vmbr1 vlan 0 dhcp")[
        "servers-0-vm_extra_nics"]
    two = {"server_count": "2", "servers-0-name": "a", "servers-0-pve_vmid": "4100", "servers-0-net_mode": "dhcp",
           "servers-0-net_mac": "BC:24:11:00:00:01", "servers-1-name": "b", "servers-1-pve_vmid": "4101",
           "servers-1-net_mode": "dhcp", "servers-1-net_mac": "bc:24:11:00:00:01"}
    assert post(ui, "/servers", two).json()["errors"] == {"servers-1-net_mac": "Another server already uses this."}

    # Saved: no address for the DHCP server, the cards as the role's list, and the gateway moved to a card.
    r = post(ui, "/servers", {"server_count": "3", "servers-0-name": "app-01", "servers-0-pve_vmid": "4100",
                              "servers-0-net_mode": "dhcp", "servers-0-net_mac": "bc:24:11:00:00:01",
                              "servers-0-vm_extra_nics": "vmbr1 vlan 30 dhcp, vmbr2 none",
                              "servers-1-name": "gw-01", "servers-1-pve_vmid": "4101", "servers-1-net_mode": "static",
                              "servers-1-ansible_host": "10.0.0.11",
                              "servers-1-vm_extra_nics": "vmbr1 vlan 20 10.20.0.11/24 via 10.20.0.1 mtu 9000",
                              "servers-2-net_mode": "static"})
    assert r.json()["message"] == "Saved 2 server(s)."
    text = ui.app_state.settings.inventory_file.read_text()
    hosts = load_yaml(text)["all"]["children"]["ubuntu_servers"]["hosts"]
    assert hosts["app-01"] == {"pve_vmid": 4100, "net_mode": "dhcp", "net_mac": "BC:24:11:00:00:01",
                               "vm_extra_nics": [{"mode": "dhcp", "bridge": "vmbr1", "vlan": 30},
                                                 {"mode": "none", "bridge": "vmbr2"}]}
    assert hosts["gw-01"]["net_gateway"] == "" and "net_mode" not in hosts["gw-01"]
    assert hosts["gw-01"]["vm_extra_nics"] == [{"mode": "static", "bridge": "vmbr1", "vlan": 20,
                                                "address": "10.20.0.11", "prefix": 24, "gateway": "10.20.0.1",
                                                "mtu": 9000}]
    assert "!unsafe 'vmbr1'" in text  # every typed string is tagged
    rows = get(ui, "/api/servers").json()["rows"]
    assert rows[0]["ansible_host"] == "" and rows[0]["net_mode"] == "dhcp"
    assert rows[0]["vm_extra_nics"] == "vmbr1 vlan 30 dhcp, vmbr2 none"
    assert rows[1]["vm_extra_nics"] == "vmbr1 vlan 20 10.20.0.11/24 via 10.20.0.1 mtu 9000"

    gateway_step = next(s for s in get(ui, "/api/overview").json()["steps"] if "gateway" in s["label"])
    # Without a gateway on the Defaults page, a static server needs a card with one.
    assert post(ui, "/defaults", {"action": "save", **{**DEFAULTS, "net_gateway": ""}}).status_code == 200
    assert "gateway on the Defaults page" in errors(ansible_host="10.0.0.9")["servers-0-vm_extra_nics"]
    assert post(ui, "/servers", {**row, "servers-0-ansible_host": "10.0.0.9",
                                 "servers-0-vm_extra_nics": "vmbr1 10.2.0.5/24 via 10.2.0.1"}).status_code == 200
    def gateway_done():
        return {s["label"]: s["done"] for s in get(ui, "/api/overview").json()["steps"]}[gateway_step["label"]]

    assert gateway_step["done"] and gateway_done()  # every server has its default route without that gateway
    post(ui, "/servers", {"server_count": "0"})
    assert not gateway_done()


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


def test_a_node_user_other_than_root(ui, monkeypatch):
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    post(ui, "/ssh", {"action": "address", "node": "pve-1", "node_address": "10.0.0.2"})
    assert get(ui, "/api/ssh").json()["node_user"] == "root"
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_ssh_user": "usas-build;id"})
    assert r.status_code == 422 and "pve_ssh_user" in r.json()["errors"]
    r = post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_ssh_user": "usas-build"})
    assert r.status_code == 200 and get(ui, "/api/ssh").json()["node_user"] == "usas-build"
    text = ui.app_state.settings.inventory_file.read_text()
    assert "pve_ssh_user: !unsafe 'usas-build'" in text and "ansible_user: !unsafe 'usas-build'" in text
    # Every node is reached as that user.
    config = ui.app_state.store.load()
    config.node_addresses["pve-2"] = "10.0.0.3"
    ui.app_state.store.save(config)
    hosts = load_yaml(ui.app_state.settings.inventory_file.read_text())["all"]["children"]["proxmox_nodes"]["hosts"]
    assert {host["ansible_user"] for host in hosts.values()} == {"usas-build"} and set(hosts) == {"pve-1", "pve-2"}
    r = post(ui, "/ssh", {"action": "generate"})
    assert "Add its public key to usas-build's authorized_keys" in r.json()["message"]
    keys = ui.app_state.keys
    monkeypatch.setattr(keys, "test_login", lambda host, user: f"Logged in to {user}@{host} with the UI's key.")
    assert "usas-build@10.0.0.2" in post(ui, "/ssh", {"action": "test", "node": "pve-1"}).json()["message"]


def test_no_public_key_yet(ui):
    assert ui.get("/ssh/id_ed25519.pub").status_code == 404


def cluster_test(ui, monkeypatch, nodes, iso_storages=None):
    """A connection test that lists these nodes (and ISO storages), so the UI knows the cluster."""
    monkeypatch.setattr(m, "test_proxmox", lambda *a: PveResult(
        version="9.1.1", nodes=nodes, status=dict.fromkeys(nodes, "online"), cluster="homelab",
        iso_storages=iso_storages or {}))
    assert post(ui, "/proxmox", {"action": "test", **PROXMOX}).status_code == 200


def test_servers_pick_their_node_from_the_cluster(ui, monkeypatch):
    configure(ui, servers=2)
    cluster_test(ui, monkeypatch, ["pve-1", "pve-2"])
    data = get(ui, "/api/servers").json()
    assert data["nodes"] == ["pve-1", "pve-2"] and data["default_node"] == "pve-1"
    names = [c["name"] for c in data["columns"]]
    assert names[names.index("pve_vmid") + 1] == "pve_node" and data["rows"][0]["pve_node"] == ""
    base = {"server_count": "2", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
            "servers-0-pve_vmid": "4000", "servers-1-name": "web-01", "servers-1-ansible_host": "10.0.0.11",
            "servers-1-pve_vmid": "4001"}
    r = post(ui, "/servers", {**base, "servers-0-pve_node": "PVE-2", "servers-1-pve_node": "bad node"})
    errors = r.json()["errors"]
    assert "PVE-2 isn't a node in the cluster (pve-1, pve-2)" in errors["servers-0-pve_node"]
    assert "did you mean pve-2?" in errors["servers-0-pve_node"]
    assert "accepted format" in errors["servers-1-pve_node"]
    # A server can't be named like a node, the default one or another server's.
    r = post(ui, "/servers", {**base, "servers-0-pve_node": "pve-2", "servers-1-name": "pve-2"})
    assert r.json()["errors"] == {"servers-1-name": "That is the name of a Proxmox node."}
    r = post(ui, "/servers", {**base, "servers-1-pve_node": "pve-2"})
    assert r.json()["message"] == "Saved 2 server(s)."
    config = ui.app_state.store.load()
    assert "pve_node" not in config.servers[0] and config.servers[1]["pve_node"] == "pve-2"
    assert config.nodes_in_use() == ["pve-1", "pve-2"]
    assert get(ui, "/api/servers").json()["rows"][1]["pve_node"] == "pve-2"


@pytest.mark.parametrize(("storage", "kind", "expected"), [
    ("local", "dir", [("pve-1", "local", False, "pve-1"), ("pve-2", "local", False, "pve-2")]),
    ("synology-nfs", "nfs, shared",
     [("pve-1", "synology-nfs", True, "pve-1"), ("pve-2", "synology-nfs", True, "pve-1")]),
])
def test_run_page_shows_where_the_iso_goes(ui, monkeypatch, storage, kind, expected):
    configure(ui, servers=2)
    cluster_test(ui, monkeypatch, ["pve-1", "pve-2"], {storage: kind})
    post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_iso_storage": storage})
    post(ui, "/servers", {"server_count": "2", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
                          "servers-0-pve_vmid": "4000", "servers-1-name": "web-01",
                          "servers-1-ansible_host": "10.0.0.11", "servers-1-pve_vmid": "4001",
                          "servers-1-pve_node": "pve-2"})
    plan = get(ui, "/api/run").json()["iso_plan"]
    assert plan["storage"] == storage and plan["known"] is True and plan["shared"] == ("shared" in kind)
    assert [(p["node"], p["storage"], p["shared"], p["built_by"]) for p in plan["pairs"]] == expected
    assert plan["pairs"][1]["servers"] == ["web-01"]
    assert plan["builders"] == sorted({e[3] for e in expected})


def test_run_page_plan_before_a_connection_test(ui):
    configure(ui)
    plan = get(ui, "/api/run").json()["iso_plan"]
    assert plan["known"] is False and plan["pairs"][0]["node"] == "pve-1" and plan["pairs"][0]["servers"] == ["web-00"]


def test_node_host_key(ui, monkeypatch):
    r = post(ui, "/ssh", {"action": "address", "node": "pve-1", "node_address": "10.0.0.2"})
    assert r.status_code == 422 and "isn't one of the nodes" in r.json()["error"]
    assert get(ui, "/api/ssh").json()["nodes"] == []
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    r = post(ui, "/ssh", {"action": "address", "node": "pve-1", "node_address": "-oProxyCommand=id"})
    assert r.status_code == 422 and r.json()["area"] == "address" and r.json()["node"] == "pve-1"
    r = post(ui, "/ssh", {"action": "address", "node": "pve-1", "node_address": "10.0.0.2"})
    assert "Saved node pve-1's SSH address" in r.json()["message"]
    keys = ui.app_state.keys
    offered = [HostKey("10.0.0.2", "ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMq")]
    monkeypatch.setattr(keys, "scan", lambda host: offered)
    r = post(ui, "/ssh", {"action": "scan", "node": "pve-1"})
    assert r.status_code == 200
    data = get(ui, "/api/ssh").json()
    assert data["pending"] == {"node": "pve-1",
                               "keys": [{"keytype": "ssh-ed25519", "fingerprint": offered[0].fingerprint}]}
    assert data["nodes"] == [{"name": "pve-1", "address": "10.0.0.2", "default": True, "in_use": True,
                              "servers": [], "trusted": []}]
    r = post(ui, "/ssh", {"action": "trust", "node": "pve-1"})
    assert r.status_code == 422 and "tick the box" in r.json()["error"] and r.json()["area"] == "hostkey"
    post(ui, "/ssh", {"action": "scan", "node": "pve-1"})
    r = post(ui, "/ssh", {"action": "trust", "node": "pve-1", "confirm": "on"})
    assert "Trusted 10.0.0.2" in r.json()["message"] and keys.trusted("10.0.0.2") == offered
    assert get(ui, "/api/ssh").json()["nodes"][0]["trusted"][0]["fingerprint"] == offered[0].fingerprint
    monkeypatch.setattr(keys, "test_login", lambda host, user: f"Logged in to {user}@{host} with the UI's key.")
    assert "Logged in to root@10.0.0.2" in post(ui, "/ssh", {"action": "test", "node": "pve-1"}).json()["message"]

    def failing(host, user):
        raise KeyProblem("SSH to root@10.0.0.2 failed: Permission denied")

    monkeypatch.setattr(keys, "test_login", failing)
    r = post(ui, "/ssh", {"action": "test", "node": "pve-1"})
    assert r.status_code == 422 and "Permission denied" in r.json()["error"] and r.json()["area"] == "hostkey"
    r = post(ui, "/ssh", {"action": "forget", "node": "pve-1"})
    assert "Forgot" in r.json()["message"] and keys.trusted("10.0.0.2") == []
    # A new address drops host keys scanned for the old one.
    post(ui, "/ssh", {"action": "scan", "node": "pve-1"})
    post(ui, "/ssh", {"action": "address", "node": "pve-1", "node_address": "10.0.0.3"})
    assert get(ui, "/api/ssh").json()["pending"] is None


def test_scan_needs_an_address(ui):
    post(ui, "/proxmox", {"action": "save", **PROXMOX})
    r = post(ui, "/ssh", {"action": "scan", "node": "pve-1"})
    assert r.status_code == 422 and "Save node pve-1's SSH address first" in r.json()["error"]


def test_each_node_in_use_gets_its_own_address_and_host_key(ui, monkeypatch):
    configure(ui, servers=3)
    cluster_test(ui, monkeypatch, ["pve-1", "pve-2", "pve-3"])
    rows = {"server_count": "3", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
            "servers-0-pve_vmid": "4000", "servers-1-name": "web-01", "servers-1-ansible_host": "10.0.0.11",
            "servers-1-pve_vmid": "4001", "servers-1-pve_node": "pve-2", "servers-2-name": "web-02",
            "servers-2-ansible_host": "10.0.0.12", "servers-2-pve_vmid": "4002", "servers-2-pve_node": "pve-2"}
    assert post(ui, "/servers", rows).status_code == 200
    data = get(ui, "/api/ssh").json()
    assert [(n["name"], n["default"], n["servers"]) for n in data["nodes"]] == [
        ("pve-1", True, ["web-00"]), ("pve-2", False, ["web-01", "web-02"])]
    keys = ui.app_state.keys
    monkeypatch.setattr(keys, "scan", lambda host: [HostKey(host, "ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMq")])
    for node, address in (("pve-1", "10.0.0.2"), ("pve-2", "10.0.0.3")):
        assert post(ui, "/ssh", {"action": "address", "node": node, "node_address": address}).status_code == 200
    # Keys scanned for one node can't be trusted for another.
    post(ui, "/ssh", {"action": "scan", "node": "pve-1"})
    r = post(ui, "/ssh", {"action": "trust", "node": "pve-2", "confirm": "on"})
    assert r.status_code == 422 and keys.trusted("10.0.0.3") == []
    post(ui, "/ssh", {"action": "scan", "node": "pve-1"})
    post(ui, "/ssh", {"action": "trust", "node": "pve-1", "confirm": "on"})
    steps = {s["label"]: s["done"] for s in get(ui, "/api/overview").json()["steps"]}
    assert steps["Each Proxmox node's SSH address and trusted host key"] is False  # pve-2 isn't trusted yet
    post(ui, "/ssh", {"action": "scan", "node": "pve-2"})
    post(ui, "/ssh", {"action": "trust", "node": "pve-2", "confirm": "on"})
    steps = {s["label"]: s["done"] for s in get(ui, "/api/overview").json()["steps"]}
    assert steps["Each Proxmox node's SSH address and trusted host key"] is True
    inventory = ui.app_state.settings.inventory_file.read_text()
    hosts = load_yaml(inventory)["all"]["children"]["proxmox_nodes"]["hosts"]
    assert {name: host["ansible_host"] for name, host in hosts.items()} == {"pve-1": "10.0.0.2", "pve-2": "10.0.0.3"}
    assert "ansible_host: !unsafe '10.0.0.3'" in inventory and "pve_node: !unsafe 'pve-2'" in inventory
    assert {"pve-1", "pve-2"} <= ui.app_state.store.host_names()
    # A node no server uses any more keeps its address until it is removed; one in use can't be removed.
    r = post(ui, "/ssh", {"action": "remove", "node": "pve-2"})
    assert r.status_code == 422 and "Servers still use pve-2" in r.json()["error"]
    rows.pop("servers-1-pve_node")
    rows.pop("servers-2-pve_node")
    post(ui, "/servers", rows)
    data = get(ui, "/api/ssh").json()
    assert [(n["name"], n["in_use"]) for n in data["nodes"]] == [("pve-1", True), ("pve-2", False)]
    assert "Removed node pve-2's" in post(ui, "/ssh", {"action": "remove", "node": "pve-2"}).json()["message"]
    assert [n["name"] for n in get(ui, "/api/ssh").json()["nodes"]] == ["pve-1"]


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


def test_the_entry_point_logs_what_happens_at_start_up(monkeypatch, settings, capsys):
    # The Vault repair (and anything else create_app logs) used to go nowhere: the handler came after it.
    import usas_web.__main__ as entry

    def create_app(s):
        entry.logging.getLogger("usas_web").info("Rewrote the Vault")
        return m.create_app(s)

    monkeypatch.setattr(entry, "from_env", lambda: settings)
    monkeypatch.setattr(entry, "create_app", create_app)
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: None)
    ui_log = entry.logging.getLogger("usas_web")
    before = list(ui_log.handlers)
    try:
        entry.main()
    finally:
        for handler in ui_log.handlers[len(before):]:
            ui_log.removeHandler(handler)
    assert "INFO:     Rewrote the Vault" in capsys.readouterr().err


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


# --- VMs ----------------------------------------------------------------------------------------------------


def fake_pve(monkeypatch, version="9.1.1", agent_status=200, resources=None, calls=None):
    """Answers the VMs page's API calls: two VMs, one with an agent."""
    def get(url, **kw):
        path = url.split("/api2/json", 1)[1]
        if calls is not None:
            calls.append((url, kw))
        data = {
            "/version": {"version": version},
            "/cluster/resources?type=vm": [
                {"vmid": 4000, "name": "web-00", "node": "pve-1", "type": "qemu", "status": "running"},
                {"vmid": 101, "name": "app", "node": "pve-1", "type": "qemu", "status": "running"},
            ] if resources is None else resources,
            "/nodes/pve-1/qemu/4000/config": {"ostype": "l26"},
            "/nodes/pve-1/qemu/101/config": {"agent": "1", "ostype": "l26"},
            "/nodes/pve-1/qemu/101/agent/network-get-interfaces": {"result": [
                {"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.51", "ip-address-type": "ipv4"}]}]},
            "/nodes/pve-1/qemu/101/agent/get-osinfo": {"result": {"id": "ubuntu", "version-id": "26.04"}},
            "/nodes/pve-1/qemu/101/agent/get-host-name": {"result": {"host-name": "app"}},
        }.get(path)
        status = agent_status if "/agent/" in path else 200 if data is not None else 500
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({"data": data} if status < 400 else {"data": None, "message": "nope"}).encode()
        return response
    monkeypatch.setattr(remote.requests, "get", get)


def test_vms_needs_sign_in_and_the_api(ui, monkeypatch):
    r = get(ui, "/api/vms")
    assert r.status_code == 409 and r.json()["setup"] == "/proxmox" and "Proxmox API first" in r.json()["error"]
    post(ui, "/logout")
    r = get(ui, "/api/vms")
    assert r.status_code == 401 and r.json()["redirect"] == "/login?next=/vms"


def test_vms_lists_the_cluster(ui, monkeypatch):
    configure(ui)
    calls = []
    fake_pve(monkeypatch, calls=calls)
    r = get(ui, "/api/vms")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == "9.1.1" and data["complete"] and data["notes"] == [] and data["fetched"] > 0
    rows = {row["vmid"]: row for row in data["vms"]}
    # The inventory knows VM 4000 (web-00); the guest agent knows VM 101.
    assert (rows[4000]["ip"], rows[4000]["ip_source"], rows[4000]["inventory_name"]) == \
        ("10.0.0.10", "inventory", "web-00")
    assert (rows[101]["ip"], rows[101]["ip_source"], rows[101]["support"]) == ("10.0.0.51", "agent", "supported")
    url, kw = calls[0]
    assert url.startswith("https://pve.example.lan:8006/api2/json/")
    assert kw["headers"]["Authorization"] == f"PVEAPIToken=ansible@pve!automation={TOKEN_SECRET}"
    assert kw["verify"] is True and kw["timeout"] == m.VM_API_TIMEOUT  # verified TLS, a short timeout
    assert TOKEN_SECRET not in r.text


@pytest.mark.parametrize("version,words", [("9.1.1", "add VM.GuestAgent.Audit"), ("8.4.14", "needs VM.Monitor")])
def test_vms_explains_a_forbidden_agent(ui, monkeypatch, version, words):
    configure(ui)
    fake_pve(monkeypatch, version=version, agent_status=403)
    data = get(ui, "/api/vms").json()
    assert data["forbidden"] == 1 and len(data["notes"]) == 1 and words in data["notes"][0]["message"]


def test_vms_without_tls_verification_says_so(ui, monkeypatch):
    configure(ui)
    post(ui, "/proxmox", {"action": "save", **{**PROXMOX, "pve_validate_certs": ""}})
    calls = []
    fake_pve(monkeypatch, calls=calls)
    data = get(ui, "/api/vms").json()
    assert calls[0][1]["verify"] is False and "isn't verified" in data["notes"][0]["message"]


def test_vms_when_the_cluster_cant_be_listed(ui, monkeypatch):
    configure(ui)

    def down(url, **kw):
        raise requests.exceptions.ConnectionError()
    monkeypatch.setattr(remote.requests, "get", down)
    r = get(ui, "/api/vms")
    assert r.status_code == 502 and "Couldn't connect to pve.example.lan:8006" in r.json()["error"]


# --- VMs: selecting VMs and running the baseline against them in check mode ------------------------------------

ED = "AAAAC3NzaC1lZDI1NTE5AAAAIOMq"


def finished(c, run_id):
    deadline = time.time() + 20
    while time.time() < deadline:
        run = c.app_state.runner.get(run_id)
        if run.done:
            return run
        time.sleep(0.05)
    raise AssertionError("the run didn't finish")


def scan_answers(monkeypatch, calls=None):
    from usas_web import sshkeys

    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        if argv[0] == "ssh-keyscan":
            return sshkeys.subprocess.CompletedProcess(argv, 0, f"{argv[-1]} ssh-ed25519 {ED}\n", "")
        return sshkeys.subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(sshkeys.subprocess, "run", run)


def test_vms_pin_plan_and_check(ui, monkeypatch):
    configure(ui)
    fake_pve(monkeypatch)
    calls = []
    scan_answers(monkeypatch, calls)
    rows = {row["vmid"]: row for row in get(ui, "/api/vms").json()["vms"]}
    assert rows[101]["selectable"] and rows[101]["alias"] == "usas-pve-101" and rows[101]["pinned"] == []
    data = get(ui, "/api/vms").json()
    assert data["run"]["default_preset"] == "full" and data["run"]["max_hosts"] == 25
    assert data["run"]["user"] == "sysadmin"

    # Pin VM 101's key: scan, then trust only with the box ticked.
    r = post(ui, "/vms/hostkey", {"action": "scan", "vmid": "101", "address": "10.0.0.51", "port": "2222"})
    assert r.status_code == 200 and r.json()["scanned"][0]["fingerprint"].startswith("SHA256:")
    assert calls[-1][-4:] == ["-p", "2222", "--", "10.0.0.51"]
    assert post(ui, "/vms/hostkey", {"action": "trust", "vmid": "101"}).status_code == 422
    post(ui, "/vms/hostkey", {"action": "scan", "vmid": "101", "address": "10.0.0.51", "port": ""})
    r = post(ui, "/vms/hostkey", {"action": "trust", "vmid": "101", "confirm": "on"})
    assert r.status_code == 200 and r.json()["pinned"]
    assert "usas-pve-101" in ui.app_state.keys.pins()
    ui.app_state.keys.generate()
    r = post(ui, "/vms/hostkey", {"action": "test", "vmid": "101", "address": "10.0.0.51", "port": "",
                                  "user": "ubuntu"})
    assert r.status_code == 200 and "ubuntu@10.0.0.51" in r.json()["message"]
    r = post(ui, "/vms/hostkey", {"action": "test", "vmid": "101", "address": "10.0.0.51", "user": "Root"})
    assert r.status_code == 422 and "Linux user name" in r.json()["error"]

    # web-00 (VM 4000) has no pin yet, and no known key: the plan says so per VM.
    plan = {"vmid": ["101", "4000"], "preset": "full", "vm-101-address": "10.0.0.51", "vm-101-user": "ubuntu"}
    r = post(ui, "/vms/plan", plan)
    assert r.status_code == 422 and "Trust VM 4000" in r.json()["errors"]["vm-4000-hostkey"]
    # A server the project installed was trusted then: its pin starts from that entry.
    known = ui.app_state.settings.known_hosts
    known.write_text(known.read_text() + f"10.0.0.10 ssh-ed25519 {ED}\n")
    r = post(ui, "/vms/plan", plan)
    assert r.status_code == 200, r.text
    reviewed = r.json()
    assert [t["name"] for t in reviewed["plan"]["targets"]] == ["vm-101", "web-00"]
    assert any("firewall" in w for w in reviewed["plan"]["warnings"])

    r = post(ui, "/vms/run", {"plan_id": reviewed["plan_id"], "mode": "apply"})
    assert r.status_code == 422 and "Only check runs" in r.json()["error"]
    r = post(ui, "/vms/run", {"plan_id": "nope", "mode": "check"})
    assert r.status_code == 422 and "expired" in r.json()["error"]
    r = post(ui, "/vms/run", {"plan_id": reviewed["plan_id"], "mode": "check"})
    assert r.status_code == 200, r.text
    run = finished(ui, r.json()["run"]["id"])
    assert (run.kind, run.check, run.diff, run.plan) == ("vms", True, True, reviewed["plan"]["digest"])
    assert [t["vmid"] for t in run.targets] == [101, 4000]
    log = ui.app_state.runner.log_path(run.id).read_text()
    assert "--check --diff --limit=vm-101,web-00 playbooks/baseline.yml" in log
    assert f"{run.id}.inventory.yml" in log and "--tags" not in log
    inventory = ui.app_state.runner.path(run.id, ".inventory.yml").read_text()
    assert TOKEN_SECRET not in inventory and "admin pass" not in inventory
    detail = get(ui, f"/api/runs/{run.id}").json()
    assert detail["hosts"] is None and detail["run"]["kind"] == "vms"
    ui.app_state.runner.path(run.id, ".hosts.json").write_text(json.dumps(
        {"finished": True, "hosts": {"vm-101": {"ok": 1, "failed_tasks": [{"task": "t", "message": TOKEN_SECRET}]}}}))
    hosts = get(ui, f"/api/runs/{run.id}/hosts").json()
    assert hosts["done"] and hosts["hosts"]["hosts"]["vm-101"]["failed_tasks"][0]["message"] == "********"
    assert get(ui, "/api/runs/20250101-000000-abcdef/hosts").status_code == 404

    assert post(ui, "/vms/hostkey", {"action": "forget", "vmid": "101"}).status_code == 200
    assert "usas-pve-101" not in ui.app_state.keys.pins()


def test_vms_plan_refuses(ui, monkeypatch):
    configure(ui)
    fake_pve(monkeypatch)
    scan_answers(monkeypatch)
    for data in ({"action": "scan", "vmid": "12"}, {"action": "rm", "vmid": "101"}):
        assert post(ui, "/vms/hostkey", data).status_code == 403
    r = post(ui, "/vms/hostkey", {"action": "scan", "vmid": "101", "address": "-oProxyCommand=id"})
    assert r.status_code == 422
    assert post(ui, "/vms/plan", {"vmid": ["101;id"], "preset": "full"}).status_code == 403
    r = post(ui, "/vms/plan", {"vmid": ["101"], "preset": "everything"})
    assert r.status_code == 422 and "preset" in r.json()["errors"]
    r = post(ui, "/vms/plan", {"vmid": [str(n) for n in range(100, 126)], "preset": "full"})
    assert r.status_code == 422 and "at most 25" in r.json()["errors"]["selection"]
    ui.app_state.keys.generate()
    r = post(ui, "/vms/plan", {"vmid": ["101"], "preset": "keys", "vm-101-address": "10.0.0.51",
                               "vm-101-user": "ubuntu"})
    assert r.status_code == 422 and "Trust VM 101" in r.json()["errors"]["vm-101-hostkey"]


def test_vms_plan_needs_the_api_and_a_cluster(ui, monkeypatch):
    r = post(ui, "/vms/plan", {"vmid": ["101"], "preset": "full"})
    assert r.status_code == 409
    configure(ui)
    ui.app_state.keys.generate()

    def down(url, **kw):
        raise requests.exceptions.ConnectionError()
    monkeypatch.setattr(remote.requests, "get", down)
    r = post(ui, "/vms/plan", {"vmid": ["101"], "preset": "full"})
    assert r.status_code == 502


def test_vms_run_refused_while_another_runs(ui, monkeypatch):
    configure(ui)
    fake_pve(monkeypatch)
    scan_answers(monkeypatch)
    r = post(ui, "/vms/plan", {"vmid": ["101"], "preset": "keys"})
    assert r.status_code == 422 and "SSH key first" in r.json()["errors"]["selection"]
    ui.app_state.keys.generate()
    post(ui, "/vms/hostkey", {"action": "scan", "vmid": "101", "address": "10.0.0.51"})
    post(ui, "/vms/hostkey", {"action": "trust", "vmid": "101", "confirm": "on"})
    reviewed = post(ui, "/vms/plan", {"vmid": ["101"], "preset": "keys", "vm-101-address": "10.0.0.51",
                                      "vm-101-user": "ubuntu"}).json()
    ui.app_state.runner.settings.run_env["FAKE_DELAY"] = "5"
    first = post(ui, "/vms/run", {"plan_id": reviewed["plan_id"], "mode": "check"}).json()["run"]
    r = post(ui, "/vms/run", {"plan_id": reviewed["plan_id"], "mode": "check"})
    assert r.status_code == 422 and "Another run" in r.json()["error"]
    ui.app_state.runner.stop(first["id"])
    finished(ui, first["id"])
    # Plans expire.
    session_plans = next(iter(ui.app_state.auth._sessions.values())).vm_plans
    for key, (_, plan) in list(session_plans.items()):
        session_plans[key] = (0.0, plan)
    r = post(ui, "/vms/run", {"plan_id": reviewed["plan_id"], "mode": "check"})
    assert r.status_code == 422 and "expired" in r.json()["error"]
