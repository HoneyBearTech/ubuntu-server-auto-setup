import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { formData, post } from "../api";
import { Icon } from "../components/Icons";
import { Loading, Notice, PageHead } from "../components/Parts";
import { useData } from "../data";
import { Link } from "../router";
import type { Notice as NoticeT, SshData } from "../types";

type Area = "key" | "address" | "hostkey";

const AREAS: Record<string, Area> = {
  generate: "key", upload: "key", address: "address", scan: "hostkey", trust: "hostkey", forget: "hostkey",
  test: "hostkey",
};

function Status({ notice }: { notice?: NoticeT }) {
  return <div aria-live="polite">{notice ? <Notice notice={notice} /> : null}</div>;
}

function Button({ action, children, secondary, busy }: {
  action: string;
  children: ReactNode;
  secondary?: boolean;
  busy: boolean;
}) {
  return (
    <button type="submit" className={`button${secondary ? " secondary" : ""}`} name="action" value={action}
            aria-disabled={busy || undefined}>
      {children}
    </button>
  );
}

export function Ssh() {
  const page = useData<SshData>("/api/ssh");
  const [notes, setNotes] = useState<Partial<Record<Area, NoticeT>>>({});
  const [busy, setBusy] = useState(false);
  const { data } = page;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const body = formData(event.currentTarget, (event.nativeEvent as SubmitEvent).submitter);
    const action = String(body.get("action") ?? "");
    setBusy(true);
    const reply = await post<{ message?: string; error?: string; area?: Area }>("/api/ssh", body);
    setBusy(false);
    const area = reply.data.area ?? AREAS[action] ?? "key";
    const notice: NoticeT | undefined = reply.ok
      ? (reply.data.message ? { kind: "ok", message: reply.data.message } : undefined)
      : { kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` };
    setNotes({ [area]: notice });
    if (reply.ok) page.reload();
  };

  if (!data) return <><PageHead title="SSH key and the Proxmox node" /><Loading error={page.error} /></>;
  const { public_key: pub, key_info: info, node_address: node, trusted, pending } = data;
  return (
    <>
      <PageHead title="SSH key and the Proxmox node" crumb="Set up · step 2" />

      <section className="card" aria-labelledby="key-heading" key={`key-${page.loaded}`}>
        <div className="card-body">
          <h2 id="key-heading">The UI's SSH key</h2>
          <p className="lead">
            The UI logs in to the Proxmox node as <code>root</code> with this key (to build the installer ISO and
            the seed files), and to the servers as the admin user. The private key never leaves the data volume.
          </p>
          {pub && info ? (
            <>
              <p>Public key ({info.type}, <code className="fp">{info.fingerprint}</code>):</p>
              <pre className="key" id="public-key">{pub}</pre>
              <p>
                <a href="/ssh/id_ed25519.pub" download><Icon name="download" /> Download the public key</a>. On the
                node, add it to <code>/root/.ssh/authorized_keys</code>, for example in the node's shell in the
                Proxmox web UI:
              </p>
              <pre className="key">{`echo '${pub}' >> /root/.ssh/authorized_keys`}</pre>
            </>
          ) : <p>There is no key yet. Create one (recommended) or upload one.</p>}
          <form method="post" action="/api/ssh" onSubmit={submit} className="stack-sm">
            {pub ? (
              <div className="check">
                <input type="checkbox" id="replace" name="replace" />
                <label htmlFor="replace">
                  Replace the current key (servers and the node must then be given the new one)
                </label>
              </div>
            ) : null}
            <div className="actions">
              <Button action="generate" busy={busy}>{pub ? "Create a new key" : "Create a key"}</Button>
            </div>
          </form>
          <details>
            <summary>Upload a private key instead</summary>
            <form method="post" action="/api/ssh" encType="multipart/form-data" onSubmit={submit}>
              <div className="field">
                <label htmlFor="key_file">Private key file</label>
                <input type="file" id="key_file" name="key_file" aria-describedby="key-file-help" />
                <p className="help" id="key-file-help">
                  OpenSSH or PEM format; Ed25519, ECDSA or RSA (3072 bits or more). It is stored without a passphrase.
                </p>
              </div>
              <div className="field">
                <label htmlFor="key_text">Or paste it</label>
                <textarea id="key_text" name="key_text" rows={4} spellCheck={false} autoComplete="off" />
              </div>
              <div className="field">
                <label htmlFor="passphrase">Passphrase, if the key has one</label>
                <input type="password" id="passphrase" name="passphrase" autoComplete="off" />
              </div>
              <div className="actions"><Button action="upload" busy={busy}>Upload the key</Button></div>
            </form>
          </details>
          <Status notice={notes.key} />
        </div>
      </section>

      <section className="card" aria-labelledby="node-heading" key={`node-${page.loaded}`}>
        <div className="card-body">
          <h2 id="node-heading">The Proxmox node over SSH</h2>
          <form method="post" action="/api/ssh" onSubmit={submit} className="stack">
            <div className="field narrow">
              <label htmlFor="node_address">The node's SSH address</label>
              <input type="text" id="node_address" name="node_address" defaultValue={node} className="mono"
                     spellCheck={false} autoComplete="off" aria-describedby="node-address-help" />
              <p className="help" id="node-address-help">
                Host name or IP address of node <code>{data.pve_node || "(set the node name on the Proxmox page)"}</code>,
                port 22, user root.
                {!data.pve_node ? <> Set it on the <Link href="/proxmox">Proxmox</Link> page.</> : null}
              </p>
            </div>
            <div className="actions"><Button action="address" busy={busy}>Save the address</Button></div>
          </form>
          <Status notice={notes.address} />
        </div>
        {node ? (
          <div className="card-body">
            <h3>Host key</h3>
            {trusted.length ? (
              <>
                <p>Trusted host keys of {node}:</p>
                <ul className="plain-list">
                  {trusted.map((k) => <li key={k.fingerprint}>{k.keytype} <code className="fp">{k.fingerprint}</code></li>)}
                </ul>
              </>
            ) : <p>The node's host key isn't trusted yet: the playbooks refuse to connect until it is.</p>}
            {pending ? (
              <form method="post" action="/api/ssh" onSubmit={submit} className="confirm">
                <p>
                  {node} offers these host keys. <strong>Compare them</strong> with what the node itself shows (in its
                  shell: <code>{'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done'}</code>) before
                  you trust them; a mismatch means someone else answered.
                </p>
                <ul className="plain-list">
                  {pending.map((k) => <li key={k.fingerprint}>{k.keytype} <code className="fp">{k.fingerprint}</code></li>)}
                </ul>
                <div className="check">
                  <input type="checkbox" id="confirm" name="confirm" />
                  <label htmlFor="confirm">The fingerprints match what the node shows</label>
                </div>
                <div className="actions"><Button action="trust" busy={busy}>Trust these keys</Button></div>
              </form>
            ) : null}
            <form method="post" action="/api/ssh" onSubmit={submit}>
              <div className="actions">
                <Button action="scan" secondary busy={busy}>
                  {trusted.length || pending ? "Scan again" : "Scan the host key"}
                </Button>
                {trusted.length ? (
                  <>
                    <Button action="test" secondary busy={busy}>Test the SSH login</Button>
                    <Button action="forget" secondary busy={busy}>Forget the host key</Button>
                  </>
                ) : null}
              </div>
            </form>
            <Status notice={notes.hostkey} />
          </div>
        ) : null}
      </section>
    </>
  );
}
