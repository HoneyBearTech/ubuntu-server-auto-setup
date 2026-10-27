// Running the baseline against the VMs selected on the VMs page: what to run, each VM's settings and pinned
// host keys, then a review of the plan the server checked, then a check run (--check --diff, nothing changes).
// The server checks everything again and builds the run from its own listing of the cluster; this part only
// collects the choices. The review is a section of the page, not a dialog, and takes the focus when it appears.

import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { post } from "../api";
import { FieldError } from "../components/Field";
import { Icon } from "../components/Icons";
import { ErrorMessage, ErrorSummary, Notice } from "../components/Parts";
import { plural } from "../format";
import { Link, useRouter } from "../router";
import type { Errors, HostKey, Notice as NoticeT, Vm, VmPlan, VmRunInfo } from "../types";

export interface VmSettings {
  address: string; // one of the VM's known addresses, or "manual"
  manual: string;
  port: string;
  user: string;
  ufw: string;
  hostname: boolean;
  override: boolean;
}

const MANUAL = "manual";

const SOURCE_WORDS: Record<string, string> = {
  agent: "guest agent",
  ipconfig0: "cloud-init settings",
  inventory: "inventory",
  manual: "typed in",
  dhcp: "guest agent, at run time",
};

// The addresses a VM outside the inventory can be reached on: the guest agent's, then cloud-init's.
export function addressChoices(vm: Vm): string[] {
  return [...new Set([...vm.addresses, ...(vm.ip && vm.ip_source === "ipconfig0" ? [vm.ip] : [])])];
}

export function defaultSettings(vm: Vm, user: string): VmSettings {
  const choices = addressChoices(vm);
  return { address: choices[0] ?? MANUAL, manual: "", port: "", user, ufw: "", hostname: false, override: false };
}

export function chosenAddress(s: VmSettings): string {
  return s.address === MANUAL ? s.manual.trim() : s.address;
}

// The form the server reads (POST /api/vms/plan): vmid once per VM, and each VM's settings as vm-<vmid>-<name>.
export function planForm(vms: Vm[], settings: Record<number, VmSettings>, preset: string, tags: string[]): FormData {
  const form = new FormData();
  form.append("preset", preset);
  if (preset === "custom") for (const tag of tags) form.append("tags", tag);
  for (const vm of vms) {
    form.append("vmid", String(vm.vmid));
    const s = settings[vm.vmid];
    if (!s) continue;
    const prefix = `vm-${vm.vmid}-`;
    if (s.override) form.append(`${prefix}override`, "on");
    if (vm.inventory_name) continue; // a server runs with its own settings
    form.append(`${prefix}address`, chosenAddress(s));
    form.append(`${prefix}port`, s.port.trim());
    form.append(`${prefix}user`, s.user.trim());
    form.append(`${prefix}ufw`, s.ufw.trim());
    if (s.hostname) form.append(`${prefix}hostname`, "on");
  }
  return form;
}

function Keys({ keys }: { keys: HostKey[] }) {
  return (
    <ul className="plain-list">
      {keys.map((k) => <li key={k.fingerprint}>{k.keytype} <code className="fp">{k.fingerprint}</code></li>)}
    </ul>
  );
}

function Status({ notice }: { notice?: NoticeT }) {
  return <div aria-live="polite">{notice ? <Notice notice={notice} /> : null}</div>;
}

function describedBy(id: string, help: unknown, error?: string): string | undefined {
  return [help ? `${id}-help` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
}

interface KeyState {
  scanned?: HostKey[];
  confirm?: boolean;
  notice?: NoticeT;
}

function Text({ id, label, value, onChange, help, error, className, autoComplete = "off" }: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  help?: ReactNode;
  error?: string;
  className?: string;
  autoComplete?: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input type="text" id={id} value={value} onChange={(e) => onChange(e.target.value)} className={className}
             spellCheck={false} autoComplete={autoComplete} aria-describedby={describedBy(id, help, error)}
             aria-invalid={error ? true : undefined} />
      {help ? <p className="help" id={`${id}-help`}>{help}</p> : null}
      <FieldError id={id} error={error} />
    </div>
  );
}

