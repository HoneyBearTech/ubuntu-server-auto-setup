"""The form fields come from the roles, and validate like the roles' assert tasks."""

import re
import shutil

import pytest
import yaml
from usas_web import schema as s
from usas_web.schema import Invalid, Schema, Spec, parse_ufw_rule, parse_value

from .conftest import REPO


@pytest.fixture(scope="module")
def sch():
    return Schema(REPO)


def test_every_field_names_a_role_variable(sch):
    for fields in sch.sections.values():
        for f in fields:
            if f.spec.role:
                assert f.name in sch.roles.defaults[f.spec.role]
            else:
                assert f.vault, f.name


def test_every_pattern_in_the_roles_is_used(sch):
    """A new allowlist pattern in roles/*/vars/main.yml must reach the UI."""
    assert sch.roles.all_var_names() - s.NOT_FORM_FORMATS <= sch.pattern_vars_used()
    assert sch.roles.all_var_names() >= s.NOT_FORM_FORMATS


def test_defaults_and_patterns_come_from_the_role_files(sch):
    common = yaml.safe_load((REPO / "roles/common/defaults/main.yml").read_text())
    patterns = yaml.safe_load((REPO / "roles/ssh_hardening/vars/main.yml").read_text())
    assert sch.by_name["admin_user"].default == common["admin_user"]
    assert sch.by_name["ssh_allow_users"].pattern == patterns["ssh_allow_users_pattern"]
    assert sch.by_name["ssh_permit_root_login"].choices == tuple(patterns["ssh_permit_root_login_values"])
    assert len(sch.by_name["admin_user"].patterns) == 2


def test_help_text_comes_from_the_defaults_comments(sch):
    assert "Remove" not in sch.by_name["github_user"].help
    assert sch.by_name["pve_storage"].help == "Storage for the VM disks."
    assert "repeat offence" in sch.by_name["fail2ban_bantime_increment"].help


def test_inline_ranges_match_the_role_asserts():
    tasks = (REPO / "roles/ssh_hardening/tasks/main.yml").read_text()
    assert f"{s.PORT_RANGE[0]} <= (ssh_port | int) <= {s.PORT_RANGE[1]}" in tasks
    tasks = (REPO / "roles/proxmox_vm/tasks/main.yml").read_text()
    assert f"{s.PREFIX_RANGE[0]} <= (net_prefix | int) <= {s.PREFIX_RANGE[1]}" in tasks
    assert "pve_vmid | string is match('^[0-9]+\\Z')" in tasks
    assert "fail2ban_maxretry | string is match('^[0-9]+\\Z')" in (REPO / "roles/fail2ban/tasks/main.yml").read_text()


def test_a_missing_variable_or_pattern_is_an_error(tmp_path, sch):
    with pytest.raises(s.SchemaError, match="no variable"):
        sch._resolve(Spec("no_such_var", "x", "text", "common"))
    with pytest.raises(s.SchemaError, match="defines"):
        sch._resolve(Spec("admin_user", "x", "text", "common", pattern_var="no_such_pattern"))
    # A renamed default in a role breaks the UI at startup, not silently.
    project = tmp_path / "project"
    shutil.copytree(REPO / "roles", project / "roles")
    defaults = project / "roles/ssh_hardening/defaults/main.yml"
    defaults.write_text(defaults.read_text().replace("ssh_allow_users:", "ssh_allowed_users:"))
    with pytest.raises(s.SchemaError, match="ssh_allow_users"):
        Schema(project)


def test_comments_parser():
    text = "---\n# --- Section ---\n# First line\n# second line.\na: 1   # trailing\n\nb: 2\n#   - { example }\nc: 3\n"
    assert s._comments(text) == {"a": "First line second line. trailing", "b": "", "c": ""}


