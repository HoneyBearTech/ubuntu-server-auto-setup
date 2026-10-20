"""The inventory and the Vault the UI writes: readable by Ansible, user values never templated."""

import os
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from usas_web.schema import Schema
from usas_web.store import Config, Store, Unsafe, dump_yaml, load_yaml, write_private

from .conftest import REPO, TOKEN_SECRET

ANSIBLE = shutil.which("ansible") or str(Path(os.sys.executable).with_name("ansible"))


@pytest.fixture
def store(settings):
    settings.data_dir.mkdir(parents=True)
    return Store(settings, Schema(REPO))


def sample(store) -> Config:
    config = Config()
    config.values.update({
        "pve_api_host": "pve.example.lan", "pve_node": "pve-1", "admin_user": "sysadmin",
        "timezone": "{{ lookup('pipe', 'touch /tmp/pwned') }}",  # can't come from the form; must stay data
        "ssh_port": 22, "net_dns": ["10.0.0.1"], "ufw_allow": [{"port": "80", "proto": "tcp"}],
        "admin_extra_ssh_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKE extra@laptop"],
    })
    config.secrets.update({"vault_admin_password": "p {{ w }}", "vault_pve_api_token_id": "automation",
                           "vault_pve_api_token_secret": TOKEN_SECRET})
    config.node_address = "10.0.0.2"
    config.servers = [
        {"name": "web-01", "ansible_host": "10.0.0.11", "pve_vmid": 4001, "vm_cores": 4, "vm_memory_mb": None,
         "ufw_ports": [{"port": "443", "proto": "tcp"}]},
        {"name": "db-01", "ansible_host": "10.0.0.12", "pve_vmid": 4002, "ufw_ports": []},
    ]
    return config


def test_round_trip(store):
    config = sample(store)
    store.save(config)
    loaded = store.load()
    assert loaded.values["pve_api_host"] == "pve.example.lan"
    assert loaded.values["ufw_allow"] == [{"port": "80", "proto": "tcp"}]
    assert loaded.secrets == config.secrets
    assert loaded.node_address == "10.0.0.2"
    assert loaded.servers[0] == {"name": "web-01", "ansible_host": "10.0.0.11", "pve_vmid": 4001, "vm_cores": 4,
                                 "ufw_ports": [{"port": "443", "proto": "tcp"}]}
    assert loaded.servers[1]["ufw_ports"] == []
    assert "ssh_port" not in loaded.values  # equal to the role default, so left out
    assert store.host_names() >= {"web-01", "db-01", "pve-1", "all", "ubuntu_servers"}


def test_files_are_private_and_the_vault_is_encrypted(store, settings):
    store.save(sample(store))
    for path in (settings.inventory_file, settings.vault_file, settings.vault_password_file):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    vault = settings.vault_file.read_text()
    assert vault.startswith("$ANSIBLE_VAULT;1.1;AES256")
    assert TOKEN_SECRET not in vault and TOKEN_SECRET not in settings.inventory_file.read_text()


def test_user_values_are_tagged_unsafe(store, settings):
    store.save(sample(store))
    text = settings.inventory_file.read_text()
    assert "timezone: !unsafe '{{ lookup(''pipe'', ''touch /tmp/pwned'') }}'" in text
    assert "admin_password: '{{ vault_admin_password }}'" in text
    assert "ufw_allow: '{{ webui_ufw_allow + (webui_ufw_allow_host | default([])) }}'" in text


def test_the_ui_key_is_authorized_unless_turned_off(store, settings):
    settings.ssh_dir.mkdir()
    settings.ssh_key.with_suffix(".pub").write_text("ssh-ed25519 AAAAUIKEY ubuntu-server-auto-setup-webui\n")
    config = sample(store)
    store.save(config)
    assert "AAAAUIKEY" in settings.inventory_file.read_text()
    loaded = store.load()
    assert loaded.authorize_ui_key and loaded.values["admin_extra_ssh_keys"] == config.values["admin_extra_ssh_keys"]
    loaded.authorize_ui_key = False
    store.save(loaded)
    assert "AAAAUIKEY" not in settings.inventory_file.read_text()
    assert store.load().authorize_ui_key is False


@pytest.mark.skipif(not Path(ANSIBLE).exists(), reason="ansible isn't installed")
def test_ansible_reads_the_inventory_without_templating_user_values(store, settings, tmp_path):
    store.save(sample(store))
    env = {**os.environ, "ANSIBLE_CONFIG": str(REPO / "ansible.cfg")}
    result = subprocess.run(
        [ANSIBLE, "-i", str(settings.inventory_file), "--vault-password-file", str(settings.vault_password_file),
         "web-01", "-c", "local", "-m", "ansible.builtin.debug",
         "-a", "msg={{ [timezone, admin_password, ufw_allow | length, pve_api_token_id, ansible_user] }}"],
        capture_output=True, text=True, env=env, cwd=tmp_path, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "lookup('pipe', 'touch /tmp/pwned')" in out and "p {{ w }}" in out
    assert "automation" in out and "sysadmin" in out
    assert not Path("/tmp/pwned").exists()


def test_a_configured_vault_password_file_must_exist(settings, tmp_path):
    custom = replace(settings, vault_password_file=tmp_path / "secret")
    custom.data_dir.mkdir(parents=True)
    store = Store(custom, Schema(REPO))
    with pytest.raises(RuntimeError, match="doesn't exist"):
        store.vault_password()
    (tmp_path / "secret").write_text("\n")
    with pytest.raises(RuntimeError, match="empty"):
        store.vault_password()
    (tmp_path / "secret").write_text("from a docker secret\n")
    assert store.vault_password() == b"from a docker secret"


def test_load_without_files(store):
    config = store.load()
    assert config.values == {} and config.servers == [] and config.secrets == {}


def test_yaml_helpers(tmp_path):
    text = dump_yaml({"a": Unsafe("{{ x }}"), "b": "{{ y }}", "c": [Unsafe("z")]})
    assert "a: !unsafe '{{ x }}'" in text and "b: '{{ y }}'" in text
    assert load_yaml(text) == {"a": "{{ x }}", "b": "{{ y }}", "c": ["z"]}
    path = tmp_path / "sub" / "f"
    write_private(path, "one")
    write_private(path, b"two")
    assert path.read_text() == "two" and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["f"]
