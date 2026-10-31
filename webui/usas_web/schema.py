"""The form fields, built from the roles themselves.

Every field names the role variable it sets. Its default and help text are read from
roles/<role>/defaults/main.yml, and its allowlist pattern or list of allowed values from
roles/<role>/vars/main.yml, the same patterns the roles' assert tasks check. A field that names a variable or
pattern the roles don't have is an error at startup (and in the tests), so the UI can't drift from the
playbooks. Fields marked ``ui_pattern`` have a format the roles don't check themselves (the API host, the
token name); the UI checks them so that only plain values reach the inventory.
"""

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .problems import Problem

ROLES = ("os_catalog", "installer_iso", "proxmox_vm", "common", "firewall", "ssh_hardening", "fail2ban",
         "hardening")

# The OS catalog (roles/os_catalog/vars/catalog.yml): an OS field (choices_var=OS_CATALOG) offers its releases that
# are still supported and that this release can install, with one of these methods.
CATALOG = Path("roles/os_catalog/vars/catalog.yml")
OS_CATALOG = "os_catalog"
INSTALL_METHODS = ("autoinstall",)

# Names in roles/*/vars/main.yml that aren't the format of a form field: the sysctl set is a mapping the form
# doesn't edit, the rest are file paths and package or platform lists.
NOT_FORM_FORMATS = frozenset({
    "hardening_sysctl_key_pattern", "hardening_sysctl_value_pattern", "hardening_sysctl_file",
    "hardening_audit_rules_file", "hardening_audit_immutable_file", "hardening_container_types",
    "common_msmtp_password_file", "common_other_mtas", "fail2ban_webhook_action_file", "pve_iso_dir_pattern",
    "pve_iso_dirs_file", "common_time_sync_other_services", "common_timesyncd_dropin", "common_chrony_sources_file",
    "common_chrony_off_mark",
    # What a dropped API connection looks like in proxmox_kvm's errors, for retrying it: not a value anyone types.
    "pve_api_dropped_pattern",
    # The keys a network card may have: the Servers page writes only keys it parsed (parse_nic).
    "provision_nic_keys",
    "provision_installer_config_copies",
    # A server's OS: checked by the roles (roles/os_catalog/tasks/select.yml); an image outside the catalog is
    # set in the inventory, not in the UI.
    "os_catalog_id_pattern", "os_catalog_install_methods", "os_catalog_families", "vm_os_image_url_pattern",
    "vm_os_image_sha256_pattern",
    # firewalld's zone on the RHEL family: fixed, not a setting.
    "firewall_os_zone",
    # The inventory's server groups (servers, and ubuntu_servers before 0.7.0), for build-installer's plan.
    "pve_plan_server_group",
    # The installer ISO's file name: it follows each server's OS (vm_os), which the forms set.
    "installer_iso_name_pattern",
})

# The roles/proxmox_vm/vars/main.yml formats an extra network card is checked against (parse_nic).
NIC_FORMATS = ("provision_bridge_pattern", "provision_vlan_pattern", "provision_ipv4_pattern",
               "provision_prefix_pattern", "provision_mtu_pattern", "provision_mac_pattern", "provision_nic_models",
               "provision_nic_modes")

# Formats the roles check inline in their assert tasks rather than in vars/main.yml (kept in step with
# roles/*/tasks/main.yml; tests/test_schema.py reads those tasks to check).
PORT_RANGE = (1, 65535)
PREFIX_RANGE = (1, 32)
DIGITS = r"^[0-9]+\Z"

# Formats only the UI checks: plain values, no spaces, quotes or template characters.
GITHUB_USER = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z"
API_HOST = r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z|^\[[0-9A-Fa-f:.]+\]\Z"
API_USER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
TOKEN_ID = r"^[A-Za-z][A-Za-z0-9._-]{0,63}\Z"  # noqa: S105 (a format, not a secret)
TOKEN_SECRET = r"^[A-Za-z0-9-]{1,128}\Z"  # noqa: S105 (a format, not a secret)
STORAGE_ID = r"^[A-Za-z][A-Za-z0-9._-]{0,63}\Z"
TIMEZONE = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}\Z"
SSH_PUBLIC_KEY = r"^(ssh-|ecdsa-|sk-)[A-Za-z0-9@.-]+ [A-Za-z0-9+/=]+( [^\r\n{}%]*)?\Z"
_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
UFW_FROM = rf"^(any|({_OCTET}\.){{3}}{_OCTET}(/([0-9]|[12][0-9]|3[0-2]))?)\Z"
UFW_PORT = r"^[0-9]{1,5}(:[0-9]{1,5})?\Z"


