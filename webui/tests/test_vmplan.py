"""Plans for VMs page runs: who may be selected, the per-host settings, the run's inventory and its secrets."""

import pytest
import yaml
from usas_web import vmplan as p
from usas_web.schema import Invalid
from usas_web.store import load_yaml

OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
FORMATS = p.Formats(ipv4=rf"^({OCTET}\.){{3}}{OCTET}\Z", users=(r"^[a-z_][a-z0-9_-]{0,31}\Z",))
SETTINGS = p.Settings(admin_user="sysadmin", ssh_port=22, github_user="octocat", ufw_rules=["443/tcp"],
                      known_hosts="/data/ssh/known_hosts")
SERVERS = [{"name": "web-00", "ansible_host": "10.0.0.10", "pve_vmid": 4000}]


def row(vmid, **kw):
    base = {"vmid": vmid, "name": f"vm{vmid}", "node": "pve-1", "type": "qemu", "status": "running",
            "template": False, "lock": "", "support": "supported", "reason": "Ubuntu 26.04 LTS.",
            "addresses": [f"10.0.1.{vmid % 200}"], "ip": f"10.0.1.{vmid % 200}", "ip_source": "agent"}
    base.update(kw)
    return base


def build(selected, form=None, rows=None, pins=None, tags=(), preset="full", settings=SETTINGS, servers=SERVERS):
    rows = rows or {vmid: row(vmid) for vmid in selected}
    aliases = {vmid: f"usas-pve-{vmid}" for vmid in selected}
    pins = {a: ["SHA256:x"] for a in aliases.values()} if pins is None else pins
    return p.build(rows, list(selected), form or {}, servers=servers, pins=pins, aliases=aliases, formats=FORMATS,
                   settings=settings, tags=list(tags), preset=preset)


def foreign_form(vmid, **kw):
    form = {f"vm-{vmid}-address": f"10.0.1.{vmid % 200}", f"vm-{vmid}-user": "ubuntu"}
    form.update({f"vm-{vmid}-{k}": val for k, val in kw.items()})
    return form


@pytest.mark.parametrize(("changes", "reason"), [
    ({"type": "lxc"}, "containers"), ({"template": True}, "templates"), ({"status": "stopped"}, "isn't running"),
    ({"lock": "backup"}, "locked"), ({"support": "unsupported", "reason": "Windows"}, "Windows"),
])
def test_what_cannot_be_selected(changes, reason):
    ok, why = p.selectable(row(101, **changes))
    assert not ok and reason in why
    assert p.selectable(row(101)) == (True, "")


def test_presets():
    assert p.preset_tags("keys", []) == ["ssh_keys"]
    assert p.preset_tags("full", ["firewall"]) == []
    assert p.preset_tags("custom", ["fail2ban", "firewall"]) == ["firewall", "fail2ban"]
    for preset, custom in (("custom", []), ("custom", ["firewall", "--become"]), ("rm", []), ("", [])):
        with pytest.raises(p.PlanProblem):
            p.preset_tags(preset, custom)


def test_a_server_in_the_inventory_keeps_its_own_settings():
    plan = build([4000], form={"vm-4000-address": "10.9.9.9", "vm-4000-user": "root"})
    target = plan.targets[0]
    assert (target.name, target.static, target.address, target.source, target.user) == (
        "web-00", True, "10.0.0.10", "inventory", "sysadmin")
    host = load_yaml(plan.inventory)["all"]["children"]["servers"]["hosts"]["web-00"]
    # Only the pinned host key and its own SSH master connection: address, user and sudo come from the inventory.
    assert set(host) == {"ansible_ssh_common_args", "ansible_control_path"}
    assert "HostKeyAlias=usas-pve-4000" in host["ansible_ssh_common_args"]
    assert plan.limit == "web-00"


def test_a_vm_outside_the_inventory_gets_no_secrets():
    plan = build([101], form=foreign_form(101, port="2222", ufw="8080/tcp, 53/udp from 10.0.0.0/24"))
    target = plan.targets[0]
    assert (target.name, target.static, target.source, target.port) == ("vm-101", False, "agent", 2222)
    data = load_yaml(plan.inventory)["all"]["children"]
    assert set(data) == {"usas_vms"}
    host = data["usas_vms"]["hosts"]["vm-101"]
    for name in p.VAULT_VARS:
        assert host[name] == ""
    assert "vault_" not in plan.inventory
    assert host["admin_sudo_nopasswd"] is True and host["manage_hostname"] is False
    assert host["ansible_user"] == host["admin_user"] == "ubuntu" and host["ansible_port"] == 2222
    assert host["webui_firewall_allow_host"] == [{"port": "8080", "proto": "tcp"},
                                            {"port": "53", "proto": "udp", "from": "10.0.0.0/24"}]
    assert host["firewall_allow"].startswith("{{") and host["usas_vmid"] == 101
    assert host["ansible_control_path"] == "%(directory)s/v101-%%C"
    # Typed values are !unsafe: Ansible never templates them.
    raw = yaml.safe_load(plan.inventory.replace("!unsafe", ""))
    assert raw["all"]["children"]["usas_vms"]["hosts"]["vm-101"]["ansible_host"] == "10.0.1.101"
    assert "!unsafe '10.0.1.101'" in plan.inventory and "!unsafe 'ubuntu'" in plan.inventory


