#!/usr/bin/env python3
"""Drives the VMs page's runs in the integration test (tests/webui-vms.sh), with the standard library only, the
way the app does in a browser. Fails with a message at the first problem."""

import argparse
import json
import re
import subprocess
import sys
import time

from webui_smoke import TOKEN_SECRET, UI, check

PASSWORD = "vms test passphrase"
ADMIN_PASSWORD = "vms admin password"  # the Vault's: it must never reach the VM


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


def wait_run(ui: UI, run_id: str) -> dict:
    deadline = time.time() + 900
    while time.time() < deadline:
        status, data = ui.get(f"/runs/{run_id}")
        if data.get("run", {}).get("done"):
            return data
        time.sleep(3)
    check(False, f"run {run_id} finished in time")
    return {}


def set_up(ui: UI) -> None:
    status, data = ui.post("/setup", {"setup_code": ARGS.setup_code, "password": PASSWORD, "confirm": PASSWORD})
    check(status == 200, "signed in", data)
    with open(ARGS.ca) as fh:
        ui.post("/proxmox", {"action": "save-ca", "ca_pem": fh.read()})
    api = {"pve_api_host": "pve-mock", "pve_api_port": "8006", "pve_api_user": "ansible@pve",
           "pve_api_token_id": "test", "pve_api_token_secret": TOKEN_SECRET, "pve_validate_certs": "on",
           "pve_node": "pve-test", "pve_storage": "local-lvm", "pve_efidisk_format": "raw", "pve_bridge": "vmbr0",
           "pve_vlan": "", "pve_iso_storage": "local"}
    status, data = ui.post("/proxmox", {"action": "test", **api})
    check("Connected" in data.get("message", ""), "the API connection test passes", data)
    check(ui.post("/proxmox", {"action": "save", **api})[0] == 200, "the Proxmox settings are saved")
    ui.post("/ssh", {"action": "generate"})
    status, page = ui.get("/ssh")
    public = page.get("public_key", "")
    check(public.startswith("ssh-ed25519 "), "the UI has an SSH key", page)
    defaults = {"github_user": "test", "github_keys_url": "http://pve-mock:8081/test.keys",
                "admin_extra_ssh_keys": "", "authorize_ui_key": "on", "admin_user": "sysadmin",
                "admin_password": ADMIN_PASSWORD, "timezone": "Etc/UTC", "net_gateway": "10.10.10.1",
                "net_prefix": "24", "net_dns": "10.10.10.1", "net_search": "example.lan",
                "autoinstall_storage_layout": "lvm"}
    check(ui.post("/defaults", {"action": "save", **defaults})[0] == 200, "the defaults are saved")
    hardening = {"ssh_port": "22", "ssh_permit_root_login": "no", "fail2ban_sshd_mode": "normal",
                 "fail2ban_maxretry": "5", "fail2ban_findtime": "10m", "fail2ban_bantime": "1h",
                 "fail2ban_bantime_maxtime": "1w", "firewall_ssh_sources": "any", "firewall_ssh_limit": "on",
                 "fail2ban_bantime_increment": "on", "firewall_allow": "9100/tcp from 10.10.10.0/24"}
    check(ui.post("/hardening", hardening)[0] == 200, "the hardening settings are saved")
    # Whoever runs the VMs lets the UI's key in for its user (the page says so).
    for vm in (ARGS.srv, ARGS.upd):
        docker("exec", vm, "sh", "-c", f"echo '{public}' > /home/ubuntu/.ssh/authorized_keys && "
               "chown ubuntu: /home/ubuntu/.ssh/authorized_keys && chmod 600 /home/ubuntu/.ssh/authorized_keys")


def console_fingerprints(vm: str = "") -> set[str]:
    """What the VM's console shows: ssh-keygen -lf on its host keys."""
    out = docker("exec", vm or ARGS.srv, "sh", "-c",
                 'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done')
    return {line.split()[1] for line in out.splitlines() if line.strip()}


def shown(name: str) -> str:
    """A package name as the run's page shows it: the mock's API token ID is "test", a Vault value, and the UI
    hides Vault values wherever they appear."""
    return name.replace("test", "********")


def version(vm: str, package: str) -> str:
    return docker("exec", vm, "dpkg-query", "-W", "-f=${Version}", package).strip()


