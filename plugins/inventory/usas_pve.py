"""Inventory plugin: every VM of the Proxmox VE cluster, for the updates playbook.

Listed after the static inventory (-i inventory/hosts.yml -i inventory/pve.usas_pve.yml), it asks the cluster
for its VMs and:
- a VM whose ID is a static host's pve_vmid stays that host: it only gains usas_pve_* variables (its live node,
  name, tags, pool, HA state, the guest's OS), never another address or name;
- every other QEMU VM becomes vm-<vmid> in the group usas_pve_vms, reached at its static ansible_host if a host
  of that name is in the inventory, else at the address its guest agent reports, else its cloud-init ipconfig0.
  Its SSH host keys must be pinned in known_hosts as usas-<cluster>-<vmid> (HostKeyAlias), and it gets its own
  SSH master connection, so a guest that reports another VM's address only gets a refused connection;
- a VM that mustn't be updated (a container, a template, stopped, locked by Proxmox, Windows, no address, or
  tagged usas-no-updates) goes into usas_pve_skipped with the reason, and the playbook reports it without
  connecting.
Names and groups never come from what a guest or a VM's owner chose: hosts are vm-<vmid>, and tag, pool and node
groups have fixed prefixes (pve_tag_, pve_pool_, pve_node_), so a renamed or retagged VM can't pick up another
host's or group's variables. Guest data is parsed by plugins/module_utils/usas_vms.py (the web UI's VMs page
uses the same code) and is display-only, apart from the address, which the host key pin guards.

The API settings default to the inventory's own pve_api_host, pve_api_port, pve_api_user, pve_api_token_id,
pve_api_token_secret and pve_validate_certs (with their group_vars and the Vault), so the configuration file
can be just "plugin: usas_pve". tests/fuzz/fuzz_vm_inventory.py checks the naming and grouping (plan()).
"""

import concurrent.futures
import fnmatch
import importlib.util
import ipaddress
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ansible.errors import AnsibleParserError
from ansible.plugins.inventory import BaseInventoryPlugin

DOCUMENTATION = r"""
name: usas_pve
plugin_type: inventory
short_description: Every VM of a Proxmox VE cluster, for the updates playbook
description:
  - Lists the cluster's VMs through the Proxmox VE API and adds the ones the static inventory doesn't have as
    vm-<vmid> (group usas_pve_vms), pinned by host key alias, and the ones that mustn't be updated to
    usas_pve_skipped with the reason. See docs/updates.md.
  - The configuration file's name must end in usas_pve.yml or usas_pve.yaml.
options:
  plugin:
    description: The name of this plugin.
    required: true
    choices: [usas_pve]
  api_host:
    description: A Proxmox VE node's host name or address. Default, the inventory's pve_api_host.
    type: str
  api_port:
    description: The API's port. Default, the inventory's pve_api_port, or 8006.
    type: int
  api_user:
    description: The API user, such as ansible@pve. Default, the inventory's pve_api_user.
    type: str
  api_token_id:
    description: The token's name. Default, the inventory's pve_api_token_id.
    type: str
  api_token_secret:
    description: The token's secret. Default, the inventory's pve_api_token_secret (normally from the Vault).
    type: str
    env:
      - name: USAS_PVE_TOKEN_SECRET
  validate_certs:
    description: Check the API's certificate. Default, the inventory's pve_validate_certs, or true.
    type: bool
  ca_file:
    description: A CA bundle to check the API's certificate with (default, the system's).
    type: path
  timeout:
    description: Seconds for each API call.
    type: int
    default: 10
  cluster:
    description:
      - The cluster's name in host key aliases (usas-<cluster>-<vmid>). Default, the name /cluster/status
        reports, or pve when it can't be read (a single node, or no Sys.Audit).
    type: str
    default: ""
  agent:
    description: Ask running VMs' guest agents for their addresses and OS (VM.GuestAgent.Audit).
    type: bool
    default: true
  nodes:
    description: Only VMs on these nodes.
    type: list
    elements: str
    default: []
  tags_any:
    description: Only VMs with at least one of these tags.
    type: list
    elements: str
    default: []
  tags_all:
    description: Only VMs with all of these tags.
    type: list
    elements: str
    default: []
  pools:
    description: Only VMs in one of these resource pools.
    type: list
    elements: str
    default: []
  name_patterns:
    description: Only VMs whose Proxmox name, or inventory name, matches one of these shell globs.
    type: list
    elements: str
    default: []
  exclude:
    description: VM IDs or names (Proxmox or inventory) to leave out.
    type: list
    elements: str
    default: []
"""