class SchemaError(RuntimeError):
    """A field names a role variable or pattern that doesn't exist."""


@dataclass(frozen=True)
class Spec:
    name: str
    label: str
    kind: str  # text, int, bool, choice, list, secret
    role: str | None = None  # role whose defaults/main.yml has the variable
    pattern_var: str | tuple[str, ...] | None = None  # vars/main.yml pattern(s) for the value (or each list item)
    choices_var: str | None = None  # vars/main.yml list of allowed values, or OS_CATALOG
    ui_pattern: str | None = None  # format checked by the UI only
    ui_choices: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    required: bool = False
    vault: str | None = None  # stored in the Vault under this name instead of the inventory
    help: str = ""  # used when the defaults file has no comment for the variable
    advanced: bool = False
    default: Any = None  # for variables no role defines (Vault values), or whose role default is a template


@dataclass(frozen=True)
class Field:
    spec: Spec
    default: Any
    help: str
    pattern: str | None
    choices: tuple[str, ...] | None
    patterns: tuple[str, ...] = ()  # every pattern the value must match (roles that read it may differ)
    formats: dict[str, Any] = field(default_factory=dict)  # NIC_FORMATS, for the network cards column
    choice_labels: dict[str, str] = field(default_factory=dict)  # what the form shows for each choice, if not itself

    def __getattr__(self, item):
        return getattr(self.spec, item)


@dataclass
class RoleData:
    defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    comments: dict[str, dict[str, str]] = field(default_factory=dict)
    vars: dict[str, dict[str, Any]] = field(default_factory=dict)

    def default_for(self, name: str, role: str) -> Any:
        try:
            return self.defaults[role][name]
        except KeyError:
            raise SchemaError(f"roles/{role}/defaults/main.yml has no variable {name}") from None

    def var(self, name: str) -> Any:
        for values in self.vars.values():
            if name in values:
                return values[name]
        raise SchemaError(f"no roles/*/vars/main.yml defines {name}")

    def all_var_names(self) -> set[str]:
        return {name for values in self.vars.values() for name in values}


_VAR_LINE = re.compile(r"^([a-z_][a-z0-9_]*):(?P<rest>.*)$")


def _comments(text: str) -> dict[str, str]:
    """Help text for each top-level variable: the comment lines above it plus its trailing comment."""
    result: dict[str, str] = {}
    pending: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            # Section rulers ("# --- Placement ---") and example lines ("#   - { port: ... }") aren't help.
            if body.startswith("---") or line.lstrip("#").startswith("   "):
                continue
            pending.append(body)
            continue
        match = _VAR_LINE.match(line)
        if match:
            rest = match.group("rest")
            trailing = ""
            if " #" in rest:
                trailing = rest.split(" #", 1)[1].strip()
            text_parts = [part for part in [" ".join(pending), trailing] if part]
            result[match.group(1)] = " ".join(text_parts)
            pending = []
        elif not stripped or not line.startswith(" "):
            pending = []
    return result


def os_choices(catalog: list[dict[str, Any]], today: datetime.date) -> dict[str, str]:
    """The catalog's releases the forms offer, id -> label such as "Debian 13 (trixie) · until 2030-06-30": those
    still supported that this release can install."""
    choices = {}
    for entry in catalog:
        if str(entry["eol"]) < today.isoformat() or not set(entry["methods"]) & set(INSTALL_METHODS):
            continue
        codename = f" ({entry['codename']})" if entry.get("codename") else ""
        choices[str(entry["id"])] = f"{entry['name']} {entry['version']}{codename} · until {entry['eol']}"
    return choices