def updates(ui: UI) -> None:
    """The updates playbook from the VMs page, on VM 4073: a check, the refused and the confirmed real run, and a
    second check that finds nothing to install."""
    form = {"action": "scan", "vmid": "4073", "address": ARGS.upd_address, "port": ""}
    status, data = ui.post("/vms/hostkey", form)
    check(status == 200 and {k["fingerprint"] for k in data.get("scanned", [])} == console_fingerprints(ARGS.upd),
          "VM 4073's scanned fingerprints are the ones its console shows", data)
    check(ui.post("/vms/hostkey", {"action": "trust", "vmid": "4073", "confirm": "on"})[0] == 200,
          "VM 4073's keys are pinned")
    status, data = ui.post("/vms/snapshots", {"vmid": "4073"})
    check(status == 200 and not data["vms"][0]["possible"] and "VM.Snapshot" in data["vms"][0]["reason"],
          "no snapshot is offered without VM.Snapshot", data)
    form = {"vmid": "4073", "preset": "updates", "updates-scope": "security", "vm-4073-address": ARGS.upd_address,
            "vm-4073-user": "ubuntu"}
    status, data = ui.post("/vms/plan", form)
    check(status == 200 and data["plan"]["playbook"] == "updates"
          and data["plan"]["options"]["updates_scope"] == "security", "the updates plan is reviewed", data)
    status, started = ui.post("/vms/run", {"plan_id": data["plan_id"], "mode": "check"})
    check(status == 200, "the updates check starts", started)
    check_id = started["run"]["id"]
    done = wait_run(ui, check_id)
    result = ((done.get("hosts") or {}).get("updates") or {}).get("hosts", {}).get("vm-4073", {})
    check(done["run"]["status"] == "succeeded" and result.get("status") == "pending"
          and shown("usas-test-tool") in result.get("planned", [])
          and result.get("deferred") == [shown("usas-test-phased")],
          "the check lists the VM's pending updates", (result, done.get("log", "")[-3000:]))
    check("--check --limit=vm-4073 playbooks/updates.yml" in done["log"], "it ran the updates playbook in check mode",
          done["log"][:800])
    check(version(ARGS.upd, "usas-test-tool") == "1.0", "the check installed nothing")
    status, data = ui.post("/vms/apply", {"run_id": check_id})
    check(status == 422 and "Tick the box" in data.get("error", ""), "installing needs the confirmation", data)
    status, started = ui.post("/vms/apply", {"run_id": check_id, "confirm": "on"})
    check(status == 200, "the real updates run starts", started)
    real = wait_run(ui, started["run"]["id"])
    result = ((real.get("hosts") or {}).get("updates") or {}).get("hosts", {}).get("vm-4073", {})
    check(real["run"]["status"] == "succeeded" and result.get("status") == "updated"
          and sorted(u["name"] for u in result.get("upgraded", [])) == [
              shown("usas-test-newdep"), shown("usas-test-pcr7"), shown("usas-test-tool")],
          "the security updates are installed", (result, real.get("log", "")[-3000:]))
    check(version(ARGS.upd, "usas-test-tool") == "1.2" and version(ARGS.upd, "usas-test-extra") == "1.0",
          "the VM has the security update and not the others")
    status, data = ui.post("/vms/plan", form)
    status, started = ui.post("/vms/run", {"plan_id": data["plan_id"], "mode": "check"})
    again = wait_run(ui, started["run"]["id"])
    result = ((again.get("hosts") or {}).get("updates") or {}).get("hosts", {}).get("vm-4073", {})
    check(again["run"]["status"] == "succeeded" and result.get("status") == "nothing",
          "a second check finds no security update left", result)

    # That check becomes a schedule (with the password again), and the command line runs it now.
    form = {"action": "create", "run_id": again["run"]["id"], "name": "Nightly", "kind": "check", "day-sun": "on",
            "start": "03:00", "minutes": "60", "timezone": "Etc/UTC", "schedule_current": "wrong"}
    status, data = ui.post("/updates/schedules", form)
    check(status == 422 and "schedule_current" in data.get("errors", {}), "a schedule needs the password", data)
    status, data = ui.post("/updates/schedules", {**form, "schedule_current": PASSWORD})
    check(status == 200, "the schedule is saved", data)
    status, page = ui.get("/updates")
    [schedule] = page["schedules"]
    out = subprocess.run(["docker", "exec", ARGS.app, "python", "-m", "usas_web", "updates",  # noqa: S603, S607
                          "--schedule", schedule["id"]], capture_output=True, text=True, timeout=900, check=False)
    check(out.returncode == 0 and "Check run" in out.stdout, "the command line runs the schedule",
          (out.stdout, out.stderr[-2000:]))
    status, page = ui.get("/updates")
    check(page["schedules"][0]["last_run"] and page["hosts"].get("vm-4073", {}).get("result", {}).get("status")
          == "nothing", "the Updates page shows the schedule's run and the VM's newest result", page)