def test_hostname_override_and_manual_address():
    rows = {101: row(101, support="override", reason="Ubuntu 22.04")}
    form = foreign_form(101, address="192.168.5.5", hostname="on", override="on")
    plan = build([101], form=form, rows=rows)
    host = load_yaml(plan.inventory)["all"]["children"]["usas_vms"]["hosts"]["vm-101"]
    assert host["manage_hostname"] is True and host["server_hostname"] == "vm101"
    assert host["baseline_allow_unsupported"] is True
    assert plan.targets[0].source == "manual"
    assert any("renamed to vm101" in w for w in plan.warnings)
    assert any("isn't a release of the OS catalog" in w for w in plan.warnings)


def test_errors_are_collected_per_field():
    rows = {101: row(101, support="unknown", name="bad_name"), 102: row(102), 103: row(103, status="stopped")}
    form = {**foreign_form(101, hostname="on"), "vm-101-port": "70000", "vm-101-user": "Root",
            "vm-101-ufw": "22 from everywhere", **foreign_form(102, address="127.0.0.1")}
    with pytest.raises(p.PlanProblem) as caught:
        build([101, 102, 103, 104], form=form, rows=rows, pins={})
    errors = caught.value.errors
    assert {"vm-101-override", "vm-101-port", "vm-101-user", "vm-101-ufw", "vm-101-hostname", "vm-101-hostkey",
            "vm-102-address", "vm-103-address", "vm-104-address"} <= set(errors)
    assert "isn't running" in errors["vm-103-address"] and "isn't on the cluster" in errors["vm-104-address"]


def test_addresses_must_differ_and_not_be_a_servers():
    form = {**foreign_form(101), **foreign_form(102, address="10.0.1.101")}
    with pytest.raises(p.PlanProblem) as caught:
        build([101, 102], form=form)
    assert "same address" in caught.value.errors["vm-102-address"]
    with pytest.raises(p.PlanProblem) as caught:
        build([101], form=foreign_form(101, address="10.0.0.10"))
    assert "web-00" in caught.value.errors["vm-101-address"]


def test_name_clash_and_limits():
    servers = [*SERVERS, {"name": "vm-101", "ansible_host": "10.0.0.11", "pve_vmid": 4001}]
    with pytest.raises(p.PlanProblem) as caught:
        build([101], form=foreign_form(101), servers=servers)
    assert "another VM ID" in caught.value.errors["vm-101-address"]
    with pytest.raises(p.PlanProblem, match="at least one"):
        build([])
    with pytest.raises(p.PlanProblem, match="at most 25"):
        build(list(range(100, 126)))


def test_the_ui_key_must_stay():
    settings = p.Settings(**{**SETTINGS.__dict__, "ui_key_authorized": False})
    with pytest.raises(p.PlanProblem, match="locking it"):
        build([101], form=foreign_form(101), settings=settings)
    # Without the keys in the run, nothing is removed.
    assert build([101], form=foreign_form(101), settings=settings, tags=["firewall"], preset="custom").targets


def test_warnings_follow_the_tags():
    full = build([101], form=foreign_form(101)).warnings
    assert any("default for incoming traffic is deny" in w and "443/tcp" in w for w in full)
    assert any("ssh_keys_exclusive" in w and "octocat" in w for w in full)
    assert "Every package is upgraded (pkg_upgrade: dist)." in full
    assert any("sudoers.d/90-ansible-<user>" in w and "ubuntu" in w for w in full)
    keys = build([101], form=foreign_form(101), tags=["ssh_keys"], preset="keys").warnings
    assert len(keys) == 1 and "ssh_keys_exclusive" in keys[0]
    moved = p.Settings(**{**SETTINGS.__dict__, "ssh_port": 2222})
    ssh = build([101], form=foreign_form(101), tags=["ssh", "fail2ban"], preset="ssh", settings=moved).warnings
    assert any("port 2222" in w for w in ssh) and any("isn't part of this run" in w for w in ssh)


