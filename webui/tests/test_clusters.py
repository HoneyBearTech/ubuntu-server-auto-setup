"""Kubernetes clusters in the web UI (clusters.py, the Defaults, Servers, Run and VMs pages): the k8s_<cluster>
groups round-trip through the inventory writer, the join tokens are write-only, and kubernetes.yml runs only
after a passing check."""

import json
import re
import time

import pytest
from usas_web import clusters as k
from usas_web import schema as s
from usas_web.store import load_yaml

from .conftest import REPO, TOKEN_SECRET, configure, post
from .test_app import fake_pve, finished, get, pinned

SERVER_TOKEN = "a1" * 32
AGENT_TOKEN = "b2" * 32
CLUSTER = {"cluster_count": "1", "clusters-0-name": "lab", "clusters-0-k8s_version": "v1.36.5+k3s1",
           "clusters-0-k8s_api_vip": "10.0.0.99", "clusters-0-k8s_lb_pool": "10.0.0.90-10.0.0.95",
           "clusters-0-k8s_api_sources": "10.0.0.5", "clusters-0-k8s_disable": ["servicelb", "traefik"],
           "clusters-0-k8s_flannel_backend": "vxlan", "clusters-0-k8s_server_taint": "on",
           "clusters-0-k8s_secrets_encryption": "on", "clusters-0-k8s_ready_timeout": "300"}