def plan(ui: UI, vmid: int, preset: str = "full", **extra: str) -> tuple[int, dict]:
    form = {"vmid": str(vmid), "preset": preset, f"vm-{vmid}-address": ARGS.address, f"vm-{vmid}-user": "ubuntu"}
    form.update({f"vm-{vmid}-{k}": v for k, v in extra.items()})
    return ui.post("/vms/plan", form)


def main() -> None:
    ui = UI(ARGS.url)
    ui.page("/setup")
    set_up(ui)

    status, data = ui.get("/vms")
    rows = {row["vmid"]: row for row in data.get("vms", [])}
    vm = rows.get(4071, {})
    check(vm.get("selectable") and vm.get("ip") == ARGS.address and vm.get("alias") == "usas-mock-cluster-4071"
          and vm.get("pinned") == [] and not vm.get("inventory_name"),
          "the VM outside the inventory can be selected, with its agent's address and no pinned key", vm)

    status, data = plan(ui, 4071)
    check(status == 422 and "Trust VM 4071" in data.get("errors", {}).get("vm-4071-hostkey", ""),
          "a plan needs the VM's host key pinned first", data)

    status, data = ui.post("/vms/hostkey", {"action": "scan", "vmid": "4071", "address": ARGS.address, "port": ""})
    scanned = {k["fingerprint"] for k in data.get("scanned", [])}
    check(status == 200 and scanned == console_fingerprints(),
          "the scanned fingerprints are the ones the VM's console shows", data)
    status, data = ui.post("/vms/hostkey", {"action": "trust", "vmid": "4071"})
    check(status == 422, "pinning needs the box ticked", data)
    ui.post("/vms/hostkey", {"action": "scan", "vmid": "4071", "address": ARGS.address, "port": ""})
    status, data = ui.post("/vms/hostkey", {"action": "trust", "vmid": "4071", "confirm": "on"})
    check(status == 200 and {k["fingerprint"] for k in data.get("pinned", [])} == scanned, "the keys are pinned", data)
    status, data = ui.post("/vms/hostkey", {"action": "test", "vmid": "4071", "address": ARGS.address, "port": "",
                                            "user": "ubuntu"})
    check(status == 200 and "Logged in to ubuntu@" in data.get("message", ""), "the login test passes", data)

    status, data = plan(ui, 4071, ufw="8080/tcp")
    check(status == 200, "the plan is reviewed", data)
    target = data["plan"]["targets"][0]
    check((target["name"], target["static"], target["source"], target["user"]) == ("vm-4071", False, "agent", "ubuntu"),
          "the VM runs as vm-4071 with the agent's address and its own user", target)
    check(any("default for incoming traffic is deny" in w for w in data["plan"]["warnings"]),
          "the review warns about the firewall", data["plan"]["warnings"])
    status, refused = ui.post("/vms/run", {"plan_id": data["plan_id"], "mode": "apply"})
    check(status == 422, "a plan can't run for real before its check", refused)

    status, started = ui.post("/vms/run", {"plan_id": data["plan_id"], "mode": "check"})
    check(status == 200, "the check run starts", started)
    check_id = started["run"]["id"]
    done = wait_run(ui, check_id)
    host = (done.get("hosts") or {}).get("hosts", {}).get("vm-4071", {})
    check(done["run"]["status"] == "succeeded" and host.get("changed", 0) > 0 and host.get("failed") == 0,
          f"the check run passes and would change things ({host.get('changed')} tasks)", done.get("log", "")[-3000:])
    check("--check --diff --limit=vm-4071" in done["log"], "it ran in check mode on the VM only", done["log"][:600])
    check(done.get("apply", {}).get("possible"), "its page offers the real run", done.get("apply"))
    check(docker("exec", ARGS.srv, "sh", "-c", "command -v ufw || true").strip() == "",
          "the check run changed nothing on the VM (UFW isn't installed)")

    status, data = ui.post("/vms/apply", {"run_id": check_id})
    check(status == 422 and "Tick the box" in data.get("error", ""), "the real run needs the confirmation", data)
    status, started = ui.post("/vms/apply", {"run_id": check_id, "confirm": "on"})
    check(status == 200, "the real run starts", started)
    real = wait_run(ui, started["run"]["id"])
    host = (real.get("hosts") or {}).get("hosts", {}).get("vm-4071", {})
    check(real["run"]["status"] == "succeeded" and real["run"]["applied_from"] == check_id and not real["run"]["check"]
          and host.get("failed") == 0, "the real run succeeds", real.get("log", "")[-3000:])
    status, data = ui.post("/vms/apply", {"run_id": check_id, "confirm": "on"})
    check(status == 422 and "run already" in data.get("error", ""), "a check run's plan runs for real only once", data)

    ufw = docker("exec", ARGS.srv, "ufw", "status")
    check("Status: active" in ufw and "8080/tcp" in ufw and "9100/tcp" in ufw,
          "the VM's firewall is on with its own rule and every server's", ufw)
    check(docker("exec", ARGS.srv, "hostname").strip() == "app-4071", "its host name is unchanged")
    sudoers = docker("exec", ARGS.srv, "cat", "/etc/sudoers.d/90-ansible-ubuntu")
    check("ubuntu ALL=(ALL:ALL) NOPASSWD: ALL" in sudoers, "sudo stays passwordless for its user", sudoers)
    keys = docker("exec", ARGS.srv, "cat", "/home/ubuntu/.ssh/authorized_keys")
    check("vms@ubuntu-server-auto-setup" in keys and "ubuntu-server-auto-setup-webui" in keys,
          "its user has the GitHub key and still the UI's", keys)

    status, data = plan(ui, 4071, ufw="8080/tcp")
    status, started = ui.post("/vms/run", {"plan_id": data["plan_id"], "mode": "check"})
    again = wait_run(ui, started["run"]["id"])
    host = (again.get("hosts") or {}).get("hosts", {}).get("vm-4071", {})
    check(again["run"]["status"] == "succeeded" and host.get("changed") == 0,
          "a second check run finds nothing to change", host)

    # VM 4072 claims VM 4071's address. Its own key was pinned before (on its console); the connection to the
    # address it reports gets VM 4071's key, which doesn't match: refused.
    other = docker("exec", ARGS.app, "sh", "-c", "ssh-keygen -q -t ed25519 -N '' -f /tmp/k4072 && cat /tmp/k4072.pub")
    keytype, blob = other.split()[:2]
    docker("exec", ARGS.app, "sh", "-c", f"echo 'usas-mock-cluster-4072 {keytype} {blob}' >> /data/ssh/known_hosts")
    status, data = plan(ui, 4072, preset="keys")
    check(status == 200, "a plan for the lying guest is made (its key is pinned)", data)
    status, started = ui.post("/vms/run", {"plan_id": data["plan_id"], "mode": "check"})
    lied = wait_run(ui, started["run"]["id"])
    host = (lied.get("hosts") or {}).get("hosts", {}).get("vm-4072", {})
    check(lied["run"]["status"] == "failed" and host.get("unreachable", 0) > 0
          and re.search(r"Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED", lied["log"]),
          "a guest that reports another VM's address gets a refused connection", lied.get("log", "")[-2000:])

    updates(ui)

    status, _, info, raw = ui.request("GET", "/audit.jsonl")
    entries = [json.loads(line) for line in raw.decode().splitlines()]
    events = [e["event"] for e in entries]
    check(status == 200 and {"hostkey.pin", "run.check", "run.apply", "run.finished", "run.refused"} <= set(events),
          "the audit log records the pins, the runs, the refusals and the outcomes", events)
    applied = next(e for e in entries if e["event"] == "run.apply")
    check(applied["check"] == check_id and applied["targets"][0]["address"] == ARGS.address
          and set(applied["targets"][0]["fingerprints"]) == scanned and applied["session"] and applied["client"],
          "a real run's entry names the check, the VM, its address, its keys, the session and the client", applied)
    check(TOKEN_SECRET not in raw.decode() and ADMIN_PASSWORD not in raw.decode(), "the audit log holds no secret")
    updated = [e for e in entries if e["event"] == "run.apply" and e.get("playbook") == "updates"]
    check(len(updated) == 1 and updated[0]["options"]["updates_scope"] == "security"
          and updated[0]["targets"][0]["vmid"] == 4073, "the updates run's entry names the playbook and its choices",
          updated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--setup-code", required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--srv", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--upd", required=True)
    parser.add_argument("--upd-address", required=True)
    ARGS = parser.parse_args()
    main()
    sys.exit(0)
