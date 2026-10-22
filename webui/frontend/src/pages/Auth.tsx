// Sign-in (with the password or a passkey), the first-run setup, and the account page: the password change and
// the passkeys.

import { useRef, useState } from "react";
import type { FormEvent, InputHTMLAttributes } from "react";
import { formData, location_, post } from "../api";
import { fieldId } from "../components/Field";
import { Icon } from "../components/Icons";
import { ErrorMessage, ErrorSummary, Loading, Notice, PageHead } from "../components/Parts";
import { errorText, useData } from "../data";
import { isoTime, when } from "../format";
import { createPasskey, getPasskey, passkeyError, passkeyProblem } from "../passkeys";
import type { CreationOptionsJSON, RequestOptionsJSON } from "../passkeys";
import { useRouter } from "../router";
import type { AccountData, Errors, Notice as NoticeT, Passkey, SessionData } from "../types";

interface Reply {
  error?: string;
  message?: string;
  redirect?: string;
}

function usePost(path: string) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return null;
    setBusy(true);
    const reply = await post<Reply>(path, formData(event.currentTarget));
    setBusy(false);
    if (reply.status === 409 && reply.data.redirect) {
      location_.assign(reply.data.redirect);
      return null;
    }
    setError(reply.ok ? null : reply.data.error ?? `The server answered ${reply.status}.`);
    return reply.ok ? reply.data : null;
  };
  return { error, busy, send };
}

// Signs in with a passkey: the server's challenge, the browser's prompt, the server's check. Returns what went
// wrong, or null once signed in.
async function passkeySignIn(next: string, rpId: string): Promise<{ error: string } | { redirect: string }> {
  const start = await post<{ options?: RequestOptionsJSON; error?: string }>("/api/passkeys/login-options", {});
  if (!start.ok || !start.data.options) return { error: errorText(start.data, start.status) };
  let credential: string;
  try {
    credential = await getPasskey(start.data.options);
  } catch (error) {
    return { error: passkeyError(error, rpId) };
  }
  const reply = await post<Reply>("/api/passkeys/login", { credential, next });
  if (!reply.ok) return { error: errorText(reply.data, reply.status) };
  return { redirect: reply.data.redirect ?? "/" };
}

function PasskeyButton({ onClick, busy, secondary }: { onClick: () => void; busy: boolean; secondary?: boolean }) {
  return (
    <button type="button" className={`button${secondary ? " secondary" : ""}`} onClick={onClick}
            aria-disabled={busy || undefined}>
      <Icon name="key" />
      Sign in with a passkey
    </button>
  );
}

export function Login() {
  const { navigate, search } = useRouter();
  const { error, busy, send } = usePost("/api/login");
  const session = useData<SessionData>("/api/session");
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyBusy, setKeyBusy] = useState(false);
  const next = new URLSearchParams(search).get("next") ?? "/";
  const info = session.data?.passkeys;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    setKeyError(null);
    const reply = await send(event);
    if (reply) navigate(reply.redirect ?? "/");
  };
  const withPasskey = async () => {
    if (keyBusy || !info) return;
    setKeyBusy(true);
    const result = await passkeySignIn(next, info.rp_id);
    setKeyBusy(false);
    if ("redirect" in result) navigate(result.redirect);
    else setKeyError(result.error);
  };

  if (!info) {
    return (
      <div className="stack">
        <h1 id="page-title" tabIndex={-1}>Sign in</h1>
        <Loading error={session.error} />
      </div>
    );
  }
  const problem = info.registered || info.required ? passkeyProblem(info.rp_id, info.public_url) : null;
  const offer = (info.registered || info.required) && !problem;
  return (
    <div className="stack">
      <h1 id="page-title" tabIndex={-1}>Sign in</h1>
      <ErrorMessage message={keyError ?? error} />
      {info.required ? (
        <>
          <p>A passkey is required to sign in: signing in with the password is turned off.</p>
          {problem ? <Notice notice={{ kind: "warning", message: problem }} /> : null}
          {offer ? <div className="actions"><PasskeyButton onClick={withPasskey} busy={keyBusy} /></div> : null}
          <p className="help">
            Lost every passkey? Delete <code>passkeys.json</code> from the data volume to sign in with the password
            again (see the docs).
          </p>
        </>
      ) : (
        <>
          <form method="post" action="/api/login" className="stack" onSubmit={submit}>
            <input type="hidden" name="next" value={next} />
            <div className="field">
              <label htmlFor="password">Password</label>
              <input type="password" id="password" name="password" autoComplete="current-password" required
                     autoFocus />
            </div>
            <div className="actions">
              <button type="submit" className="button" aria-disabled={busy || undefined}>Sign in</button>
              {offer ? <PasskeyButton onClick={withPasskey} busy={keyBusy} secondary /> : null}
            </div>
          </form>
          {problem ? <p className="help">{problem}</p> : null}
          <p className="help">
            Forgot the password? Delete <code>auth.json</code> from the data volume and restart the container to set
            a new one (see the docs).
          </p>
        </>
      )}
    </div>
  );
}

