// The settings pages built from the roles' variables: Proxmox, Defaults and Hardening (and the Updates page's
// settings, which reuse the form helpers below).

import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { formData, post } from "../api";
import { Field, IsoStorageField, NodeField } from "../components/Field";
import { FieldSet, groupFields, named } from "../components/FieldSet";
import { ErrorSummary, Loading, Notice, PageHead } from "../components/Parts";
import { useData } from "../data";
import { Link } from "../router";
import { Clusters } from "./Clusters";
import type { Cluster, Errors, FieldMeta, KeyInfo, Notice as NoticeT, SectionData, Values } from "../types";

interface Reply {
  errors?: Errors;
  message?: string;
  error?: string;
  test_error?: string;
  ca_error?: string;
  cluster?: Cluster;
  preview?: KeyInfo[];
  extra_keys?: KeyInfo[];
  preview_error?: string | null;
  github_user?: string;
}

function submitter(event: FormEvent<HTMLFormElement>): HTMLElement | null {
  return (event.nativeEvent as SubmitEvent).submitter ?? null;
}

// Posts a section's form. 422 with errors shows them by the fields; anything else goes to onReply.
export function useSectionForm(api: string, reload: () => void) {
  const [errors, setErrors] = useState<Errors>({});
  const [status, setStatus] = useState<NoticeT | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>, onReply?: (action: string, data: Reply,
                                                                       body: FormData) => boolean) => {
    event.preventDefault();
    if (busy) return;
    const body = formData(event.currentTarget, submitter(event));
    const action = String(body.get("action") ?? "save");
    setBusy(true);
    const reply = await post<Reply>(api, body);
    setBusy(false);
    if (reply.status === 422 && reply.data.errors) {
      setErrors(reply.data.errors);
      setStatus(null);
      return;
    }
    setErrors({});
    if (onReply?.(action, reply.data, body)) return;
    if (reply.ok) {
      setStatus({ kind: "ok", message: reply.data.message ?? "Saved." });
      reload();
    } else {
      setStatus({ kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` });
    }
  };
  return { errors, status, setStatus, busy, submit };
}

// The answer to a form's post, above its buttons. The live region is always there (so screen readers
// announce what appears in it) and takes no room while it is empty.
export function FormStatus({ status, children }: { status: NoticeT | null; children?: ReactNode }) {
  return (
    <div className="form-status" aria-live="polite">
      {status ? <Notice notice={status}>{children}</Notice> : null}
    </div>
  );
}

// --- Proxmox ---------------------------------------------------------------------------------------------

const API_FIELDS = ["pve_api_host", "pve_api_port", "pve_api_user", "pve_api_token_id", "pve_api_token_secret",
  "pve_validate_certs"];

function ClusterFacts({ cluster }: { cluster: Cluster }) {
  const nodes = Object.entries(cluster.nodes ?? {}).sort();
  const storages = Object.keys(cluster.iso_storages ?? {}).sort();
  return (
    <aside className="card" aria-labelledby="cluster-heading">
      <div className="card-body">
        <h2 id="cluster-heading">Last connection test</h2>
        {nodes.length ? (
          <dl className="facts">
            {cluster.name ? <><dt>Cluster</dt><dd>{cluster.name}</dd></> : null}
            <dt>Nodes</dt>
            <dd>
              <ul>
                {nodes.map(([name, state]) => (
                  <li key={name}>
                    {name}
                    <span className={`badge ${state === "online" ? "badge-success" : "badge-error"}`}>{state}</span>
                  </li>
                ))}
              </ul>
            </dd>
            {storages.length ? (
              <>
                <dt>ISO storages</dt>
                <dd>
                  <ul>
                    {storages.map((s) => (
                      <li key={s}>
                        {s}
                        {cluster.import_storages?.includes(s) ? (
                          // Short, like the nodes' status badges: the card is narrow.
                          <span className="badge badge-info">
                            <span className="visually-hidden">allows </span>Import<span className="visually-hidden"> content</span>
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </dd>
              </>
            ) : null}
          </dl>
        ) : (
          <p className="help">
            No test yet. <strong>Test the connection</strong> to pick the node and the ISO storage from lists.
          </p>
        )}
        <p className="help">A test saves nothing. Test again after adding a node or a storage.</p>
      </div>
    </aside>
  );
}

function CaSection({ data, reload }: { data: SectionData; reload: () => void }) {
  const [status, setStatus] = useState<NoticeT | null>(null);
  const [busy, setBusy] = useState(false);
  const ca = data.ca ?? [];
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    setBusy(true);
    const reply = await post<Reply>("/api/proxmox", formData(form, submitter(event)));
    setBusy(false);
    if (reply.ok) {
      setStatus({ kind: "ok", message: reply.data.message ?? "Done." });
      form.reset();
      reload();
    } else {
      setStatus({ kind: "error", message: reply.data.ca_error ?? reply.data.error ?? "That didn't work." });
    }
  };
  return (
    <section className="card" aria-labelledby="ca-heading">
      <form method="post" action="/api/proxmox" onSubmit={submit} noValidate>
        <div className="card-body">
          <h2 id="ca-heading">Cluster CA certificate</h2>
          <p className="lead">
            Proxmox nodes use a certificate signed by the cluster's own CA. Add that CA here and keep certificate
            verification on: the playbooks and the connection test then trust it, and nobody on the network can
            intercept the token.
          </p>
          {ca.length ? (
            <ul className="plain-list">
              {ca.map((c) => (
                <li key={c.fingerprint}>
                  <strong>{c.subject}</strong>
                  <span className="help">SHA-256 <code className="fp">{c.fingerprint}</code>, expires {c.expires}</span>
                </li>
              ))}
            </ul>
          ) : <p>No CA certificate saved.</p>}
          <div className="field">
            <label htmlFor="ca_pem">CA certificate (PEM)</label>
            <textarea id="ca_pem" name="ca_pem" rows={5} spellCheck={false} aria-describedby="ca-help" />
            <p className="help" id="ca-help">
              Paste <code>/etc/pve/pve-root-ca.pem</code> from the node, or fetch it over the verified SSH
              connection (after the <Link href="/ssh">SSH key</Link> step).
            </p>
          </div>
          <FormStatus status={status} />
        </div>
        <div className="card-foot">
          <button type="submit" className="button" name="action" value="save-ca" aria-disabled={busy || undefined}>
            Save the certificate
          </button>
          <button type="submit" className="button secondary" name="action" value="fetch-ca"
                  aria-disabled={busy || undefined}>
            Fetch it from the node
          </button>
          {ca.length ? (
            <button type="submit" className="button secondary" name="action" value="remove-ca"
                    aria-disabled={busy || undefined}>
              Remove it
            </button>
          ) : null}
        </div>
      </form>
    </section>
  );
}

export function Proxmox() {
  const page = useData<SectionData>("/api/proxmox");
  const [cluster, setCluster] = useState<Cluster | null>(null); // from a connection test since the page loaded
  const [typed, setTyped] = useState<Values>({}); // the node and storage typed before that test
  const form = useSectionForm("/api/proxmox", () => {
    setTyped({});
    setCluster(null);
    page.reload();
  });
  const { data } = page;
  if (!data) {
    return <><PageHead title="Proxmox API" /><Loading error={page.error} /></>;
  }
  const known = cluster ?? data.cluster ?? {};
  const values = { ...data.values, ...typed };
  const { errors } = form;
  const groups = groupFields(data.fields, [
    { legend: "API", test: named(...API_FIELDS) },
    { legend: "Placement", test: () => true },
  ]);
  const render = (f: FieldMeta) => {
    if (f.name === "pve_node" && known.nodes && Object.keys(known.nodes).length) {
      return <NodeField field={f} values={values} error={errors[f.name]} cluster={known} />;
    }
    if (f.name === "pve_iso_storage" && known.iso_storages && Object.keys(known.iso_storages).length) {
      return <IsoStorageField field={f} values={values} error={errors[f.name]} cluster={known} />;
    }
    return <Field field={f} values={values} error={errors[f.name]} />;
  };
  const onReply = (action: string, reply: Reply, body: FormData) => {
    if (action !== "test") return false;
    if (reply.test_error) {
      form.setStatus({ kind: "error", message: reply.test_error });
    } else {
      form.setStatus({ kind: "ok", message: reply.message ?? "Connected." });
      setTyped({ pve_node: String(body.get("pve_node") ?? ""), pve_iso_storage: String(body.get("pve_iso_storage") ?? "") });
      setCluster(reply.cluster ?? null);
    }
    return true;
  };
  return (
    <>
      <PageHead title="Proxmox API" crumb="Set up · step 1" />
      <p className="lead">
        The Proxmox VE API and the node the VMs are created on. The token needs the privileges listed in{" "}
        <a href="https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs/installing.md#2-create-the-proxmox-api-token">
          installing.md</a>. The token is stored in the encrypted Vault and never shown again.
      </p>
      <ErrorSummary errors={errors} />
      <div className="two-columns">
        <form key={page.loaded} className="card" method="post" action="/api/proxmox" noValidate
              onSubmit={(e) => form.submit(e, onReply)}>
          {groups.map((g) => (
            <FieldSet key={g.legend} legend={g.legend} fields={g.fields} values={values} errors={errors}
                      render={render}>
              {g.legend === "Placement" && !(known.nodes && Object.keys(known.nodes).length) ? (
                <p className="help wide">
                  Use <strong>Test the connection</strong> to pick the node from a list of the cluster's nodes.
                </p>
              ) : null}
            </FieldSet>
          ))}
          <FormStatus status={form.status} />
          <div className="card-foot">
            <button type="submit" className="button" name="action" value="save" aria-disabled={form.busy || undefined}>
              Save
            </button>
            <button type="submit" className="button secondary" name="action" value="test"
                    aria-disabled={form.busy || undefined}>
              Test the connection
            </button>
          </div>
        </form>
        <ClusterFacts cluster={known} />
      </div>
      <CaSection data={data} reload={page.reload} />
    </>
  );
}

// --- Defaults --------------------------------------------------------------------------------------------

function KeysPreview({ reply }: { reply: Reply | null }) {
  if (!reply) return null;
  if (reply.preview_error) return <Notice notice={{ kind: "error", message: reply.preview_error }} />;
  const keys = [...(reply.preview ?? []), ...(reply.extra_keys ?? [])];
  if (!keys.length) return null;
  return (
    <div className="table-wrap card" tabIndex={0} role="region" aria-labelledby="preview-caption">
      <table>
        <caption id="preview-caption">
          Keys on GitHub for {reply.github_user} (what the servers will accept)
        </caption>
        <thead><tr><th scope="col">Type</th><th scope="col">Fingerprint</th><th scope="col">Comment</th></tr></thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.fingerprint + k.comment}>
              <td>{k.type}</td>
              <td><code className="fp">{k.fingerprint}</code></td>
              <td>{k.comment}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Defaults() {
  const page = useData<SectionData>("/api/defaults");
  const form = useSectionForm("/api/defaults", () => page.reload());
  const [preview, setPreview] = useState<Reply | null>(null);
  const { data } = page;
  if (!data) {
    return <><PageHead title="Defaults for every server" /><Loading error={page.error} /></>;
  }
  const { errors } = form;
  const groups = groupFields(data.fields, [
    { legend: "SSH keys for the admin user", test: named("github_user", "admin_extra_ssh_keys", "github_keys_url") },
    { legend: "Admin user", test: named("admin_user", "admin_password", "timezone") },
    { legend: "Network", test: named("net_gateway", "net_prefix", "net_dns", "net_search") },
    { legend: "Installation", test: named("autoinstall_storage_layout", "vm_secure_boot") },
    { legend: "Disk encryption (off by default)", test: named("vm_disk_encryption", "vm_disk_passphrase") },
  ]);
  const render = (f: FieldMeta) => (
    <Field field={f} values={data.values} error={errors[f.name]} list={f.name === "timezone" ? "timezones" : undefined} />
  );
  const onReply = (action: string, reply: Reply) => {
    if (action !== "preview") return false;
    setPreview(reply);
    form.setStatus(null);
    return true;
  };
  return (
    <>
      <PageHead title="Defaults for every server" crumb="Set up · step 3" />
      <p className="lead">
        Settings every new server gets. The admin password and the disk passphrase are stored in the encrypted
        Vault.
      </p>
      <ErrorSummary errors={errors} />
      <form key={page.loaded} className="card" method="post" action="/api/defaults" noValidate
            onSubmit={(e) => form.submit(e, onReply)}>
        {groups.map((g) => (
          <FieldSet key={g.legend} legend={g.legend} fields={g.fields} values={data.values} errors={errors}
                    render={render}
                    after={g.legend.startsWith("SSH keys") ? (
                      <>
                        <div className="actions">
                          <button type="submit" className="button secondary" name="action" value="preview"
                                  aria-disabled={form.busy || undefined}>
                            Preview the keys
                          </button>
                        </div>
                        <div aria-live="polite"><KeysPreview reply={preview} /></div>
                      </>
                    ) : null}>
            {g.legend.startsWith("SSH keys") ? (
              <div className="check-field">
                <div className="check">
                  <input type="checkbox" id="authorize_ui_key" name="authorize_ui_key"
                         defaultChecked={data.authorize_ui_key} aria-describedby="authorize-help" />
                  <label htmlFor="authorize_ui_key">Also authorize this UI's SSH key</label>
                </div>
                <p className="help" id="authorize-help">
                  The baseline logs in to the servers with the UI's key, so it must be authorized.{" "}
                  {data.ui_key ? <>It is <code className="fp">{data.ui_key}</code>.</> : (
                    <>Create it on the <Link href="/ssh">SSH key</Link> page.</>
                  )}
                </p>
              </div>
            ) : null}
            {g.legend.startsWith("Disk encryption") ? (
              <p className="help">
                LUKS2, unlocked at boot by the VM's virtual TPM while its Secure Boot state is unchanged. It
                protects a copied disk or backup without the TPM state, not a VM whose Proxmox host is compromised:
                see <a href="https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs/security.md#secure-boot-and-disk-encryption">
                security.md</a>.
              </p>
            ) : null}
          </FieldSet>
        ))}
        <datalist id="timezones">
          {(data.timezones ?? []).map((tz) => <option key={tz} value={tz} />)}
        </datalist>
        <FormStatus status={form.status} />
        <div className="card-foot">
          <button type="submit" className="button" name="action" value="save" aria-disabled={form.busy || undefined}>
            Save
          </button>
        </div>
      </form>
      <Clusters />
    </>
  );
}

// --- Hardening -------------------------------------------------------------------------------------------

// Where update reports and ban notices go; sending mail needs the relay below or a mail server of the server's own.
const NOTIFICATION_FIELDS = ["unattended_upgrades_mail", "unattended_upgrades_mail_report", "fail2ban_destemail",
  "fail2ban_sender", "fail2ban_webhook_url", "fail2ban_webhook_format"];

export function Hardening() {
  const page = useData<SectionData>("/api/hardening");
  const form = useSectionForm("/api/hardening", () => page.reload());
  const { data } = page;
  if (!data) {
    return <><PageHead title="Hardening" /><Loading error={page.error} /></>;
  }
  const { errors } = form;
  const groups = groupFields(data.fields, [
    { legend: "SSH", test: (f) => f.name.startsWith("ssh_") || f.name === "admin_sudo_nopasswd" },
    { legend: "Firewall", test: (f) => f.name.startsWith("firewall_") },
    { legend: "Fail2Ban", test: (f) => f.name.startsWith("fail2ban_") && !NOTIFICATION_FIELDS.includes(f.name) },
    { legend: "Extra hardening (off by default)", test: (f) => f.name.startsWith("hardening_") },
    { legend: "Notifications", test: named(...NOTIFICATION_FIELDS) },
    { legend: "Mail relay", test: (f) => f.name.startsWith("mail_relay_") },
  ]);
  return (
    <>
      <PageHead title="Hardening" crumb="Set up · what the baseline applies" />
      <p className="lead">
        What the baseline applies to every server. <strong>Wrong values can lock you out</strong>: check the SSH
        port, the allowed users and the firewall sources, keep the Proxmox console open, and try a run in check
        mode first. The extra hardening and the notifications stay off until you turn them on.
      </p>
      <ErrorSummary errors={errors} />
      <form key={page.loaded} className="card" method="post" action="/api/hardening" noValidate
            onSubmit={(e) => form.submit(e)}>
        {groups.map((g) => (
          <FieldSet key={g.legend} legend={g.legend} fields={g.fields} values={data.values} errors={errors} />
        ))}
        <FormStatus status={form.status} />
        <div className="card-foot">
          <button type="submit" className="button" name="action" value="save" aria-disabled={form.busy || undefined}>
            Save
          </button>
        </div>
      </form>
    </>
  );
}
