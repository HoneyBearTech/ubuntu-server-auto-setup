"""The front end's tests use the API's real answers for the settings pages (webui/frontend/test/fixtures/
sections.json). This checks the file is still what the API answers, so the app's tests can't drift from the
server. When a role or schema.py changes a field, write the file again:
USAS_UPDATE_FIXTURES=1 python -m pytest tests/test_frontend_fixtures.py (see docs/testing.md)."""

import json
import os

from .conftest import DEFAULTS, HARDENING, PROXMOX, REPO, post

FIXTURES = REPO / "webui" / "frontend" / "test" / "fixtures" / "sections.json"


def answers(ui) -> dict:
    post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_node": "hephaestus"})
    post(ui, "/defaults", {"action": "save", **DEFAULTS})
    post(ui, "/hardening", HARDENING)
    post(ui, "/servers", {"server_count": "2", "servers-0-name": "web-01", "servers-0-ansible_host": "10.0.0.61",
                          "servers-0-pve_vmid": "4061", "servers-0-ufw_ports": "443/tcp", "servers-1-name": "db-01",
                          "servers-1-ansible_host": "10.0.0.70", "servers-1-pve_vmid": "4070",
                          "servers-1-vm_memory_mb": "8192"})
    ui.app_state.store.save_cluster("olympus", {"hephaestus": "online", "ares": "online", "hermes": "offline"},
                                    {"local": "dir", "synology-nfs": "nfs, shared"})
    data = {k: ui.get(f"/api/{k}").json() for k in ("proxmox", "defaults", "hardening", "servers")}
    data["defaults"]["timezones"] = ["America/Chicago", "Etc/UTC", "Europe/Berlin"]  # a few are enough
    return data


def test_the_fixtures_are_what_the_api_answers(ui):
    live = answers(ui)
    if os.environ.get("USAS_UPDATE_FIXTURES"):
        FIXTURES.write_text(json.dumps(live, indent=1, ensure_ascii=False) + "\n")
    assert json.loads(FIXTURES.read_text()) == live
