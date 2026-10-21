// What the API answers, for the tests. sections.json is the server's own output for the settings pages
// (webui/tests/test_frontend_fixtures.py checks it still matches the fields schema.py builds).

import type { OverviewData, Run, RunPageData, SectionData, ServersData, SshData } from "../src/types";
import sections from "./fixtures/sections.json";

export const proxmox = sections.proxmox as unknown as SectionData;
export const defaults = sections.defaults as unknown as SectionData;
export const hardening = sections.hardening as unknown as SectionData;
export const servers = sections.servers as unknown as ServersData;

export const running: Run = {
  id: "20261021-140211-a1b2c3", action: "provision", check: false, diff: true, limit: "web-01", status: "running",
  started: 1792591331, finished: null, returncode: null, done: false, playbook: "playbooks/provision-vms.yml",
};

export const finished: Run = {
  id: "20261021-134101-d4e5f6", action: "baseline", check: true, diff: false, limit: "", status: "failed",
  started: 1792590061, finished: 1792590169, returncode: 2, done: true, playbook: "playbooks/baseline.yml",
};

export const succeeded: Run = {
  ...finished, id: "20261021-132000-abcdef", action: "build-installer", check: false, status: "succeeded",
  returncode: 0, playbook: "playbooks/build-installer.yml",
};

export const runs: Run[] = [running, finished, succeeded];

export const overview: OverviewData = {
  steps: [
    { label: "Proxmox API host, token and node", done: true, href: "/proxmox" },
    { label: "SSH key", done: true, href: "/ssh" },
    { label: "Proxmox node's SSH address and trusted host key", done: false, href: "/ssh" },
    { label: "Admin password, gateway and SSH keys for the servers", done: true, href: "/defaults" },
    { label: "At least one server", done: true, href: "/servers" },
  ],
  runs,
  current_run: running,
  node: "hephaestus",
  cluster: "olympus",
};

export const runPage: RunPageData = {
  playbooks: [
    { key: "build-installer", playbook: "playbooks/build-installer.yml", description: "Build the installer ISO on the Proxmox node", extra: [] },
    { key: "provision", playbook: "playbooks/provision-vms.yml", description: "Create and install the VMs that don't exist yet", extra: [] },
    { key: "baseline", playbook: "playbooks/baseline.yml", description: "Apply the baseline to the servers", extra: [] },
    { key: "site", playbook: "playbooks/site.yml", description: "All three, in order", extra: [] },
    { key: "keys", playbook: "playbooks/baseline.yml", description: "Re-sync the admin user's SSH keys only", extra: ["--tags", "ssh_keys"] },
  ],
  names: ["all", "db-01", "ubuntu_servers", "web-01"],
  steps: overview.steps,
  current_run: null,
};

export const ssh: SshData = {
  public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl ubuntu-server-auto-setup",
  key_info: { type: "ssh-ed25519", fingerprint: "SHA256:q9Vx1y2z3fQk", comment: "ubuntu-server-auto-setup" },
  node_address: "192.168.1.20",
  trusted: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:trusted1" }],
  pending: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:offered1" }],
  pve_node: "hephaestus",
};

export const log = [
  "PLAY [Create Ubuntu VMs on Proxmox and install them unattended] ****************",
  "",
  "TASK [proxmox_vm : Check required variables] ***********************************",
  "ok: [web-01 -> localhost]",
  "changed: [web-01 -> localhost]",
  "skipping: [web-01]",
  "FAILED - RETRYING: [web-01 -> localhost]: Wait for the installer (119 retries left).",
  "fatal: [web-01 -> localhost]: FAILED! => {\"msg\": \"Timeout\"}",
  "[WARNING]: Platform linux on host web-01 is using the discovered Python interpreter",
  "",
].join("\n");