def test_the_digest_names_the_plan():
    one = build([101], form=foreign_form(101))
    assert one.digest == build([101], form=foreign_form(101)).digest
    assert one.digest != build([101], form=foreign_form(101, port="22")).digest
    assert one.digest != build([101], form=foreign_form(101), tags=["ssh_keys"], preset="keys").digest
    assert one.digest != build([101], form=foreign_form(101), pins={"usas-pve-101": ["SHA256:other"]}).digest
    data = p.plan_json(build([101], form=foreign_form(101, ufw="443")))
    assert data["targets"][0]["ufw"] == ["443/tcp"] and data["preset_label"] == "Full baseline"
    assert one.summary()[0]["fingerprints"] == ["SHA256:x"]


@pytest.mark.parametrize("bad", ["0", "65536", "22a", "-1", " 2 2", "1e3"])
def test_ports(bad):
    with pytest.raises(Invalid):
        p.parse_port(bad)
    assert p.parse_port(" ") is None and p.parse_port("2222") == 2222


def test_too_many_rules():
    with pytest.raises(Invalid, match="At most"):
        p.parse_rules(",".join(["443"] * 21))


def updates(selected, form=None, rows=None, options=None, servers=SERVERS):
    rows = rows or {vmid: row(vmid) for vmid in selected}
    aliases = {vmid: f"usas-pve-{vmid}" for vmid in selected}
    pins = {a: ["SHA256:x"] for a in aliases.values()}
    return p.build(rows, list(selected), form or {}, servers=servers, pins=pins, aliases=aliases, formats=FORMATS,
                   settings=SETTINGS, tags=["ssh"], preset="ignored", playbook=p.UPDATES,
                   options=p.update_options(options or {}))


def test_update_options():
    assert p.update_options({}) == {"updates_scope": "all", "updates_reboot": "never", "updates_snapshot": "false",
                                    "updates_ignore_window": "false"}
    assert p.update_options({"updates-scope": "security", "updates-reboot": "if_required", "updates-snapshot": "on",
                             "updates-window": "on"}) == {"updates_scope": "security", "updates_reboot": "if_required",
                                                          "updates_snapshot": "true", "updates_ignore_window": "true"}
    for name, value in (("updates-scope", "dist"), ("updates-reboot", "always"), ("updates-scope", "all\n")):
        with pytest.raises(p.PlanProblem) as caught:
            p.update_options({name: value})
        assert name in caught.value.errors


def test_an_updates_plan():
    rows = {101: row(101, support="unknown", reason="The guest didn't say.", tags=["usas-group-db", "{{x}}"],
                     hastate="started"),
            4000: row(4000)}
    plan = updates([101, 4000], form=foreign_form(101, ufw="8080/tcp", hostname="on", override="on"), rows=rows,
                   options={"updates-reboot": "if_required"})
    # No tags, no override needed for an OS the guest didn't report (the playbook decides), no baseline settings.
    assert (plan.playbook, plan.preset, plan.tags) == ("updates", "updates", [])
    assert plan.options["updates_reboot"] == "if_required"
    children = load_yaml(plan.inventory)["all"]["children"]
    foreign = children["usas_vms"]["hosts"]["vm-101"]
    assert set(foreign) == {"ansible_host", "ansible_user", *p.VAULT_VARS, "ansible_ssh_common_args",
                            "ansible_control_path", "usas_pve_vmid", "usas_pve_node", "usas_pve_name",
                            "usas_pve_alias", "usas_pve_tags", "usas_pve_hastate"}
    assert all(foreign[name] == "" for name in p.VAULT_VARS)
    assert foreign["usas_pve_tags"] == ["usas-group-db", "{{x}}"] and "!unsafe '{{x}}'" in plan.inventory
    server = children["servers"]["hosts"]["web-00"]
    assert "ansible_host" not in server and server["usas_pve_vmid"] == 4000 and server["usas_pve_node"] == "pve-1"
    assert any("reboot if an update asks" in w and "vm-101, web-00" in w for w in plan.warnings)
    assert any("passwordless sudo" in w for w in plan.warnings)
    data = p.plan_json(plan)
    assert data["preset_label"] == "Updates" and data["playbook"] == "updates"


def test_an_updates_plan_has_its_own_digest():
    form = foreign_form(101)
    plan = updates([101], form=form)
    assert plan.digest == updates([101], form=form).digest
    assert plan.digest != updates([101], form=form, options={"updates-scope": "security"}).digest
    assert plan.digest != build([101], form=form).digest
    assert p.digest([], plan.inventory, {"usas-pve-101": ["SHA256:x"]}, p.UPDATES, plan.options) == plan.digest
    warnings = updates([101], form=form, options={"updates-snapshot": "on", "updates-window": "on",
                                                  "updates-scope": "security"}).warnings
    assert any("Only security updates" in w for w in warnings) and any("VM.Snapshot" in w for w in warnings)
    assert any("updates_window" in w for w in warnings)
    assert any("Nothing reboots" in w for w in updates([101], form=form).warnings)
