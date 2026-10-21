// The VMs page: a read-only list of the cluster's VMs and containers, with their addresses and whether the
// playbooks support them. It asks the server again every 30 seconds while the tab is visible (sooner while
// guest agents are still answering, later after an error), and a refresh never reorders the rows or moves the
// focus: a new order comes only from the sort buttons.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "../api";
import { Icon } from "../components/Icons";
import type { IconName } from "../components/Icons";
import { Loading, Notice, PageHead } from "../components/Parts";
import { errorText } from "../data";
import { plural } from "../format";
import { Link } from "../router";
import type { IpSource, Vm, VmSupport, VmsData } from "../types";

export const POLL_MS = 30_000;
export const FILL_MS = 5_000; // while guest agents are still answering
export const BACKOFF_MS = 120_000; // after an error

export type SortKey = "name" | "vmid" | "node" | "status" | "cpu" | "mem" | "ip" | "support";
export interface Sort {
  key: SortKey;
  ascending: boolean;
}

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "vmid", label: "VMID" },
  { key: "node", label: "Node" },
  { key: "status", label: "Status" },
  { key: "cpu", label: "CPU" },
  { key: "mem", label: "Memory" },
  { key: "ip", label: "Address" },
  { key: "support", label: "Playbooks" },
];

const SUPPORT: Record<VmSupport, { label: string; badge: string; rank: number }> = {
  supported: { label: "Supported", badge: "success", rank: 0 },
  override: { label: "Untested release", badge: "warning", rank: 1 },
  unknown: { label: "Confirm the OS", badge: "info", rank: 2 },
  unsupported: { label: "Not supported", badge: "error", rank: 3 },
};

export const SOURCES: Record<IpSource, string> = {
  agent: "guest agent",
  inventory: "inventory",
  ipconfig0: "cloud-init settings",
};

const STATUS_ICONS: Record<string, IconName> = { running: "play", stopped: "stop", paused: "pause" };

// Why there is no guest-agent data, in words.
const AGENT_STATES: Record<string, string> = {
  pending: "Asking the guest agent…",
  off: "No guest agent configured",
  "not-running": "Guest agent not running",
  forbidden: "The token may not ask the guest agent",
  timeout: "Guest agent didn't answer in time",
  error: "Guest agent error",
  unknown: "Couldn't read the VM's config",
};

export function statusLabel(status: string): string {
  return status ? status[0].toUpperCase() + status.slice(1) : "Unknown";
}

export function bytes(n: number): string {
  const gib = n / 1024 ** 3;
  if (gib >= 1) return `${gib >= 10 ? Math.round(gib) : Math.round(gib * 10) / 10} GiB`;
  return `${Math.round(n / 1024 ** 2)} MiB`;
}

function ipKey(ip: string): number {
  if (!ip) return Number.POSITIVE_INFINITY;
  return ip.split(".").reduce((sum, part) => sum * 256 + Number(part), 0);
}

function sortValue(vm: Vm, key: SortKey): number | string {
  switch (key) {
    case "vmid": return vm.vmid;
    case "cpu": return vm.status === "running" ? vm.cpu : -1;
    case "mem": return vm.maxmem && vm.status === "running" ? vm.mem / vm.maxmem : -1;
    case "ip": return ipKey(vm.ip);
    case "support": return SUPPORT[vm.support]?.rank ?? 9;
    default: return String(vm[key]).toLowerCase();
  }
}

export function compareVms(a: Vm, b: Vm, sort: Sort): number {
  const x = sortValue(a, sort.key);
  const y = sortValue(b, sort.key);
  const order = typeof x === "number" && typeof y === "number" ? x - y
    : String(x).localeCompare(String(y), undefined, { numeric: true });
  return (sort.ascending ? order : -order) || a.vmid - b.vmid;
}