def saved(ui, **extra):
    r = post(ui, "/clusters", {**CLUSTER, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def members(ui, roles=("server", "agent")):
    """web-00 and web-01 in cluster lab, with the given roles."""
    rows = {"server_count": str(len(roles))}
    for i, role in enumerate(roles):
        rows.update({f"servers-{i}-name": f"web-{i:02d}", f"servers-{i}-ansible_host": f"10.0.0.{10 + i}",
                     f"servers-{i}-pve_vmid": str(4000 + i), f"servers-{i}-k8s_cluster": "lab",
                     f"servers-{i}-k8s_role": role})
    r = post(ui, "/servers", rows)
    assert r.status_code == 200, r.text


def inventory(ui):
    return load_yaml(ui.app_state.settings.inventory_file.read_text())


def test_the_cluster_name_is_what_the_roles_group_patterns_take():
    roles = s.load_roles(REPO)
    body = s.CLUSTER_NAME.removeprefix("^").removesuffix("\\Z")
    for role, name in (("k3s", "k3s_group_pattern"), ("kubernetes_node", "k8s_node_group_pattern"),
                       ("proxmox_vm", "provision_k8s_group_pattern")):
        assert roles.vars[role][name] == f"^k8s_({body})_(servers|agents)\\Z", name
    assert roles.defaults["k3s"]["k8s_flannel_backend"] == roles.defaults["kubernetes_node"]["k8s_flannel_backend"]


def test_the_cluster_fields_come_from_the_roles():
    sch = s.Schema(REPO)
    fields = {f.name: f for f in sch.cluster_fields}
    assert fields["k8s_version"].choices == ("v1.36.5+k3s1", "v1.37.1+k3s1")
    assert fields["k8s_disable"].choices == tuple(sch.roles.var("k3s_disable_choices"))
    assert fields["k8s_lb_pool"].pattern == sch.roles.var("k3s_pool_pattern")
    assert fields["k8s_api_sources"].pattern == sch.roles.var("k8s_node_source_pattern")
    assert fields["k8s_disable"].default == ["servicelb"] and fields["k8s_server_taint"].default is True
    assert "kube-vip" in fields["k8s_api_vip"].help
    assert s.token_vault_name(sch.cluster_tokens[1], "home-lab") == "vault_k8s_agent_token_home_lab"
    with pytest.raises(s.Invalid, match="takes only"):
        s.parse_value(fields["k8s_disable"], "servicelb\ncoredns")
    with pytest.raises(s.Invalid, match="one of"):
        s.parse_value(fields["k8s_version"], "v1.20.0+k3s1")
    with pytest.raises(s.Invalid, match="format"):
        s.parse_value(sch.cluster_tokens[0], "short")


def test_clusters_save_and_round_trip(ui):
    configure(ui)
    data = saved(ui, **{"clusters-0-k8s_token": SERVER_TOKEN, "clusters-0-k8s_agent_token": AGENT_TOKEN})
    assert data["clusters"][0]["tokens"] == {"k8s_token": True, "k8s_agent_token": True}
    members(ui)
    group = inventory(ui)["all"]["children"]["servers"]["children"]["k8s_lab"]
    assert group["vars"] == {"k8s_version": "v1.36.5+k3s1", "k8s_api_vip": "10.0.0.99",
                             "k8s_lb_pool": "10.0.0.90-10.0.0.95", "k8s_api_sources": ["10.0.0.5"],
                             "k8s_disable": ["servicelb", "traefik"]}
    assert group["children"]["k8s_lab_servers"]["hosts"] == {"web-00": {}}
    assert group["children"]["k8s_lab_agents"]["hosts"] == {"web-01": {}}
    text = ui.app_state.settings.inventory_file.read_text()
    assert "k8s_api_vip: !unsafe '10.0.0.99'" in text and SERVER_TOKEN not in text
    secrets = ui.app_state.store.read_secrets()
    assert secrets["vault_k8s_token_lab"] == SERVER_TOKEN and secrets["vault_k8s_agent_token_lab"] == AGENT_TOKEN
    # The tokens are never sent back; the page shows the members and the defaults it left out.
    page = get(ui, "/api/clusters").json()
    assert SERVER_TOKEN not in json.dumps(page) and AGENT_TOKEN not in json.dumps(page)
    cluster = page["clusters"][0]
    assert (cluster["servers"], cluster["agents"]) == (["web-00"], ["web-01"])
    assert cluster["values"]["k8s_server_taint"] is True and cluster["values"]["k8s_disable"] == "servicelb\ntraefik"
    assert cluster["token_vars"]["k8s_agent_token"] == "vault_k8s_agent_token_lab"
    rows = get(ui, "/api/servers").json()["rows"]
    assert [(r["k8s_cluster"], r["k8s_role"]) for r in rows] == [("lab", "server"), ("lab", "agent")]
    assert get(ui, "/api/servers").json()["clusters"] == ["lab"]
    # Saving the other pages keeps the cluster and the tokens.
    before = ui.app_state.settings.inventory_file.read_text()
    post(ui, "/hardening", {"ssh_port": "22", "firewall_ssh_sources": "any"})
    assert ui.app_state.settings.inventory_file.read_text().count("k8s_lab") == before.count("k8s_lab")
    assert ui.app_state.store.read_secrets()["vault_k8s_token_lab"] == SERVER_TOKEN
    # The run page's limit may name the cluster's groups.
    assert {"k8s_lab", "k8s_lab_servers", "k8s_lab_agents"} <= ui.app_state.store.host_names()


def test_what_was_set_by_hand_is_kept(ui):
    configure(ui, servers=2)
    path = ui.app_state.settings.inventory_file
    data = load_yaml(path.read_text())
    data["all"]["children"]["servers"]["children"] = {"k8s_lab": {
        "vars": {"k8s_version": "v1.36.5+k3s1", "k8s_api_vip": "{{ lab_vip }}", "updates_k8s_drain": True,
                 "k8s_tls_san": ["api.lab.example"], "custom_note": "kept"},
        "children": {"k8s_lab_servers": {"hosts": {"web-00": {"k8s_node_ip": "10.0.0.10"}, "cp-x": {}}},
                     "k8s_lab_agents": {"vars": {"k8s_longhorn_prereqs": True}, "hosts": {"web-01": None}},
                     "k8s_lab_extra": {"hosts": {"web-01": {}}}}}}
    data["all"]["children"]["k8s_edge"] = {"vars": {"k8s_version": "v1.37.1+k3s1"},
                                           "children": {"k8s_edge_servers": {"hosts": {"edge-1": {}}}}}
    from usas_web.store import dump_yaml
    path.write_text(dump_yaml(data))
    page = get(ui, "/api/clusters").json()
    lab, edge = page["clusters"]
    assert lab["kept"] == ["custom_note", "k8s_api_vip", "updates_k8s_drain"]
    assert lab["values"]["k8s_tls_san"] == "api.lab.example" and lab["other_members"] == ["cp-x"]
    assert (lab["servers"], lab["agents"], edge["other_members"]) == (["web-00"], ["web-01"], ["edge-1"])
    # Save the clusters page as it is shown, then the servers: nothing set by hand is lost.
    form = {"cluster_count": "2"}
    for i, c in enumerate(page["clusters"]):
        form[f"clusters-{i}-original"] = c["name"]
        for name, value in c["values"].items():
            if isinstance(value, bool):
                if value:
                    form[f"clusters-{i}-{name}"] = "on"
            elif name == "k8s_disable":
                form[f"clusters-{i}-{name}"] = value.splitlines()
            else:
                form[f"clusters-{i}-{name}"] = value
    assert post(ui, "/clusters", form).status_code == 200
    rows = get(ui, "/api/servers").json()["rows"]
    form = {"server_count": "2"}
    for i, row in enumerate(rows):
        form.update({f"servers-{i}-{key}": value for key, value in row.items()})
    assert post(ui, "/servers", form).status_code == 200, form
    after = load_yaml(path.read_text())
    lab_group = after["all"]["children"]["servers"]["children"]["k8s_lab"]
    assert lab_group["vars"]["k8s_api_vip"] == "{{ lab_vip }}" and "!unsafe '{{ lab_vip }}'" not in path.read_text()
    assert lab_group["vars"]["custom_note"] == "kept" and lab_group["vars"]["updates_k8s_drain"] is True
    assert lab_group["vars"]["k8s_tls_san"] == ["api.lab.example"]
    assert lab_group["children"]["k8s_lab_servers"]["hosts"] == {"web-00": {"k8s_node_ip": "10.0.0.10"}, "cp-x": {}}
    assert lab_group["children"]["k8s_lab_agents"] == {"vars": {"k8s_longhorn_prereqs": True},
                                                       "hosts": {"web-01": {}}}
    assert lab_group["children"]["k8s_lab_extra"] == {"hosts": {"web-01": {}}}
    assert after["all"]["children"]["k8s_edge"]["children"]["k8s_edge_servers"]["hosts"] == {"edge-1": {}}
    assert "k8s_edge" not in after["all"]["children"]["servers"]["children"]


@pytest.mark.parametrize(("extra", "field", "says"), [
    ({"clusters-0-name": "Lab"}, "clusters-0-name", "format"),
    ({"clusters-0-name": "k" * 32}, "clusters-0-name", "format"),
    ({"clusters-0-k8s_version": ""}, "clusters-0-k8s_version", "required"),
    ({"clusters-0-k8s_version": "v1.20.0+k3s1"}, "clusters-0-k8s_version", "one of"),
    ({"clusters-0-k8s_api_vip": "10.0.0.256"}, "clusters-0-k8s_api_vip", "format"),
    ({"clusters-0-k8s_api_vip": ""}, "clusters-0-k8s_lb_pool", "needs the API address"),
    ({"clusters-0-k8s_disable": ["traefik"]}, "clusters-0-k8s_lb_pool", "servicelb"),
    ({"clusters-0-k8s_disable": ["servicelb", "coredns"]}, "clusters-0-k8s_disable", "takes only"),
    ({"clusters-0-k8s_api_vip": "10.0.0.92"}, "clusters-0-k8s_lb_pool", "can't be in"),
    ({"clusters-0-k8s_api_vip": "10.0.0.10"}, "clusters-0-k8s_api_vip", "web-00 already use"),
    ({"clusters-0-k8s_lb_pool": "10.0.0.0/28"}, "clusters-0-k8s_lb_pool", "web-00 already use"),
    ({"clusters-0-k8s_api_sources": "10.0.0.0/24 any"}, "clusters-0-k8s_api_sources", "format"),
    ({"clusters-0-k8s_flannel_backend": "host-gw"}, "clusters-0-k8s_flannel_backend", "one of"),
    ({"clusters-0-k8s_api_vip_interface": "eth0; x"}, "clusters-0-k8s_api_vip_interface", "format"),
    ({"clusters-0-k8s_ready_timeout": "5"}, "clusters-0-k8s_ready_timeout", "between"),
    ({"clusters-0-k8s_token": "{{ lookup('pipe', 'id') }}" + "a" * 40}, "clusters-0-k8s_token", "format"),
    ({"cluster_count": "2", "clusters-1-name": "lab", "clusters-1-k8s_version": "v1.36.5+k3s1"},
     "clusters-1-name", "Another cluster"),
    ({"cluster_count": "2", "clusters-1-name": "edge", "clusters-1-k8s_version": "v1.36.5+k3s1",
      "clusters-1-k8s_api_vip": "10.0.0.91"}, "clusters-1-k8s_api_vip", "overlaps cluster lab"),
])
def test_clusters_the_roles_would_refuse(ui, extra, field, says):
    configure(ui)
    r = post(ui, "/clusters", {**CLUSTER, **extra})
    assert r.status_code == 422 and says in r.json()["errors"][field], r.text


def test_a_saved_token_is_replaced_only_when_asked(ui):
    configure(ui)
    saved(ui, **{"clusters-0-k8s_token": SERVER_TOKEN})
    again = {**CLUSTER, "clusters-0-original": "lab", "clusters-0-k8s_token": "c3" * 32}
    r = post(ui, "/clusters", again)
    assert r.status_code == 422 and "tick the box" in r.json()["errors"]["clusters-0-k8s_token"]
    assert post(ui, "/clusters", {**again, "clusters-0-k8s_token__replace": "on"}).status_code == 200
    assert ui.app_state.store.read_secrets()["vault_k8s_token_lab"] == "c3" * 32
    events = [e for e in ui.app_state.audit.entries() if e["event"] == "k8s.token_set"]
    assert [e["token"] for e in events] == ["vault_k8s_token_lab", "vault_k8s_token_lab"]
    assert SERVER_TOKEN not in json.dumps(ui.app_state.audit.entries())


def test_generate_a_token_and_show_it_once(ui):
    configure(ui)
    saved(ui)
    r = post(ui, "/clusters/token", {"cluster": "lab", "token": "k8s_agent_token"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    token = r.json()["token"]
    assert re.fullmatch(r"[0-9a-f]{64}", token) and r.json()["variable"] == "vault_k8s_agent_token_lab"
    assert ui.app_state.store.read_secrets()["vault_k8s_agent_token_lab"] == token
    assert token not in json.dumps(get(ui, "/api/clusters").json())
    assert token not in json.dumps(ui.app_state.audit.entries())
    r = post(ui, "/clusters/token", {"cluster": "lab", "token": "k8s_agent_token"})
    assert r.status_code == 422 and "tick the box" in r.json()["error"]
    r = post(ui, "/clusters/token", {"cluster": "lab", "token": "k8s_agent_token", "replace": "on"})
    assert r.status_code == 200 and r.json()["token"] != token
    for form in ({"cluster": "nope", "token": "k8s_token"}, {"cluster": "lab", "token": "vault_admin_password"}):
        assert post(ui, "/clusters/token", form).status_code == 422


def test_renaming_and_removing_clusters(ui):
    configure(ui)
    saved(ui, **{"clusters-0-k8s_token": SERVER_TOKEN})
    members(ui, roles=("server",))
    r = post(ui, "/clusters", {"cluster_count": "0"})
    assert r.status_code == 422 and "web-00" in r.json()["errors"]["clusters"]
    renamed = {**CLUSTER, "clusters-0-original": "lab", "clusters-0-name": "home-lab"}
    assert post(ui, "/clusters", renamed).status_code == 200
    secrets = ui.app_state.store.read_secrets()
    assert secrets["vault_k8s_token_home_lab"] == SERVER_TOKEN and "vault_k8s_token_lab" not in secrets
    rows = get(ui, "/api/servers").json()["rows"]
    assert rows[0]["k8s_cluster"] == "home-lab"
    assert "k8s_home-lab_servers" in ui.app_state.settings.inventory_file.read_text()
    post(ui, "/servers", {"server_count": "1", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
                          "servers-0-pve_vmid": "4000"})
    assert post(ui, "/clusters", {"cluster_count": "0"}).status_code == 200
    assert "k8s_" not in ui.app_state.settings.inventory_file.read_text()


@pytest.mark.parametrize(("row", "field", "says"), [
    ({"servers-0-k8s_cluster": "nope", "servers-0-k8s_role": "server"}, "k8s_cluster", "no cluster nope"),
    ({"servers-0-k8s_cluster": "lab"}, "k8s_role", "both"),
    ({"servers-0-k8s_role": "agent"}, "k8s_cluster", "both"),
    ({"servers-0-k8s_cluster": "lab", "servers-0-k8s_role": "boss"}, "k8s_role", "one of"),
    ({"servers-0-k8s_cluster": "lab", "servers-0-k8s_role": "server", "servers-0-name": "Web-00"}, "name", "node name"),
    ({"servers-0-k8s_cluster": "lab", "servers-0-k8s_role": "agent", "servers-0-ansible_host": "",
      "servers-0-net_mode": "dhcp"}, "ansible_host", "reserved for it on DHCP"),
    ({"servers-0-ansible_host": "10.0.0.99"}, "ansible_host", "API address"),
    ({"servers-0-ansible_host": "10.0.0.93"}, "ansible_host", "LoadBalancer pool"),
])
def test_server_membership_the_roles_would_refuse(ui, row, field, says):
    configure(ui)
    saved(ui)
    form = {"server_count": "1", "servers-0-name": "web-00", "servers-0-ansible_host": "10.0.0.10",
            "servers-0-pve_vmid": "4000", **row}
    r = post(ui, "/servers", form)
    assert r.status_code == 422 and says in r.json()["errors"][f"servers-0-{field}"], r.text


def test_kubernetes_runs_check_first_then_apply(ui, monkeypatch):
    configure(ui)
    saved(ui)
    members(ui)
    data = get(ui, "/api/run").json()
    assert "kubernetes" not in [p["key"] for p in data["playbooks"]]
    entry = data["kubernetes"]["clusters"][0]
    assert entry["cluster"] == "lab" and entry["members"] == ["web-00", "web-01"]
    assert entry["needs"] == ["its server join token (Defaults page)", "its agent join token (Defaults page)"]
    assert [a["key"] for a in data["kubernetes"]["actions"]] == ["install", "upgrade"]
    r = post(ui, "/run", {"playbook": "kubernetes"})
    assert r.status_code == 422 and "as a check" in r.json()["error"]
    r = post(ui, "/kubernetes/run", {"cluster": "lab", "action": "install"})
    assert r.status_code == 422 and "join token" in r.json()["error"]
    saved(ui, **{"clusters-0-original": "lab", "clusters-0-k8s_token": SERVER_TOKEN,
                 "clusters-0-k8s_agent_token": AGENT_TOKEN})
    for form in ({"cluster": "lab", "action": "exec"}, {"cluster": "other", "action": "install"}):
        assert post(ui, "/kubernetes/run", form).status_code == 422

    r = post(ui, "/kubernetes/run", {"cluster": "lab", "action": "upgrade"})
    assert r.status_code == 200, r.text
    check = finished(ui, r.json()["run"]["id"])
    assert (check.kind, check.action, check.check, check.diff, check.tags, check.limit) == \
        ("kubernetes", "kubernetes", True, True, ["upgrade"], "k8s_lab")
    assert check.targets == [{"cluster": "lab", "action": "upgrade", "version": "v1.36.5+k3s1",
                              "members": ["web-00", "web-01"]}]
    log = ui.app_state.runner.log_path(check.id).read_text()
    assert "--tags=upgrade --check --diff --limit=k8s_lab playbooks/kubernetes.yml" in log
    assert SERVER_TOKEN not in log and TOKEN_SECRET not in log
    detail = get(ui, f"/api/runs/{check.id}").json()
    assert detail["apply"]["possible"], detail["apply"]

    # The real run: ticked, and an upgrade's version typed again.
    r = post(ui, "/kubernetes/apply", {"run_id": check.id, "confirm_version": "v1.36.5+k3s1"})
    assert r.status_code == 422 and "Tick the box" in r.json()["error"]
    r = post(ui, "/kubernetes/apply", {"run_id": check.id, "confirm": "on", "confirm_version": "v1.37.1+k3s1"})
    assert r.status_code == 422 and "Type the version" in r.json()["error"]
    r = post(ui, "/kubernetes/apply", {"run_id": check.id, "confirm": "on", "confirm_version": " v1.36.5+k3s1 "})
    assert r.status_code == 200, r.text
    real = finished(ui, r.json()["run"]["id"])
    assert (real.check, real.applied_from, real.tags) == (False, check.id, ["upgrade"])
    r = post(ui, "/kubernetes/apply", {"run_id": check.id, "confirm": "on", "confirm_version": "v1.36.5+k3s1"})
    assert r.status_code == 422 and "already" in r.json()["error"]
    events = [e["event"] for e in ui.app_state.audit.entries()]
    assert events.count("run.check") == 1 and events.count("run.apply") == 1 and "run.refused" in events
    finished_entries = [e for e in ui.app_state.audit.entries() if e["event"] == "run.finished"]
    assert {e["run"] for e in finished_entries} >= {check.id, real.id}


def test_a_kubernetes_check_goes_stale(ui, monkeypatch):
    configure(ui)
    saved(ui, **{"clusters-0-k8s_token": SERVER_TOKEN, "clusters-0-k8s_agent_token": AGENT_TOKEN})
    members(ui)
    check = finished(ui, post(ui, "/kubernetes/run", {"cluster": "lab", "action": "install"}).json()["run"]["id"])
    assert get(ui, f"/api/runs/{check.id}").json()["apply"]["possible"]
    # Another version saved since: the check is for the old settings.
    saved(ui, **{"clusters-0-original": "lab", "clusters-0-k8s_version": "v1.37.1+k3s1"})
    r = post(ui, "/kubernetes/apply", {"run_id": check.id, "confirm": "on"})
    assert r.status_code == 422 and "Settings were saved" in r.json()["error"]
    # A failed check, and an old one.
    ui.app_state.settings.run_env["FAKE_RC"] = "2"
    failed = finished(ui, post(ui, "/kubernetes/run", {"cluster": "lab", "action": "install"}).json()["run"]["id"])
    assert "didn't succeed" in get(ui, f"/api/runs/{failed.id}").json()["apply"]["problem"]
    del ui.app_state.settings.run_env["FAKE_RC"]
    old = finished(ui, post(ui, "/kubernetes/run", {"cluster": "lab", "action": "install"}).json()["run"]["id"])
    old.finished = time.time() - 3600
    ui.app_state.runner._save(old)
    assert "30 minutes" in get(ui, f"/api/runs/{old.id}").json()["apply"]["problem"]
    # A VMs page run's apply can't run a Kubernetes check, nor the other way round.
    fresh = finished(ui, post(ui, "/kubernetes/run", {"cluster": "lab", "action": "install"}).json()["run"]["id"])
    r = post(ui, "/vms/apply", {"run_id": fresh.id, "confirm": "on"})
    assert r.status_code == 422
    r = post(ui, "/kubernetes/apply", {"run_id": "nope", "confirm": "on"})
    assert r.status_code == 422 and "Only a Kubernetes check" in r.json()["error"]
    # A member removed since the check changes its plan.
    data = ui.app_state.store.load()
    data.clusters[0].other_members["agents"] = {"late": {}}
    ui.app_state.store.save(data)
    check_run = ui.app_state.runner.get(fresh.id)
    check_run.settings = ui.app_state.runner.settings_digest()
    ui.app_state.runner._save(check_run)
    assert "cluster changed" in get(ui, f"/api/runs/{fresh.id}").json()["apply"]["problem"]


def test_the_vms_page_shows_members_and_never_takes_the_vip(ui, monkeypatch):
    configure(ui)
    saved(ui)
    members(ui, roles=("server",))
    resources = [{"vmid": 4000, "name": "web-00", "node": "pve-1", "type": "qemu", "status": "running",
                  "tags": "usas-k8s-lab-server"},
                 {"vmid": 101, "name": "app", "node": "pve-1", "type": "qemu", "status": "running",
                  "tags": "usas-k8s-edge-agent"}]
    fake_pve(monkeypatch, resources=resources, extra={
        "/nodes/pve-1/qemu/101/agent/network-get-interfaces": {"result": [{"name": "eth0", "ip-addresses": [
            {"ip-address": "10.0.0.99", "ip-address-type": "ipv4", "prefix": 24},
            {"ip-address": "10.0.0.91", "ip-address-type": "ipv4", "prefix": 24},
            {"ip-address": "10.0.0.51", "ip-address-type": "ipv4", "prefix": 24}]}]}})
    rows = {row["vmid"]: row for row in get(ui, "/api/vms").json()["vms"]}
    assert rows[4000]["k8s"] == {"cluster": "lab", "role": "server", "inventory": True, "drain": False}
    assert rows[101]["k8s"] == {"cluster": "edge", "role": "agent", "inventory": False, "drain": False}
    assert rows[101]["addresses"] == ["10.0.0.51"] and rows[101]["ip"] == "10.0.0.51"


def test_an_update_warns_when_a_member_isnt_drained(ui, monkeypatch):
    pinned(ui, monkeypatch, extra=None)
    config = ui.app_state.store.load()
    warnings = k.drain_warnings(config, [(101, "vm-101")], {101: ["usas-k8s-lab-agent"]}, False)
    assert len(warnings) == 1 and "doesn't have" in warnings[0]
    from .test_app import UPDATES_FORM
    r = post(ui, "/vms/plan", UPDATES_FORM)
    assert r.status_code == 200 and not any("Kubernetes" in w for w in r.json()["plan"]["warnings"])


def test_drain_and_membership_rules():
    cluster = k.Cluster(name="lab", extra_vars={"updates_k8s_drain": True})
    config = k.Config(servers=[{"name": "w", "pve_vmid": 7, "k8s_cluster": "lab", "k8s_role": "agent"}],
                      clusters=[cluster])
    assert k.drain(config, cluster, False) is True
    assert k.drain(config, k.Cluster(name="x", member_vars={"agents": {"updates_k8s_drain": "false"}}), True) is False
    assert k.drain(config, None, True) is True
    assert k.drain_warnings(config, [(7, "w")], {}, False) == []
    cluster.extra_vars = {}
    assert "draining is off" in k.drain_warnings(config, [(7, "w")], {}, False)[0]
    assert k.membership(config, 8, ["prod", "usas-k8s-Lab-server"]) is None
    assert k.address_problem("10.0.0.5", config) == "" and k.address_problem("", config) == ""
    assert k._network("10.0.0.9-10.0.0.1") is None and k._network("x") is None