@pytest.mark.parametrize("name,raw,expected", [
    ("ssh_port", "2222", 2222),
    ("ssh_port", "", 22),
    ("pve_vlan", "", None),
    ("ufw_ssh_limit", "on", True),
    ("ufw_ssh_limit", None, False),
    ("net_dns", "10.0.0.1, 1.1.1.1\n9.9.9.9", ["10.0.0.1", "1.1.1.1", "9.9.9.9"]),
    ("net_search", "example.lan\nsub.example.com", ["example.lan", "sub.example.com"]),
    ("fail2ban_bantime", "1h30m", "1h30m"),
    ("fail2ban_sshd_mode", "aggressive", "aggressive"),
    ("ubuntu_point_release", "26.04.2", "26.04.2"),
    ("ssh_allow_users", "sysadmin, deploy@10.0.0.*", ["sysadmin", "deploy@10.0.0.*"]),
    ("admin_password", " spaces kept ", " spaces kept "),
    ("unattended_upgrades_mail", "", ""),
    ("unattended_upgrades_mail", "ops+updates@mail.example.com", "ops+updates@mail.example.com"),
    ("fail2ban_webhook_url", "https://hooks.slack.com/services/T0/B0/x-Y_z", "https://hooks.slack.com/services/T0/B0/x-Y_z"),
    ("fail2ban_webhook_url", "https://gotify.example.com:8443/message?token=A.b-c", "https://gotify.example.com:8443/message?token=A.b-c"),
    ("mail_relay_password", 'p "w" # x', 'p "w" # x'),
    ("hardening_sysctl_enabled", "on", True),
    ("ufw_allow", "443/tcp\n6000:6007/udp from 10.0.0.0/24", [
        {"port": "443", "proto": "tcp"}, {"port": "6000:6007", "proto": "udp", "from": "10.0.0.0/24"}]),
])
def test_valid_values(sch, name, raw, expected):
    assert parse_value(sch.by_name[name], raw) == expected


@pytest.mark.parametrize("name,raw,message", [
    ("ssh_port", "0", "between"),
    ("ssh_port", "70000", "between"),
    ("ssh_port", "22; rm", "whole number"),
    ("pve_api_host", "", "required"),
    ("pve_api_host", "-oProxyCommand=x", "format"),
    ("admin_user", "Root", "format"),
    ("admin_user", "sys\nadmin", "format"),
    ("net_gateway", "10.0.0.256", "format"),
    ("net_search", "bad_label.example", "format"),
    ("ssh_allow_users", "sysadmin PermitRootLogin", "format"),
    ("fail2ban_ignoreip", "10.0.0.0/8 enabled", "format"),
    ("fail2ban_bantime", "1 hour", "format"),
    ("fail2ban_sshd_mode", "paranoid", "one of"),
    ("ubuntu_point_release", "26.04.1/../x", "format"),
    ("ssh_permit_root_login", "maybe", "one of"),
    ("admin_password", "two\nlines", "control characters"),
    ("unattended_upgrades_mail", "root", "format"),
    ("fail2ban_destemail", "a@example.com b@example.com", "format"),
    ("fail2ban_webhook_url", "ftp://example.com/x", "format"),
    ("fail2ban_webhook_url", "https://example.com/<ip>", "format"),
    ("mail_relay_host", "-oProxyCommand=x", "format"),
    ("mail_relay_port", "0", "between"),
    ("mail_relay_tls", "ssl", "one of"),
    ("timezone", "{{ lookup('pipe', 'id') }}", "format"),
    ("admin_extra_ssh_keys", "not a key", "format"),
    ("ufw_allow", "443/sctp", "port must be"),
    ("ufw_allow", "443/tcp to 10.0.0.1", "write a port"),
    ("ufw_allow", "443 from 10.0.0.0/33", "from"),
    ("ufw_allow", "7000:6000", "lower one first"),
])
def test_invalid_values(sch, name, raw, message):
    with pytest.raises(Invalid, match=message):
        parse_value(sch.by_name[name], raw)


def test_ufw_rule_round_trip():
    for text in ("443/tcp", "53/udp", "5432/tcp from 10.0.0.0/24", "6000:6007/tcp"):
        assert s.format_ufw_rule(parse_ufw_rule(text)) == text
    assert s.format_ufw_rule({"port": "22", "from": "any"}) == "22/tcp"


def test_display_values(sch):
    assert s.display_value(sch.by_name["net_dns"], ["1.1.1.1", "9.9.9.9"]) == "1.1.1.1\n9.9.9.9"
    assert s.display_value(sch.by_name["ufw_allow"], [{"port": "80", "proto": "tcp"}]) == "80/tcp"
    assert s.display_value(sch.by_name["ssh_port"], None) == ""
    assert s.display_value(sch.by_name["pve_validate_certs"], True) == "true"


def test_server_columns(sch):
    names = [c.name for c in sch.server_columns]
    assert names[:3] == ["name", "ansible_host", "pve_vmid"]
    vmid = next(c for c in sch.server_columns if c.name == "pve_vmid")
    with pytest.raises(Invalid):
        parse_value(vmid, "")
    assert re.match(next(c for c in sch.server_columns if c.name == "name").pattern, "web-01")
