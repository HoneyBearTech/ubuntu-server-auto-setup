"""Reads and writes the inventory and the Vault in the data directory.

The inventory (inventory/hosts.yml) is an ordinary Ansible inventory: the same playbooks can use it from the
command line (with the Vault password file). Every value a user typed is written with the ``!unsafe`` tag, so
Ansible never treats it as a template, whatever it contains; only the few templates the UI itself writes
(such as ``admin_password: "{{ vault_admin_password }}"``) are plain strings. The secrets (the admin password
and the API token) are in inventory/group_vars/all/vault.yml, encrypted with Ansible Vault.
"""

import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from ansible.parsing.vault import VaultLib, VaultSecret

from .config import Settings
from .schema import Schema

VAULT_NAMES = ("vault_admin_password", "vault_pve_api_token_id", "vault_pve_api_token_secret")
SERVER_HOST_VARS = ("pve_vmid", "vm_cores", "vm_memory_mb", "vm_disk_gb")


class Unsafe(str):
    """A value Ansible must not template (written as !unsafe)."""


class _Dumper(yaml.SafeDumper):
    pass


class _Loader(yaml.SafeLoader):
    pass


_Dumper.add_representer(Unsafe, lambda dumper, data: dumper.represent_scalar("!unsafe", str(data), style="'"))
_Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))


def _mark(value: Any) -> Any:
    """Tags every string in a user-supplied value as unsafe."""
    if isinstance(value, str):
        return Unsafe(value)
    if isinstance(value, list):
        return [_mark(v) for v in value]
    if isinstance(value, dict):
        return {k: _mark(v) for k, v in value.items()}
    return value


def dump_yaml(data: Any) -> str:
    return "---\n" + yaml.dump(data, Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=1000)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)  # noqa: S506 (a SafeLoader subclass)