def load_roles(project_dir: Path) -> RoleData:
    data = RoleData()
    for role in ROLES:
        defaults = project_dir / "roles" / role / "defaults" / "main.yml"
        text = defaults.read_text(encoding="utf-8")
        data.defaults[role] = yaml.safe_load(text) or {}
        data.comments[role] = _comments(text)
        varfile = project_dir / "roles" / role / "vars" / "main.yml"
        if varfile.exists():
            data.vars[role] = yaml.safe_load(varfile.read_text(encoding="utf-8")) or {}
    return data


S = Spec
# Section -> fields, in form order.
SECTIONS: dict[str, tuple[Spec, ...]] = {
    "proxmox": (
        S("pve_api_host", "API host", "text", "proxmox_vm", ui_pattern=API_HOST, required=True,
          help="Host name or IP address of a Proxmox VE node (the API listens on it)."),
        S("pve_api_port", "API port", "int", "proxmox_vm", minimum=PORT_RANGE[0], maximum=PORT_RANGE[1]),
        S("pve_api_user", "API user", "text", "proxmox_vm", ui_pattern=API_USER,
          help="The user that owns the token, user@realm."),
        S("pve_api_token_id", "Token name", "text", ui_pattern=TOKEN_ID, required=True,
          vault="vault_pve_api_token_id", default="",
          help="The token's name only (automation, not ansible@pve!automation)."),
        S("pve_api_token_secret", "Token secret", "secret", ui_pattern=TOKEN_SECRET, required=True,
          vault="vault_pve_api_token_secret", default="", help="The token's secret, a UUID."),
        S("pve_validate_certs", "Verify the API's TLS certificate", "bool", "proxmox_vm",
          help="Leave this on. For the node's self-signed certificate, add the cluster CA below instead."),
        S("pve_node", "Node name", "text", "proxmox_vm", pattern_var="provision_hostname_pattern", required=True,
          help="The Proxmox node the VMs are created on, as Proxmox names it, unless a server names its own node "
               "on the Servers page."),
        S("pve_storage", "VM disk storage", "text", "proxmox_vm", ui_pattern=STORAGE_ID),
        S("pve_efidisk_format", "EFI disk format", "choice", "proxmox_vm", ui_choices=("raw", "qcow2")),
        S("pve_bridge", "Network bridge", "text", "proxmox_vm", pattern_var="provision_bridge_pattern"),
        S("pve_vlan", "VLAN tag", "int", "proxmox_vm", minimum=1, maximum=4094),
        S("pve_iso_storage", "ISO storage", "text", "os_catalog", pattern_var="pve_storage_id_pattern",
          advanced=True, help="A Proxmox storage that allows ISO images (Content: ISO image), such as local or an NFS "
          "share for the whole cluster. Its folder on the node is found automatically."),
        S("pve_api_timeout", "API request timeout (seconds)", "int", "proxmox_vm", minimum=5, maximum=600,
          advanced=True, help="How long one Proxmox API request may take. Allocating disks can keep the API busy."),
        S("pve_ssh_user", "Node SSH user", "text", "os_catalog", pattern_var="pve_ssh_user_pattern",
          advanced=True, help="The UI and the playbooks log in to the node over SSH as this user."),
    ),
    "defaults": (
        S("github_user", "GitHub user", "text", "common", ui_pattern=GITHUB_USER,
          help="Whose public keys (https://github.com/<user>.keys) are installed for the admin user."),
        S("github_keys_url", "Keys URL", "text", "common", advanced=True,
          ui_pattern=r"^https?://[A-Za-z0-9.:\[\]-]+(/[A-Za-z0-9._~/-]*)?(\{\{ github_user \}\}[A-Za-z0-9._~/-]*)?\Z",
          help="Where the keys come from. Leave the default unless you mirror them."),
        S("admin_extra_ssh_keys", "Extra public keys", "list", "common", ui_pattern=SSH_PUBLIC_KEY,
          help="One public key per line, installed besides the GitHub ones."),
        S("admin_user", "Admin user", "text", "common",
          pattern_var=("common_username_pattern", "provision_username_pattern")),
        S("admin_password", "Admin password", "secret", required=True, vault="vault_admin_password", default="",
          help="For the console and sudo; SSH stays key-only. Hashed (SHA-512) before it reaches the installer."),
        S("timezone", "Time zone", "text", "common", ui_pattern=TIMEZONE),
        S("time_sync_servers", "Time servers (NTP)", "list", "common", pattern_var="common_time_server_pattern"),
        S("time_sync_pools", "Time server pools (NTP)", "list", "common", pattern_var="common_time_server_pattern",
          advanced=True),
        S("vm_os", "Default OS", "choice", OS_CATALOG, choices_var=OS_CATALOG, default="ubuntu-26.04",
          help="For servers that don't choose their own on the Servers page."),
        S("net_gateway", "Gateway", "text", "proxmox_vm", pattern_var="provision_ipv4_pattern",
          help="Needed for servers with a static address (unless an extra network card has the default route)."),
        S("net_prefix", "Network prefix length", "int", "proxmox_vm", minimum=PREFIX_RANGE[0],
          maximum=PREFIX_RANGE[1]),
        S("net_dns", "DNS servers", "list", "proxmox_vm", pattern_var="provision_ipv4_pattern"),
        S("net_search", "Search domains", "list", "proxmox_vm", pattern_var="provision_hostname_pattern",
          help="Each part of a domain (between dots) must be a valid host name."),
        S("autoinstall_storage_layout", "Disk layout", "choice", "proxmox_vm", ui_choices=("lvm", "direct", "zfs"),
          help="For the Ubuntu installer; cloud images come with their own layout."),
        S("vm_secure_boot", "Secure Boot keys", "bool", "proxmox_vm", advanced=True),
        S("vm_disk_encryption", "Encrypt the root disk", "bool", "proxmox_vm",
          help="Only for VMs created from now on; existing VMs are left as they are."),
        S("vm_disk_passphrase", "Disk passphrase", "secret", pattern_var="provision_disk_passphrase_pattern",
          vault="vault_disk_passphrase", default="",
          help="The recovery key: typed at the VM's console when the TPM doesn't unlock the disk. 12 to 512 "
               "printable ASCII characters, no braces. Kept in the Vault and never shown again, so keep a copy in "
               "your password manager. A new one applies to VMs created after it; existing VMs keep theirs."),
    ),
    "hardening": (
        S("ssh_port", "SSH port", "int", "ssh_hardening", minimum=PORT_RANGE[0], maximum=PORT_RANGE[1],
          help="Anything other than 22 switches sshd from ssh.socket to ssh.service; the firewall rule moves too."),
        S("ssh_allow_users", "Allowed SSH users", "list", "ssh_hardening", pattern_var="ssh_allow_users_pattern",
          help="Empty allows every user. If set, it must include the admin user."),
        S("ssh_permit_root_login", "Root login over SSH", "choice", "ssh_hardening",
          choices_var="ssh_permit_root_login_values"),
        S("admin_sudo_nopasswd", "Passwordless sudo for the admin user", "bool", "common",
          help="Off by default: the first run removes the installer's passwordless sudo, and later runs use "
               "the admin password. On is convenient, but anyone with an authorized key has root."),
        S("firewall_ssh_limit", "Rate-limit SSH in the firewall", "bool", "firewall", default=True),
        S("firewall_ssh_sources", "SSH allowed from", "list", "firewall", ui_pattern=UFW_FROM, default=["any"],
          help='"any", or addresses and networks (CIDR), one per line.'),
        S("firewall_allow", "Extra firewall rules (all servers)", "list", "firewall", ui_pattern="ufw_rule",
          default=[],
          help='One rule per line: port or range, optional /tcp or /udp, optional "from" a network. '
               "Example: 443/tcp or 5432/tcp from 10.0.0.0/24."),
        S("fail2ban_ignoreip", "Never ban", "list", "fail2ban", pattern_var="fail2ban_ignoreip_pattern",
          help="Your own management networks, so a typo can't lock you out."),
        S("fail2ban_maxretry", "Failures before a ban", "int", "fail2ban", minimum=1, maximum=1000),
        S("fail2ban_findtime", "Counting window", "text", "fail2ban", pattern_var="fail2ban_time_pattern"),
        S("fail2ban_bantime", "First ban", "text", "fail2ban", pattern_var="fail2ban_time_pattern"),
        S("fail2ban_bantime_increment", "Longer bans for repeat offenders", "bool", "fail2ban"),
        S("fail2ban_bantime_maxtime", "Longest ban", "text", "fail2ban", pattern_var="fail2ban_time_pattern"),
        S("fail2ban_sshd_mode", "sshd filter mode", "choice", "fail2ban", choices_var="fail2ban_sshd_modes"),
        S("fail2ban_banaction", "Ban action", "text", "fail2ban", pattern_var="fail2ban_banaction_pattern",
          advanced=True),
        S("hardening_sysctl_enabled", "Kernel and network settings (sysctl)", "bool", "hardening"),
        S("hardening_auditd_enabled", "Audit changes with auditd", "bool", "hardening"),
        S("hardening_auditd_immutable", "Lock the audit rules until a reboot", "bool", "hardening", advanced=True),
        S("hardening_apparmor_enabled", "Keep AppArmor on and report profiles in complain mode", "bool", "hardening"),
        S("unattended_upgrades_mail", "Mail update reports to", "text", "common", pattern_var="common_email_pattern"),
        S("unattended_upgrades_mail_report", "Which update reports", "choice", "common",
          choices_var="common_mail_report_values"),
        S("fail2ban_destemail", "Mail bans to", "text", "fail2ban", pattern_var="fail2ban_email_pattern"),
        S("fail2ban_sender", "Ban mail sender", "text", "fail2ban", pattern_var="fail2ban_email_pattern",
          advanced=True),
        S("fail2ban_webhook_url", "Webhook for bans", "secret", pattern_var="fail2ban_webhook_url_pattern",
          vault="vault_fail2ban_webhook_url", default="",
          help="An ntfy, Gotify, Slack, Discord or Mattermost URL that gets a message for every ban. It often holds "
               "a token, so it is kept in the Vault."),
        S("fail2ban_webhook_format", "Webhook message format", "choice", "fail2ban",
          choices_var="fail2ban_webhook_formats"),
        S("mail_relay_enabled", "Send mail through a relay (msmtp)", "bool", "common"),
        S("mail_relay_host", "Relay host", "text", "common", pattern_var="common_mail_host_pattern"),
        S("mail_relay_port", "Relay port", "int", "common", minimum=PORT_RANGE[0], maximum=PORT_RANGE[1]),
        S("mail_relay_tls", "Relay encryption", "choice", "common", choices_var="common_mail_tls_values"),
        S("mail_relay_from", "Sender address", "text", "common", pattern_var="common_email_pattern"),
        S("mail_relay_user", "Relay user", "text", "common", pattern_var="common_mail_user_pattern"),
        S("mail_relay_password", "Relay password", "secret", pattern_var="common_mail_password_pattern",
          vault="vault_mail_relay_password", default="",
          help="Kept in the Vault; on the server only root can read it."),
    ),
}