EXAMPLES = r"""
# inventory/pve.usas_pve.yml
plugin: usas_pve
tags_any: [prod]
exclude: ["9000"]
"""

try:
    from ansible.utils.unsafe_proxy import wrap_var
except ImportError:  # pragma: no cover
    def wrap_var(value):
        return value


def _load_shared():
    """plugins/module_utils/usas_vms.py, the parser the web UI's VMs page uses too."""
    if "usas_vms" in sys.modules:
        return sys.modules["usas_vms"]
    path = Path(__file__).resolve().parents[1] / "module_utils" / "usas_vms.py"
    spec = importlib.util.spec_from_file_location("usas_vms", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["usas_vms"] = module
    spec.loader.exec_module(module)
    return module


V = _load_shared()

GROUP = "usas_pve_vms"
SKIPPED = "usas_pve_skipped"
OPT_OUT_TAG = "usas-no-updates"
GROUP_NAME = re.compile(r"[^a-z0-9_]")
# Filter values: node names as the roles check them, tags and pools as Proxmox allows, globs of names.
TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}\Z")
GLOB = re.compile(r"^[A-Za-z0-9*?.\[\]_-]{1,64}\Z")
EXCLUDE = re.compile(r"^([0-9]{3,9}|[A-Za-z0-9][A-Za-z0-9._-]{0,63})\Z")
WORKERS = 4
BUDGET_SECONDS = 15


def _ipv4(value):
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


def group_name(prefix, value):
    """A fixed-prefix group name: lowercase letters, digits and _ only."""
    return prefix + GROUP_NAME.sub("_", str(value).lower())[:60]


def option_problems(opts):
    """What is wrong with the filter options, as messages."""
    problems = []
    checks = (("nodes", V.NODE_PATTERN), ("tags_any", TAG), ("tags_all", TAG), ("pools", TAG),
              ("name_patterns", GLOB), ("exclude", EXCLUDE))
    for name, pattern in checks:
        bad = [str(v) for v in opts.get(name) or [] if not pattern.match(str(v))]
        if bad:
            problems.append(f"{name} has values that aren't plain names: {', '.join(V.text(b, 40) for b in bad)}")
    if opts.get("cluster") and not V.CLUSTER_PATTERN.match(str(opts["cluster"])):
        problems.append("cluster must be lowercase letters, digits and -, starting with a letter or digit")
    return problems


def _wanted(vm, name, opts):
    """Whether the filters keep a VM (all of them must agree)."""
    tags = {t.lower() for t in vm.tags}
    excluded = {str(e).lower() for e in opts.get("exclude") or []}
    if str(vm.vmid) in excluded or vm.name.lower() in excluded or name.lower() in excluded:
        return False
    if opts.get("nodes") and vm.node not in opts["nodes"]:
        return False
    if opts.get("tags_any") and not tags & {str(t).lower() for t in opts["tags_any"]}:
        return False
    if opts.get("tags_all") and not {str(t).lower() for t in opts["tags_all"]} <= tags:
        return False
    if opts.get("pools") and vm.pool not in opts["pools"]:
        return False
    patterns = opts.get("name_patterns") or []
    return not patterns or any(fnmatch.fnmatchcase(n, str(p)) for p in patterns for n in (vm.name, name) if n)


def _skip(vm, config, address, static):
    """Why the playbook must leave this VM alone: (status, reason), or None."""
    if OPT_OUT_TAG in {t.lower() for t in vm.tags}:
        return "skipped", f"opted out (Proxmox tag {OPT_OUT_TAG})"
    if vm.type == "lxc":
        return "skipped", "a container (LXC): not updated by this playbook"
    if vm.template:
        return "skipped", "a template"
    if vm.status != "running":
        return "skipped", f"{vm.status}: not updated (never started by this playbook)"
    if vm.lock:
        return "busy", f"Proxmox has it locked ({vm.lock})"
    if config is not None and config.ostype.startswith("win"):
        return "unsupported", "Windows (Proxmox's OS type)"
    if not static and not address:
        return "skipped", "no address: its guest agent didn't report one and it has no cloud-init ipconfig0"
    return None


