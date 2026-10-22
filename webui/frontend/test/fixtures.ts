// What the API answers, for the tests. sections.json is the server's own output for the settings pages
// (webui/tests/test_frontend_fixtures.py checks it still matches the fields schema.py builds).

import type {
  AccountData, OverviewData, Run, RunPageData, SectionData, ServersData, SessionData, SshData, Vm, VmsData,
} from "../src/types";
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
    { label: "Each Proxmox node's SSH address and trusted host key", done: false, href: "/ssh" },
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
  iso_plan: {
    storage: "synology-nfs", shared: true, known: true, builders: ["ares"],
    pairs: [
      { node: "ares", storage: "synology-nfs", shared: true, built_by: "ares", servers: ["db-01"], address: "" },
      { node: "hephaestus", storage: "synology-nfs", shared: true, built_by: "ares", servers: ["web-01"],
        address: "192.168.1.20" },
    ],
  },
};

export const ssh: SshData = {
  public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl ubuntu-server-auto-setup",
  key_info: { type: "ssh-ed25519", fingerprint: "SHA256:q9Vx1y2z3fQk", comment: "ubuntu-server-auto-setup" },
  nodes: [
    { name: "hephaestus", address: "192.168.1.20", default: true, in_use: true, servers: ["web-01"],
      trusted: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:trusted1" }] },
    { name: "ares", address: "192.168.1.21", default: false, in_use: true, servers: ["db-01"], trusted: [] },
    { name: "hermes", address: "192.168.1.22", default: false, in_use: false, servers: [], trusted: [] },
  ],
  pending: { node: "hephaestus", keys: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:offered1" }] },
  pve_node: "hephaestus",
  node_user: "root",
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

const vmBase: Vm = {
  vmid: 101, name: "web-01", node: "hephaestus", type: "qemu", status: "running", template: false, lock: "",
  tags: ["ubuntu", "ansible"], cpu: 0.12, maxcpu: 4, mem: 1610612736, maxmem: 4294967296, uptime: 86400,
  agent: "ok", addresses: ["10.0.0.21", "192.168.50.21"], ip: "10.0.0.21", ip_source: "agent",
  os: "Ubuntu 26.04 LTS", hostname: "web-01", support: "supported", reason: "Ubuntu 26.04 LTS (reported by the guest).",
  inventory_name: "web-01", ours: true, name_ok: true,
};

export const vmList: Vm[] = [
  vmBase,
  { ...vmBase, vmid: 102, name: "db-01", node: "ares", cpu: 0.53, maxcpu: 2, mem: 3221225472, addresses: [],
    ip: "10.0.0.22", ip_source: "inventory", agent: "forbidden", os: "", hostname: "", support: "unknown",
    reason: "The guest agent didn't report the OS: confirm it is Ubuntu before running a playbook.", ours: false,
    inventory_name: "database", tags: [] },
  { ...vmBase, vmid: 103, name: "jammy", status: "stopped", cpu: 0, mem: 0, agent: "stopped", addresses: [],
    ip: "10.0.0.23", ip_source: "ipconfig0", os: "", hostname: "", support: "override",
    reason: "Ubuntu 22.04 LTS (reported by the guest): only 26.04 and 24.04 are tested.", ours: false,
    inventory_name: "", tags: [] },
  { ...vmBase, vmid: 104, name: "win-11", status: "paused", agent: "ok", addresses: [], ip: "", ip_source: null,
    os: "Windows 11 Pro", hostname: "WIN-11", support: "unsupported",
    reason: "Windows: the playbooks set up Ubuntu Server only.", ours: false, inventory_name: "", tags: [],
    lock: "backup" },
  { ...vmBase, vmid: 200, name: "ct-dns", type: "lxc", agent: "none", addresses: [], ip: "", ip_source: null,
    os: "", hostname: "", support: "unsupported", reason: "A container (LXC), not a VM: the playbooks set up whole servers.",
    ours: false, inventory_name: "", tags: ["dns"] },
  { ...vmBase, vmid: 105, name: "golden", template: true, status: "stopped", agent: "none", addresses: [], ip: "",
    ip_source: null, os: "", hostname: "", support: "unsupported", reason: "A template: clone it into a VM first.",
    ours: false, inventory_name: "", tags: [] },
];

export const vms: VmsData = {
  version: "9.1.1",
  vms: vmList,
  complete: true,
  forbidden: 1,
  notes: [{ kind: "warning", message: "The token may not ask the guest agents of 1 VM(s) for their addresses: add VM.GuestAgent.Audit to its role (see docs/installing.md)." }],
  cluster: "olympus",
  fetched: 1792591331,
};

export const session: SessionData = {
  configured: true, signed_in: false, csrf: "test-token", version: "1.2.3", theme: "dark",
  passkeys: { registered: false, required: false, rp_id: "localhost", public_url: "" },
};

export const account: AccountData = {
  passkeys: [
    { id: "cred-laptop", name: "Laptop", created: 1792590061, last_used: 1792591331, backed_up: false },
    { id: "cred-phone", name: "Phone", created: 1792591000, last_used: null, backed_up: true },
  ],
  require_passkey: false, broken: false, rp_id: "localhost", public_url: "", max_passkeys: 20, max_name: 64,
  current: "cred-laptop",
};