def write_private(path: Path, content: str | bytes) -> None:
    """Writes a file readable by the owner only, replacing the old one in one step."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = content.encode() if isinstance(content, str) else content
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class Config:
    """What the forms show and edit."""

    values: dict[str, Any] = field(default_factory=dict)  # variables set in the inventory
    secrets: dict[str, str] = field(default_factory=dict)  # Vault values
    servers: list[dict[str, Any]] = field(default_factory=list)
    node_address: str = ""
    authorize_ui_key: bool = True

    def secret_is_set(self, vault_name: str) -> bool:
        return bool(self.secrets.get(vault_name))


class Store:
    def __init__(self, settings: Settings, schema: Schema):
        self.settings = settings
        self.schema = schema

    # --- Vault ---------------------------------------------------------------------------------------------

    def vault_password(self) -> bytes:
        path = self.settings.vault_password_file
        if not path.exists():
            if path != self.settings.data_dir / ".vault-pass":
                raise RuntimeError(f"USAS_VAULT_PASSWORD_FILE {path} doesn't exist")
            write_private(path, secrets.token_urlsafe(32) + "\n")
        password = path.read_bytes().strip()
        if not password:
            raise RuntimeError(f"the Vault password file {path} is empty")
        return password

    def _vault(self) -> VaultLib:
        return VaultLib([("default", VaultSecret(self.vault_password()))])

    def read_secrets(self) -> dict[str, str]:
        path = self.settings.vault_file
        if not path.exists():
            return {}
        data = load_yaml(self._vault().decrypt(path.read_bytes()).decode()) or {}
        return {k: str(v) for k, v in data.items() if k in VAULT_NAMES and v is not None}

    def write_secrets(self, values: dict[str, str]) -> None:
        plain = dump_yaml({k: Unsafe(values[k]) for k in VAULT_NAMES if values.get(k)})
        write_private(self.settings.vault_file, self._vault().encrypt(plain.encode()))

    # --- UI-only settings (webui.json) ---------------------------------------------------------------------

    def _ui_settings(self) -> dict[str, Any]:
        path = self.settings.settings_file
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def cluster(self) -> dict[str, Any]:
        """The cluster's name and nodes ({"name": ..., "nodes": {name: status}}) from the last connection test."""
        cluster = self._ui_settings().get("cluster") or {}
        nodes = cluster.get("nodes") if isinstance(cluster, dict) else None
        if not isinstance(nodes, dict):
            return {}
        storages = cluster.get("iso_storages")
        return {"name": str(cluster.get("name") or ""), "nodes": {str(k): str(v) for k, v in nodes.items()},
                "iso_storages": {str(k): str(v) for k, v in storages.items()} if isinstance(storages, dict) else {}}

    def save_cluster(self, name: str, nodes: dict[str, str], iso_storages: dict[str, str] | None = None) -> None:
        data = self._ui_settings()
        data["cluster"] = {"name": name, "nodes": dict(sorted(nodes.items())),
                           "iso_storages": dict(sorted((iso_storages or {}).items()))}
        write_private(self.settings.settings_file, json.dumps(data, indent=2) + "\n")

    # --- Inventory ---------------------------------------------------------------------------------------

    def ui_public_key(self) -> str:
        pub = self.settings.ssh_key.with_suffix(".pub")
        return pub.read_text().strip() if pub.exists() else ""

    def load(self) -> Config:
        config = Config(secrets=self.read_secrets())
        path = self.settings.inventory_file
        if not path.exists():
            return config
        inventory = load_yaml(path.read_text()) or {}
        top = inventory.get("all", {}) or {}
        all_vars = top.get("vars", {}) or {}
        children = top.get("children", {}) or {}
        for name in self.schema.by_name:
            if name in all_vars and not self.schema.by_name[name].vault:
                config.values[name] = all_vars[name]
        if "webui_ufw_allow" in all_vars or isinstance(config.values.get("ufw_allow"), str):
            config.values["ufw_allow"] = all_vars.get("webui_ufw_allow", [])
        keys = list(config.values.get("admin_extra_ssh_keys") or [])
        ui_key = self.ui_public_key()
        config.authorize_ui_key = bool(all_vars.get("webui_authorize_ui_key", True))
        if ui_key:
            keys = [k for k in keys if k.strip() != ui_key]
        config.values["admin_extra_ssh_keys"] = keys
        nodes = ((children.get("proxmox_nodes") or {}).get("hosts") or {})
        for node in nodes.values():
            config.node_address = str((node or {}).get("ansible_host", ""))
        hosts = ((children.get("ubuntu_servers") or {}).get("hosts") or {})
        for name, host in hosts.items():
            host = host or {}
            server = {"name": str(name), "ansible_host": str(host.get("ansible_host", ""))}
            for var in SERVER_HOST_VARS:
                if var in host:
                    server[var] = host[var]
            server["ufw_ports"] = host.get("webui_ufw_allow_host", [])
            config.servers.append(server)
        return config

    def save(self, config: Config) -> None:
        """Writes the whole inventory and the Vault from config."""
        s = self.settings
        all_vars: dict[str, Any] = {}
        for name, f in self.schema.by_name.items():
            if f.vault or name not in config.values:
                continue
            value = config.values[name]
            if name in ("ufw_allow", "admin_extra_ssh_keys"):
                continue
            # Values equal to the role default are left out, so a new release's default applies; admin_user is
            # always written because the inventory's ansible_user refers to it.
            if value is None or (value == f.default and name != "admin_user"):
                continue
            all_vars[name] = _mark(value)

        extra_keys = list(config.values.get("admin_extra_ssh_keys") or [])
        ui_key = self.ui_public_key()
        if ui_key and config.authorize_ui_key:
            extra_keys = [ui_key, *[k for k in extra_keys if k != ui_key]]
        if extra_keys:
            all_vars["admin_extra_ssh_keys"] = _mark(extra_keys)
        all_vars["webui_authorize_ui_key"] = config.authorize_ui_key

        # Secrets come from the Vault, by name (templates, not values).
        all_vars["admin_password"] = "{{ vault_admin_password }}"  # noqa: S105
        all_vars["pve_api_token_id"] = "{{ vault_pve_api_token_id }}"  # noqa: S105
        all_vars["pve_api_token_secret"] = "{{ vault_pve_api_token_secret }}"  # noqa: S105

        # The UI's own SSH key and known_hosts, for the Proxmox node and the servers.
        all_vars["ansible_ssh_private_key_file"] = str(s.ssh_key)
        all_vars["ansible_ssh_common_args"] = f"-o UserKnownHostsFile={s.known_hosts} -o StrictHostKeyChecking=yes"
        all_vars["provision_known_hosts_file"] = str(s.known_hosts)
        all_vars["ssh_known_hosts_file"] = str(s.known_hosts)

        # Firewall rules for every server plus each server's own: a host's ufw_allow would replace the
        # group's, so the inventory combines them per host.
        common_rules = list(config.values.get("ufw_allow") or [])
        if common_rules or any(srv.get("ufw_ports") for srv in config.servers):
            all_vars["webui_ufw_allow"] = _mark(common_rules)
            all_vars["ufw_allow"] = "{{ webui_ufw_allow + (webui_ufw_allow_host | default([])) }}"

        hosts: dict[str, Any] = {}
        for srv in config.servers:
            host: dict[str, Any] = {"ansible_host": Unsafe(srv["ansible_host"])}
            for var in SERVER_HOST_VARS:
                if srv.get(var) is not None:
                    host[var] = srv[var]
            if srv.get("ufw_ports"):
                host["webui_ufw_allow_host"] = _mark(list(srv["ufw_ports"]))
            hosts[str(srv["name"])] = host  # a host name (checked against the roles' pattern)

        children: dict[str, Any] = {}
        node = config.values.get("pve_node")
        if node and config.node_address:
            children["proxmox_nodes"] = {
                "hosts": {str(node): {"ansible_host": Unsafe(config.node_address), "ansible_user": "root"}}
            }
        children["ubuntu_servers"] = {
            "vars": {
                "ansible_user": "{{ admin_user }}",
                # Only used with admin_sudo_nopasswd: false.
                "ansible_become_password": "{{ vault_admin_password }}",
            },
            "hosts": hosts,
        }
        inventory = {"all": {"vars": all_vars, "children": children}}
        header = (
            "# Written by the ubuntu-server-auto-setup web UI. Changes made here by hand are kept only if\n"
            "# the UI can read them back; secrets are in group_vars/all/vault.yml (Ansible Vault).\n"
        )
        write_private(s.inventory_file, header + dump_yaml(inventory))
        self.write_secrets(config.secrets)

    def host_names(self) -> set[str]:
        """Every host and group name, for checking --limit."""
        config = self.load()
        names = {"all", "ubuntu_servers", "proxmox_nodes", "ungrouped"}
        names.update(s["name"] for s in config.servers)
        if config.values.get("pve_node"):
            names.add(str(config.values["pve_node"]))
        return names
