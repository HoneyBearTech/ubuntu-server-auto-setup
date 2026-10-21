import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { formData, post } from "../api";
import { Icon } from "../components/Icons";
import { LogView } from "../components/LogView";
import { ErrorMessage, Loading, Notice, PageHead, StatusBadge } from "../components/Parts";
import { useData } from "../data";
import { duration, isoTime, runOptions, when } from "../format";
import { Link, useRouter } from "../router";
import type { Notice as NoticeT, RunDetailData, RunPageData } from "../types";
import { RunsTable } from "./Overview";

export function RunPage() {
  const page = useData<RunPageData>("/api/run");
  const { navigate } = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data } = page;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const reply = await post<{ redirect?: string; error?: string }>("/api/run", formData(event.currentTarget));
    setBusy(false);
    if (reply.ok && reply.data.redirect) {
      navigate(reply.data.redirect);
    } else {
      setError(reply.data.error ?? `The server answered ${reply.status}.`);
    }
  };

  if (!data) return <><PageHead title="Run a playbook" /><Loading error={page.error} /></>;
  const missing = data.steps.filter((s) => !s.done);
  return (
    <>
      <PageHead title="Run a playbook" crumb="Playbooks" />
      {missing.length ? (
        <Notice notice={{ kind: "warning", message: "Some steps aren't done yet; the run will probably fail:" }}>
          <ul>{missing.map((s) => <li key={s.label}><Link href={s.href}>{s.label}</Link></li>)}</ul>
        </Notice>
      ) : null}
      {data.current_run ? (
        <Notice notice={{ kind: "warning", message: "A run is going. Only one runs at a time." }}>
          <p><Link href={`/runs/${data.current_run.id}`}>Open the {data.current_run.action} run</Link></p>
        </Notice>
      ) : null}
      <ErrorMessage message={error} />
      <p className="lead">
        <strong>Provisioning creates VMs and the installer wipes their disks; the baseline changes SSH and the
        firewall.</strong> Run against one server first (Limit), and try check mode before a real run.
      </p>
      <form className="card" method="post" action="/api/run" noValidate onSubmit={submit}>
        <fieldset className="card-body fieldset">
          <legend>What to run</legend>
          <div className="choices">
            {data.playbooks.map((p, i) => (
              <div className="choice" key={p.key}>
                <input type="radio" id={`pb-${p.key}`} name="playbook" value={p.key} defaultChecked={i === 0}
                       aria-describedby={`pb-${p.key}-help`} />
                <div>
                  <label htmlFor={`pb-${p.key}`}>{p.key}</label>
                  <p className="help" id={`pb-${p.key}-help`}>
                    {p.description} (<code>{p.playbook}{p.extra.length ? ` ${p.extra.join(" ")}` : ""}</code>)
                  </p>
                </div>
              </div>
            ))}
          </div>
        </fieldset>
        <fieldset className="card-body fieldset">
          <legend>Options</legend>
          <div className="fields">
            <div className="check-field">
              <div className="check">
                <input type="checkbox" id="check" name="check" aria-describedby="check-help" />
                <label htmlFor="check">Check mode</label>
              </div>
              <p className="help" id="check-help">(<code>--check</code>) report what would change without changing it</p>
            </div>
            <div className="check-field">
              <div className="check">
                <input type="checkbox" id="diff" name="diff" aria-describedby="diff-help" />
                <label htmlFor="diff">Show differences</label>
              </div>
              <p className="help" id="diff-help">(<code>--diff</code>) show file changes</p>
            </div>
            <div className="field">
              <label htmlFor="limit">Limit</label>
              <input type="text" id="limit" name="limit" list="names" className="mono" spellCheck={false}
                     autoComplete="off" aria-describedby="limit-help" />
              <datalist id="names">{data.names.map((n) => <option key={n} value={n} />)}</datalist>
              <p className="help" id="limit-help">
                (<code>--limit</code>) servers or groups, separated by commas; empty runs on all of them.
                {data.names.length ? ` Known: ${data.names.join(", ")}.` : ""}
              </p>
            </div>
          </div>
        </fieldset>
        <div className="card-foot">
          <button type="submit" className="button" aria-disabled={busy || undefined}>
            <Icon name="play" />Start the run
          </button>
        </div>
      </form>
    </>
  );
}

export function RunsPage() {
  const { data, error } = useData<{ runs: RunDetailData["run"][] }>("/api/runs");
  return (
    <>
      <PageHead title="Run history" crumb="Playbooks"
                actions={<Link className="button" href="/run"><Icon name="play" />Run a playbook</Link>} />
      {!data ? <Loading error={error} /> : data.runs.length ? (
        <RunsTable runs={data.runs} caption={`The last ${data.runs.length} runs, newest first`} />
      ) : <p>No runs yet. <Link href="/run">Start one</Link>.</p>}
    </>
  );
}

function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

export function RunDetail({ id }: { id: string }) {
  const page = useData<RunDetailData>(`/api/runs/${encodeURIComponent(id)}`);
  const { navigate } = useRouter();
  const [ended, setEnded] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeT | null>(null);
  const { data, reload } = page;
  const run = data?.run;
  const running = !!run && !run.done && !ended;
  const now = useNow(running);
  const onEnd = useCallback((status: string) => {
    setEnded(status);
    reload();
  }, [reload]);

  useEffect(() => {
    if (page.error && /no such run/i.test(page.error)) navigate("/runs");
  }, [page.error, navigate]);

  const stop = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const reply = await post<{ message?: string; kind?: NoticeT["kind"] }>(
      `/api/runs/${encodeURIComponent(id)}/stop`, {});
    if (reply.data.message) setNotice({ kind: reply.data.kind ?? "ok", message: reply.data.message });
  };

  if (!data || !run) return <><PageHead title="Run" /><Loading error={page.error} /></>;
  const status = run.done ? run.status : ended ?? run.status;
  return (
    <>
      <PageHead title={`${run.action}${run.check ? " (check mode)" : ""}`} crumb={<>History · run {run.id}</>}
                badge={<span id="run-status" aria-live="polite"><StatusBadge status={status} returncode={run.returncode} /></span>}
                actions={
                  <>
                    <a className="button secondary" href={`/runs/${run.id}/log`} download>
                      <Icon name="download" />Download the log
                    </a>
                    {running ? (
                      <form method="post" action={`/api/runs/${run.id}/stop`} onSubmit={stop}>
                        <button type="submit" className="button danger"><Icon name="stop" />Stop the run</button>
                      </form>
                    ) : null}
                  </>
                } />
      {notice ? <Notice notice={notice} /> : null}
      <dl className="meta">
        <div><dt>Started</dt><dd><time dateTime={isoTime(run.started)}>{when(run.started)}</time></dd></div>
        <div><dt>{running ? "Elapsed" : "Duration"}</dt><dd>{duration(run, now)}</dd></div>
        <div><dt>Options</dt><dd>{runOptions(run)}</dd></div>
        {run.playbook ? <div><dt>Playbook</dt><dd>{run.playbook}</dd></div> : null}
      </dl>
      <LogView key={run.id} initial={data.log}
               stream={!run.done ? `/runs/${encodeURIComponent(run.id)}/stream?offset=${data.offset}` : null}
               onEnd={onEnd} />
    </>
  );
}
