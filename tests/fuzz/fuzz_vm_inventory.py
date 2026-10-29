#!/usr/bin/env python3
"""Fuzz target: the VMs page's run plans (webui/usas_web/vmplan.py), the run's inventory, --limit and --tags
(webui/usas_web/runner.py), and the VMs' names and host-key names in known_hosts (vm_alias, ALIAS_PATTERN and
host_name in plugins/module_utils/usas_vms.py, shared with the fleet updates; Keys.pins in
webui/usas_web/sshkeys.py).

Properties, for any cluster listing and any posted settings:
- vmplan.build either refuses with PlanProblem or returns a plan; nothing else is raised.
- A plan's inventory is YAML that loads back to the hosts planned: servers by their inventory names, every other
  VM as vm-<vmid> in usas_vms. Every value someone typed or a guest reported is written !unsafe, and no value
  holds a line break.
- A VM outside the inventory gets none of the Vault's secrets (each is ""), keeps passwordless sudo, connects
  to a usable IPv4 address in the address format, with its host keys pinned by its alias.
- The plan's --limit passes the runner's own check against the inventory's names and comes back unchanged; its
  tags are in the baseline's allowlist.
- A VM's alias always matches sshkeys.ALIAS, and reading any known_hosts text finds only aliases.
"""

import pathlib
import re
import sys
import tempfile

import atheris
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "webui"))

with atheris.instrument_imports():
    from usas_web import runner, sshkeys, vmplan
    from usas_web.config import from_env
    from usas_web.schema import Invalid

IPV4 = r"^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\Z"
FORMATS = vmplan.Formats(ipv4=IPV4, users=(r"^[a-z_][a-z0-9_-]{0,31}\Z",))
SETTINGS = vmplan.Settings(known_hosts="/data/ssh/known_hosts", ufw_rules=["443/tcp"])
SERVERS = [{"name": "web-00", "ansible_host": "10.0.0.10", "pve_vmid": 4000},
           {"name": "db-01", "ansible_host": "", "pve_vmid": 4001}]
SUPPORT = ("supported", "override", "unknown", "unsupported", "")
STATUS = ("running", "stopped", "paused", "")
# The values the web UI itself writes (no typed or reported text in them).
OWN = {"ansible_ssh_common_args", "ansible_control_path", "ufw_allow", *vmplan.VAULT_VARS}
TMP = pathlib.Path(tempfile.mkdtemp(prefix="fuzz-vm-"))
KEYS = sshkeys.Keys(from_env({"USAS_DATA_DIR": str(TMP), "USAS_PROJECT_DIR": str(TMP)}))


class Typed(str):
    pass


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!unsafe", lambda loader, node: Typed(loader.construct_scalar(node)))


def text(fdp, limit=40):
    return fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, limit))


def maybe_ip(fdp):
    choice = fdp.ConsumeIntInRange(0, 3)
    if choice == 0:
        return text(fdp, 20)
    return ".".join(str(fdp.ConsumeIntInRange(0, 300)) for _ in range(4))


def looks_typed(text):
    return yaml.SafeDumper(None).resolve(yaml.ScalarNode, text, (True, False)) != "tag:yaml.org,2002:str"


