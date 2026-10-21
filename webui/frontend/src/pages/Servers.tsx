import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { formData, post } from "../api";
import { Icon } from "../components/Icons";
import { ErrorSummary, Loading, Notice, PageHead } from "../components/Parts";
import { useData } from "../data";
import type { Errors, Notice as NoticeT, ServersData } from "../types";

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
  const { columns } = data;
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
  const lastHelp = columns[columns.length - 1]?.help ?? "";

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
                    return (
                      <td key={c.name}>
                        <input type="text" id={id} name={id} defaultValue={row.values[c.name] ?? ""}
                               aria-label={`${c.label}, row ${i + 1}`}
                               inputMode={c.kind === "int" ? "numeric" : undefined}
                               placeholder={c.kind === "int" && c.default ? c.default : undefined}
                               spellCheck={false} autoComplete="off" aria-invalid={error ? true : undefined}
                               aria-describedby={error ? `${id}-error` : undefined} />
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
            address the installer configures. Extra firewall rules: {lastHelp}
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