# Columns of the servers table. "name" is the inventory host name; "firewall_ports" are extra rules for that host.
SERVER_COLUMNS: tuple[Spec, ...] = (
    S("name", "Name", "text", pattern_var=("provision_hostname_pattern", "common_hostname_pattern"), required=True,
      default=""),
    S("ansible_host", "IP address", "text", pattern_var="provision_ipv4_pattern", default=""),
    S("net_mode", "Address", "choice", "proxmox_vm", choices_var="provision_net_modes"),
    S("net_mac", "MAC address", "text", "proxmox_vm", pattern_var="provision_mac_pattern"),
    S("pve_vmid", "VM ID", "int", "proxmox_vm", minimum=100, maximum=999999999, required=True),
    S("pve_node", "Node", "text", "proxmox_vm", pattern_var="provision_hostname_pattern",
      help="The Proxmox node of this server's VM; empty uses the node on the Proxmox page."),
    S("vm_os", "OS", "choice", OS_CATALOG, choices_var=OS_CATALOG, default="",
      help="Empty uses the default OS on the Defaults page."),
    S("vm_cores", "Cores", "int", "proxmox_vm", minimum=1, maximum=512),
    S("vm_memory_mb", "Memory (MB)", "int", "proxmox_vm", minimum=512, maximum=16777216),
    S("vm_disk_gb", "Disk (GB)", "int", "proxmox_vm", minimum=8, maximum=131072),
    S("vm_extra_nics", "Extra network cards", "list", "proxmox_vm", ui_pattern="network_cards",
      help="Comma-separated, each a bridge, then vlan and a tag, then dhcp, none or an address/prefix (optionally "
           "via a gateway), optionally mtu, model and mac. Example: vmbr1 vlan 20 10.20.0.5/24 mtu 9000, vmbr1 dhcp."),
    S("firewall_ports", "Extra firewall rules", "list", ui_pattern="ufw_rule", default=[],
      help="Comma-separated, for example 80/tcp, 443/tcp, 5432/tcp from 10.0.0.0/24."),
)


