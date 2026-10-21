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

export interface RunPageData {
  playbooks: Playbook[];
  names: string[];
  steps: Step[];
  current_run: Run | null;
}

export interface RunDetailData {
  run: Run;
  log: string;
  offset: number;
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

export interface SshData {
  public_key: string;
  key_info: KeyInfo | null;
  node_address: string;
  trusted: HostKey[];
  pending: HostKey[] | null;
  pve_node: string;
}

export interface ServersData {
  columns: FieldMeta[];
  rows: Record<string, string>[];
}

export interface Notice {
  kind: "ok" | "warning" | "error" | "info";
  message: string;
}
