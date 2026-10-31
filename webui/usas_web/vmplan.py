"""Plans for running the baseline, or the updates playbook, against VMs picked on the VMs page: which hosts, with
which settings, and the inventory that run gets.

Everything here is pure (no network, no files, no FastAPI), so the tests and the fuzzer can call it directly.
The server builds a plan from the cluster listing it fetched itself and from the posted form, never from what
the browser says about a VM; every value that reaches the inventory is checked against a \\Z-anchored format
first and written as !unsafe.

Credentials follow the static inventory, never the VM's tags or description (which anyone who may edit the VM
can set): a VM whose VM ID is a server's pve_vmid on the Servers page runs as that server, with its address and
settings. Any other VM is named vm-<vmid> (never its Proxmox name, which could match a host_vars file), goes in
the group usas_vms (which inherits none of the servers group's variables), and gets none of the Vault's secrets: no
admin password, no sudo password, no relay password or webhook URL. Every host connects with its host keys
pinned by cluster and VM ID (sshkeys.vm_alias).

An updates plan (playbook "updates") runs playbooks/updates.yml with the choices of UPDATE_OPTIONS as allowlisted
extra variables; its hosts get the cluster's facts the cluster inventory would give them (node, tags, HA state),
which only order the waves.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from . import vms as v
from .schema import Invalid, parse_ufw_rule
from .store import Unsafe, dump_yaml

MAX_HOSTS = 25
MAX_RULES = 20
GROUP = "usas_vms"  # the VMs that aren't servers in the inventory (named v.FOREIGN_NAME)
# The baseline's tags a VMs page run may use, and the presets. "full" passes no --tags: every role runs.
TAGS = ("ssh_keys", "common", "firewall", "ssh", "fail2ban", "hardening")
PRESETS = {
    "keys": ("SSH keys only", ("ssh_keys",)),
    "ssh": ("SSH hardening and Fail2Ban", ("ssh", "fail2ban")),
    "full": ("Full baseline", ()),
    "custom": ("Custom", None),
}
DEFAULT_PRESET = "full"
# The updates playbook's choices on the VMs page: the form field, the variable, and the values (the first is the
# default). The two boxes are "true" when ticked.
UPDATES = "updates"
UPDATES_LABEL = "Updates"
UPDATE_OPTIONS = {
    "updates-scope": ("updates_scope", ("all", "security")),
    "updates-reboot": ("updates_reboot", ("never", "if_required")),
}
UPDATE_BOXES = {"updates-snapshot": "updates_snapshot", "updates-window": "updates_ignore_window"}
# Variables that come from the Vault: a VM outside the inventory gets them empty (the role defaults).
VAULT_VARS = ("admin_password", "ansible_become_password", "mail_relay_password", "fail2ban_webhook_url")
PORT = re.compile(r"^[0-9]{1,5}\Z")


class PlanProblem(Invalid):
    """A plan that can't run; errors are keyed by the form field they belong to."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(next(iter(errors.values())))
        self.errors = errors


@dataclass
class Formats:
    """The roles' formats the plan checks values against (from the schema)."""

    ipv4: str  # provision_ipv4_pattern
    users: tuple[str, ...]  # common_username_pattern and provision_username_pattern


@dataclass
class Settings:
    """What the plan needs from the saved settings."""

    admin_user: str = "sysadmin"
    ssh_port: int = 22
    github_user: str = ""
    ssh_keys_exclusive: bool = True
    ui_key_authorized: bool = True
    apt_upgrade: str = "dist"
    ufw_default_incoming: str = "deny"
    ufw_rules: list[str] = field(default_factory=list)  # every server's rules, as "443/tcp from any"
    known_hosts: str = ""


@dataclass
class Target:
    vmid: int
    name: str  # the inventory host name: the server's, or vm-<vmid>
    pve_name: str
    node: str
    static: bool  # a server on the Servers page
    address: str  # "" for a server on DHCP: the baseline asks the guest agent
    source: str  # inventory, agent, ipconfig0, manual or dhcp
    port: int | None  # None: the baseline's probe (ssh_port, then 22)
    user: str
    alias: str
    fingerprints: list[str]
    manage_hostname: bool = False
    ufw: list[dict[str, str]] = field(default_factory=list)
    override: bool = False  # a release outside the OS catalog, or an OS the guest didn't report, confirmed
    pve_tags: list[str] = field(default_factory=list)  # the VM's Proxmox tags (an updates run's groups)
    hastate: str = ""  # set when Proxmox HA manages the VM