class Schema:
    def __init__(self, project_dir: Path):
        self.roles = load_roles(project_dir)
        catalog = yaml.safe_load((project_dir / CATALOG).read_text(encoding="utf-8"))["os_catalog"]
        self.os_choices = os_choices(catalog, datetime.date.today())
        # Each release's installer ISO, for the Run page's plan.
        self.installer_isos = {str(e["id"]): str(e["methods"]["autoinstall"]["file"])
                               for e in catalog if "autoinstall" in e["methods"]}
        self.sections = {name: [self._resolve(spec) for spec in specs] for name, specs in SECTIONS.items()}
        self.server_columns = [self._resolve(spec) for spec in SERVER_COLUMNS]
        self.by_name = {f.name: f for fields in self.sections.values() for f in fields}

    def _resolve(self, spec: Spec) -> Field:
        if spec.role:
            default = self.roles.default_for(spec.name, spec.role)
            # A default the role works out from other settings (a template) shows as the field's own.
            if spec.default is not None and isinstance(default, str) and "{{" in default:
                default = spec.default
            comment = _sentence(self.roles.comments.get(spec.role, {}).get(spec.name, ""))
            help_text = " ".join(part for part in (spec.help, comment) if part)
        else:
            default, help_text = spec.default, spec.help
        names = (spec.pattern_var,) if isinstance(spec.pattern_var, str) else (spec.pattern_var or ())
        patterns = tuple(self.roles.var(name) for name in names) or ((spec.ui_pattern,) if spec.ui_pattern else ())
        pattern = patterns[0] if patterns else None
        labels = self.os_choices if spec.choices_var == OS_CATALOG else {}
        if labels:
            choices: tuple[str, ...] | None = tuple(labels)
        elif spec.choices_var:
            choices = tuple(str(c) for c in self.roles.var(spec.choices_var))
        else:
            choices = spec.ui_choices
        for p in patterns:
            if p not in ("ufw_rule", "network_cards"):
                re.compile(p)
        formats = {name: self.roles.var(name) for name in NIC_FORMATS} if spec.ui_pattern == "network_cards" else {}
        return Field(spec=spec, default=default, help=help_text, pattern=pattern, choices=choices, patterns=patterns,
                     formats=formats, choice_labels=labels)

    def pattern_vars_used(self) -> set[str]:
        specs = [s for specs in SECTIONS.values() for s in specs] + list(SERVER_COLUMNS)
        used = set()
        for s in specs:
            used.update((s.pattern_var,) if isinstance(s.pattern_var, str) else (s.pattern_var or ()))
            if s.choices_var and s.choices_var != OS_CATALOG:
                used.add(s.choices_var)
            if s.ui_pattern == "network_cards":
                used.update(NIC_FORMATS)
        return used


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?)" else text + "."