// The rows' order after a refresh: the VMs already shown keep their places, new ones go at the end in the
// current sort order, and VMs that are gone drop out.
export function arrange(previous: number[], vms: Vm[], sort: Sort): number[] {
  const present = new Set(vms.map((vm) => vm.vmid));
  const kept = previous.filter((id) => present.has(id));
  const known = new Set(kept);
  const added = vms.filter((vm) => !known.has(vm.vmid)).sort((a, b) => compareVms(a, b, sort)).map((vm) => vm.vmid);
  return [...kept, ...added];
}

interface Filters {
  text: string;
  node: string;
  status: string;
  support: string;
}

export function matches(vm: Vm, filters: Filters): boolean {
  const text = filters.text.trim().toLowerCase();
  if (text) {
    const haystack = [vm.name, String(vm.vmid), vm.ip, ...vm.addresses, vm.os, vm.inventory_name, ...vm.tags]
      .join(" ").toLowerCase();
    if (!text.split(/\s+/).every((word) => haystack.includes(word))) return false;
  }
  if (filters.node && vm.node !== filters.node) return false;
  if (filters.status && (filters.status === "other" ? ["running", "stopped"].includes(vm.status) : vm.status !== filters.status)) {
    return false;
  }
  return !filters.support || vm.support === filters.support;
}

interface Poll {
  data: VmsData | null;
  error: string | null;
  setup: string | null;
  busy: boolean;
  refresh: () => void;
}

