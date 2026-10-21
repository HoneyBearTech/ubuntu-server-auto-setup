"""The form fields, built from the roles themselves.

Every field names the role variable it sets. Its default and help text are read from
roles/<role>/defaults/main.yml, and its allowlist pattern or list of allowed values from
roles/<role>/vars/main.yml, the same patterns the roles' assert tasks check. A field that names a variable or
pattern the roles don't have is an error at startup (and in the tests), so the UI can't drift from the
playbooks. Fields marked ``ui_pattern`` have a format the roles don't check themselves (the API host, the
token name); the UI checks them so that only plain values reach the inventory.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROLES = ("ubuntu_release", "installer_iso", "proxmox_vm", "common", "firewall", "ssh_hardening", "fail2ban")

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
BRIDGE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}\Z"
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
    choices_var: str | None = None  # vars/main.yml list of allowed values
    ui_pattern: str | None = None  # format checked by the UI only
    ui_choices: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    required: bool = False
    vault: str | None = None  # stored in the Vault under this name instead of the inventory
    help: str = ""  # used when the defaults file has no comment for the variable
    advanced: bool = False
    default: Any = None  # for variables no role defines (Vault values)


@dataclass(frozen=True)
class Field:
    spec: Spec
    default: Any
    help: str
    pattern: str | None
    choices: tuple[str, ...] | None
    patterns: tuple[str, ...] = ()  # every pattern the value must match (roles that read it may differ)

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
          help="The Proxmox node the VMs are created on, as Proxmox names it."),
        S("pve_storage", "VM disk storage", "text", "proxmox_vm", ui_pattern=STORAGE_ID),
        S("pve_efidisk_format", "EFI disk format", "choice", "proxmox_vm", ui_choices=("raw", "qcow2")),
        S("pve_bridge", "Network bridge", "text", "proxmox_vm", ui_pattern=BRIDGE),
        S("pve_vlan", "VLAN tag", "int", "proxmox_vm", minimum=1, maximum=4094),
        S("pve_iso_storage", "ISO storage", "text", "ubuntu_release", pattern_var="pve_storage_id_pattern",
          advanced=True, help="A Proxmox storage that allows ISO images (Content: ISO image), such as local or an NFS "
          "share for the whole cluster. Its folder on the node is found automatically."),
        S("pve_api_timeout", "API request timeout (seconds)", "int", "proxmox_vm", minimum=5, maximum=600,
          advanced=True, help="How long one Proxmox API request may take. Allocating disks can keep the API busy."),
        # The ISO file name is built from it, and installer_iso checks that name against this pattern.
        S("ubuntu_point_release", "Ubuntu point release", "text", "ubuntu_release",
          pattern_var="installer_iso_name_pattern", advanced=True,
          help="The installer ISO's version, such as 26.04.1. Change it when Ubuntu replaces the point release."),
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
        S("net_gateway", "Gateway", "text", "proxmox_vm", pattern_var="provision_ipv4_pattern", required=True),
        S("net_prefix", "Network prefix length", "int", "proxmox_vm", minimum=PREFIX_RANGE[0],
          maximum=PREFIX_RANGE[1]),
        S("net_dns", "DNS servers", "list", "proxmox_vm", pattern_var="provision_ipv4_pattern"),
        S("net_search", "Search domains", "list", "proxmox_vm", pattern_var="provision_hostname_pattern",
          help="Each part of a domain (between dots) must be a valid host name."),
        S("autoinstall_storage_layout", "Disk layout", "choice", "proxmox_vm", ui_choices=("lvm", "direct", "zfs")),
        S("vm_secure_boot", "Secure Boot keys", "bool", "proxmox_vm", advanced=True),
    ),
    "hardening": (
        S("ssh_port", "SSH port", "int", "ssh_hardening", minimum=PORT_RANGE[0], maximum=PORT_RANGE[1],
          help="Anything other than 22 switches sshd from ssh.socket to ssh.service; the firewall rule moves too."),
        S("ssh_allow_users", "Allowed SSH users", "list", "ssh_hardening", pattern_var="ssh_allow_users_pattern",
          help="Empty allows every user. If set, it must include the admin user."),
        S("ssh_permit_root_login", "Root login over SSH", "choice", "ssh_hardening",
          choices_var="ssh_permit_root_login_values"),
        S("admin_sudo_nopasswd", "Passwordless sudo for the admin user", "bool", "common",
          help="Convenient for unattended runs, but anyone with an authorized key has root."),
        S("ufw_ssh_limit", "Rate-limit SSH in the firewall", "bool", "firewall"),
        S("ufw_ssh_sources", "SSH allowed from", "list", "firewall", ui_pattern=UFW_FROM,
          help='"any", or addresses and networks (CIDR), one per line.'),
        S("ufw_allow", "Extra firewall rules (all servers)", "list", "firewall", ui_pattern="ufw_rule",
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
    ),
}

# Columns of the servers table. "name" is the inventory host name; "ufw_ports" are extra rules for that host.
SERVER_COLUMNS: tuple[Spec, ...] = (
    S("name", "Name", "text", pattern_var=("provision_hostname_pattern", "common_hostname_pattern"), required=True,
      default=""),
    S("ansible_host", "IP address", "text", pattern_var="provision_ipv4_pattern", required=True, default=""),
    S("pve_vmid", "VM ID", "int", "proxmox_vm", minimum=100, maximum=999999999, required=True),
    S("vm_cores", "Cores", "int", "proxmox_vm", minimum=1, maximum=512),
    S("vm_memory_mb", "Memory (MB)", "int", "proxmox_vm", minimum=512, maximum=16777216),
    S("vm_disk_gb", "Disk (GB)", "int", "proxmox_vm", minimum=8, maximum=131072),
    S("ufw_ports", "Extra firewall rules", "list", ui_pattern="ufw_rule", default=[],
      help="Comma-separated, for example 80/tcp, 443/tcp, 5432/tcp from 10.0.0.0/24."),
)


class Schema:
    def __init__(self, project_dir: Path):
        self.roles = load_roles(project_dir)
        self.sections = {name: [self._resolve(spec) for spec in specs] for name, specs in SECTIONS.items()}
        self.server_columns = [self._resolve(spec) for spec in SERVER_COLUMNS]
        self.by_name = {f.name: f for fields in self.sections.values() for f in fields}

    def _resolve(self, spec: Spec) -> Field:
        if spec.role:
            default = self.roles.default_for(spec.name, spec.role)
            comment = _sentence(self.roles.comments.get(spec.role, {}).get(spec.name, ""))
            help_text = " ".join(part for part in (spec.help, comment) if part)
        else:
            default, help_text = spec.default, spec.help
        names = (spec.pattern_var,) if isinstance(spec.pattern_var, str) else (spec.pattern_var or ())
        patterns = tuple(self.roles.var(name) for name in names) or ((spec.ui_pattern,) if spec.ui_pattern else ())
        pattern = patterns[0] if patterns else None
        choices = tuple(str(c) for c in self.roles.var(spec.choices_var)) if spec.choices_var else spec.ui_choices
        for p in patterns:
            if p != "ufw_rule":
                re.compile(p)
        return Field(spec=spec, default=default, help=help_text, pattern=pattern, choices=choices, patterns=patterns)

    def pattern_vars_used(self) -> set[str]:
        specs = [s for specs in SECTIONS.values() for s in specs] + list(SERVER_COLUMNS)
        used = set()
        for s in specs:
            used.update((s.pattern_var,) if isinstance(s.pattern_var, str) else (s.pattern_var or ()))
            if s.choices_var:
                used.add(s.choices_var)
        return used


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?)" else text + "."


# --- Parsing and validation of submitted values ----------------------------------------------------------


class Invalid(ValueError):
    pass


def _check(patterns: tuple[str, ...], value: str, what: str) -> None:
    for pattern in patterns:
        if pattern == "ufw_rule":
            parse_ufw_rule(value)
        elif not re.match(pattern, value):
            raise Invalid(f"{what} isn't in the accepted format.")


def parse_ufw_rule(text: str) -> dict[str, str]:
    """'443', '443/tcp', '6000:6007/udp', '5432/tcp from 10.0.0.0/24' -> a firewall role ufw_allow entry."""
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
        items = split_list(text, commas=f.name not in ("admin_extra_ssh_keys", "ufw_allow"))
        for item in items:
            if f.name == "net_search":
                for label in item.split("."):
                    _check(f.patterns, label, f"'{item}'")
            else:
                _check(f.patterns, item, f"'{item}'")
        if f.pattern == "ufw_rule":
            return [parse_ufw_rule(item) for item in items]
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
        return "\n".join(str(v) for v in value or [])
    return value
