// The Defaults page's Kubernetes clusters: each cluster's settings (the group_vars of k8s_<cluster>, from the k3s
// and kubernetes_node roles' defaults) and its two join tokens. A token is write-only: the password field is
// emptied after every post, and the server never sends a saved one back. "Make a token" asks the server for one
// (secrets.token_hex(32)), which it saves and shows once, for the password manager; it stays on the page only
// until it is hidden or the page changes.

import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { formData, post } from "../api";
import { Field, FieldError, fieldId } from "../components/Field";
import { FieldSet, groupFields, named } from "../components/FieldSet";
import { Icon } from "../components/Icons";
import { ErrorSummary, Loading } from "../components/Parts";
import { useData } from "../data";
import { Link } from "../router";
import type { ClusterInfo, ClustersData, Errors, FieldMeta, Notice as NoticeT, Values } from "../types";
import { FormStatus } from "./Sections";

interface Row {
  key: number;
  original: string; // the name it was loaded with ("" for a new cluster)
  values: Values;
  info: ClusterInfo | null;
}

interface Shown {
  cluster: string;
  label: string;
  variable: string;
  token: string;
}

function TokenField({ token, row, index, error, onMake, busy }: {
  token: FieldMeta;
  row: Row;
  index: number;
  error?: string;
  onMake: (token: FieldMeta, replace: boolean) => void;
  busy: boolean;
}) {
  const name = `clusters-${index}-${token.name}`;
  const id = fieldId(name);
  const saved = !!row.info?.tokens[token.name];
  const variable = row.info?.token_vars[token.name] ?? token.variable;
  const [replace, setReplace] = useState(false);
  const described = [`${id}-help`, `${id}-saved`, ...(error ? [`${id}-error`] : [])].join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>{token.label}</label>
      <input type="password" id={id} name={name} autoComplete="off" spellCheck={false}
             aria-describedby={described} aria-invalid={error ? true : undefined} />
      <p className="saved" id={`${id}-saved`}>
        {saved ? "A token is saved. Leave this empty to keep it." : "Not set yet."}
      </p>
      {saved ? (
        <div className="check">
          <input type="checkbox" id={`${id}-replace`} name={`${name}__replace`} checked={replace}
                 onChange={(e) => setReplace(e.target.checked)} />
          <label htmlFor={`${id}-replace`}>Replace the saved token</label>
        </div>
      ) : null}
      <p className="help" id={`${id}-help`}>
        {token.help} Write-only: kept in the Vault as <code>{variable}</code> and never shown again.
      </p>
      {row.original ? (
        <div className="actions">
          <button type="button" className="button secondary small" aria-disabled={busy || (saved && !replace) || undefined}
                  onClick={() => !busy && (!saved || replace) && onMake(token, replace)}>
            <Icon name="key" />Make a token and show it once
          </button>
        </div>
      ) : null}
      <FieldError id={id} error={error} />
    </div>
  );
}

function ClusterCard({ row, index, data, errors, onRemove, onMake, busy }: {
  row: Row;
  index: number;
  data: ClustersData;
  errors: Errors;
  onRemove: () => void;
  onMake: (token: FieldMeta, replace: boolean) => void;
  busy: boolean;
}) {
  const prefix = `clusters-${index}-`;
  const title = row.original || `New cluster ${index + 1}`;
  const groups = groupFields(data.fields, [
    { legend: "Cluster", test: named("name", "k8s_version") },
    { legend: "Addresses", test: named("k8s_api_vip", "k8s_lb_pool", "k8s_api_sources", "k8s_api_vip_interface",
      "k8s_tls_san") },
    { legend: "Nodes", test: (f) => !f.name.startsWith("k8s_bootstrap_") },
    { legend: "Hand over to Argo CD (off by default)", test: () => true },
  ]);
  const errorOf = (f: FieldMeta) => errors[prefix + f.name];
  const info = row.info;
  return (
    <section className="card nested-card" aria-labelledby={`${prefix}heading`}>
      <div className="card-body">
        <h3 id={`${prefix}heading`}>{title}</h3>
        <input type="hidden" name={`${prefix}original`} value={row.original} />
        {info ? (
          <p className="help">
            Servers: {info.servers.length ? info.servers.join(", ") : "none yet"}. Agents:{" "}
            {info.agents.length ? info.agents.join(", ") : "none"}
            {info.other_members.length ? `. Set in the inventory by hand: ${info.other_members.join(", ")}` : ""}.
            Choose the members on the <Link href="/servers">Servers</Link> page.
            {info.kept.length ? <> Kept as set by hand in the inventory: <code>{info.kept.join(", ")}</code>.</> : null}
          </p>
        ) : null}
      </div>
      {groups.map((g) => (
        <FieldSet key={g.legend} legend={g.legend} fields={g.fields} values={row.values}
                  errors={Object.fromEntries(g.fields.flatMap((f) => (errorOf(f) ? [[f.name, errorOf(f)]] : [])))}
                  render={(f) => <Field field={f} values={row.values} error={errorOf(f)} prefix={prefix} />} />
      ))}
      <fieldset className="card-body fieldset">
        <legend>Join tokens</legend>
        <div className="fields">
          {data.tokens.map((t) => (
            <TokenField key={t.name} token={t} row={row} index={index} error={errors[prefix + t.name]}
                        onMake={onMake} busy={busy} />
          ))}
        </div>
        {!row.original ? <p className="help">Save the cluster first to make its tokens here.</p> : null}
      </fieldset>
      <div className="card-foot">
        <button type="button" className="button secondary small" onClick={onRemove}
                aria-label={`Remove cluster ${title}`}>
          Remove
        </button>
      </div>
    </section>
  );
}

