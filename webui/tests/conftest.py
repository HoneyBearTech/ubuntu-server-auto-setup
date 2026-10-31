import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from usas_web.config import from_env
from usas_web.main import create_app

REPO = Path(__file__).resolve().parents[2]
PASSWORD = "correct horse battery staple"
TOKEN_SECRET = "00000000-0000-4000-8000-000000000000"


def csrf(c) -> str:
    """The session's CSRF token, as the app gets it (the page shell has it too)."""
    return c.get("/api/session").json()["csrf"]


@pytest.fixture
def settings(tmp_path):
    return from_env({"USAS_PROJECT_DIR": str(REPO), "USAS_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def fake_ansible(tmp_path):
    """A stand-in for ansible-playbook: prints its arguments, a secret and a few lines, then exits."""
    script = tmp_path / "ansible-playbook"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import os, sys, time
        print("ARGS", " ".join(sys.argv[1:]), flush=True)
        print("SECRET {TOKEN_SECRET}", flush=True)
        print("CA", os.environ.get("SSL_CERT_FILE", "none"), flush=True)
        delay = float(os.environ.get("FAKE_DELAY", "0"))
        time.sleep(delay)
        print("PLAY RECAP ok=1 failed=0", flush=True)
        sys.exit(int(os.environ.get("FAKE_RC", "0")))
        """))
    script.chmod(0o755)
    return script


def make_client(settings, base_url="http://localhost"):
    app = create_app(settings)
    return TestClient(app, base_url=base_url), app


@pytest.fixture
def client(settings, fake_ansible):
    c, app = make_client(replace(settings, ansible_playbook=str(fake_ansible)))
    c.app_state = app.state
    return c


def post(c, path, data=None, files=None):
    """Posts a form to the API for a page (/proxmox posts to /api/proxmox) as the app does: the same field
    names, the session's CSRF token in X-CSRF-Token, and the page it was sent from in X-USAS-Page."""
    return c.post("/api" + path, data=data or {}, files=files,
                  headers={"X-CSRF-Token": csrf(c), "X-USAS-Page": path})


def sign_up(c) -> None:
    r = post(c, "/setup", {"setup_code": c.app_state.auth.setup_code, "password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 200 and r.json()["redirect"] == "/proxmox", r.text[:500]


@pytest.fixture
def ui(client):
    sign_up(client)
    return client


PROXMOX = {
    "pve_api_host": "pve.example.lan", "pve_api_port": "8006", "pve_api_user": "ansible@pve",
    "pve_api_token_id": "automation", "pve_api_token_secret": TOKEN_SECRET, "pve_validate_certs": "on",
    "pve_node": "pve-1", "pve_storage": "local-lvm", "pve_efidisk_format": "raw", "pve_bridge": "vmbr0",
    "pve_vlan": "", "pve_iso_storage": "local",
}
DEFAULTS = {
    "github_user": "octocat", "github_keys_url": "https://github.com/{{ github_user }}.keys",
    "admin_extra_ssh_keys": "", "authorize_ui_key": "on", "admin_user": "sysadmin",
    "admin_password": "admin pass {{ x }}", "timezone": "Europe/Berlin", "net_gateway": "10.0.0.1",
    "net_prefix": "24", "net_dns": "10.0.0.1, 1.1.1.1", "net_search": "example.lan",
    "time_sync_servers": "ntp1.example.lan, 192.0.2.123", "autoinstall_storage_layout": "lvm",
}
HARDENING = {
    "ssh_port": "22", "ssh_allow_users": "", "ssh_permit_root_login": "no", "admin_sudo_nopasswd": "on",
    "firewall_ssh_limit": "on", "firewall_ssh_sources": "any", "firewall_allow": "", "fail2ban_ignoreip": "10.0.0.0/24",
    "fail2ban_maxretry": "5", "fail2ban_findtime": "10m", "fail2ban_bantime": "1h",
    "fail2ban_bantime_increment": "on", "fail2ban_bantime_maxtime": "1w", "fail2ban_sshd_mode": "normal",
    "fail2ban_banaction": "",
}


def configure(c, servers=1):
    assert post(c, "/proxmox", {"action": "save", **PROXMOX}).status_code == 200
    assert post(c, "/defaults", {"action": "save", **DEFAULTS}).status_code == 200
    rows = {"server_count": str(servers), "action": "save"}
    for i in range(servers):
        rows.update({f"servers-{i}-name": f"web-{i:02d}", f"servers-{i}-ansible_host": f"10.0.0.{10 + i}",
                     f"servers-{i}-pve_vmid": str(4000 + i), f"servers-{i}-firewall_ports": "443/tcp"})
    assert post(c, "/servers", rows).status_code == 200
