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
    ("firewall_ssh_limit", "on", True),
    ("firewall_ssh_limit", None, False),
    ("net_dns", "10.0.0.1, 1.1.1.1\n9.9.9.9", ["10.0.0.1", "1.1.1.1", "9.9.9.9"]),
    ("net_search", "example.lan\nsub.example.com", ["example.lan", "sub.example.com"]),
    ("time_sync_servers", "ntp1.example.lan, 192.0.2.123\n2001:db8::123",
     ["ntp1.example.lan", "192.0.2.123", "2001:db8::123"]),
    ("time_sync_servers", "", []),
    ("time_sync_pools", "pool.ntp.org", ["pool.ntp.org"]),
    ("fail2ban_bantime", "1h30m", "1h30m"),
    ("fail2ban_sshd_mode", "aggressive", "aggressive"),
    ("vm_os", "ubuntu-24.04", "ubuntu-24.04"),
    ("vm_os", "", "ubuntu-26.04"),
    ("ssh_allow_users", "sysadmin, deploy@10.0.0.*", ["sysadmin", "deploy@10.0.0.*"]),
    ("admin_password", " spaces kept ", " spaces kept "),
    ("unattended_upgrades_mail", "", ""),
    ("unattended_upgrades_mail", "ops+updates@mail.example.com", "ops+updates@mail.example.com"),
    ("fail2ban_webhook_url", "https://hooks.slack.com/services/T0/B0/x-Y_z", "https://hooks.slack.com/services/T0/B0/x-Y_z"),
    ("fail2ban_webhook_url", "https://gotify.example.com:8443/message?token=A.b-c", "https://gotify.example.com:8443/message?token=A.b-c"),
    ("mail_relay_password", 'p "w" # x', 'p "w" # x'),
    ("hardening_sysctl_enabled", "on", True),
    ("firewall_allow", "443/tcp\n6000:6007/udp from 10.0.0.0/24", [
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
    ("time_sync_servers", "ntp.example.lan iburst", "format"),
    ("time_sync_pools", "-pool.example.lan", "format"),
    ("ssh_allow_users", "sysadmin PermitRootLogin", "format"),
    ("fail2ban_ignoreip", "10.0.0.0/8 enabled", "format"),
    ("fail2ban_bantime", "1 hour", "format"),
    ("fail2ban_sshd_mode", "paranoid", "one of"),
    ("vm_os", "debian-13", "must be one of"),
    ("vm_os", "ubuntu-26.04; x", "must be one of"),
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
    ("firewall_allow", "443/sctp", "port must be"),
    ("firewall_allow", "443/tcp to 10.0.0.1", "write a port"),
    ("firewall_allow", "443 from 10.0.0.0/33", "from"),
    ("firewall_allow", "7000:6000", "lower one first"),
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
    assert s.display_value(sch.by_name["firewall_allow"], [{"port": "80", "proto": "tcp"}]) == "80/tcp"
    assert s.display_value(sch.by_name["ssh_port"], None) == ""
    assert s.display_value(sch.by_name["pve_validate_certs"], True) == "true"


def test_server_columns(sch):
    names = [c.name for c in sch.server_columns]
    assert names[:5] == ["name", "ansible_host", "net_mode", "net_mac", "pve_vmid"]
    assert next(c for c in sch.server_columns if c.name == "net_mode").choices == ("static", "dhcp")
    vmid = next(c for c in sch.server_columns if c.name == "pve_vmid")
    with pytest.raises(Invalid):
        parse_value(vmid, "")
    assert re.match(next(c for c in sch.server_columns if c.name == "name").pattern, "web-01")


def nic_field(sch):
    return next(c for c in sch.server_columns if c.name == "vm_extra_nics")


@pytest.mark.parametrize(("text", "nic"), [
    ("vmbr1 dhcp", {"mode": "dhcp", "bridge": "vmbr1"}),
    ("vmbr2 none", {"mode": "none", "bridge": "vmbr2"}),
    ("vmbr1 vlan 20 10.20.0.5/24", {"mode": "static", "bridge": "vmbr1", "vlan": 20, "address": "10.20.0.5",
                                    "prefix": 24}),
    ("vmbr1 10.20.0.5/24 via 10.20.0.1 mtu 9000 model virtio mac bc:24:11:00:00:01",
     {"mode": "static", "bridge": "vmbr1", "address": "10.20.0.5", "prefix": 24, "gateway": "10.20.0.1", "mtu": 9000,
      "model": "virtio", "mac": "bc:24:11:00:00:01"}),
    ("vmbr1.30 model e1000 dhcp", {"mode": "dhcp", "bridge": "vmbr1.30", "model": "e1000"}),
])
def test_network_cards_are_parsed_with_the_role_formats(sch, text, nic):
    assert s.parse_nic(text, nic_field(sch).formats) == nic
    again = s.format_nic(nic)
    assert s.parse_nic(again, nic_field(sch).formats) == nic
    assert set(nic) <= set(sch.roles.var("provision_nic_keys"))


@pytest.mark.parametrize(("text", "says"), [
    ("", "start with the bridge"),
    ("vmbr1,tag=5 dhcp", "start with the bridge"),
    ("vmbr1", "add dhcp, none or an address"),
    ("vmbr1 static", "address/prefix"),
    ("vmbr1 dhcp none", "once"),
    ("vmbr1 dhcp vlan", "isn't understood"),
    ("vmbr1 dhcp vlan 1 vlan 2", "isn't understood"),
    ("vmbr1 dhcp tag 5", "isn't understood"),
    ("vmbr1 dhcp vlan 4095", "VLAN tag 1-4094"),
    ("vmbr1 dhcp vlan 20,mtu=1", "VLAN tag 1-4094"),
    ("vmbr1 10.0.0.300/24", "IPv4 address and a prefix"),
    ("vmbr1 10.0.0.3/33", "IPv4 address and a prefix"),
    ("vmbr1 10.0.0.3/24 via 10.0.0", "gateway's IPv4"),
    ("vmbr1 dhcp via 10.0.0.1", "needs a static address"),
    ("vmbr1 dhcp mtu 100", "576-9216"),
    ("vmbr1 dhcp mtu 9000 model e1000", "virtio cards only"),
    ("vmbr1 dhcp model ne2k", "model is one of"),
    ("vmbr1 dhcp mac 01:00:5e:00:00:01", "unicast MAC"),
])
def test_network_cards_the_role_would_refuse_are_refused(sch, text, says):
    with pytest.raises(Invalid) as exc:
        s.parse_nic(text, nic_field(sch).formats)
    assert says in exc.value.message
    if "," not in text and text:  # the form splits the cards at commas, so a comma can't reach a card
        with pytest.raises(Invalid):
            parse_value(nic_field(sch), text)


def test_network_cards_display(sch):
    nics = [{"mode": "dhcp", "bridge": "vmbr1", "vlan": 30},
            {"mode": "static", "bridge": "vmbr2", "address": "10.0.0.5", "prefix": 24}, "not a card"]
    assert s.display_value(nic_field(sch), nics) == "vmbr1 vlan 30 dhcp\nvmbr2 10.0.0.5/24"
    assert parse_value(nic_field(sch), "vmbr1 dhcp, vmbr2 none") == [{"mode": "dhcp", "bridge": "vmbr1"},
                                                                       {"mode": "none", "bridge": "vmbr2"}]
    assert set(sch.roles.var("provision_nic_modes")) == {"static", "dhcp", "none"}