function ClustersForm({ data, reload, shown, setShown, status, setStatus }: {
  data: ClustersData;
  reload: () => void;
  shown: Shown | null;
  setShown: (s: Shown | null) => void;
  status: NoticeT | null; // kept by Clusters, so it outlives the reload after a save
  setStatus: (n: NoticeT | null) => void;
}) {
  const blank = (): Values => Object.fromEntries(data.fields.map((f) => [f.name,
    f.kind === "bool" ? f.default === "true" : f.kind === "list" && f.choices ? (f.default ?? "").split(", ").join("\n")
      : ""]));
  const [rows, setRows] = useState<Row[]>(() => data.clusters.map((c, key) => ({
    key, original: c.name, values: c.values, info: c })));
  const nextKey = useRef(rows.length);
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  const clearTokens = () => {
    formRef.current?.querySelectorAll<HTMLInputElement>("input[type=password]").forEach((input) => {
      input.value = "";
    });
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const body = formData(event.currentTarget, (event.nativeEvent as SubmitEvent).submitter);
    clearTokens(); // a typed token leaves the page with the post, whatever the answer
    const reply = await post<{ errors?: Errors; message?: string; error?: string }>("/api/clusters", body);
    setBusy(false);
    if (reply.status === 422 && reply.data.errors) {
      setErrors(reply.data.errors);
      setStatus(null);
    } else if (reply.ok) {
      setErrors({});
      setStatus({ kind: "ok", message: reply.data.message ?? "Saved." });
      reload();
    } else {
      setStatus({ kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` });
    }
  };
  const make = (row: Row) => async (token: FieldMeta, replace: boolean) => {
    if (busy) return;
    setBusy(true);
    const reply = await post<{ token?: string; variable?: string; message?: string; error?: string }>(
      "/api/clusters/token", { cluster: row.original, token: token.name, ...(replace ? { replace: "on" } : {}) });
    setBusy(false);
    if (reply.ok && reply.data.token) {
      setShown({ cluster: row.original, label: token.label, variable: reply.data.variable ?? "", token: reply.data.token });
      reload(); // the token now shows as saved; the one made stays above until hidden
    } else {
      setStatus({ kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` });
    }
  };
  const add = () => {
    setRows([...rows, { key: nextKey.current++, original: "", values: blank(), info: null }]);
    setErrors({});
  };
  const remove = (index: number) => {
    setRows(rows.filter((_, i) => i !== index));
    setErrors({});
    setStatus({ kind: "info", message: "Cluster removed from the form. Save to keep the change." });
  };
  const label = (key: string) => {
    const match = /^clusters-(\d+)-/.exec(key);
    return match ? `Cluster ${Number(match[1]) + 1}` : "Clusters";
  };

  return (
    <>
      <ErrorSummary errors={errors} label={label} id="clusters-error-summary"
                    target={(key) => (key === "clusters" ? "clusters-error" : `f-${key}`)} />
      {shown ? (
        <section className="card" aria-labelledby="token-once-heading">
          <div className="card-body token-once" role="status">
            <h2 id="token-once-heading">{shown.label} of {shown.cluster}</h2>
            <p>
              Saved in the Vault as <code>{shown.variable}</code>. Copy it into your password manager now: it is
              shown only this once.
            </p>
            <code className="fp" aria-label={`${shown.label} of ${shown.cluster}`}>{shown.token}</code>
            <div className="actions">
              <button type="button" className="button secondary small" onClick={() => setShown(null)}>
                I have copied it: hide it
              </button>
            </div>
          </div>
        </section>
      ) : null}
      <form ref={formRef} className="card" method="post" action="/api/clusters" noValidate onSubmit={submit}
            aria-labelledby="clusters-heading">
        <div className="card-body">
          <h2 id="clusters-heading">Kubernetes clusters</h2>
          <p className="lead">
            k3s clusters on the servers: each cluster's members are chosen on the Servers page, its settings and
            join tokens here. The baseline prepares the members; a Kubernetes run on the Run page installs or
            upgrades k3s, a check first. The admin kubeconfig never leaves the servers.
          </p>
          <input type="hidden" name="cluster_count" value={rows.length} />
          {errors.clusters ? <FieldError id="clusters" error={errors.clusters} /> : null}
          {rows.length === 0 ? <p>No clusters yet.</p> : null}
        </div>
        {rows.map((row, i) => (
          <ClusterCard key={row.key} row={row} index={i} data={data} errors={errors} onRemove={() => remove(i)}
                       onMake={make(row)} busy={busy} />
        ))}
        <FormStatus status={status} />
        <div className="card-foot">
          <button type="submit" className="button" name="action" value="save" aria-disabled={busy || undefined}>
            Save the clusters
          </button>
          {rows.length < data.max_clusters ? (
            <button type="button" className="button secondary" onClick={add}>
              <Icon name="plus" />Add a cluster
            </button>
          ) : null}
        </div>
      </form>
    </>
  );
}

export function Clusters() {
  const page = useData<ClustersData>("/api/clusters");
  const [shown, setShown] = useState<Shown | null>(null);
  const [status, setStatus] = useState<NoticeT | null>(null);
  if (!page.data) return <Loading error={page.error} />;
  return <ClustersForm key={page.loaded} data={page.data} reload={page.reload} shown={shown} setShown={setShown}
                       status={status} setStatus={setStatus} />;
}