def plan(vms, configs, agents, static_hosts, cluster, opts):
    """Which hosts the cluster adds: a list of {"name", "static", "groups", "vars"}, at most one per VM ID.

    vms: usas_vms.Vm list; configs: {vmid: VmConfig}; agents: {vmid: (addresses, OsInfo or None)};
    static_hosts: {inventory name: {"pve_vmid", "ansible_host"}}; cluster: the alias prefix's cluster name;
    opts: the filter options. A VM that is a static host keeps that host's name; any other is vm-<vmid>."""
    servers = [{"name": n, "pve_vmid": h.get("pve_vmid")} for n, h in static_hosts.items() if h.get("pve_vmid")]
    hosts, seen = [], set()
    for vm in vms:
        name, server = V.host_name(vm.vmid, servers)
        if vm.vmid in seen or not _wanted(vm, name, opts):
            continue
        seen.add(vm.vmid)
        config = configs.get(vm.vmid)
        addresses, osinfo = agents.get(vm.vmid, ([], None))
        declared = str((static_hosts.get(name) or {}).get("ansible_host") or "")
        address = declared or (addresses[0] if addresses else "") or (config.ipconfig0 if config else "")
        variables = {"usas_pve_vmid": vm.vmid, "usas_pve_name": vm.name, "usas_pve_node": vm.node,
                     "usas_pve_tags": list(vm.tags), "usas_pve_pool": vm.pool, "usas_pve_hastate": vm.hastate,
                     "usas_pve_status": vm.status}
        if osinfo is not None:
            variables["usas_os_hint"] = osinfo.pretty or osinfo.id
        groups = [group_name("pve_node_", vm.node)] + [group_name("pve_tag_", t) for t in vm.tags]
        if vm.pool:
            groups.append(group_name("pve_pool_", vm.pool))
        skip = _skip(vm, config, address, server is not None)
        if skip:
            groups.append(SKIPPED)
            variables["usas_pve_skip_status"], variables["usas_pve_skip_reason"] = skip
        if server is None:
            alias = V.vm_alias(cluster, vm.vmid)
            if not skip:
                groups.append(GROUP)
            variables.update({"usas_pve_alias": alias, "ansible_ssh_extra_args": f"-o HostKeyAlias={alias}",
                              "ansible_control_path": V.control_path(vm.vmid)})
            if address and not declared:
                variables["ansible_host"] = address
        hosts.append({"name": name, "static": server is not None, "groups": groups, "vars": variables})
    return hosts


