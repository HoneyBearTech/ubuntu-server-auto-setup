import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { formData, post } from "../api";
import { HostResults } from "../components/HostResults";
import { Icon } from "../components/Icons";
import { LogView } from "../components/LogView";
import { ErrorMessage, Loading, Notice, PageHead, StatusBadge } from "../components/Parts";
import { useData } from "../data";
import { duration, isoTime, plural, runOptions, when } from "../format";
import { Link, useRouter } from "../router";
import type { ApplyInfo, IsoPlan, Notice as NoticeT, Run, RunDetailData, RunPageData } from "../types";
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
      <IsoPlanCard plan={data.iso_plan} />
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

// Where build-installer puts the installer ISO for the servers (worked out by the playbooks' own code).
function IsoPlanCard({ plan }: { plan: IsoPlan }) {
  const builders = plan.builders.length;
  return (
    <section className="card" aria-labelledby="iso-plan-heading">
      <div className="card-body">
        <h2 id="iso-plan-heading">Where build-installer puts the installer ISOs and cloud images</h2>
        {plan.pairs.length ? (
          <p>
            ISO storage <code>{plan.storage}</code>
            {plan.shared ? " is shared by the cluster: each ISO is built once, on " : " is each node's own: each ISO is built on "}
            {builders === 1 ? "one node" : `${builders} nodes`}, one per Ubuntu release the servers there install. A cloud
            image is downloaded the same way, into the storage's Import folder. A node already holding the current
            ISO or image is skipped.
          </p>
        ) : <p>No server has a node yet: add servers on the <Link href="/servers">Servers</Link> page.</p>}
        {!plan.known && plan.pairs.length ? (
          <p className="help">
            Test the connection on the <Link href="/proxmox">Proxmox</Link> page to find out whether {plan.storage} is
            shared; until then it is shown as each node's own. The playbooks ask Proxmox again when they run.
          </p>
        ) : null}
      </div>
      {plan.pairs.length ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="iso-plan-caption">
          <table>
            <caption id="iso-plan-caption" className="visually-hidden">The nodes the servers use, and which node builds
              each installer ISO, or downloads each cloud image, they need</caption>
            <thead>
              <tr>
                <th scope="col">Node</th>
                <th scope="col">Servers</th>
                <th scope="col">ISO storage</th>
                <th scope="col">Installer ISO or cloud image</th>
                <th scope="col">Built by</th>
              </tr>
            </thead>
            <tbody>
              {plan.pairs.map((p) => (
                <tr key={`${p.node}/${p.storage}/${p.iso}`}>
                  <th scope="row"><code>{p.node}</code>{p.address ? null : <span className="help"> (no SSH address yet)</span>}</th>
                  <td>{p.servers.join(", ")}</td>
                  <td><code>{p.storage}</code>{p.shared ? " (shared)" : ""}</td>
                  <td><code>{p.iso}</code>{p.image ? " (cloud image)" : ""}</td>
                  <td>{p.built_by === p.node ? "this node" : <code>{p.built_by}</code>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {plan.pairs.length ? (
        <div className="card-body">
          <p className="help">
            With a limit, only the servers in it count, and a node builds only if it is in the limit too (add its
            name, such as <code>{plan.pairs[0].node}</code>); a node left out is skipped, and the run says so.
          </p>
        </div>
      ) : null}
    </section>
  );
}

export function RunsPage() {
  const { data, error } = useData<{ runs: RunDetailData["run"][] }>("/api/runs");
  return (
    <>
      <PageHead title="Run history" crumb="Playbooks"
                actions={
                  <>
                    <a className="button secondary" href="/audit.jsonl" download>
                      <Icon name="download" />Download the audit log
                    </a>
                    <Link className="button" href="/run"><Icon name="play" />Run a playbook</Link>
                  </>
                } />
      <p className="lead">
        The last 200 runs, with their logs. The audit log records what was done to VMs from the VMs page (pinned
        host keys, check runs, real runs and their results), with the session and address it came from.
      </p>
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

// After a VMs page check run that passed: the same plan for real, within 30 minutes, once, after a confirmation.
// For the updates playbook the confirmation names the VMs that may reboot.
function ApplyPart({ run, apply }: { run: Run; apply: ApplyInfo }) {
  const { navigate } = useRouter();
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hosts = run.targets ?? [];
  const updates = run.kind === "updates";
  const reboots = updates && run.options?.updates_reboot === "if_required";
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const reply = await post<{ redirect?: string; error?: string }>("/api/vms/apply",
      confirm ? { run_id: run.id, confirm: "on" } : { run_id: run.id });
    setBusy(false);
    if (reply.ok && reply.data.redirect) navigate(reply.data.redirect);
    else setError(reply.data.error ?? `The server answered ${reply.status}.`);
  };
  return (
    <section className="card" aria-labelledby="apply-heading">
      <div className="card-body">
        <h2 id="apply-heading">Run it for real</h2>
        {apply.possible ? (
          <form noValidate onSubmit={submit} className="stack">
            <ErrorMessage message={error} id="apply-error" />
            {updates ? (
              <p>
                The same plan without check mode: the same {plural(hosts.length, "VM", "VMs")}, choices and pinned
                host keys. It installs what the results above list, a wave at a time
                {apply.until ? <>; possible until <time dateTime={isoTime(apply.until)}>{when(apply.until)}</time></> : null}.
              </p>
            ) : (
              <p>
                The same plan without check mode: the same {plural(hosts.length, "VM", "VMs")}, settings, parts and
                pinned host keys. It changes them as the results above say
                {apply.until ? <>; possible until <time dateTime={isoTime(apply.until)}>{when(apply.until)}</time></> : null}.
              </p>
            )}
            <ul className="plain-list">
              {hosts.map((t) => <li key={t.vmid}><code>{t.name}</code> (VM {t.vmid}{t.address ? `, ${t.address}` : ""})</li>)}
            </ul>
            {updates ? (
              reboots ? (
                <Notice notice={{ kind: "warning", message: `These VMs may reboot, inside their reboot window, if an update asks for it: ${
                  hosts.map((t) => t.name).join(", ")}.` }} />
              ) : <p>Nothing reboots (reboots: never).</p>
            ) : null}
            <div className="check">
              <input type="checkbox" id="apply-confirm" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
              <label htmlFor="apply-confirm">
                {updates
                  ? `I have read what would be installed, and want to update ${hosts.length === 1 ? "this VM" : `these ${hosts.length} VMs`} now${reboots ? `, rebooting ${hosts.length === 1 ? "it" : "them"} if needed` : ""}`
                  : `I have read what would change, and want to change ${hosts.length === 1 ? "this VM" : `these ${hosts.length} VMs`} now`}
              </label>
            </div>
            <div className="actions">
              <button type="submit" className="button danger" aria-disabled={busy || undefined}>
                <Icon name="play" />{updates ? "Install the updates for real" : "Run the baseline for real"}
              </button>
            </div>
          </form>
        ) : (
          <p>{apply.problem} <Link href="/vms">Open the VMs page</Link></p>
        )}
      </div>
    </section>
  );
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
        {run.kind === "vms" || run.kind === "updates" ? (
          <div><dt>Started from</dt><dd>The VMs page{run.kind === "updates" ? " (updates)" : ""}</dd></div>
        ) : null}
        {run.applied_from ? (
          <div><dt>Checked by</dt><dd><Link href={`/runs/${run.applied_from}`}>check run {run.applied_from}</Link></dd></div>
        ) : null}
      </dl>
      {data.apply && !running ? <ApplyPart run={run} apply={data.apply} /> : null}
      {run.kind === "vms" || run.kind === "updates" || data.hosts ? (
        <HostResults runId={run.id} initial={data.hosts ?? null} targets={run.targets ?? []} running={running}
                     check={run.check} />
      ) : null}
      <LogView key={run.id} initial={data.log}
               stream={!run.done ? `/runs/${encodeURIComponent(run.id)}/stream?offset=${data.offset}` : null}
               onEnd={onEnd} />
    </>
  );
}
