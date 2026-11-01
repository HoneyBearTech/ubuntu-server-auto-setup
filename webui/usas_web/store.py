"""Reads and writes the inventory and the Vault in the data directory.

The inventory (inventory/hosts.yml) is an ordinary Ansible inventory: the same playbooks can use it from the
command line (with the Vault password file). Every value a user typed is written with the ``!unsafe`` tag, so
Ansible never treats it as a template, whatever it contains; only the few templates the UI itself writes
(such as ``admin_password: "{{ vault_admin_password }}"``) are plain strings. The secrets (the admin password,
the API token, and the mail relay password, webhook URL and disk passphrase when set) are in
inventory/group_vars/all/vault.yml, encrypted with Ansible Vault, and so are the Kubernetes clusters' join tokens.

A Kubernetes cluster is the group k8s_<cluster> (a child of servers) with the cluster's settings as its vars and
the children k8s_<cluster>_servers and k8s_<cluster>_agents, whose hosts are the members. Whatever else such a
group holds (a variable the UI has no field for, a value it can't read back, the member groups' own vars, a
member the Servers page doesn't list) was set by hand and is written back as it was.
"""

import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from ansible.parsing.vault import VaultLib, VaultSecret

from .config import Settings
from .schema import Invalid, Schema, parse_value

VAULT_NAMES = ("vault_admin_password", "vault_pve_api_token_id", "vault_pve_api_token_secret",
               "vault_mail_relay_password", "vault_fail2ban_webhook_url", "vault_disk_passphrase")
# A Kubernetes cluster's join tokens (schema.CLUSTER_TOKENS): vault_k8s_token_<cluster> and
# vault_k8s_agent_token_<cluster>.
K8S_TOKEN_NAME = re.compile(r"^vault_k8s_(agent_)?token_[a-z][a-z0-9_]{0,30}\Z")
# A cluster's groups: k8s_<cluster>, and its members' k8s_<cluster>_servers and k8s_<cluster>_agents.
CLUSTER_GROUP = re.compile(r"^k8s_([a-z][a-z0-9-]{0,30})\Z")
MEMBER_GROUPS = {"servers": "server", "agents": "agent"}
# Secrets a server may go without: the inventory refers to them only once they are saved.
OPTIONAL_SECRETS = {"mail_relay_password": "vault_mail_relay_password",
                    "fail2ban_webhook_url": "vault_fail2ban_webhook_url",
                    "vm_disk_passphrase": "vault_disk_passphrase"}
SERVER_HOST_VARS = ("pve_vmid", "vm_cores", "vm_memory_mb", "vm_disk_gb", "net_mode", "net_mac", "vm_extra_nics")
# Host variables that hold text the user typed (written as !unsafe).
SERVER_TEXT_VARS = ("pve_node", "vm_os", "vm_install_method")
# Names an inventory written before 0.7.0 may hold, and the fields they now fill (the playbooks read both until 1.0).
OLD_NAMES = {"ufw_ssh_limit": "firewall_ssh_limit", "ufw_ssh_sources": "firewall_ssh_sources"}
# The servers' group, and its name before 0.7.0: an older inventory is read from it and written back as servers.
SERVER_GROUPS = ("servers", "ubuntu_servers")


def vault_name_known(name: str) -> bool:
    """A name the UI keeps in the Vault: its own secrets and the clusters' join tokens."""
    return name in VAULT_NAMES or bool(K8S_TOKEN_NAME.match(name))


class Unsafe(str):
    """A value Ansible must not template (written as !unsafe)."""


class _Dumper(yaml.SafeDumper):
    pass


class _Loader(yaml.SafeLoader):
    pass


def _represent_unsafe(dumper: yaml.SafeDumper, data: Unsafe) -> yaml.ScalarNode:
    """!unsafe, except for text that would load as another type. Ansible types an !unsafe scalar as if it had
    no quotes, so !unsafe '123456' loads as an int ('0123' as 83, 'true' as a bool, 'null' as None). Such text
    can't hold Jinja, so it is written as a plain quoted string, which loads as the string it is."""
    text = str(data)
    if dumper.resolve(yaml.ScalarNode, text, (True, False)) != "tag:yaml.org,2002:str":
        return dumper.represent_scalar("tag:yaml.org,2002:str", text, style="'")
    return dumper.represent_scalar("!unsafe", text, style="'")


