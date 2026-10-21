// Sign-in, the first-run setup and the password change.

import { useState } from "react";
import type { FormEvent } from "react";
import { formData, location_, post } from "../api";
import { ErrorMessage, Notice, PageHead } from "../components/Parts";
import { useRouter } from "../router";
import type { Notice as NoticeT } from "../types";

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

export function Login() {
  const { navigate, search } = useRouter();
  const { error, busy, send } = usePost("/api/login");
  const next = new URLSearchParams(search).get("next") ?? "/";
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    const reply = await send(event);
    if (reply) navigate(reply.redirect ?? "/");
  };
  return (
    <div className="stack">
      <h1 id="page-title" tabIndex={-1}>Sign in</h1>
      <ErrorMessage message={error} />
      <form method="post" action="/api/login" className="stack" onSubmit={submit}>
        <input type="hidden" name="next" value={next} />
        <div className="field">
          <label htmlFor="password">Password</label>
          <input type="password" id="password" name="password" autoComplete="current-password" required autoFocus />
        </div>
        <div className="actions">
          <button type="submit" className="button" aria-disabled={busy || undefined}>Sign in</button>
        </div>
      </form>
      <p className="help">
        Forgot the password? Delete <code>auth.json</code> from the data volume and restart the container to set a
        new one (see the docs).
      </p>
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

export function Account() {
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
    <>
      <PageHead title="Change the password" crumb="Account" />
      <ErrorMessage message={error} />
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
    </>
  );
}
