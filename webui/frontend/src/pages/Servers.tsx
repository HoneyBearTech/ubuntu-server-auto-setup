import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { formData, post } from "../api";
import { Icon } from "../components/Icons";
import { ErrorSummary, Loading, Notice, PageHead } from "../components/Parts";
import { useData } from "../data";
import type { Errors, FieldMeta, Notice as NoticeT, ServersData } from "../types";

// A choice as the form shows it ("Ubuntu 24.04 (noble) · until 2029-05-31"), or the value itself.
const label = (c: FieldMeta, choice: string) => c.choice_labels?.[choice] ?? choice;

interface Row {
  key: number;
  values: Record<string, string>;
}

function ServersForm({ data, reload, status, setStatus }: {
  data: ServersData;
  reload: () => void;
  status: NoticeT | null;
  setStatus: (n: NoticeT | null) => void;
}) {
  const { columns, nodes } = data;
  const clusters = data.clusters ?? [];
  // The Defaults page's choices, which an empty OS or install method stands for.
  const defaults: Record<string, string> = { vm_os: data.default_os, vm_install_method: data.default_install_method };
  const blank = () => Object.fromEntries(columns.map((c) => [c.name, ""]));
  const [rows, setRows] = useState<Row[]>(() =>
    (data.rows.length ? data.rows : [blank()]).map((values, key) => ({ key, values })));
  const nextKey = useRef(rows.length);
  const [errors, setErrors] = useState<Errors>({});
  const [focus, setFocus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (focus) document.getElementById(focus)?.focus();
    setFocus(null);
  }, [focus]);

  const add = () => {
    setRows([...rows, { key: nextKey.current++, values: blank() }]);
    setErrors({});
    setFocus(`servers-${rows.length}-${columns[0].name}`);
  };
  const remove = (index: number) => {
    const kept = rows.filter((_, i) => i !== index);
    setRows(kept.length ? kept : [{ key: nextKey.current++, values: blank() }]);
    setErrors({});
    setStatus({ kind: "info", message: "Row removed. Save to keep the change." });
    setFocus(kept.length ? `servers-${Math.min(index, kept.length - 1)}-${columns[0].name}` : "add-row");
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const reply = await post<{ errors?: Errors; message?: string; error?: string }>("/api/servers",
      formData(event.currentTarget, (event.nativeEvent as SubmitEvent).submitter));
    setBusy(false);
    if (reply.status === 422 && reply.data.errors) {
      setErrors(reply.data.errors);
      setStatus(null);
    } else if (reply.ok) {
      setStatus({ kind: "ok", message: reply.data.message ?? "Saved." });
      reload();
    } else {
      setStatus({ kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` });
    }
  };
  const rowOf = (key: string) => `Row ${Number(key.split("-")[1]) + 1}`;
  const help = (name: string) => columns.find((c) => c.name === name)?.help ?? "";

  return (
    <>
      <ErrorSummary errors={errors} label={rowOf} />
      <form className="card" method="post" action="/api/servers" noValidate onSubmit={submit}>
        <input type="hidden" name="server_count" value={rows.length} />
        <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="servers-caption">
          <table className="servers">
            <caption id="servers-caption">Servers ({rows.length} {rows.length === 1 ? "row" : "rows"})</caption>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th scope="col" id={`col-${c.name}`} key={c.name}>
                    {c.label}{c.required ? <span className="req"> (required)</span> : null}
                  </th>
                ))}
                <th scope="col"><span className="visually-hidden">Remove</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={row.key}>
                  {columns.map((c) => {
                    const id = `servers-${i}-${c.name}`;
                    const error = errors[id];
                    const value = row.values[c.name] ?? "";
                    const common = {
                      id, name: id, "aria-label": `${c.label}, row ${i + 1}`,
                      "aria-invalid": error ? true : undefined,
                      "aria-describedby": error ? `${id}-error` : undefined,
                    };
                    return (
                      <td key={c.name}>
                        {c.name === "k8s_cluster" ? (
                          // The Defaults page's clusters; empty: not a member.
                          <div className="select">
                            <select {...common} defaultValue={value}>
                              <option value="">None</option>
                              {(value && !clusters.includes(value) ? [...clusters, value] : clusters).map((n) => (
                                <option key={n} value={n}>{n}</option>
                              ))}
                            </select>
                          </div>
                        ) : c.name === "pve_node" && nodes.length ? (
                          // The cluster's nodes from the last connection test; empty is the default node.
                          <div className="select">
                            <select {...common} defaultValue={value}>
                              <option value="">Default{data.default_node ? ` (${data.default_node})` : ""}</option>
                              {(value && !nodes.includes(value) ? [...nodes, value] : nodes).map((n) => (
                                <option key={n} value={n}>{n}</option>
                              ))}
                            </select>
                          </div>
                        ) : c.name in defaults ? (
                          // The catalog's supported releases, or the install methods; empty is the Defaults page's.
                          <div className="select">
                            <select {...common} defaultValue={value}>
                              <option value="">Default{defaults[c.name] ? ` (${label(c, defaults[c.name])})` : ""}</option>
                              {(value && !(c.choices ?? []).includes(value) ? [...(c.choices ?? []), value]
                                : c.choices ?? []).map((choice) => (
                                <option key={choice} value={choice}>{label(c, choice)}</option>
                              ))}
                            </select>
                          </div>
                        ) : c.kind === "choice" ? (
                          <div className="select">
                            <select {...common} defaultValue={value || c.default || ""}>
                              {(c.choices ?? []).map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                            </select>
                          </div>
                        ) : (
                          <input type="text" {...common} defaultValue={value}
                                 inputMode={c.kind === "int" ? "numeric" : undefined}
                                 placeholder={c.kind === "int" && c.default ? c.default
                                   : c.name === "pve_node" ? data.default_node || undefined : undefined}
                                 spellCheck={false} autoComplete="off" />
                        )}
                        {error ? (
                          <p className="error" id={`${id}-error`}>
                            <span className="visually-hidden">Error: </span>{error}
                          </p>
                        ) : null}
                      </td>
                    );
                  })}
                  <td>
                    <button type="button" className="button secondary small" aria-label={`Remove row ${i + 1}`}
                            onClick={() => remove(i)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card-body">
          <p className="help">
            Name: a host name (letters, digits, hyphens), also the VM's name in Proxmox. IP address: the static IPv4
            address the installer configures. Address: static, or dhcp to get the address from your DHCP server; the
            playbooks then read it from the VM's guest agent, unless you enter the address reserved for it as the IP
            address. MAC address (optional): the first card's, for a DHCP reservation. Node: the Proxmox node the VM
            is created on; the default is the node on the Proxmox page{nodes.length ? ""
              : " (test the connection there to pick from the cluster's nodes)"}. Extra network
            cards: {help("vm_extra_nics")} Extra firewall rules: {help("firewall_ports")} OS: the operating
            system, from the releases this version supports; the default is the one on the Defaults page. Install
            method: the Ubuntu installer (Ubuntu only) or the vendor's cloud image (every release; the ISO storage
            must allow Import content); the default is the one on the Defaults page. Rocky Linux 10 and CentOS
            Stream 10 need a node whose CPU is x86-64-v3 (AVX2): the SSH page's login test reads each node's CPU.
            Kubernetes cluster and role: a cluster from the Defaults page{clusters.length ? ""
              : " (add one there first)"}, and server (control plane and etcd) or agent (worker); a member needs
            its IP address here, also on DHCP, and a lowercase name.
          </p>
          <div aria-live="polite">{status ? <Notice notice={status} /> : null}</div>
        </div>
        <div className="card-foot">
          <button type="submit" className="button" name="action" value="save" aria-disabled={busy || undefined}>
            Save
          </button>
          <button type="button" className="button secondary" id="add-row" onClick={add}>
            <Icon name="plus" />Add a row
          </button>
        </div>
      </form>
    </>
  );
}

export function Servers() {
  const page = useData<ServersData>("/api/servers");
  const [status, setStatus] = useState<NoticeT | null>(null);
  const sizes = page.data?.columns.filter((c) => ["vm_cores", "vm_memory_mb", "vm_disk_gb"].includes(c.name))
    .map((c) => `${c.label.toLowerCase()} ${c.default}`).join(", ");
  return (
    <>
      <PageHead title="Servers" crumb="Set up · step 4" />
      <p className="lead">
        One row per VM. <strong>Provisioning creates a VM for every VM ID that doesn't exist yet in the cluster,
        and the installer wipes that VM's disk.</strong> VMs that already exist are never touched.
        {sizes ? ` Empty sizes use the defaults (${sizes}).` : null}
      </p>
      {page.data ? (
        <ServersForm key={page.loaded} data={page.data} reload={page.reload} status={status} setStatus={setStatus} />
      ) : <Loading error={page.error} />}
    </>
  );
}