_Dumper.add_representer(Unsafe, _represent_unsafe)
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
    node_addresses: dict[str, str] = field(default_factory=dict)  # Proxmox node name -> its SSH address
    authorize_ui_key: bool = True
    # The Kubernetes clusters, in inventory order (Cluster). A server's membership is its k8s_cluster and k8s_role.
    clusters: list["Cluster"] = field(default_factory=list)

    def secret_is_set(self, vault_name: str) -> bool:
        return bool(self.secrets.get(vault_name))

    @property
    def default_node(self) -> str:
        return str(self.values.get("pve_node") or "")

    def server_node(self, server: dict[str, Any]) -> str:
        """The node a server's VM goes on: its own pve_node, or the default one."""
        return str(server.get("pve_node") or self.default_node)

    def nodes_in_use(self) -> list[str]:
        """The default node first, then the other nodes the servers name."""
        nodes = [self.default_node] if self.default_node else []
        return nodes + sorted({self.server_node(s) for s in self.servers} - {"", *nodes})

    def node_names(self) -> list[str]:
        """The nodes in use, then the others that have an SSH address saved."""
        used = self.nodes_in_use()
        return used + sorted(set(self.node_addresses) - set(used))


@dataclass
class Cluster:
    """A Kubernetes cluster's group: the settings the Defaults page edits (values) and what it keeps as it was."""

    name: str
    values: dict[str, Any] = field(default_factory=dict)  # the fields' values (schema.CLUSTER_FIELDS)
    # Set by hand, written back as read: other vars of k8s_<cluster> (and fields' values the UI can't read),
    # the vars of the member groups, other children, and members that aren't servers of the Servers page.
    extra_vars: dict[str, Any] = field(default_factory=dict)
    member_vars: dict[str, dict[str, Any]] = field(default_factory=dict)  # "servers"/"agents" -> that group's vars
    other_children: dict[str, Any] = field(default_factory=dict)
    other_members: dict[str, dict[str, Any]] = field(default_factory=dict)  # "servers"/"agents" -> {host: vars}
    member_host_vars: dict[str, Any] = field(default_factory=dict)  # a listed server's vars set in a member group
    under_servers: bool = True  # a child of servers (as the UI writes it), else of all (written by hand)


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
        return {str(k): str(v) for k, v in data.items() if vault_name_known(str(k)) and v is not None}

    def _secrets_text(self, values: dict[str, str]) -> str:
        names = [*VAULT_NAMES, *sorted(k for k in values if K8S_TOKEN_NAME.match(k))]
        return dump_yaml({k: Unsafe(values[k]) for k in names if values.get(k)})

    def write_secrets(self, values: dict[str, str]) -> None:
        write_private(self.settings.vault_file, self._vault().encrypt(self._secrets_text(values).encode()))

    def repair_secrets(self) -> bool:
        """Writes the Vault again if an earlier version wrote a value Ansible loads as another type (an
        all-digit password as !unsafe '123456' became an int, and the playbooks stopped). True if it did."""
        path = self.settings.vault_file
        if not path.exists():
            return False
        plain = self._vault().decrypt(path.read_bytes()).decode()
        values = self.read_secrets()
        if not values or plain == self._secrets_text(values):
            return False
        self.write_secrets(values)
        return True

    # --- UI-only settings (webui.json) ---------------------------------------------------------------------

    def _ui_settings(self) -> dict[str, Any]:
        path = self.settings.settings_file
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def cluster(self) -> dict[str, Any]:
        """The cluster's name and nodes ({"name": ..., "nodes": {name: status}}), its ISO storages and those that
        allow Import content ("import_storages", only if the last connection test could list them), from the last
        connection test."""
        cluster = self._ui_settings().get("cluster") or {}
        nodes = cluster.get("nodes") if isinstance(cluster, dict) else None
        if not isinstance(nodes, dict):
            return {}
        storages = cluster.get("iso_storages")
        result = {"name": str(cluster.get("name") or ""), "nodes": {str(k): str(v) for k, v in nodes.items()},
                  "iso_storages": {str(k): str(v) for k, v in storages.items()} if isinstance(storages, dict) else {}}
        imports = cluster.get("import_storages")
        if isinstance(imports, list):
            result["import_storages"] = [str(i) for i in imports]
        return result

    def node_cpus(self) -> dict[str, dict[str, Any]]:
        """Each node's CPU from the last login test on the SSH page: {node: {"model", "missing"}}, missing being the
        x86-64-v3 flags it lacks."""
        cpus = self._ui_settings().get("node_cpus")
        return {str(node): {"model": str(cpu.get("model") or ""), "missing": [str(f) for f in cpu.get("missing") or []]}
                for node, cpu in (cpus.items() if isinstance(cpus, dict) else ()) if isinstance(cpu, dict)}

    def save_cluster(self, name: str, nodes: dict[str, str], iso_storages: dict[str, str] | None = None,
                     import_storages: list[str] | None = None) -> None:
        data = self._ui_settings()
        data["cluster"] = {"name": name, "nodes": dict(sorted(nodes.items())),
                           "iso_storages": dict(sorted((iso_storages or {}).items()))}
        if import_storages is not None:
            data["cluster"]["import_storages"] = sorted(import_storages)
        write_private(self.settings.settings_file, json.dumps(data, indent=2) + "\n")

    def save_node_cpu(self, node: str, model: str, missing: list[str]) -> None:
        """A node's CPU model and the x86-64-v3 flags it lacks, from the SSH page's login test."""
        data = self._ui_settings()
        cpus = data.get("node_cpus") if isinstance(data.get("node_cpus"), dict) else {}
        cpus[node] = {"model": model, "missing": sorted(missing)}
        data["node_cpus"] = dict(sorted(cpus.items()))
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
        for old, new in OLD_NAMES.items():
            if old in all_vars and new not in all_vars:
                all_vars[new] = all_vars[old]
        for name in self.schema.by_name:
            if name in all_vars and not self.schema.by_name[name].vault:
                config.values[name] = all_vars[name]
        # The rules for every server are kept apart from each server's own (see save), under either name.
        for kept, combined in (("webui_firewall_allow", "firewall_allow"), ("webui_ufw_allow", "ufw_allow")):
            if kept in all_vars or isinstance(all_vars.get(combined), str):
                config.values["firewall_allow"] = all_vars.get(kept, [])
                break
        else:
            if isinstance(all_vars.get("ufw_allow"), list) and "firewall_allow" not in all_vars:
                config.values["firewall_allow"] = all_vars["ufw_allow"]
        keys = list(config.values.get("admin_extra_ssh_keys") or [])
        ui_key = self.ui_public_key()
        config.authorize_ui_key = bool(all_vars.get("webui_authorize_ui_key", True))
        if ui_key:
            keys = [k for k in keys if k.strip() != ui_key]
        config.values["admin_extra_ssh_keys"] = keys
        nodes = ((children.get("proxmox_nodes") or {}).get("hosts") or {})
        for name, node in nodes.items():
            config.node_addresses[str(name)] = str((node or {}).get("ansible_host", ""))
        hosts: dict[str, Any] = {}
        for group in SERVER_GROUPS:
            for name, host in ((children.get(group) or {}).get("hosts") or {}).items():
                hosts.setdefault(name, host)
        for name, host in hosts.items():
            host = host or {}
            server = {"name": str(name), "ansible_host": str(host.get("ansible_host", ""))}
            for var in SERVER_HOST_VARS + SERVER_TEXT_VARS:
                if var in host:
                    server[var] = host[var]
            server["firewall_ports"] = host.get("webui_firewall_allow_host", host.get("webui_ufw_allow_host", []))
            config.servers.append(server)
        self._load_clusters(config, top, children)
        return config

    def _load_clusters(self, config: Config, top: dict[str, Any], children: dict[str, Any]) -> None:
        """The k8s_<cluster> groups under servers (or all), and each listed server's membership."""
        servers = {s["name"]: s for s in config.servers}
        under_servers = (children.get("servers") or {}).get("children") or {}
        groups = [*under_servers.items(), *((g, b) for g, b in children.items() if g not in under_servers)]
        fields = {f.name: f for f in self.schema.cluster_fields if f.name != "name"}
        for group, body in groups:
            match = CLUSTER_GROUP.match(str(group))
            if not match or any(c.name == match.group(1) for c in config.clusters):
                continue
            body = body if isinstance(body, dict) else {}
            cluster = Cluster(name=match.group(1), under_servers=group in under_servers)
            for key, value in (body.get("vars") or {}).items():
                f = fields.get(str(key))
                if f is not None and self._readable(f, value):
                    cluster.values[str(key)] = value
                else:
                    cluster.extra_vars[str(key)] = value
            for child, child_body in (body.get("children") or {}).items():
                kind = next((k for k in MEMBER_GROUPS if child == f"k8s_{cluster.name}_{k}"), None)
                if kind is None:
                    cluster.other_children[str(child)] = child_body
                    continue
                child_body = child_body if isinstance(child_body, dict) else {}
                if child_body.get("vars"):
                    cluster.member_vars[kind] = child_body["vars"]
                for host, host_vars in (child_body.get("hosts") or {}).items():
                    server = servers.get(str(host))
                    if server is None or server.get("k8s_cluster"):
                        cluster.other_members.setdefault(kind, {})[str(host)] = host_vars
                        continue
                    server.update(k8s_cluster=cluster.name, k8s_role=MEMBER_GROUPS[kind])
                    if host_vars:
                        cluster.member_host_vars[str(host)] = host_vars
            config.clusters.append(cluster)

    @staticmethod
    def _readable(f: Any, value: Any) -> bool:
        """Whether the form can show a value read from the inventory as it is (else it is kept as written)."""
        if f.kind == "bool":
            return isinstance(value, bool)
        if f.kind == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if f.kind == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                return False
            raw = "\n".join(value)
        elif isinstance(value, str):
            raw = value
        else:
            return False
        try:
            parse_value(f, raw)
        except Invalid:
            return False
        return True

    def save(self, config: Config) -> None:
        """Writes the whole inventory and the Vault from config."""
        s = self.settings
        all_vars: dict[str, Any] = {}
        for name, f in self.schema.by_name.items():
            if f.vault or name not in config.values:
                continue
            value = config.values[name]
            if name in ("firewall_allow", "admin_extra_ssh_keys"):
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
        for name, vault_name in OPTIONAL_SECRETS.items():
            if config.secret_is_set(vault_name):
                all_vars[name] = "{{ " + vault_name + " }}"

        # The UI's own SSH key and known_hosts, for the Proxmox node and the servers.
        all_vars["ansible_ssh_private_key_file"] = str(s.ssh_key)
        all_vars["ansible_ssh_common_args"] = f"-o UserKnownHostsFile={s.known_hosts} -o StrictHostKeyChecking=yes"
        all_vars["provision_known_hosts_file"] = str(s.known_hosts)
        all_vars["ssh_known_hosts_file"] = str(s.known_hosts)

        # Firewall rules for every server plus each server's own: a host's firewall_allow would replace the
        # group's, so the inventory combines them per host.
        common_rules = list(config.values.get("firewall_allow") or [])
        if common_rules or any(srv.get("firewall_ports") for srv in config.servers):
            all_vars["webui_firewall_allow"] = _mark(common_rules)
            all_vars["firewall_allow"] = "{{ webui_firewall_allow + (webui_firewall_allow_host | default([])) }}"

        hosts: dict[str, Any] = {}
        for srv in config.servers:
            # A server on DHCP may have no address here: then the playbooks ask its guest agent.
            host: dict[str, Any] = {"ansible_host": Unsafe(srv["ansible_host"])} if srv.get("ansible_host") else {}
            for var in SERVER_HOST_VARS:
                if srv.get(var) not in (None, "", []) and not (var == "net_mode" and srv[var] == "static"):
                    host[var] = _mark(srv[var])
            for var in SERVER_TEXT_VARS:
                if srv.get(var):
                    host[var] = Unsafe(str(srv[var]))
            # An extra card with the default route replaces the group's gateway on net0.
            if any(nic.get("gateway") for nic in srv.get("vm_extra_nics") or []):
                host["net_gateway"] = ""
            if srv.get("firewall_ports"):
                host["webui_firewall_allow_host"] = _mark(list(srv["firewall_ports"]))
            hosts[str(srv["name"])] = host  # a host name (checked against the roles' pattern)

        children: dict[str, Any] = {}
        # One host per node with an SSH address, named like the node (a host name, checked against the roles'
        # pattern): build-installer and provisioning reach each node the servers use through it, as pve_ssh_user.
        user = Unsafe(self.node_user(config))
        nodes = {name: {"ansible_host": Unsafe(address), "ansible_user": user}
                 for name, address in sorted(config.node_addresses.items()) if address}
        if nodes:
            children["proxmox_nodes"] = {"hosts": nodes}
        children["servers"] = {
            "vars": {
                "ansible_user": "{{ admin_user }}",
                # Sudo needs it once the first baseline run removes passwordless sudo (the default).
                "ansible_become_password": "{{ vault_admin_password }}",
            },
            "hosts": hosts,
        }
        clusters = self._cluster_groups(config)
        if any(c.under_servers for c in config.clusters):
            children["servers"]["children"] = {f"k8s_{c.name}": clusters[f"k8s_{c.name}"] for c in config.clusters
                                               if c.under_servers}
        children.update({f"k8s_{c.name}": clusters[f"k8s_{c.name}"] for c in config.clusters if not c.under_servers})
        inventory = {"all": {"vars": all_vars, "children": children}}
        header = (
            "# Written by the ubuntu-server-auto-setup web UI. Changes made here by hand are kept only if\n"
            "# the UI can read them back; secrets are in group_vars/all/vault.yml (Ansible Vault).\n"
        )
        write_private(s.inventory_file, header + dump_yaml(inventory))
        self.write_secrets(config.secrets)

    def _cluster_groups(self, config: Config) -> dict[str, Any]:
        """The k8s_<cluster> groups: the fields' values (unsafe, left out at the role default), what was set by
        hand as it was, and the members from the servers' k8s_cluster and k8s_role."""
        defaults = {f.name: f.default for f in self.schema.cluster_fields}
        groups: dict[str, Any] = {}
        for cluster in config.clusters:
            variables: dict[str, Any] = dict(cluster.extra_vars)
            for name, value in cluster.values.items():
                variables.pop(name, None)
                if value is not None and value != defaults.get(name):
                    variables[name] = _mark(value)
            body: dict[str, Any] = {"vars": variables} if variables else {}
            members: dict[str, Any] = {}
            for kind, role in MEMBER_GROUPS.items():
                hosts = {str(s["name"]): cluster.member_host_vars.get(str(s["name"])) or {} for s in config.servers
                         if s.get("k8s_cluster") == cluster.name and s.get("k8s_role") == role}
                hosts.update(cluster.other_members.get(kind) or {})
                group: dict[str, Any] = {}
                if cluster.member_vars.get(kind):
                    group["vars"] = cluster.member_vars[kind]
                group["hosts"] = hosts
                members[f"k8s_{cluster.name}_{kind}"] = group
            body["children"] = {**members, **cluster.other_children}
            groups[f"k8s_{cluster.name}"] = body
        return groups

    def node_user(self, config: Config) -> str:
        """The user the playbooks and the UI log in to the Proxmox node as (pve_ssh_user, root by default)."""
        return str(config.values.get("pve_ssh_user") or self.schema.by_name["pve_ssh_user"].default)

    def host_names(self) -> set[str]:
        """Every host and group name, for checking --limit."""
        config = self.load()
        names = {"all", "servers", "proxmox_nodes", "ungrouped"}
        names.update(s["name"] for s in config.servers)
        for cluster in config.clusters:
            names.update(f"k8s_{cluster.name}{suffix}" for suffix in ("", "_servers", "_agents"))
        names.update(name for name, address in config.node_addresses.items() if address)
        return names