// Loads /api/vms now and then again: every POLL_MS, FILL_MS while agents are still answering, BACKOFF_MS
// after an error, and not at all while the tab is hidden (it catches up when the tab shows again).
function useVms(): Poll {
  const [state, setState] = useState<{ data: VmsData | null; error: string | null; setup: string | null }>(
    { data: null, error: null, setup: null });
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const loading = useRef(false);
  const last = useRef(0);
  const next = useRef(POLL_MS);
  const live = useRef(true);

  const schedule = useCallback((ms: number, run: () => void) => {
    clearTimeout(timer.current);
    next.current = ms;
    if (!document.hidden) timer.current = setTimeout(run, ms);
  }, []);

  const load = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    setBusy(true);
    clearTimeout(timer.current);
    const reply = await get<VmsData & { error?: string; setup?: string }>("/api/vms");
    loading.current = false;
    if (!live.current) return;
    setBusy(false);
    last.current = Date.now();
    if (reply.ok) {
      setState({ data: reply.data, error: null, setup: null });
      schedule(reply.data.complete ? POLL_MS : FILL_MS, load);
    } else if (reply.status !== 401) {
      setState((s) => ({ ...s, error: errorText(reply.data, reply.status), setup: reply.data.setup ?? null }));
      schedule(BACKOFF_MS, load);
    }
  }, [schedule]);

  useEffect(() => {
    live.current = true;
    load();
    const onVisibility = () => {
      clearTimeout(timer.current);
      if (document.hidden) return;
      const due = next.current - (Date.now() - last.current);
      if (due <= 0) load();
      else timer.current = setTimeout(load, due);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      live.current = false;
      clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  return { ...state, busy, refresh: load };
}

function Usage({ id, label, value, max, text }: { id: string; label: string; value: number; max: number; text: string }) {
  return (
    <span className="usage">
      <meter id={id} min={0} max={max} value={Math.min(value, max)} aria-label={label} />
      <span className="usage-text">{text}</span>
    </span>
  );
}

function VmRow({ vm }: { vm: Vm }) {
  const running = vm.status === "running";
  const support = SUPPORT[vm.support] ?? SUPPORT.unknown;
  const kind = vm.type === "lxc" ? "container" : vm.template ? "template" : null;
  const others = vm.addresses.filter((a) => a !== vm.ip);
  const agentNote = AGENT_STATES[vm.agent];
  return (
    <tr>
      <th scope="row" data-label="Name">
        <span className="vm-name">{vm.name || `VM ${vm.vmid}`}</span>
        {kind ? <span className="sub">{kind === "container" ? "Container (LXC)" : "Template"}</span> : null}
        {vm.inventory_name && vm.inventory_name !== vm.name ? (
          <span className="sub">Inventory: {vm.inventory_name}</span>
        ) : null}
        {vm.hostname && vm.hostname !== vm.name ? <span className="sub">Host name: {vm.hostname}</span> : null}
        {vm.ours ? <span className="sub">Installed by USAS</span> : null}
        {vm.lock ? <span className="sub">Locked: {vm.lock}</span> : null}
      </th>
      <td data-label="VMID" className="mono">{vm.vmid}</td>
      <td data-label="Node" className="nowrap">{vm.node}</td>
      <td data-label="Status">
        <div className="cell">
          <span className={`vm-status vm-status-${STATUS_ICONS[vm.status] ? vm.status : "other"}`}>
            <Icon name={STATUS_ICONS[vm.status] ?? "alert"} />
            {statusLabel(vm.status)}
          </span>
          {running && agentNote ? <span className="sub">{agentNote}</span> : null}
        </div>
      </td>
      <td data-label="CPU">
        {running ? (
          <Usage id={`cpu-${vm.vmid}`} label={`CPU use of ${vm.name || vm.vmid}`} value={vm.cpu} max={1}
                 text={`${Math.round(vm.cpu * 100)} % of ${plural(vm.maxcpu, "vCPU", "vCPUs")}`} />
        ) : <span className="muted">—</span>}
      </td>
      <td data-label="Memory">
        {running && vm.maxmem ? (
          <Usage id={`mem-${vm.vmid}`} label={`Memory use of ${vm.name || vm.vmid}`} value={vm.mem} max={vm.maxmem}
                 text={`${bytes(vm.mem)} of ${bytes(vm.maxmem)}`} />
        ) : <span className="muted">—</span>}
      </td>
      <td data-label="Address">
        {vm.ip ? (
          <div className="cell">
            <span className="mono">{vm.ip}</span>
            <span className="sub">from the {vm.ip_source ? SOURCES[vm.ip_source] : "?"}</span>
            {others.length ? <span className="sub">Also {others.join(", ")}</span> : null}
          </div>
        ) : <span className="muted">Not known</span>}
      </td>
      <td data-label="Playbooks" className="reason">
        <div className="cell">
          <span className={`badge badge-${support.badge}`}>{support.label}</span>
          <span className="sub">{vm.reason}</span>
        </div>
      </td>
    </tr>
  );
}

function SortButton({ column, sort, onSort }: { column: (typeof COLUMNS)[number]; sort: Sort; onSort: (key: SortKey) => void }) {
  const active = sort.key === column.key;
  return (
    <button type="button" className="sort" onClick={() => onSort(column.key)}>
      {column.label}
      <Icon name={active ? (sort.ascending ? "sort-up" : "sort-down") : "sort"} />
    </button>
  );
}

const NO_FILTERS: Filters = { text: "", node: "", status: "", support: "" };

export function Vms() {
  const { data, error, setup, busy, refresh } = useVms();
  const [sort, setSort] = useState<Sort>({ key: "vmid", ascending: true });
  const [order, setOrder] = useState<number[]>([]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);

  // A refresh keeps the rows where they are; a click on a sort button sorts them again.
  const arranged = useMemo(() => (data ? arrange(order, data.vms, sort) : []), [order, data, sort]);
  useEffect(() => {
    if (arranged.join() !== order.join()) setOrder(arranged);
  }, [arranged, order]);

  const onSort = (key: SortKey) => {
    const next = { key, ascending: sort.key === key ? !sort.ascending : true };
    setSort(next);
    if (data) setOrder([...data.vms].sort((a, b) => compareVms(a, b, next)).map((vm) => vm.vmid));
  };

  const byId = useMemo(() => new Map((data?.vms ?? []).map((vm) => [vm.vmid, vm])), [data]);
  const rows = arranged.map((id) => byId.get(id)).filter((vm): vm is Vm => !!vm);
  const shown = rows.filter((vm) => matches(vm, filters));
  const nodes = [...new Set((data?.vms ?? []).map((vm) => vm.node))].sort();
  const set = (name: keyof Filters) => (event: { target: { value: string } }) =>
    setFilters({ ...filters, [name]: event.target.value });
  const filtered = Object.values(filters).some(Boolean);
  const sortedBy = COLUMNS.find((c) => c.key === sort.key)?.label ?? "";

  const actions = (
    <button type="button" className="button secondary" onClick={refresh} aria-disabled={busy || undefined}>
      <Icon name="refresh" />{busy ? "Refreshing…" : "Refresh"}
    </button>
  );
  const crumb = data ? `Playbooks · ${data.cluster ? `cluster ${data.cluster} · ` : ""}Proxmox VE ${data.version || "?"}` : "Playbooks";

  if (!data) {
    return (
      <>
        <PageHead title="VMs" crumb="Playbooks" actions={actions} />
        <Loading error={error} />
        {setup ? <p><Link href={setup}>Open the Proxmox page</Link></p> : null}
      </>
    );
  }
  return (
    <>
      <PageHead title="VMs" crumb={crumb} actions={actions} />
      <p className="lead">
        Every VM and container on the cluster, with the address the playbooks would use and whether they support
        it. The list refreshes every 30 seconds while this page is open. Selecting VMs to run the baseline
        against them comes in a later release.
      </p>
      {error ? <Notice notice={{ kind: "error", message: `The last refresh failed: ${error}` }} /> : null}
      {data.notes.map((note) => <Notice key={note.message} notice={note} />)}
      <form className="card filters" role="search" aria-label="Filter the VMs" onSubmit={(e) => e.preventDefault()}>
        <div className="card-body fields">
          <div className="field">
            <label htmlFor="vm-filter">Find</label>
            <input type="search" id="vm-filter" value={filters.text} onChange={set("text")} autoComplete="off"
                   spellCheck={false} aria-describedby="vm-filter-help" />
            <p className="help" id="vm-filter-help">Name, VMID, address, OS or tag</p>
          </div>
          <div className="field">
            <label htmlFor="vm-node">Node</label>
            <div className="select">
              <select id="vm-node" value={filters.node} onChange={set("node")}>
                <option value="">All nodes</option>
                {nodes.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="vm-status">Status</label>
            <div className="select">
              <select id="vm-status" value={filters.status} onChange={set("status")}>
                <option value="">Any status</option>
                <option value="running">Running</option>
                <option value="stopped">Stopped</option>
                <option value="other">Other</option>
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="vm-support">Playbook support</label>
            <div className="select">
              <select id="vm-support" value={filters.support} onChange={set("support")}>
                <option value="">Any</option>
                {(Object.keys(SUPPORT) as VmSupport[]).map((k) => <option key={k} value={k}>{SUPPORT[k].label}</option>)}
              </select>
            </div>
          </div>
        </div>
        {filtered ? (
          <div className="card-foot">
            <button type="button" className="button secondary small" onClick={() => setFilters(NO_FILTERS)}>
              Clear the filters
            </button>
          </div>
        ) : null}
      </form>
      <p className="count" role="status" id="vm-count">
        {filtered ? `${shown.length} of ${plural(rows.length, "VM", "VMs")} shown` : plural(rows.length, "VM", "VMs")}
      </p>
      {rows.length === 0 ? <p>The cluster has no VMs or containers.</p> : (
        <div className="card table-wrap" tabIndex={0} role="region" aria-labelledby="vms-caption">
          <table className="stacked vms">
            <caption id="vms-caption">
              VMs and containers{data.cluster ? ` on cluster ${data.cluster}` : ""}
              <span className="visually-hidden">, sorted by {sortedBy}, {sort.ascending ? "ascending" : "descending"}</span>
            </caption>
            <thead>
              <tr>
                {COLUMNS.map((column) => (
                  <th key={column.key} scope="col"
                      aria-sort={sort.key === column.key ? (sort.ascending ? "ascending" : "descending") : undefined}>
                    <SortButton column={column} sort={sort} onSort={onSort} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((vm) => <VmRow key={vm.vmid} vm={vm} />)}
            </tbody>
          </table>
          {shown.length === 0 ? <p className="card-body">No VM matches the filters.</p> : null}
        </div>
      )}
    </>
  );
}