@dataclass
class Plan:
    preset: str
    tags: list[str]
    targets: list[Target]
    warnings: list[str]
    inventory: str
    digest: str  # the same plan (hosts, settings, tags, pinned keys, options) gives the same digest
    playbook: str = "baseline"  # or UPDATES
    options: dict[str, str] = field(default_factory=dict)  # an updates plan's extra variables

    @property
    def limit(self) -> str:
        return ",".join(t.name for t in self.targets)

    def summary(self) -> list[dict[str, Any]]:
        """The targets as a run records them."""
        return [{"vmid": t.vmid, "name": t.name, "pve_name": t.pve_name, "node": t.node, "static": t.static,
                 "address": t.address, "source": t.source, "port": t.port, "user": t.user, "alias": t.alias,
                 "fingerprints": t.fingerprints, "manage_hostname": t.manage_hostname, "override": t.override}
                for t in self.targets]


def selectable(row: dict[str, Any]) -> tuple[bool, str]:
    """Whether a VMs page row can be selected, and why not."""
    if row.get("type") != "qemu" or row.get("template"):
        return False, "Only VMs can be selected, not containers or templates."
    if row.get("support") == v.UNSUPPORTED:
        return False, str(row.get("reason") or "The playbooks don't support its OS.")
    if row.get("status") != "running":
        return False, "It isn't running."
    if row.get("lock"):
        return False, f"It is locked ({row['lock']})."
    return True, ""


def update_options(form: dict[str, str]) -> dict[str, str]:
    """An updates plan's extra variables from the form, each one of its allowed values."""
    options = {}
    for name, (variable, values) in UPDATE_OPTIONS.items():
        value = form.get(name) or values[0]
        if value not in values:
            raise PlanProblem({name: "Choose one of the options."})
        options[variable] = value
    for name, variable in UPDATE_BOXES.items():
        options[variable] = "true" if form.get(name) == "on" else "false"
    return options


def preset_tags(preset: str, custom: list[str]) -> list[str]:
    if preset not in PRESETS:
        raise PlanProblem({"preset": "Choose what to run."})
    tags = PRESETS[preset][1]
    if tags is None:
        unknown = [t for t in custom if t not in TAGS]
        chosen = [t for t in TAGS if t in custom]
        if unknown or not chosen:
            raise PlanProblem({"tags": "Choose at least one part of the baseline."})
        return chosen
    return list(tags)