# --- Parsing and validation of submitted values ----------------------------------------------------------


class Invalid(Problem, ValueError):
    """A value a field doesn't accept; the message says what it must look like."""


def _check(patterns: tuple[str, ...], value: str, what: str) -> None:
    for pattern in patterns:
        if pattern == "ufw_rule":
            parse_ufw_rule(value)
        elif pattern == "network_cards":
            pass  # parsed with the role's formats by parse_value
        elif not re.match(pattern, value):
            raise Invalid(f"{what} isn't in the accepted format.")


def parse_ufw_rule(text: str) -> dict[str, str]:
    """'443', '443/tcp', '6000:6007/udp', '5432/tcp from 10.0.0.0/24' -> a firewall role firewall_allow entry."""
    parts = text.split()
    if len(parts) not in (1, 3) or (len(parts) == 3 and parts[1] != "from"):
        raise Invalid(f"'{text}': write a port, optionally /tcp or /udp, and optionally 'from' a network.")
    port, _, proto = parts[0].partition("/")
    proto = proto or "tcp"
    if not re.match(UFW_PORT, port) or proto not in ("tcp", "udp", "any"):
        raise Invalid(f"'{text}': the port must be a number or a range like 6000:6007, then /tcp or /udp.")
    numbers = [int(p) for p in port.split(":")]
    if not all(PORT_RANGE[0] <= n <= PORT_RANGE[1] for n in numbers) or numbers != sorted(numbers):
        raise Invalid(f"'{text}': ports go from 1 to 65535, the lower one first.")
    rule = {"port": port, "proto": proto}
    if len(parts) == 3:
        if not re.match(UFW_FROM, parts[2]):
            raise Invalid(f"'{text}': 'from' takes an IPv4 address or network, or any.")
        rule["from"] = parts[2]
    return rule