function Check({ id, label, checked, onChange, help, error }: {
  id: string;
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help?: ReactNode;
  error?: string;
}) {
  return (
    <div className="check-field">
      <div className="check">
        <input type="checkbox" id={id} checked={checked} onChange={(e) => onChange(e.target.checked)}
               aria-describedby={describedBy(id, help, error)} aria-invalid={error ? true : undefined} />
        <label htmlFor={id}>{label}</label>
      </div>
      {help ? <p className="help" id={`${id}-help`}>{help}</p> : null}
      <FieldError id={id} error={error} />
    </div>
  );
}

function VmPart({ vm, settings, set, errors, keyState, setKeyState, pinned, onPinned, info }: {
  vm: Vm;
  settings: VmSettings;
  set: (change: Partial<VmSettings>) => void;
  errors: Errors;
  keyState: KeyState;
  setKeyState: (state: KeyState) => void;
  pinned: HostKey[];
  onPinned: (keys: HostKey[]) => void;
  info: VmRunInfo;
}) {
  const id = (name: string) => `f-vm-${vm.vmid}-${name}`;
  const err = (name: string) => errors[`vm-${vm.vmid}-${name}`];
  const label = vm.name || `VM ${vm.vmid}`;
  const heading = `vm-${vm.vmid}-heading`;
  const server = vm.inventory_name;
  const choices = addressChoices(vm);
  const address = server ? vm.ip : chosenAddress(settings);
  const needsOverride = vm.support === "override" || (vm.support === "unknown" && !server);
  const [busy, setBusy] = useState(false);

  const hostkey = async (action: string, extra: Record<string, string> = {}) => {
    if (busy) return;
    setBusy(true);
    const reply = await post<{ error?: string; message?: string; scanned?: HostKey[]; pinned?: HostKey[] }>(
      "/api/vms/hostkey", { action, vmid: String(vm.vmid), address, port: server ? "" : settings.port.trim(),
                            user: settings.user.trim(), ...extra });
    setBusy(false);
    if (!reply.ok) {
      setKeyState({ ...keyState, notice: { kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` } });
      return;
    }
    if (action === "scan") {
      setKeyState({ scanned: reply.data.scanned ?? [], confirm: false });
    } else {
      if (action === "trust") onPinned(reply.data.pinned ?? []);
      if (action === "forget") onPinned([]);
      setKeyState({ notice: { kind: "ok", message: reply.data.message ?? "Done." } });
    }
  };

  return (
    <fieldset className="card-body fieldset vm-part" aria-labelledby={heading}>
      <h3 id={heading}>{label} <span className="sub">VM {vm.vmid} on {vm.node}</span></h3>
      {server ? (
        <p className="help">
          Server <code>{server}</code> on the <Link href="/servers">Servers</Link> page: it runs with its own address
          {vm.ip ? <> (<code>{vm.ip}</code>)</> : null}, login user and sudo password, as from the Run page.
        </p>
      ) : (
        <div className="fields">
          <div className="field">
            <label htmlFor={id("address")}>Address</label>
            <div className="select">
              <select id={id("address")} value={settings.address} onChange={(e) => set({ address: e.target.value })}
                      aria-describedby={describedBy(id("address"), true, err("address"))}
                      aria-invalid={err("address") ? true : undefined}>
                {choices.map((a) => (
                  <option key={a} value={a}>{a} ({vm.addresses.includes(a) ? "guest agent" : "cloud-init settings"})</option>
                ))}
                <option value={MANUAL}>Another address…</option>
              </select>
            </div>
            <p className="help" id={`${id("address")}-help`}>
              The guest reports its own addresses: a wrong one only gets a refused connection, because the host key is
              pinned to this VM.
            </p>
            <FieldError id={id("address")} error={err("address")} />
          </div>
          {settings.address === MANUAL ? (
            <Text id={id("address-manual")} label="IPv4 address" value={settings.manual} className="mono"
                  onChange={(manual) => set({ manual })} />
          ) : null}
          <Text id={id("port")} label="SSH port" value={settings.port} className="mono narrow"
                onChange={(port) => set({ port })} error={err("port")}
                help={<>Empty: the baseline's port ({info.ssh_port}), then 22.</>} />
          <Text id={id("user")} label="Login user" value={settings.user} className="mono"
                onChange={(user) => set({ user })} error={err("user")}
                help="It must accept the web UI's key and have passwordless sudo: VMs outside the inventory never get the admin password." />
          <Text id={id("ufw")} label="Extra firewall rules" value={settings.ufw} className="mono"
                onChange={(ufw) => set({ ufw })} error={err("ufw")}
                help="What this VM serves besides SSH, separated by commas: such as 443/tcp, 5432/tcp from 10.0.0.0/24." />
          <Check id={id("hostname")} checked={settings.hostname} onChange={(hostname) => set({ hostname })}
                 error={err("hostname")} label={<>Set its host name to <code>{vm.name}</code></>}
                 help="Off: the host name stays as it is." />
        </div>
      )}
      {needsOverride ? (
        <Check id={id("override")} checked={settings.override} onChange={(override) => set({ override })}
               error={err("override")} label="It runs Ubuntu Server: run the baseline anyway"
               help={vm.reason} />
      ) : null}

      <h4 id={id("hostkey")} tabIndex={-1}>Host key</h4>
      <FieldError id={id("hostkey")} error={err("hostkey")} />
      {pinned.length ? (
        <>
          <p>Pinned to VM {vm.vmid}:</p>
          <Keys keys={pinned} />
        </>
      ) : (
        <p>
          Not pinned yet: the run refuses to connect until it is.
          {server ? " When you review the plan, the key recorded when the project installed it is used, if there is one." : ""}
        </p>
      )}
      {keyState.scanned ? (
        <div className="confirm">
          <p>
            {address} offers these host keys. <strong>Compare them</strong> with what the VM itself shows (on its
            console in Proxmox: <code>{'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done'}</code>)
            before you pin them; a mismatch means another machine answered.
          </p>
          <Keys keys={keyState.scanned} />
          <Check id={`confirm-${vm.vmid}`} checked={!!keyState.confirm}
                 onChange={(confirm) => setKeyState({ ...keyState, confirm })}
                 label={`The fingerprints match what ${label} shows`} />
          <div className="actions">
            <button type="button" className="button" aria-disabled={busy || undefined}
                    onClick={() => hostkey("trust", keyState.confirm ? { confirm: "on" } : {})}>
              Pin these keys
            </button>
          </div>
        </div>
      ) : null}
      <div className="actions">
        <button type="button" className="button secondary small" aria-disabled={busy || !address || undefined}
                onClick={() => address && hostkey("scan")}>
          {pinned.length || keyState.scanned ? "Scan again" : "Scan the host key"}
        </button>
        {pinned.length ? (
          <>
            {server ? null : (
              <button type="button" className="button secondary small" aria-disabled={busy || undefined}
                      onClick={() => hostkey("test")}>Test the login</button>
            )}
            <button type="button" className="button secondary small" aria-disabled={busy || undefined}
                    onClick={() => hostkey("forget")}>Forget the host key</button>
          </>
        ) : null}
      </div>
      <Status notice={keyState.notice} />
    </fieldset>
  );
}

interface Reviewed {
  id: string;
  plan: VmPlan;
}

function Review({ reviewed, busy, onRun }: { reviewed: Reviewed; busy: boolean; onRun: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), [reviewed]);
  const { plan } = reviewed;
  return (
    <section className="card review" aria-labelledby="vm-review-heading">
      <div className="card-body">
        <h2 id="vm-review-heading" tabIndex={-1} ref={heading}>Review: {plan.preset_label}</h2>
        <p>
          The check run (<code>--check --diff</code>) changes nothing: it reports what a real run would change on
          {" "}{plural(plan.targets.length, "VM", "VMs")}. If it passes, its page offers the real run of the same
          plan for 30 minutes.
        </p>
        {plan.warnings.length ? (
          <div className="notice notice-warning">
            <Icon name="alert" />
            <div>
              <p>On servers already in use, look out for:</p>
              <ul>{plan.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          </div>
        ) : null}
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="vm-review-caption">
        <table className="stacked">
          <caption id="vm-review-caption">The hosts of this run</caption>
          <thead>
            <tr>
              <th scope="col">Host</th>
              <th scope="col">VM</th>
              <th scope="col">Address</th>
              <th scope="col">Login</th>
              <th scope="col">Host key</th>
            </tr>
          </thead>
          <tbody>
            {plan.targets.map((t) => (
              <tr key={t.vmid}>
                <th scope="row" data-label="Host"><code>{t.name}</code>{t.static ? <span className="sub">Server in the inventory</span> : null}</th>
                <td data-label="VM">{t.pve_name || "?"} ({t.vmid}, {t.node})</td>
                <td data-label="Address">
                  {t.address ? <span className="mono">{t.address}{t.port ? `:${t.port}` : ""}</span> : "From the guest agent"}
                  <span className="sub">from the {SOURCE_WORDS[t.source] ?? t.source}</span>
                </td>
                <td data-label="Login">{t.static ? "Its own" : <code>{t.user}</code>}</td>
                <td data-label="Host key">{t.fingerprints.map((f) => <code key={f} className="fp">{f}</code>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-foot">
        <button type="button" className="button" aria-disabled={busy || undefined} onClick={onRun}>
          <Icon name="play" />Run the check (nothing changes)
        </button>
      </div>
    </section>
  );
}

export function VmRun({ vms, dropped, info, fetched, refresh }: {
  vms: Vm[]; // the selected VMs that can still be selected
  dropped: Vm[]; // selected, but can't be any more
  info: VmRunInfo;
  fetched: number; // when the listing was made
  refresh: () => void;
}) {
  const { navigate } = useRouter();
  const [preset, setPreset] = useState(info.default_preset);
  const [tags, setTags] = useState<string[]>([]);
  const [settings, setSettings] = useState<Record<number, VmSettings>>({});
  const [keyStates, setKeyStates] = useState<Record<number, KeyState>>({});
  const [pins, setPins] = useState<Record<number, HostKey[]>>({});
  const [errors, setErrors] = useState<Errors>({});
  const [error, setError] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<Reviewed | null>(null);
  const [busy, setBusy] = useState(false);

  // A newer listing has the pins; until it comes, the ones just pinned or forgotten here.
  useEffect(() => setPins({}), [fetched]);
  const current = (vm: Vm) => settings[vm.vmid] ?? defaultSettings(vm, info.user);
  const stale = () => setReviewed(null);
  const change = (vm: Vm) => (part: Partial<VmSettings>) => {
    setSettings((all) => ({ ...all, [vm.vmid]: { ...(all[vm.vmid] ?? defaultSettings(vm, info.user)), ...part } }));
    stale();
  };

  const review = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const all = Object.fromEntries(vms.map((vm) => [vm.vmid, current(vm)]));
    const reply = await post<{ plan_id?: string; plan?: VmPlan; errors?: Errors; error?: string }>(
      "/api/vms/plan", planForm(vms, all, preset, tags));
    setBusy(false);
    if (reply.ok && reply.data.plan_id && reply.data.plan) {
      setErrors({});
      setError(null);
      setReviewed({ id: reply.data.plan_id, plan: reply.data.plan });
      refresh(); // shows the pins made from the installer's keys
    } else {
      setReviewed(null);
      setErrors(reply.data.errors ?? {});
      setError(reply.data.errors ? null : reply.data.error ?? `The server answered ${reply.status}.`);
    }
  };

  const run = async () => {
    if (busy || !reviewed) return;
    setBusy(true);
    const reply = await post<{ redirect?: string; error?: string }>("/api/vms/run", { plan_id: reviewed.id, mode: "check" });
    setBusy(false);
    if (reply.ok && reply.data.redirect) navigate(reply.data.redirect);
    else setError(reply.data.error ?? `The server answered ${reply.status}.`);
  };

  const custom = info.presets.find((p) => p.key === preset)?.tags === null;
  return (
    <>
      <section className="card" aria-labelledby="vm-run-heading">
        <form noValidate onSubmit={review}>
          <div className="card-body">
            <h2 id="vm-run-heading">Run the baseline on {plural(vms.length, "selected VM", "selected VMs")}</h2>
            <p className="lead">
              First a check run, which changes nothing and shows what would change. Each VM's host key must be pinned
              first. At most {info.max_hosts} VMs per run.
            </p>
            {!info.key ? (
              <Notice notice={{ kind: "warning", message: "The web UI has no SSH key yet: create one on the SSH key page first." }} />
            ) : null}
            {info.busy ? <Notice notice={{ kind: "info", message: "A run is going: only one runs at a time." }} /> : null}
            {dropped.length ? (
              <Notice notice={{ kind: "warning", message: `Left out, because they can't be selected any more: ${
                dropped.map((vm) => `${vm.name || vm.vmid} (${vm.why_not})`).join("; ")}` }} />
            ) : null}
            <ErrorSummary errors={errors} id="vm-errors" />
            <ErrorMessage message={error} id="vm-error" />
          </div>
          <fieldset className="card-body fieldset">
            <legend>What to run</legend>
            <div className="choices">
              {info.presets.map((p, i) => (
                <div className="choice" key={p.key}>
                  <input type="radio" id={i === 0 ? "f-preset" : `preset-${p.key}`} name="preset" value={p.key}
                         checked={preset === p.key} aria-describedby={`preset-${p.key}-help`}
                         onChange={() => { setPreset(p.key); stale(); }} />
                  <div>
                    <label htmlFor={i === 0 ? "f-preset" : `preset-${p.key}`}>{p.label}</label>
                    <p className="help" id={`preset-${p.key}-help`}>
                      {p.tags === null ? "Choose the parts below." : p.tags.length
                        ? <>Tags <code>{p.tags.join(", ")}</code></> : "Every role of the baseline."}
                    </p>
                  </div>
                </div>
              ))}
            </div>
            {custom ? (
              <fieldset className="fieldset nested">
                <legend>Parts of the baseline</legend>
                <FieldError id="f-tags" error={errors.tags} />
                <div className="choices">
                  {info.tags.map((t, i) => (
                    <div className="check" key={t}>
                      <input type="checkbox" id={i === 0 ? "f-tags" : `tag-${t}`} checked={tags.includes(t)}
                             onChange={(e) => {
                               setTags(e.target.checked ? [...tags, t] : tags.filter((x) => x !== t));
                               stale();
                             }} />
                      <label htmlFor={i === 0 ? "f-tags" : `tag-${t}`}><code>{t}</code></label>
                    </div>
                  ))}
                </div>
              </fieldset>
            ) : null}
          </fieldset>
          {vms.map((vm) => (
            <VmPart key={vm.vmid} vm={vm} settings={current(vm)} set={change(vm)} errors={errors} info={info}
                    keyState={keyStates[vm.vmid] ?? {}} pinned={pins[vm.vmid] ?? vm.pinned}
                    setKeyState={(state) => setKeyStates((all) => ({ ...all, [vm.vmid]: state }))}
                    onPinned={(keys) => { setPins((all) => ({ ...all, [vm.vmid]: keys })); stale(); refresh(); }} />
          ))}
          <div className="card-foot">
            <button type="submit" className="button" aria-disabled={busy || undefined}>Review the plan</button>
          </div>
        </form>
      </section>
      {reviewed ? <Review reviewed={reviewed} busy={busy} onRun={run} /> : null}
    </>
  );
}