def parse_port(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    if not PORT.match(text) or not 1 <= int(text) <= 65535:
        raise Invalid("The SSH port must be a number from 1 to 65535, or empty to try the baseline's port, then 22.")
    return int(text)


def parse_rules(text: str) -> list[dict[str, str]]:
    items = [item.strip() for item in re.split(r"[,\n]", text) if item.strip()]
    if len(items) > MAX_RULES:
        raise Invalid(f"At most {MAX_RULES} firewall rules per VM.")
    return [parse_ufw_rule(item) for item in items]


def rule_text(rule: dict[str, str]) -> str:
    return f"{rule['port']}/{rule['proto']}" + (f" from {rule['from']}" if rule.get("from") else "")


def check_address(address: str, formats: Formats) -> str:
    address = address.strip()
    if not re.match(formats.ipv4, address) or not v.usable_ipv4(address):
        raise Invalid("Enter the VM's IPv4 address (not a loopback, link-local, multicast or reserved one).")
    return address


def check_user(user: str, formats: Formats) -> str:
    user = user.strip()
    if not user or not all(re.match(p, user) for p in formats.users):
        raise Invalid("Enter a Linux user name: lowercase letters, digits, '_' or '-', starting with a letter.")
    return user


def ssh_args(known_hosts: str, alias: str) -> str:
    return f"-o UserKnownHostsFile={known_hosts} -o StrictHostKeyChecking=yes -o HostKeyAlias={alias}"


def cluster_vars(target: Target) -> dict[str, Any]:
    """What the cluster inventory (plugins/inventory/usas_pve.py) tells the updates playbook about a VM."""
    return {"usas_pve_vmid": target.vmid, "usas_pve_node": Unsafe(target.node),
            "usas_pve_name": Unsafe(target.pve_name), "usas_pve_alias": Unsafe(target.alias),
            "usas_pve_tags": [Unsafe(t) for t in target.pve_tags], "usas_pve_hastate": Unsafe(target.hastate)}


def update_host_vars(target: Target, common: dict[str, Any]) -> dict[str, Any]:
    """An updates run's variables for one host: a server keeps its own login, sudo and settings; a VM outside
    the inventory logs in as its user, with passwordless sudo and none of the Vault's secrets."""
    host: dict[str, Any] = {}
    if not target.static:
        host["ansible_host"] = Unsafe(target.address)
        if target.port is not None:
            host["ansible_port"] = target.port
        host.update({"ansible_user": Unsafe(target.user), **{name: "" for name in VAULT_VARS}})
    return {**host, **common, **cluster_vars(target)}


def host_vars(target: Target, settings: Settings, playbook: str = "baseline") -> dict[str, Any]:
    """The run inventory's variables for one host."""
    common: dict[str, Any] = {"ansible_ssh_common_args": ssh_args(settings.known_hosts, target.alias),
                              "ansible_control_path": v.control_path(target.vmid)}
    if playbook == UPDATES:
        return update_host_vars(target, common)
    if target.static:
        return common  # a server keeps its own address, user, sudo and settings
    host: dict[str, Any] = {"ansible_host": Unsafe(target.address)}
    if target.port is not None:
        host["ansible_port"] = target.port
    host.update({
        "ansible_user": Unsafe(target.user), "admin_user": Unsafe(target.user),
        # None of the Vault's secrets: sudo stays passwordless for the user Ansible logs in as.
        **{name: "" for name in VAULT_VARS}, "admin_sudo_nopasswd": True,
        "manage_hostname": target.manage_hostname,
    })
    if target.manage_hostname:
        host["server_hostname"] = Unsafe(target.pve_name)
    if target.ufw:
        host["webui_firewall_allow_host"] = [{k: Unsafe(val) for k, val in rule.items()} for rule in target.ufw]
        host["firewall_allow"] = "{{ (webui_firewall_allow | default([])) + webui_firewall_allow_host }}"
    if target.override:
        host["baseline_allow_unsupported"] = True
    host.update(common)
    host.update({"usas_vmid": target.vmid, "usas_pve_name": Unsafe(target.pve_name)})
    return host


def inventory_text(targets: list[Target], settings: Settings, playbook: str = "baseline") -> str:
    static = {t.name: host_vars(t, settings, playbook) for t in targets if t.static}
    foreign = {t.name: host_vars(t, settings, playbook) for t in targets if not t.static}
    children: dict[str, Any] = {}
    if foreign:
        children[GROUP] = {"hosts": foreign}
    if static:
        children["servers"] = {"hosts": static}
    header = ("# The hosts of one VMs page run, written by the web UI. Kept with the run for the record; it holds\n"
              "# no secrets.\n")
    return header + dump_yaml({"all": {"children": children}})


def warnings(targets: list[Target], tags: list[str], settings: Settings) -> list[str]:
    """What the run would change that matters on a server already in use."""
    every = not tags
    found = []
    foreign = [t for t in targets if not t.static]
    if every or "firewall" in tags:
        rules = ", ".join(settings.ufw_rules) or "none"
        found.append(f"The firewall's default for incoming traffic is {settings.ufw_default_incoming}: anything "
                     f"these VMs serve that isn't SSH or in the rules (every server: {rules}; plus each VM's own) "
                     "becomes unreachable.")
    if (every or "common" in tags or "ssh_keys" in tags) and settings.ssh_keys_exclusive:
        source = f"on GitHub for {settings.github_user}" if settings.github_user else "listed on the Defaults page"
        found.append(f"SSH keys in the login user's authorized_keys that aren't {source} or among the extra keys "
                     "are removed (ssh_keys_exclusive). The check run's diff lists them.")
    if (every or "common" in tags) and settings.apt_upgrade != "no":
        found.append(f"Every package is upgraded (apt_upgrade: {settings.apt_upgrade}).")
    if (every or "ssh" in tags) and settings.ssh_port != 22:
        found.append(f"SSH moves to port {settings.ssh_port}.")
        if not every and "firewall" not in tags:
            found.append(f"The firewall isn't part of this run: if UFW is active on a VM, allow port "
                         f"{settings.ssh_port} first, or include the firewall.")
    if every or "ssh" in tags:
        found.append("SSH hardening turns password logins and root logins off: the login user must have a key.")
    if foreign and (every or "common" in tags):
        users = ", ".join(sorted({t.user for t in foreign}))
        found.append(f"VMs outside the inventory get no admin password: sudo stays passwordless for {users}, and "
                     "the baseline writes /etc/sudoers.d/90-ansible-<user> to keep it so.")
    for t in targets:
        if t.manage_hostname:
            found.append(f"{t.name} is renamed to {t.pve_name}.")
        if t.override:
            found.append(f"{t.name}: its OS isn't a release of the OS catalog, or the guest didn't report it (you "
                         "chose to run it anyway).")
    return found


def update_warnings(targets: list[Target], options: dict[str, str]) -> list[str]:
    """What an updates run would do that matters on a server already in use."""
    found = []
    if options.get("updates_scope") == "security":
        found.append("Only security updates are installed (updates_scope: security).")
    else:
        found.append("Every pending update is installed, kernels too (apt upgrade --with-new-pkgs: nothing is "
                     "removed).")
    if options.get("updates_reboot") == "if_required":
        names = ", ".join(t.name for t in targets)
        found.append(f"These VMs reboot if an update asks for it, inside their reboot window: {names}. Their "
                     "health is checked after.")
    else:
        found.append("Nothing reboots: a server that needs a reboot is reported as “reboot pending”.")
    if options.get("updates_snapshot") == "true":
        found.append("Each VM gets a Proxmox snapshot of its disks before its update (the token's role needs "
                     "VM.Snapshot). Rolling back is left to a Proxmox admin.")
    if options.get("updates_ignore_window") == "true":
        found.append("Each server's update window (updates_window) is ignored: the audit log records it.")
    foreign = [t for t in targets if not t.static]
    if foreign:
        users = ", ".join(sorted({t.user for t in foreign}))
        found.append(f"VMs outside the inventory get no sudo password: {users} needs passwordless sudo.")
    return found


def digest(tags: list[str], inventory: str, pins: dict[str, list[str]], playbook: str = "baseline",
           options: dict[str, str] | None = None) -> str:
    """Names a plan: the same playbook, tags, run inventory, pinned keys and options give the same digest."""
    data: dict[str, Any] = {"playbook": playbook, "tags": tags, "inventory": inventory,
                            "pins": {alias: sorted(found) for alias, found in pins.items()}}
    if playbook != "baseline":
        data["options"] = dict(sorted((options or {}).items()))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def build(rows: dict[int, dict[str, Any]], selected: list[int], form: dict[str, str], *, servers: list[dict[str, Any]],
          pins: dict[str, list[str]], aliases: dict[int, str], formats: Formats, settings: Settings,
          tags: list[str], preset: str, playbook: str = "baseline", options: dict[str, str] | None = None) -> Plan:
    """A plan for the selected VM IDs. rows are the cluster listing's rows by VM ID; form has each VM's settings
    as vm-<vmid>-<setting>; servers are the Servers page's rows; pins are the pinned fingerprints by alias. An
    updates plan (playbook UPDATES) has no tags and takes options (update_options); a VM's firewall rules and host
    name are the baseline's only. Raises PlanProblem with every error."""
    updates = playbook == UPDATES
    errors: dict[str, str] = {}
    if not selected:
        raise PlanProblem({"selection": "Select at least one VM."})
    if len(selected) > MAX_HOSTS:
        raise PlanProblem({"selection": f"Select at most {MAX_HOSTS} VMs for one run."})
    static_names = {str(s["name"]) for s in servers}
    static_addresses = {str(s.get("ansible_host")): str(s["name"]) for s in servers if s.get("ansible_host")}
    removes_keys = not updates and (not tags or bool({"common", "ssh_keys"} & set(tags)))
    if settings.ssh_keys_exclusive and not settings.ui_key_authorized and removes_keys:
        errors["selection"] = ("The web UI's own key would be removed from the VMs (ssh_keys_exclusive), locking it "
                               "out: tick 'Authorize the UI's key' on the Defaults page first.")
    targets: list[Target] = []
    for vmid in selected:
        prefix = f"vm-{vmid}-"
        row = rows.get(vmid)
        if row is None:
            errors[prefix + "address"] = f"VM {vmid} isn't on the cluster any more."
            continue
        ok, why = selectable(row)
        if not ok:
            errors[prefix + "address"] = f"VM {vmid} can't be selected: {why}"
            continue
        override = form.get(prefix + "override") == "on"
        name, server = v.host_name(vmid, servers)
        # A server on the Servers page runs like it does from the Run page: whose OS the guest didn't report
        # needs no confirmation. An untested release always does.
        # The updates playbook skips a release it doesn't support by itself, and says so.
        needs = () if updates else (v.NEEDS_OVERRIDE,) if server is not None else (v.NEEDS_OVERRIDE, v.UNKNOWN)
        override = override and not updates
        if row.get("support") in needs and not override:
            errors[prefix + "override"] = (f"VM {vmid}: {row.get('reason', '')} Tick “Run the baseline on it "
                                           "anyway” to go on.")
        pve_name = v.text(row.get("name"), 64)  # the listing cleaned it already
        alias = aliases[vmid]
        cluster = {"pve_tags": [v.text(t, 64) for t in row.get("tags") or []][:32],
                   "hastate": v.text(row.get("hastate"), 20)}
        if server is not None:
            address = str(server.get("ansible_host") or "")
            targets.append(Target(
                vmid=vmid, name=name, pve_name=pve_name, node=v.text(row.get("node"), 64), static=True,
                address=address, source="inventory" if address else "dhcp", port=None, user=settings.admin_user,
                alias=alias, fingerprints=pins.get(alias, []), override=override, **cluster))
        else:
            if name in static_names:
                errors[prefix + "address"] = f"A server on the Servers page is called {name} but has another VM ID."
                continue
            try:
                address = check_address(form.get(prefix + "address", ""), formats)
            except Invalid as exc:
                errors[prefix + "address"] = exc.message
                address = ""
            if address in static_addresses:
                errors[prefix + "address"] = f"That is server {static_addresses[address]}'s address."
            try:
                port = parse_port(form.get(prefix + "port", ""))
            except Invalid as exc:
                errors[prefix + "port"] = exc.message
                port = None
            try:
                user = check_user(form.get(prefix + "user", ""), formats)
            except Invalid as exc:
                errors[prefix + "user"] = exc.message
                user = ""
            try:
                rules = [] if updates else parse_rules(form.get(prefix + "ufw", ""))
            except Invalid as exc:
                errors[prefix + "ufw"] = exc.message
                rules = []
            manage = form.get(prefix + "hostname") == "on" and not updates
            if manage and not v.HOSTNAME_PATTERN.match(pve_name):
                errors[prefix + "hostname"] = f"'{pve_name}' isn't a valid host name, so it can't be set."
            source = ("agent" if address in (row.get("addresses") or [])
                      else row.get("ip_source") if address and address == row.get("ip") else "manual")
            targets.append(Target(
                vmid=vmid, name=name, pve_name=pve_name, node=v.text(row.get("node"), 64), static=False,
                address=address, source=str(source), port=port, user=user, alias=alias,
                fingerprints=pins.get(alias, []), manage_hostname=manage, ufw=rules, override=override, **cluster))
        if not pins.get(alias):
            errors.setdefault(prefix + "hostkey", f"Trust VM {vmid}'s host key first: scan it and compare the "
                                                  "fingerprints.")
    seen: dict[str, int] = {}
    for t in targets:
        if t.address and t.address in seen:
            errors[f"vm-{t.vmid}-address"] = f"VM {seen[t.address]} has the same address."
        seen.setdefault(t.address, t.vmid)
    if errors:
        raise PlanProblem(errors)
    options = dict(options or {}) if updates else {}
    tags = [] if updates else tags
    inventory = inventory_text(targets, settings, playbook)
    found = update_warnings(targets, options) if updates else warnings(targets, tags, settings)
    pinned = {t.alias: t.fingerprints for t in targets}
    return Plan(preset=UPDATES if updates else preset, tags=tags, targets=targets, warnings=found,
                inventory=inventory, digest=digest(tags, inventory, pinned, playbook, options),
                playbook=UPDATES if updates else "baseline", options=options)


def plan_json(plan: Plan) -> dict[str, Any]:
    label = UPDATES_LABEL if plan.playbook == UPDATES else PRESETS[plan.preset][0]
    return {"preset": plan.preset, "preset_label": label, "tags": plan.tags, "playbook": plan.playbook,
            "options": plan.options,
            "targets": [{**asdict(t), "ufw": [rule_text(r) for r in t.ufw]} for t in plan.targets],
            "warnings": plan.warnings, "digest": plan.digest}
