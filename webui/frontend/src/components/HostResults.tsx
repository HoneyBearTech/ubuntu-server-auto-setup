// A run's results per host, from the summary callback (callback_plugins/usas_summary.py): what each host did,
// and the tasks that failed. It asks the server again every few seconds while the run goes. Hosts report their
// own messages: they are shown as text only.

import { useEffect, useState } from "react";
import { get } from "../api";
import { plural } from "../format";
import type { HostResult, HostResults as Results, RunTarget } from "../types";
import { Icon } from "./Icons";
import type { IconName } from "./Icons";

export const HOSTS_POLL_MS = 3_000;

const EMPTY: HostResult = {
  ok: 0, changed: 0, failed: 0, unreachable: 0, skipped: 0, rescued: 0, ignored: 0, changed_tasks: [], failed_tasks: [],
};

export function outcome(result: HostResult | undefined, check: boolean, done: boolean): { word: string; icon: IconName; kind: string } {
  if (!result) return done ? { word: "Not reached", icon: "alert", kind: "warning" } : { word: "Waiting", icon: "history", kind: "info" };
  if (result.unreachable) return { word: "Unreachable", icon: "alert", kind: "error" };
  if (result.failed) return { word: "Failed", icon: "alert", kind: "error" };
  if (result.changed) return { word: check ? "Would change" : "Changed", icon: "info", kind: "warning" };
  return { word: done ? "No changes" : "No changes so far", icon: "check", kind: "success" };
}

export function HostResults({ runId, initial, targets, running, check }: {
  runId: string;
  initial: Results | null;
  targets: RunTarget[];
  running: boolean;
  check: boolean;
}) {
  const [results, setResults] = useState<Results | null>(initial);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const reply = await get<{ hosts: Results | null }>(`/api/runs/${encodeURIComponent(runId)}/hosts`);
      if (!live) return;
      if (reply.ok && reply.data.hosts) setResults(reply.data.hosts);
      if (running) timer = setTimeout(load, HOSTS_POLL_MS);
    };
    if (running) timer = setTimeout(load, HOSTS_POLL_MS);
    else load();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [runId, running]);

  const hosts = results?.hosts ?? {};
  const names = [...new Set([...targets.map((t) => t.name), ...Object.keys(hosts).sort()])];
  if (!names.length) return null;
  const byName = new Map(targets.map((t) => [t.name, t]));
  const done = !running;
  return (
    <section className="card" aria-labelledby="hosts-heading">
      <div className="card-body">
        <h2 id="hosts-heading">Results per host</h2>
        {check ? <p className="help">A check run changes nothing: "Would change" is what a real run would change.</p> : null}
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="hosts-caption">
        <table className="stacked hosts">
          <caption id="hosts-caption" className="visually-hidden">The result of each host, {plural(names.length, "host", "hosts")}</caption>
          <thead>
            <tr>
              <th scope="col">Host</th>
              <th scope="col">Result</th>
              <th scope="col">OK</th>
              <th scope="col">{check ? "Would change" : "Changed"}</th>
              <th scope="col">Failed</th>
              <th scope="col">Skipped</th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const result = hosts[name];
              const target = byName.get(name);
              const o = outcome(result, check, done);
              const r = result ?? EMPTY;
              return (
                <tr key={name}>
                  <th scope="row" data-label="Host">
                    <code>{name}</code>
                    {target ? <span className="sub">VM {target.vmid}{target.pve_name && target.pve_name !== name ? ` (${target.pve_name})` : ""}{target.address ? `, ${target.address}` : ""}</span> : null}
                  </th>
                  <td data-label="Result">
                    <div className="cell">
                      <span className={`badge badge-${o.kind}`}><Icon name={o.icon} />{o.word}</span>
                      {r.failed_tasks.length ? (
                        <details>
                          <summary>{plural(r.failed_tasks.length, "failed task", "failed tasks")}</summary>
                          <ul className="plain-list">
                            {r.failed_tasks.map((f, i) => (
                              <li key={i}><strong>{f.task}</strong>{f.message ? `: ${f.message}` : ""}</li>
                            ))}
                          </ul>
                        </details>
                      ) : null}
                      {r.changed_tasks.length ? (
                        <details>
                          <summary>{plural(r.changed_tasks.length, "task", "tasks")} {check ? "would change" : "changed"} something</summary>
                          <ul className="plain-list">{r.changed_tasks.map((t, i) => <li key={i}>{t}</li>)}</ul>
                        </details>
                      ) : null}
                    </div>
                  </td>
                  <td data-label="OK">{r.ok}</td>
                  <td data-label={check ? "Would change" : "Changed"}>{r.changed}</td>
                  <td data-label="Failed">{r.failed + r.unreachable}</td>
                  <td data-label="Skipped">{r.skipped}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