def parse_nic(text: str, formats: dict[str, Any]) -> dict[str, Any]:
    """'vmbr1 vlan 20 10.20.0.5/24 via 10.20.0.1 mtu 9000 model virtio mac BC:24:11:00:00:01', 'vmbr1 dhcp' or
    'vmbr2 none' -> a proxmox_vm vm_extra_nics entry, each value checked against the role's own formats."""
    words = text.split()
    if not words or not re.match(formats["provision_bridge_pattern"], words[0]):
        raise Invalid(f"'{text}': start with the bridge, such as vmbr1 (letters, digits, '.', '_' or '-').")
    nic: dict[str, Any] = {"bridge": words[0]}
    rest = words[1:]
    while rest:
        word = rest.pop(0)
        if word in formats["provision_nic_modes"] or "/" in word:
            if "mode" in nic:
                raise Invalid(f"'{text}': give dhcp, none or one address/prefix, once.")
            if "/" not in word:  # dhcp or none ("static" needs its address, so it is written as one)
                if word == "static":
                    raise Invalid(f"'{text}': write the static address as address/prefix, like 10.20.0.5/24.")
                nic["mode"] = word
                continue
            address, _, prefix = word.partition("/")
            if not (re.match(formats["provision_ipv4_pattern"], address)
                    and re.match(formats["provision_prefix_pattern"], prefix)):
                raise Invalid(f"'{text}': {word} must be an IPv4 address and a prefix length 1-32, like 10.20.0.5/24.")
            nic.update(mode="static", address=address, prefix=int(prefix))
            continue
        keys = {"vlan": "vlan", "via": "gateway", "mtu": "mtu", "model": "model", "mac": "mac"}
        if word not in keys or not rest or keys[word] in nic:
            raise Invalid(f"'{text}': '{word}' isn't understood here; use vlan, via, mtu, model and mac once each, "
                          "each followed by its value.")
        nic[keys[word]] = rest.pop(0)
    if "mode" not in nic:
        raise Invalid(f"'{text}': add dhcp, none or an address/prefix.")
    checks = {"vlan": ("provision_vlan_pattern", "vlan takes a VLAN tag 1-4094"),
              "gateway": ("provision_ipv4_pattern", "via takes the gateway's IPv4 address"),
              "mtu": ("provision_mtu_pattern", "mtu takes 576-9216"),
              "mac": ("provision_mac_pattern", "mac takes a unicast MAC address, like BC:24:11:12:34:56")}
    for key, (pattern, message) in checks.items():
        if key in nic and not re.match(formats[pattern], nic[key]):
            raise Invalid(f"'{text}': {message}.")
    if "model" in nic and nic["model"] not in formats["provision_nic_models"]:
        raise Invalid(f"'{text}': model is one of {', '.join(formats['provision_nic_models'])}.")
    if "mtu" in nic and nic.get("model", "virtio") != "virtio":
        raise Invalid(f"'{text}': mtu can be set on virtio cards only.")
    if "gateway" in nic and nic["mode"] != "static":
        raise Invalid(f"'{text}': via (a gateway) needs a static address.")
    for key in ("vlan", "mtu"):
        if key in nic:
            nic[key] = int(nic[key])
    order = ("mode", "bridge", "vlan", "address", "prefix", "gateway", "mtu", "model", "mac")
    return {key: nic[key] for key in order if key in nic}


