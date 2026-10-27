// The shapes of what the server's API (webui/usas_web/main.py) sends.

export type Theme = "dark" | "light" | "system";

export type FieldKind = "text" | "int" | "bool" | "choice" | "list" | "secret";

export interface FieldMeta {
  name: string;
  label: string;
  kind: FieldKind;
  help: string;
  required: boolean;
  advanced: boolean;
  variable: string;
  default: string | null;
  choices: string[] | null;
  saved?: boolean;
}

export type Values = Record<string, string | boolean>;
export type Errors = Record<string, string>;

export interface Cluster {
  name?: string;
  nodes?: Record<string, string>;
  iso_storages?: Record<string, string>;
}

export interface Certificate {
  subject: string;
  fingerprint: string;
  expires: string;
}

export interface SectionData {
  title: string;
  fields: FieldMeta[];
  values: Values;
  cluster?: Cluster;
  ca?: Certificate[];
  ui_key?: string;
  authorize_ui_key?: boolean;
  timezones?: string[];
}

export type RunStatus = "running" | "succeeded" | "failed" | "stopped" | "interrupted";

export interface Run {
  id: string;
  action: string;
  check: boolean;
  diff: boolean;
  limit: string;
  status: RunStatus | string;
  started: number;
  finished: number | null;
  returncode: number | null;
  done: boolean;
  playbook: string | null;
  // Runs from the VMs page (older runs have none of these).
  kind?: "playbook" | "vms" | string;
  tags?: string[];
  targets?: RunTarget[];
  plan?: string;
  applied_from?: string; // a real run's check run
}

// For a VMs page check run: whether its plan can now be run for real (main.py apply_json).
export interface ApplyInfo {
  possible: boolean;
  problem: string;
  until: number | null;
}

// A host a VMs page run was planned for (vmplan.Plan.summary).
export interface RunTarget {
  vmid: number;
  name: string;
  pve_name: string;
  node: string;
  static: boolean;
  address: string;
  source: string;
  port: number | null;
  user: string;
  fingerprints: string[];
  manage_hostname: boolean;
  override: boolean;
}

// The summary callback's results per host (runs/<id>.hosts.json).
export interface HostResult {
  ok: number;
  changed: number;
  failed: number;
  unreachable: number;
  skipped: number;
  rescued: number;
  ignored: number;
  changed_tasks: string[];
  failed_tasks: { task: string; message: string }[];
}

export interface HostResults {
  finished: boolean;
  updated: number;
  hosts: Record<string, HostResult>;
}

export interface Step {
  label: string;
  done: boolean;
  href: string;
}

export interface OverviewData {
  steps: Step[];
  runs: Run[];
  current_run: Run | null;
  node: string;
  cluster: string;
}

export interface Playbook {
  key: string;
  playbook: string;
  description: string;
  extra: string[];
}

// Where build-installer puts the installer ISO: one row per (node, ISO storage) the servers need.
export interface IsoPair {
  node: string;
  storage: string;
  shared: boolean;
  built_by: string; // the node that builds it
  servers: string[];
  address: string;
}

export interface IsoPlan {
  storage: string;
  shared: boolean;
  known: boolean; // false until a connection test has listed the cluster's ISO storages
  pairs: IsoPair[];
  builders: string[];
}

export interface RunPageData {
  playbooks: Playbook[];
  names: string[];
  steps: Step[];
  current_run: Run | null;
  iso_plan: IsoPlan;
}

export interface RunDetailData {
  run: Run;
  log: string;
  offset: number;
  hosts?: HostResults | null;
  apply?: ApplyInfo | null;
}

export interface HostKey {
  keytype: string;
  fingerprint: string;
}

export interface KeyInfo {
  type: string;
  fingerprint: string;
  comment: string;
}

export interface SshNode {
  name: string;
  address: string;
  default: boolean; // the node on the Proxmox page
  in_use: boolean; // the default node, or a server's
  servers: string[];
  trusted: HostKey[];
}

export interface SshData {
  public_key: string;
  key_info: KeyInfo | null;
  nodes: SshNode[];
  pending: { node: string; keys: HostKey[] } | null;
  pve_node: string;
  node_user: string;
}

export interface ServersData {
  columns: FieldMeta[];
  rows: Record<string, string>[];
  nodes: string[]; // the cluster's nodes, from the last connection test
  default_node: string;
}

export interface Notice {
  kind: "ok" | "warning" | "error" | "info";
  message: string;
}

// The VMs page (GET /api/vms): the cluster's VMs and containers, read-only.
export type VmSupport = "supported" | "override" | "unknown" | "unsupported";

export type IpSource = "agent" | "inventory" | "ipconfig0";

export interface Vm {
  vmid: number;
  name: string;
  node: string;
  type: "qemu" | "lxc";
  status: string;
  template: boolean;
  lock: string;
  tags: string[];
  cpu: number; // share of maxcpu in use, 0 to 1
  maxcpu: number;
  mem: number; // bytes
  maxmem: number;
  uptime: number;
  // ok, pending, off, stopped, not-running, forbidden, timeout, error, unknown, none (containers, templates)
  agent: string;
  addresses: string[];
  ip: string;
  ip_source: IpSource | null;
  os: string;
  hostname: string;
  support: VmSupport;
  reason: string;
  inventory_name: string;
  ours: boolean;
  name_ok: boolean;
  // Selecting it for a run: whether it can be, why not, and its host keys pinned by cluster and VM ID.
  selectable: boolean;
  why_not: string;
  alias: string;
  pinned: HostKey[];
}

export interface VmPreset {
  key: string;
  label: string;
  tags: string[] | null; // null: choose the tags
}

export interface VmRunInfo {
  presets: VmPreset[];
  default_preset: string;
  tags: string[];
  max_hosts: number;
  user: string; // the admin user, the default login for VMs outside the inventory
  ssh_port: number;
  busy: boolean; // a run is going
  key: boolean; // the web UI has an SSH key
}

export interface VmsData {
  version: string;
  vms: Vm[];
  complete: boolean;
  forbidden: number;
  notes: Notice[];
  cluster: string;
  fetched: number;
  run: VmRunInfo;
}

// POST /api/vms/plan: the plan to review before a run.
export interface VmPlanTarget extends Omit<RunTarget, "fingerprints"> {
  alias: string;
  fingerprints: string[];
  ufw: string[];
}

export interface VmPlan {
  preset: string;
  preset_label: string;
  tags: string[];
  targets: VmPlanTarget[];
  warnings: string[];
  digest: string;
}

// Passkeys (main.py /api/session and /api/account).
export interface PasskeyInfo {
  registered: boolean;
  required: boolean;
  rp_id: string;
  public_url: string;
}

export interface SessionData {
  configured: boolean;
  signed_in: boolean;
  csrf: string;
  version: string;
  theme: Theme;
  passkeys: PasskeyInfo;
}

export interface Passkey {
  id: string;
  name: string;
  created: number;
  last_used: number | null;
  backed_up: boolean;
}

// The status API's token for dashboards: never the token itself, which is shown once when it is made.
export interface StatusToken {
  exists: boolean;
  broken: boolean; // the file can't be read: no token works until it is revoked
  hint: string; // the first characters after usas_st_
  created: number | null;
  last_used: number | null;
  last_from: string;
}

export interface AccountData {
  passkeys: Passkey[];
  require_passkey: boolean;
  broken: boolean;
  rp_id: string;
  public_url: string;
  max_passkeys: number;
  max_name: number;
  current: string; // how this session signed in: "password", or the ID of the passkey it used
  status_token: StatusToken;
}
