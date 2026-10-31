// What the updates playbook says of each server (the summary callback's "updates", from the playbook's report):
// its status, the packages it would install or installed, the reboot, the snapshot and its wave. Servers report
// their own packages: the callback keeps only names that look like one, and they are shown as text only.

import { plural } from "../format";
import type { UpdateResult, UpdateStatus } from "../types";
import { Icon } from "./Icons";
import type { IconName } from "./Icons";

const STATUSES: Record<UpdateStatus, { word: string; icon: IconName; kind: string }> = {
  updated: { word: "Updated", icon: "check", kind: "success" },
  nothing: { word: "Up to date", icon: "check", kind: "success" },
  pending: { word: "Updates waiting", icon: "info", kind: "warning" },
  busy: { word: "Busy", icon: "history", kind: "info" },
  skipped: { word: "Skipped", icon: "info", kind: "info" },
  unsupported: { word: "Not supported", icon: "alert", kind: "warning" },
  unreachable: { word: "Unreachable", icon: "alert", kind: "error" },
  failed: { word: "Failed", icon: "alert", kind: "error" },
};

export function updateStatus(r: UpdateResult): { word: string; icon: IconName; kind: string } {
  return STATUSES[r.status] ?? STATUSES.failed;
}

export function rebootText(r: UpdateResult, check: boolean): string {
  if (r.reboot.done) return "Rebooted";
  if (r.reboot.held) return `Held: ${r.reboot.why}`;
  if (r.reboot.required) return check ? "Reboot waiting" : "Reboot pending";
  return "None needed";
}

function List({ summary, items }: { summary: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <details>
      <summary>{summary}</summary>
      <ul className="plain-list mono">{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </details>
  );
}

function Packages({ r, check }: { r: UpdateResult; check: boolean }) {
  if (r.upgraded.length) {
    return (
      <div className="cell">
        <List summary={`${plural(r.upgraded.length, "package", "packages")} upgraded`}
              items={r.upgraded.map((p) => `${p.name} ${p.from} → ${p.to}`)} />
        {r.security_count ? <span className="sub">{plural(r.security_count, "security update", "security updates")}</span> : null}
      </div>
    );
  }
  const waiting = r.pending.filter((p) => r.planned.length === 0 || r.planned.includes(p.name));
  return (
    <div className="cell">
      {waiting.length ? (
        <List summary={`${plural(waiting.length, "package", "packages")} ${check ? "to install" : "not installed"}`}
              items={waiting.map((p) => `${p.name} ${p.current} → ${p.candidate}${p.security === true ? " (security)" : ""}`)} />
      ) : <span>{r.status === "nothing" || r.status === "updated" ? "Nothing to install" : "—"}</span>}
      {r.security_count ? <span className="sub">{plural(r.security_count, "security update", "security updates")}</span> : null}
      <List summary={`${plural(r.held.length, "held package", "held packages")}`} items={r.held} />
      <List summary={`${plural(r.deferred.length, "deferred package", "deferred packages")} (phased or kept back)`}
            items={r.deferred} />
      <List summary={`${plural(r.excluded.length, "excluded package", "excluded packages")}`} items={r.excluded} />
    </div>
  );
}

function snapshotText(r: UpdateResult): string {
  const s = r.snapshot;
  if (s.status === "taken") return `Taken: ${s.name}`;
  if (s.status === "removed") return `Taken, then deleted: ${s.name}`;
  if (s.status === "none") return `None: ${s.reason}`;
  return "Off";
}

export function UpdateResults({ hosts, check }: { hosts: Record<string, UpdateResult>; check: boolean }) {
  const names = Object.keys(hosts).sort();
  if (!names.length) return null;
  return (
    <section className="card" aria-labelledby="updates-results-heading">
      <div className="card-body">
        <h2 id="updates-results-heading">Updates per server</h2>
        {check ? <p className="help">A check run installs nothing: the packages listed are what a real run would install.</p> : null}
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="updates-results-caption">
        <table className="stacked hosts">
          <caption id="updates-results-caption" className="visually-hidden">
            What the updates playbook says of each server, {plural(names.length, "server", "servers")}
          </caption>
          <thead>
            <tr>
              <th scope="col">Server</th>
              <th scope="col">Result</th>
              <th scope="col">Packages</th>
              <th scope="col">Reboot</th>
              <th scope="col">Snapshot</th>
              <th scope="col">Wave</th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const r = hosts[name];
              const s = updateStatus(r);
              return (
                <tr key={name}>
                  <th scope="row" data-label="Server">
                    <code>{name}</code>
                    {r.os ? <span className="sub">{r.os}</span> : null}
                  </th>
                  <td data-label="Result">
                    <div className="cell">
                      <span className={`badge badge-${s.kind}`}><Icon name={s.icon} />{s.word}</span>
                      {r.reason ? <span className="sub">{r.reason}</span> : null}
                      {r.health.status && r.health.status !== "skipped" ? (
                        <span className="sub">Health: {r.health.status}{r.health.problems.length ? ` (${r.health.problems.join("; ")})` : ""}</span>
                      ) : null}
                    </div>
                  </td>
                  <td data-label="Packages"><Packages r={r} check={check} /></td>
                  <td data-label="Reboot">
                    {rebootText(r, check)}
                    {r.restart_services.length ? (
                      <List summary={`${plural(r.restart_services.length, "service", "services")} to restart`}
                            items={r.restart_services} />
                    ) : null}
                  </td>
                  <td data-label="Snapshot">
                    {snapshotText(r)}
                    {r.snapshot.warning ? <span className="sub">{r.snapshot.warning}</span> : null}
                  </td>
                  <td data-label="Wave">
                    {r.wave.number ? `${r.wave.number} of ${r.wave.of}` : "—"}
                    {r.wave.groups.length ? <span className="sub">Groups: {r.wave.groups.join(", ")}</span> : null}
                    {r.wave.ha ? <span className="sub">Managed by Proxmox HA</span> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