class InventoryModule(BaseInventoryPlugin):
    NAME = "usas_pve"

    def verify_file(self, path):
        return super().verify_file(path) and path.endswith(("usas_pve.yml", "usas_pve.yaml"))

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path, cache)
        self._read_config_data(path)
        opts = {name: self.get_option(name) for name in
                ("nodes", "tags_any", "tags_all", "pools", "name_patterns", "exclude", "cluster")}
        problems = option_problems(opts)
        if problems:
            raise AnsibleParserError(f"{path}: " + "; ".join(problems))
        api = self._api_settings()
        vms = V.parse_resources(self._get(api, "/cluster/resources?type=vm"))
        cluster = opts["cluster"] or self._cluster_name(api)
        configs, agents = self._details(api, [vm for vm in vms if vm.type == "qemu" and not vm.template])
        static_hosts = {name: {"pve_vmid": host.vars.get("pve_vmid"), "ansible_host": host.vars.get("ansible_host")}
                        for name, host in self.inventory.hosts.items()}
        for entry in plan(vms, configs, agents, static_hosts, cluster, opts):
            name = self.inventory.add_host(entry["name"])
            for group in entry["groups"]:
                self.inventory.add_group(group)
                self.inventory.add_child(group, name)
            for key, value in entry["vars"].items():
                # Names, tags and the OS come from the cluster and its guests: never templated.
                self.inventory.set_variable(name, key, wrap_var(value))

    # The API settings: this file's options, else the inventory's own pve_api_* variables (templated with the
    # inventory's group_vars and the Vault, as the playbooks would).
    def _api_settings(self):
        inventory_vars = None

        def setting(option, variable, default=None):
            nonlocal inventory_vars
            value = self.get_option(option)
            if value not in (None, ""):
                return value
            if inventory_vars is None:
                inventory_vars = self._inventory_vars()
            if variable in inventory_vars:
                return self._template(inventory_vars[variable], inventory_vars)
            return default

        api = {"host": setting("api_host", "pve_api_host"), "port": setting("api_port", "pve_api_port", 8006),
               "user": setting("api_user", "pve_api_user"), "token_id": setting("api_token_id", "pve_api_token_id"),
               "secret": setting("api_token_secret", "pve_api_token_secret"),
               "validate": setting("validate_certs", "pve_validate_certs", True),
               "ca_file": self.get_option("ca_file"), "timeout": int(self.get_option("timeout"))}
        missing = [k for k in ("host", "user", "token_id", "secret") if not str(api[k] or "")]
        if missing:
            raise AnsibleParserError(
                "usas_pve needs the Proxmox API settings: set pve_api_host, pve_api_user, pve_api_token_id and "
                f"pve_api_token_secret in the inventory, or in this plugin's file (missing: {', '.join(missing)})")
        if not V.HOSTNAME_PATTERN.match(str(api["host"])) and not _ipv4(str(api["host"])):
            raise AnsibleParserError("usas_pve: the API host must be a host name or an IPv4 address")
        api["validate"] = str(api["validate"]).lower() not in ("false", "no", "0", "off")
        return api

    def _inventory_vars(self):
        from ansible.utils.vars import combine_vars
        from ansible.vars.plugins import get_vars_from_inventory_sources
        all_group = self.inventory.groups["all"]
        sources = getattr(self.inventory, "processed_sources", [])  # the sources parsed before this one
        found = get_vars_from_inventory_sources(self.loader, sources, [all_group], "inventory")
        return combine_vars(all_group.get_vars(), found)

    def _template(self, value, variables):
        from ansible.template import Templar
        return Templar(loader=self.loader, variables=variables).template(value)

    def _context(self, api):
        if not api["validate"]:
            return ssl._create_unverified_context()  # noqa: S323 (pve_validate_certs: false, as the playbooks do)
        return ssl.create_default_context(cafile=api["ca_file"] or None)

    def _get(self, api, path, timeout=None):
        url = f"https://{api['host']}:{int(api['port'])}/api2/json{path}"
        request = urllib.request.Request(url, headers={
            "Authorization": f"PVEAPIToken={api['user']}!{api['token_id']}={api['secret']}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout or api["timeout"],  # noqa: S310 (https only)
                                        context=self._context(api)) as response:
                return json.loads(response.read(8 * 1024 * 1024)).get("data")
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, f"{path}: HTTP {error.code} {V.text(error.reason, 80)}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ApiError(0, f"{path}: {V.text(getattr(error, 'reason', error), 120)}") from None

    def _cluster_name(self, api):
        try:
            status = self._get(api, "/cluster/status")
        except ApiError:
            return V.DEFAULT_CLUSTER
        for entry in status if isinstance(status, list) else []:
            if isinstance(entry, dict) and entry.get("type") == "cluster":
                return str(entry.get("name") or V.DEFAULT_CLUSTER)
        return V.DEFAULT_CLUSTER

    def _details(self, api, vms):
        """Each VM's config, and for running VMs with an agent its addresses and OS, 4 calls at a time within a
        15-second budget (an agent that doesn't answer in time leaves only the config)."""
        configs, agents = {}, {}
        deadline = time.monotonic() + BUDGET_SECONDS

        def one(vm):
            base = f"/nodes/{urllib.parse.quote(vm.node)}/qemu/{vm.vmid}"
            try:
                config = V.parse_config(self._get(api, f"{base}/config"))
            except ApiError:
                return vm.vmid, None, None
            if not (self.get_option("agent") and config.agent and vm.status == "running"):
                return vm.vmid, config, None
            left = deadline - time.monotonic()
            if left <= 0:
                return vm.vmid, config, None
            try:
                interfaces = (self._get(api, f"{base}/agent/network-get-interfaces", min(5, left)) or {}).get("result")
                osinfo = V.parse_osinfo((self._get(api, f"{base}/agent/get-osinfo", min(5, left)) or {}).get("result"))
            except (ApiError, AttributeError):
                return vm.vmid, config, None
            return vm.vmid, config, (V.parse_interfaces(interfaces), osinfo)

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for vmid, config, agent in pool.map(one, vms):
                if config is not None:
                    configs[vmid] = config
                if agent is not None:
                    agents[vmid] = agent
        return configs, agents


class ApiError(AnsibleParserError):
    def __init__(self, status, message):
        super().__init__(f"usas_pve couldn't read the Proxmox API: {message}")
        self.status = status