function PasswordFields({ newLabel, help }: { newLabel: string; help: string }) {
  return (
    <>
      <div className="field">
        <label htmlFor="password">{newLabel}</label>
        <input type="password" id="password" name="password" autoComplete="new-password" minLength={12} required
               aria-describedby="password-help" />
        <p className="help" id="password-help">{help}</p>
      </div>
      <div className="field">
        <label htmlFor="confirm">{newLabel} again</label>
        <input type="password" id="confirm" name="confirm" autoComplete="new-password" minLength={12} required />
      </div>
    </>
  );
}

export function Setup() {
  const { navigate } = useRouter();
  const { error, busy, send } = usePost("/api/setup");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    const reply = await send(event);
    if (reply) navigate(reply.redirect ?? "/proxmox", { kind: "ok", message: reply.message ?? "Password set." });
  };
  return (
    <div className="stack">
      <h1 id="page-title" tabIndex={-1}>Set the password</h1>
      <p>
        This is the first start. Choose the password for this web UI. It protects the Proxmox API token and the SSH
        key the UI keeps, so make it long and unique.
      </p>
      <p>
        To prove you started the container, enter the setup code from its log: run <code>docker logs usas</code> (or
        look at the window <code>start.ps1</code> opened).
      </p>
      <ErrorMessage message={error} />
      <form method="post" action="/api/setup" className="stack" onSubmit={submit}>
        <div className="field">
          <label htmlFor="setup_code">Setup code</label>
          <input type="text" id="setup_code" name="setup_code" className="mono" autoComplete="one-time-code"
                 spellCheck={false} autoCapitalize="characters" required />
        </div>
        <PasswordFields newLabel="Password" help="At least 12 characters. A passphrase of several words works well." />
        <div className="actions">
          <button type="submit" className="button" aria-disabled={busy || undefined}>Set the password</button>
        </div>
      </form>
    </div>
  );
}

function PasswordCard() {
  const { error, busy, send } = usePost("/api/account");
  const [notice, setNotice] = useState<NoticeT | null>(null);
  const [key, setKey] = useState(0);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    setNotice(null);
    const reply = await send(event);
    if (reply) {
      setNotice({ kind: "ok", message: reply.message ?? "Password changed." });
      setKey((k) => k + 1);
    }
  };
  return (
    <section className="stack" aria-labelledby="password-heading">
      <h2 id="password-heading">Password</h2>
      <ErrorMessage message={error} id="password-error" />
      <div aria-live="polite">{notice ? <Notice notice={notice} /> : null}</div>
      <form key={key} method="post" action="/api/account" className="card narrow" onSubmit={submit}>
        <div className="card-body">
          <div className="field">
            <label htmlFor="current">Current password</label>
            <input type="password" id="current" name="current" autoComplete="current-password" required />
          </div>
          <PasswordFields newLabel="New password"
                          help="At least 12 characters. Changing it signs out every other session." />
        </div>
        <div className="card-foot">
          <button type="submit" className="button" aria-disabled={busy || undefined}>Change the password</button>
        </div>
      </form>
    </section>
  );
}