def format_nic(nic: dict[str, Any]) -> str:
    words = [str(nic.get("bridge", ""))]
    if nic.get("vlan") not in (None, ""):
        words += ["vlan", str(nic["vlan"])]
    mode = nic.get("mode", "static")
    words.append(f"{nic.get('address')}/{nic.get('prefix')}" if mode == "static" else str(mode))
    for key, word in (("gateway", "via"), ("mtu", "mtu"), ("model", "model"), ("mac", "mac")):
        if nic.get(key) not in (None, ""):
            words += [word, str(nic[key])]
    return " ".join(words)


def format_ufw_rule(rule: dict[str, Any]) -> str:
    text = f"{rule.get('port')}/{rule.get('proto', 'tcp')}"
    if rule.get("from") and rule.get("from") != "any":
        text += f" from {rule['from']}"
    return text


def split_list(raw: str, commas: bool = True) -> list[str]:
    separators = r"[\r\n,]+" if commas else r"[\r\n]+"
    return [item.strip() for item in re.split(separators, raw or "") if item.strip()]


def parse_value(f: Field, raw: Any) -> Any:
    """Turns a submitted form value into the variable's value, or raises Invalid with a message."""
    if f.kind == "bool":
        return raw in ("on", "true", "1", True)
    text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    if f.kind == "list":
        # Keys contain spaces and no commas but may have comments with commas: one per line.
        items = split_list(text, commas=f.name not in ("admin_extra_ssh_keys", "firewall_allow"))
        for item in items:
            if f.name == "net_search":
                for label in item.split("."):
                    _check(f.patterns, label, f"'{item}'")
            else:
                _check(f.patterns, item, f"'{item}'")
        if f.pattern == "ufw_rule":
            return [parse_ufw_rule(item) for item in items]
        if f.pattern == "network_cards":
            return [parse_nic(item, f.formats) for item in items]
        return items
    if f.kind == "secret":
        # Kept as typed (a password may start or end with a space), but no control characters.
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise Invalid(f"{f.label} can't contain line breaks or control characters.")
        if text:
            _check(f.patterns, text, f.label)
        return text
    text = text.strip()
    if text == "":
        if f.required:
            raise Invalid(f"{f.label} is required.")
        return None if f.kind == "int" and f.default in ("", None) else f.default
    if f.kind == "int":
        if not re.match(DIGITS, text):
            raise Invalid(f"{f.label} must be a whole number.")
        number = int(text)
        if (f.minimum is not None and number < f.minimum) or (f.maximum is not None and number > f.maximum):
            raise Invalid(f"{f.label} must be between {f.minimum} and {f.maximum}.")
        return number
    if f.kind == "choice":
        if text not in (f.choices or ()):
            raise Invalid(f"{f.label} must be one of: {', '.join(f.choices or ())}.")
        return text
    _check(f.patterns, text, f.label)
    return text


def display_value(f: Field, value: Any) -> Any:
    """The value as the form shows it."""
    if value is None:
        return ""
    if isinstance(value, str):  # as submitted
        return value
    if isinstance(value, bool):  # as YAML writes it
        return "true" if value else "false"
    if f.kind == "list":
        if f.pattern == "ufw_rule":
            return "\n".join(format_ufw_rule(r) for r in value or [])
        if f.pattern == "network_cards":
            return "\n".join(format_nic(n) for n in value or [] if isinstance(n, dict))
        return "\n".join(str(v) for v in value or [])
    return value
