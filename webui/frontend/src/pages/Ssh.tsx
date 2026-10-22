import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { formData, post } from "../api";
import { Icon } from "../components/Icons";
import { Loading, Notice, PageHead } from "../components/Parts";
import { useData } from "../data";
import { Link } from "../router";
import type { HostKey, Notice as NoticeT, SshData, SshNode } from "../types";

type Area = "key" | "address" | "hostkey";

const AREAS: Record<string, Area> = {
  generate: "key", upload: "key", address: "address", remove: "address", scan: "hostkey", trust: "hostkey",
  forget: "hostkey", test: "hostkey",
};

// A notice belongs to the key card, or to one area of one node's part of the page.
const noteKey = (area: Area, node?: string) => (area === "key" || !node ? area : `${area}:${node}`);

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
  const [notes, setNotes] = useState<Record<string, NoticeT | undefined>>({});
  const [busy, setBusy] = useState(false);
  const { data } = page;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const body = formData(event.currentTarget, (event.nativeEvent as SubmitEvent).submitter);
    const action = String(body.get("action") ?? "");
    setBusy(true);
    const reply = await post<{ message?: string; error?: string; area?: Area; node?: string }>("/api/ssh", body);
    setBusy(false);
    const area = reply.data.area ?? AREAS[action] ?? "key";
    const notice: NoticeT | undefined = reply.ok
      ? (reply.data.message ? { kind: "ok", message: reply.data.message } : undefined)
      : { kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` };
    setNotes({ [noteKey(area, reply.data.node ?? String(body.get("node") ?? ""))]: notice });
    if (reply.ok) page.reload();
  };

  if (!data) return <><PageHead title="SSH key and the Proxmox nodes" /><Loading error={page.error} /></>;
  const { public_key: pub, key_info: info, nodes, pending, node_user: user } = data;
  const root = user === "root";
  return (
    <>
      <PageHead title="SSH key and the Proxmox nodes" crumb="Set up · step 2" />

      <section className="card" aria-labelledby="key-heading" key={`key-${page.loaded}`}>
        <div className="card-body">
          <h2 id="key-heading">The UI's SSH key</h2>
          <p className="lead">
            The UI logs in to each Proxmox node as <code>{user}</code> with this key (to build the installer ISO and
            the seed files), and to the servers as the admin user. The private key never leaves the data volume.
          </p>
          {pub && info ? (
            <>
              <p>Public key ({info.type}, <code className="fp">{info.fingerprint}</code>):</p>
              <pre className="key" id="public-key">{pub}</pre>
              <p>
                <a href="/ssh/id_ed25519.pub" download><Icon name="download" /> Download the public key</a>. On each
                node, add it to {root ? <code>/root/.ssh/authorized_keys</code> : <>the authorized keys
                of <code>{user}</code></>}, for example in the node's shell in the Proxmox web UI:
              </p>
              <pre className="key">{`echo '${pub}' >> ${root ? "/root" : `~${user}`}/.ssh/authorized_keys`}</pre>
              {root ? null : (
                <p>
                  The user must exist first: <code>playbooks/prepare-node.yml</code> creates it (see the
                  installation guide). That playbook replaces the user's keys with
                  its <code>proxmox_node_user_ssh_keys</code>, so list this key there.
                </p>
              )}
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

      <section className="card" aria-labelledby="nodes-heading" key={`nodes-${page.loaded}`}>
        <div className="card-body">
          <h2 id="nodes-heading">The Proxmox nodes over SSH</h2>
          <p className="lead">
            Every node a server uses needs an SSH address and a trusted host key: build-installer puts the
            installer ISO there (once for storage the cluster shares), and provisioning writes each VM's seed files
            on its own node. The playbooks check them all before they start.
          </p>
          {!nodes.length ? (
            <p>There are no nodes yet. Set the node name on the <Link href="/proxmox">Proxmox</Link> page.</p>
          ) : null}
        </div>
        {nodes.map((n, i) => (
          <NodePart key={n.name} node={n} index={i} busy={busy} submit={submit} notes={notes} user={user}
                    pending={pending && pending.node === n.name ? pending.keys : null} />
        ))}
      </section>
    </>
  );
}

function NodePart({ node: n, index: i, busy, submit, notes, pending, user }: {
  node: SshNode;
  user: string;
  index: number;
  busy: boolean;
  submit: (event: FormEvent<HTMLFormElement>) => void;
  notes: Record<string, NoticeT | undefined>;
  pending: HostKey[] | null;
}) {
  const heading = `node-${i}-heading`;
  const used = n.default
    ? `The default node${n.servers.length ? `, used by ${n.servers.join(", ")}` : ""}.`
    : n.servers.length ? `Used by ${n.servers.join(", ")}.` : "No server uses it now.";
  return (
    <div className="card-body node-part" role="group" aria-labelledby={heading}>
      <h3 id={heading}>Node <code>{n.name}</code></h3>
      <p className="help">{used}</p>
      <form method="post" action="/api/ssh" onSubmit={submit} className="stack">
        <input type="hidden" name="node" value={n.name} />
        <div className="field narrow">
          <label htmlFor={`node_address-${i}`}>SSH address of {n.name}</label>
          <input type="text" id={`node_address-${i}`} name="node_address" defaultValue={n.address} className="mono"
                 spellCheck={false} autoComplete="off" aria-describedby={`node-address-${i}-help`} />
          <p className="help" id={`node-address-${i}-help`}>
            Host name or IP address, port 22, user <code>{user}</code> (set on the <Link href="/proxmox">Proxmox</Link> page).
          </p>
        </div>
        <div className="actions">
          <Button action="address" busy={busy}>Save the address</Button>
          {!n.in_use ? <Button action="remove" secondary busy={busy}>Remove this node</Button> : null}
        </div>
      </form>
      <Status notice={notes[noteKey("address", n.name)]} />
      {n.address ? (
        <>
          <h4>Host key</h4>
          {n.trusted.length ? (
            <>
              <p>Trusted host keys of {n.address}:</p>
              <ul className="plain-list">
                {n.trusted.map((k) => <li key={k.fingerprint}>{k.keytype} <code className="fp">{k.fingerprint}</code></li>)}
              </ul>
            </>
          ) : <p>The host key of {n.name} isn't trusted yet: the playbooks refuse to connect until it is.</p>}
          {pending ? (
            <form method="post" action="/api/ssh" onSubmit={submit} className="confirm">
              <input type="hidden" name="node" value={n.name} />
              <p>
                {n.address} offers these host keys. <strong>Compare them</strong> with what node {n.name} itself shows
                (in its shell: <code>{'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done'}</code>)
                before you trust them; a mismatch means someone else answered.
              </p>
              <ul className="plain-list">
                {pending.map((k) => <li key={k.fingerprint}>{k.keytype} <code className="fp">{k.fingerprint}</code></li>)}
              </ul>
              <div className="check">
                <input type="checkbox" id={`confirm-${i}`} name="confirm" />
                <label htmlFor={`confirm-${i}`}>The fingerprints match what node {n.name} shows</label>
              </div>
              <div className="actions"><Button action="trust" busy={busy}>Trust these keys</Button></div>
            </form>
          ) : null}
          <form method="post" action="/api/ssh" onSubmit={submit}>
            <input type="hidden" name="node" value={n.name} />
            <div className="actions">
              <Button action="scan" secondary busy={busy}>
                {n.trusted.length || pending ? "Scan again" : "Scan the host key"}
              </Button>
              {n.trusted.length ? (
                <>
                  <Button action="test" secondary busy={busy}>Test the SSH login</Button>
                  <Button action="forget" secondary busy={busy}>Forget the host key</Button>
                </>
              ) : null}
            </div>
          </form>
          <Status notice={notes[noteKey("hostkey", n.name)]} />
        </>
      ) : null}
    </div>
  );
}