function TextField({ name, label, help, error, ...input }: InputHTMLAttributes<HTMLInputElement> & {
  name: string;
  label: string;
  help?: string;
  error?: string;
}) {
  const id = fieldId(name);
  const described = [help ? `${id}-help` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} name={name} aria-describedby={described || undefined} aria-invalid={error ? true : undefined}
             {...input} />
      {help ? <p className="help" id={`${id}-help`}>{help}</p> : null}
      {error ? (
        <p className="error" id={`${id}-error`}>
          <Icon name="alert" />
          <span><span className="visually-hidden">Error: </span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}

function PasskeyRows({ data, busy, remove }: { data: AccountData; busy: boolean; remove: (p: Passkey) => void }) {
  if (!data.passkeys.length) return <p>No passkeys yet.</p>;
  return (
    <div className="card table-wrap" tabIndex={0} role="region" aria-labelledby="passkeys-caption">
      <table className="stacked">
        <caption id="passkeys-caption">Your passkeys</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Added</th>
            <th scope="col">Last used</th>
            <th scope="col"><span className="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {data.passkeys.map((p) => (
            <tr key={p.id}>
              <td data-label="Name">
                {p.name}
                {p.backed_up ? <> <span className="badge badge-info">Synced</span></> : null}
                {data.current === p.id ? <> <span className="badge badge-success">This session</span></> : null}
              </td>
              <td data-label="Added"><time dateTime={isoTime(p.created)}>{when(p.created)}</time></td>
              <td data-label="Last used">
                {p.last_used ? <time dateTime={isoTime(p.last_used)}>{when(p.last_used)}</time> : "Never"}
              </td>
              <td data-label="Actions">
                <button type="button" className="button secondary small" aria-label={`Remove the passkey ${p.name}`}
                        aria-disabled={busy || undefined} onClick={() => remove(p)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PasskeysCard() {
  const page = useData<AccountData>("/api/account");
  const [errors, setErrors] = useState<Errors>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeT | null>(null);
  const [busy, setBusy] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const { data } = page;

  const start = () => {
    setErrors({});
    setError(null);
    setNotice(null);
    setBusy(true);
  };
  const done = (reply: { ok: boolean; status: number; data: Reply & { errors?: Errors } }) => {
    setBusy(false);
    if (reply.ok) {
      setNotice({ kind: "ok", message: reply.data.message ?? "Saved." });
      page.reload();
    } else if (reply.data.errors) {
      setErrors(reply.data.errors);
    } else {
      setError(errorText(reply.data, reply.status));
    }
    return reply.ok;
  };

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || !data) return;
    start();
    const begin = await post<Reply & { errors?: Errors; options?: CreationOptionsJSON }>(
      "/api/passkeys/register-options", formData(event.currentTarget));
    if (!begin.ok || !begin.data.options) {
      done(begin);
      return;
    }
    let credential: string;
    try {
      credential = await createPasskey(begin.data.options);
    } catch (problem) {
      setBusy(false);
      setError(passkeyError(problem, data.rp_id));
      return;
    }
    if (done(await post<Reply>("/api/passkeys/register", { credential }))) setFormKey((k) => k + 1);
  };

  const remove = async (passkey: Passkey) => {
    if (busy) return;
    start();
    done(await post<Reply>("/api/passkeys/remove", { id: passkey.id }));
    // The button is gone with its row: focus goes to the section, where the result is announced.
    heading.current?.focus();
  };

  const require = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    start();
    done(await post<Reply>("/api/passkeys/require", formData(event.currentTarget)));
  };

  const title = (
    <h2 id="passkeys-heading" tabIndex={-1} ref={heading} className="section-title">
      Passkeys {data ? <span className="count">{data.passkeys.length}</span> : null}
    </h2>
  );
  if (!data) return <section className="stack" aria-labelledby="passkeys-heading">{title}<Loading error={page.error} /></section>;
  const problem = passkeyProblem(data.rp_id, data.public_url);
  return (
    <section className="stack" aria-labelledby="passkeys-heading">
      {title}
      <p className="lead">
        A passkey signs you in with this device&rsquo;s screen lock, a password manager or a security key, instead
        of the password. The private key never leaves the device; the UI keeps only its public key.
      </p>
      {data.broken ? (
        <Notice notice={{ kind: "error", message: "The passkeys file can't be read, so no passkey works and " +
          "signing in with the password is turned off. Delete passkeys.json from the data volume (see the docs)." }} />
      ) : null}
      <ErrorMessage message={error} id="passkeys-error" />
      <ErrorSummary errors={errors} id="passkeys-errors" />
      <div aria-live="polite">{notice ? <Notice notice={notice} /> : null}</div>
      <PasskeyRows data={data} busy={busy} remove={remove} />
      {problem ? <Notice notice={{ kind: "info", message: problem }} /> : (
        <form key={formKey} method="post" action="/api/passkeys/register-options" className="card narrow"
              onSubmit={add}>
          <div className="card-body">
            <h3>Add a passkey</h3>
            <TextField name="passkey_name" label="Name" help="Where it is, such as the laptop or password manager."
                       error={errors.passkey_name} maxLength={data.max_name} autoComplete="off" required />
            <TextField name="passkey_current" label="Current password" type="password"
                       autoComplete="current-password" error={errors.passkey_current} required />
          </div>
          <div className="card-foot">
            <button type="submit" className="button" aria-disabled={busy || undefined}>
              <Icon name="plus" />
              Add a passkey
            </button>
          </div>
        </form>
      )}
      <form key={`require-${page.loaded}`} method="post" action="/api/passkeys/require" className="card narrow"
            onSubmit={require}>
        <div className="card-body">
          <div className="check-field">
            <div className="check">
              <input type="checkbox" id="f-require_passkey" name="require_passkey"
                     defaultChecked={data.require_passkey} aria-describedby="f-require_passkey-help" />
              <label htmlFor="f-require_passkey">Require a passkey</label>
            </div>
            <p className="help" id="f-require_passkey-help">
              Turns off signing in with the password, and signs out sessions that did. Needs at least one passkey.
              If you lose every passkey, delete <code>passkeys.json</code> from the data volume to sign in with the
              password again.
            </p>
          </div>
        </div>
        <div className="card-foot">
          <button type="submit" className="button secondary" aria-disabled={busy || undefined}>Save</button>
        </div>
      </form>
    </section>
  );
}

export function Account() {
  return (
    <>
      <PageHead title="Sign-in and security" crumb="Account" />
      <PasswordCard />
      <PasskeysCard />
    </>
  );
}