def check_strings(value, key=""):
    if isinstance(value, dict):
        for k, v in value.items():
            check_strings(v, str(k))
    elif isinstance(value, list):
        for v in value:
            check_strings(v, key)
    elif isinstance(value, str):
        assert "\n" not in value and "\r" not in value, (key, value)
        # Text that would load as a number, boolean or null is written as a plain quoted string instead of
        # !unsafe (store.py): it can't hold a template.
        if key not in OWN and value not in ("",) and not looks_typed(value):
            assert isinstance(value, Typed), (key, value)


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    vmids = [fdp.PickValueInList([101, 102, 4000, 4001, fdp.ConsumeIntInRange(100, 999_999_999)])
             for _ in range(fdp.ConsumeIntInRange(0, 4))]
    rows = {}
    for vmid in vmids:
        rows[vmid] = {
            "vmid": vmid, "name": text(fdp, 70), "node": text(fdp, 10),
            "type": fdp.PickValueInList(["qemu", "qemu", "lxc", ""]), "template": fdp.ConsumeBool(),
            "status": fdp.PickValueInList(list(STATUS)), "lock": fdp.PickValueInList(["", "", "backup", text(fdp, 5)]),
            "support": fdp.PickValueInList(list(SUPPORT)), "reason": text(fdp),
            "addresses": [maybe_ip(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))], "ip": maybe_ip(fdp),
            "ip_source": fdp.PickValueInList(["agent", "ipconfig0", "inventory", None]),
        }
    form = {}
    for vmid in vmids:
        for name in ("address", "port", "user", "ufw", "hostname", "override"):
            if fdp.ConsumeBool():
                form[f"vm-{vmid}-{name}"] = fdp.PickValueInList(["on", maybe_ip(fdp), "22", "ubuntu", text(fdp)])
    pins = {f"usas-pve-{vmid}": ["SHA256:x"] for vmid in vmids if fdp.ConsumeBool()}
    preset = fdp.PickValueInList([*vmplan.PRESETS, text(fdp, 8)])
    custom = [fdp.PickValueInList([*vmplan.TAGS, text(fdp, 8)]) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    try:
        tags = vmplan.preset_tags(preset, custom)
    except vmplan.PlanProblem:
        tags = []
    try:
        plan = vmplan.build(rows, vmids, form, servers=SERVERS, pins=pins,
                            aliases={vmid: sshkeys.vm_alias("pve", vmid) for vmid in vmids}, formats=FORMATS,
                            settings=SETTINGS, tags=tags, preset=preset if preset in vmplan.PRESETS else "full")
    except vmplan.PlanProblem as exc:
        assert exc.errors
        plan = None
    if plan is not None:
        loaded = yaml.load(plan.inventory, Loader=Loader)  # noqa: S506 (a SafeLoader subclass)
        children = loaded["all"]["children"]
        assert set(children) <= {"usas_vms", "servers"}
        planned = {t.name for t in plan.targets}
        found = {name for group in children.values() for name in group["hosts"]}
        assert found == planned, (found, planned)
        for target in plan.targets:
            if target.static:
                assert target.name in {"web-00", "db-01"} and target.name in children["servers"]["hosts"]
                continue
            assert target.name == f"vm-{target.vmid}"
            host = children["usas_vms"]["hosts"][target.name]
            for name in vmplan.VAULT_VARS:
                assert host[name] == "", name
            assert host["admin_sudo_nopasswd"] is True
            assert re.match(IPV4, host["ansible_host"]) and host["ansible_host"] not in ("10.0.0.10",)
            assert host["ansible_ssh_common_args"].endswith(f"HostKeyAlias={target.alias}")
            assert sshkeys.ALIAS.match(target.alias) and pins.get(target.alias)
            port = host.get("ansible_port")
            assert port is None or (isinstance(port, int) and 1 <= port <= 65535)
        check_strings(loaded)
        names = runner.inventory_names(plan.inventory)
        assert runner.parse_limit(plan.limit, names) == plan.limit
        assert runner.parse_tags("baseline", plan.tags) == plan.tags

    # Aliases, and finding them in any known_hosts text.
    cluster = text(fdp, 40)
    vmid = fdp.ConsumeIntInRange(0, 10**10)
    try:
        alias = sshkeys.vm_alias(cluster, vmid)
    except sshkeys.KeyProblem:
        assert not 100 <= vmid <= 999_999_999
    else:
        assert sshkeys.ALIAS.match(alias) and alias.endswith(f"-{vmid}")
        assert vmplan.v.ALIAS_PATTERN is sshkeys.ALIAS
    KEYS.settings.known_hosts.parent.mkdir(parents=True, exist_ok=True)
    KEYS.settings.known_hosts.write_text(text(fdp, 300))
    for name, found in KEYS.pins().items():
        assert sshkeys.ALIAS.match(name) and all(k.host == name for k in found)
    try:
        vmplan.parse_rules(text(fdp, 120))
        vmplan.parse_port(text(fdp, 8))
    except Invalid:
        pass


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
