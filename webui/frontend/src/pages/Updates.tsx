// The Updates page: each server's newest result from the web UI's updates runs, the schedules, and the updates
// playbook's settings (the role's defaults, like the Hardening page). A schedule is made on a passing updates
// check run's page (ScheduleForm), after the password again; here it can be deleted.

import { useState } from "react";
import type { FormEvent } from "react";
import { post } from "../api";
import { FieldError } from "../components/Field";
import { FieldSet, groupFields } from "../components/FieldSet";
import { Icon } from "../components/Icons";
import { ErrorMessage, ErrorSummary, Loading, Notice, PageHead } from "../components/Parts";
import { rebootText, updateStatus } from "../components/UpdateResults";
import { useData } from "../data";
import { isoTime, plural, when } from "../format";
import { Link, useRouter } from "../router";
import type { Errors, FieldMeta, Notice as NoticeT, Run, Schedule, UpdatesData } from "../types";
import { FormStatus, useSectionForm } from "./Sections";

export const DAY_NAMES: Record<string, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday",
};

export function windowText(s: Pick<Schedule, "days" | "start" | "minutes" | "timezone">): string {
  const days = s.days.length === 7 ? "Every day" : s.days.map((d) => DAY_NAMES[d] ?? d).join(", ");
  const hours = s.minutes % 60 ? `${s.minutes} minutes` : plural(s.minutes / 60, "hour", "hours");
  return `${days}, from ${s.start} for ${hours} (${s.timezone})`;
}

function Hosts({ hosts }: { hosts: UpdatesData["hosts"] }) {
  const names = Object.keys(hosts).sort();
  return (
    <section className="card" aria-labelledby="updates-hosts-heading">
      <div className="card-body">
        <h2 id="updates-hosts-heading">Servers</h2>
        {names.length ? null : (
          <p>No updates run yet. On the <Link href="/vms">VMs</Link> page, select VMs and choose Updates.</p>
        )}
      </div>
      {names.length ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="updates-hosts-caption">
          <table className="stacked hosts">
            <caption id="updates-hosts-caption" className="visually-hidden">
              Each server's newest updates result, {plural(names.length, "server", "servers")}
            </caption>
            <thead>
              <tr>
                <th scope="col">Server</th>
                <th scope="col">Status</th>
                <th scope="col">Pending</th>
                <th scope="col">Reboot</th>
                <th scope="col">Last run</th>
              </tr>
            </thead>
            <tbody>
              {names.map((name) => {
                const h = hosts[name];
                const r = h.result;
                const s = updateStatus(r);
                const pending = r.planned?.length || r.pending?.length || 0;
                return (
                  <tr key={name}>
                    <th scope="row" data-label="Server"><code>{name}</code>{r.os ? <span className="sub">{r.os}</span> : null}</th>
                    <td data-label="Status">
                      <span className={`badge badge-${s.kind}`}><Icon name={s.icon} />{s.word}</span>
                      {r.reason ? <span className="sub">{r.reason}</span> : null}
                    </td>
                    <td data-label="Pending">
                      {r.status === "updated" ? `${plural(r.upgraded?.length ?? 0, "package", "packages")} upgraded`
                        : pending ? plural(pending, "package", "packages") : "None"}
                      {r.security_count ? <span className="sub">{plural(r.security_count, "security update", "security updates")}</span> : null}
                    </td>
                    <td data-label="Reboot">{r.reboot ? rebootText(r, h.check) : "—"}</td>
                    <td data-label="Last run">
                      <Link href={`/runs/${h.run}`}>{h.check ? "Check" : "Update"}</Link>
                      {h.finished ? <span className="sub"><time dateTime={isoTime(h.finished)}>{when(h.finished)}</time></span> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function Schedules({ data, reload }: { data: UpdatesData; reload: () => void }) {
  const [notice, setNotice] = useState<NoticeT | null>(null);
  const [busy, setBusy] = useState(false);
  const remove = async (event: FormEvent<HTMLFormElement>, s: Schedule) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    const reply = await post<{ message?: string; error?: string }>("/api/updates/schedules", { action: "delete", id: s.id });
    setBusy(false);
    setNotice(reply.ok ? { kind: "ok", message: reply.data.message ?? "Deleted." }
      : { kind: "error", message: reply.data.error ?? `The server answered ${reply.status}.` });
    reload();
  };
  return (
    <section className="card" aria-labelledby="schedules-heading">
      <div className="card-body">
        <h2 id="schedules-heading">Schedules</h2>
        <p>
          A schedule runs the updates playbook on its VMs in a weekly window: the check only, or the check and then,
          if it passed and found nothing surprising, the update. Make one on the page of a passing updates check run
          (VMs page, then <strong>Updates</strong>). It only reaches VMs whose host keys are still pinned.
        </p>
        {data.scheduler ? null : (
          <Notice notice={{ kind: "warning", message: "The scheduler is off (USAS_SCHEDULER=off): schedules run only from the command line." }} />
        )}
        <div aria-live="polite">{notice ? <Notice notice={notice} /> : null}</div>
      </div>
      {data.schedules.length ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="schedules-caption">
          <table className="stacked">
            <caption id="schedules-caption" className="visually-hidden">The schedules, {plural(data.schedules.length, "schedule", "schedules")}</caption>
            <thead>
              <tr>
                <th scope="col">Schedule</th>
                <th scope="col">When</th>
                <th scope="col">Next</th>
                <th scope="col">Last</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.schedules.map((s) => (
                <tr key={s.id}>
                  <th scope="row" data-label="Schedule">
                    {s.name}
                    <span className="sub">{s.kind_label}</span>
                    <span className="sub">{s.targets.map((t) => t.name).join(", ")}</span>
                    <span className="sub">ID <code>{s.id}</code></span>
                  </th>
                  <td data-label="When">{windowText(s)}{s.open ? <span className="sub">Its window is open now</span> : null}</td>
                  <td data-label="Next">{s.next ? <time dateTime={isoTime(s.next)}>{when(s.next)}</time> : "—"}</td>
                  <td data-label="Last">
                    {s.last_outcome || "Not run yet"}
                    {s.last_run ? <span className="sub"><Link href={`/runs/${s.last_run}`}>Run {s.last_run}</Link></span> : null}
                  </td>
                  <td data-label="Actions">
                    <form onSubmit={(e) => remove(e, s)}>
                      <button type="submit" className="button secondary small" aria-disabled={busy || undefined}
                              aria-label={`Delete the schedule ${s.name}`}>
                        Delete
                      </button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="card-body">No schedules.</p>}
    </section>
  );
}

function Settings({ data, reload, loaded }: { data: UpdatesData; reload: () => void; loaded: number }) {
  const form = useSectionForm("/api/updates", reload);
  const groups = groupFields(data.fields, [
    { legend: "What to install", test: (f: FieldMeta) => ["updates_scope", "updates_apt_upgrade", "updates_kernel", "updates_exclude_packages"].includes(f.name) },
    { legend: "Reboots and health", test: (f: FieldMeta) => f.name.startsWith("updates_re") || f.name === "updates_health_services" },
    { legend: "Snapshots", test: (f: FieldMeta) => f.name.startsWith("updates_snapshot") },
    { legend: "Waves and notifications", test: () => true },
  ]);
  return (
    <section className="card" aria-labelledby="updates-settings-heading">
      <form key={loaded} method="post" action="/api/updates" noValidate onSubmit={(e) => form.submit(e)}>
        <div className="card-body">
          <h2 id="updates-settings-heading">Settings</h2>
          <p className="help">
            For every server, in the inventory: the command line, the Run page and the schedules use them. The
            VMs page's choices (what to install, reboots, the snapshot) override them for its runs.
          </p>
          <ErrorSummary errors={form.errors} />
        </div>
        {groups.map((g) => (
          <FieldSet key={g.legend} legend={g.legend} fields={g.fields} values={data.values} errors={form.errors} />
        ))}
        <FormStatus status={form.status} />
        <div className="card-foot">
          <button type="submit" className="button" name="action" value="save" aria-disabled={form.busy || undefined}>
            Save
          </button>
        </div>
      </form>
    </section>
  );
}

export function Updates() {
  const page = useData<UpdatesData>("/api/updates");
  const { data } = page;
  if (!data) return <><PageHead title="Updates" crumb="Playbooks" /><Loading error={page.error} /></>;
  return (
    <>
      <PageHead title="Updates" crumb="Playbooks" />
      <p className="lead">
        Package updates for the servers and the cluster's VMs: what each one had pending at its last run, the
        schedules, and the updates playbook's settings. Runs start on the <Link href="/vms">VMs</Link> page (or the
        Run page), always with a check first.
      </p>
      <Hosts hosts={data.hosts} />
      <Schedules data={data} reload={page.reload} />
      <Settings data={data} reload={page.reload} loaded={page.loaded} />
    </>
  );
}

// On a passing updates check run's page: makes a schedule of the same VMs and choices, after the password.
export function ScheduleForm({ run }: { run: Run }) {
  const info = useData<UpdatesData>("/api/updates");
  const { navigate } = useRouter();
  const [errors, setErrors] = useState<Errors>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = info.data;
  if (!data) return null;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const body = new FormData(event.currentTarget);
    body.append("action", "create");
    body.append("run_id", run.id);
    setBusy(true);
    const reply = await post<{ errors?: Errors; error?: string; redirect?: string }>("/api/updates/schedules", body);
    setBusy(false);
    if (reply.ok && reply.data.redirect) {
      navigate(reply.data.redirect);
      return;
    }
    setErrors(reply.data.errors ?? {});
    setError(reply.data.errors ? null : reply.data.error ?? `The server answered ${reply.status}.`);
  };
  const described = (id: string, help: boolean) =>
    [help ? `${id}-help` : "", errors[id.slice(2)] ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
  return (
    <section className="card" aria-labelledby="schedule-heading">
      <form noValidate onSubmit={submit}>
        <div className="card-body">
          <h2 id="schedule-heading">Schedule this plan</h2>
          <p>
            The same VMs and choices, in a weekly window. Each run is a new check first; an update follows only if the
            check passed and no server failed, couldn't be reached or would lose packages. Each server's own update
            window still applies.
          </p>
          <ErrorSummary errors={errors} id="schedule-errors" />
          <ErrorMessage message={error} id="schedule-error" />
        </div>
        <div className="card-body fields">
          <div className="field">
            <label htmlFor="f-name">Name</label>
            <input type="text" id="f-name" name="name" autoComplete="off" aria-describedby={described("f-name", true)}
                   aria-invalid={errors.name ? true : undefined} />
            <p className="help" id="f-name-help">Such as Nightly web servers.</p>
            <FieldError id="f-name" error={errors.name} />
          </div>
        </div>
        <fieldset className="card-body fieldset">
          <legend>What it does</legend>
          <div className="choices">
            {Object.entries(data.kinds).map(([kind, label], i) => (
              <div className="choice" key={kind}>
                <input type="radio" id={i === 0 ? "f-kind" : `kind-${kind}`} name="kind" value={kind}
                       defaultChecked={kind === "check"} />
                <label htmlFor={i === 0 ? "f-kind" : `kind-${kind}`}>{label}</label>
              </div>
            ))}
          </div>
        </fieldset>
        <fieldset className="card-body fieldset">
          <legend>Days</legend>
          <FieldError id="f-day-mon" error={errors["day-mon"]} />
          <div className="choices">
            {data.days.map((d) => (
              <div className="check" key={d}>
                <input type="checkbox" id={`f-day-${d}`} name={`day-${d}`} value="on"
                       aria-describedby={d === "mon" ? described("f-day-mon", false) : undefined} />
                <label htmlFor={`f-day-${d}`}>{DAY_NAMES[d] ?? d}</label>
              </div>
            ))}
          </div>
        </fieldset>
        <div className="card-body fields">
          <div className="field">
            <label htmlFor="f-start">Start time</label>
            <input type="time" id="f-start" name="start" defaultValue="02:00" required
                   aria-invalid={errors.start ? true : undefined} aria-describedby={described("f-start", false)} />
            <FieldError id="f-start" error={errors.start} />
          </div>
          <div className="field">
            <label htmlFor="f-minutes">Window (minutes)</label>
            <input type="number" id="f-minutes" name="minutes" defaultValue="180" min={data.minutes[0]} max={data.minutes[1]}
                   className="narrow" aria-describedby={described("f-minutes", true)}
                   aria-invalid={errors.minutes ? true : undefined} />
            <p className="help" id="f-minutes-help">From {data.minutes[0]} to {data.minutes[1]}: a run starts only while the window is open.</p>
            <FieldError id="f-minutes" error={errors.minutes} />
          </div>
          <div className="field">
            <label htmlFor="f-timezone">Time zone</label>
            <input type="text" id="f-timezone" name="timezone" defaultValue={data.timezone || "Etc/UTC"} list="schedule-zones"
                   spellCheck={false} autoComplete="off" aria-invalid={errors.timezone ? true : undefined}
                   aria-describedby={described("f-timezone", false)} />
            <datalist id="schedule-zones">{(data.timezones ?? []).map((z) => <option key={z} value={z} />)}</datalist>
            <FieldError id="f-timezone" error={errors.timezone} />
          </div>
          <div className="field">
            <label htmlFor="f-schedule_current">Current password</label>
            <input type="password" id="f-schedule_current" name="schedule_current" autoComplete="current-password"
                   aria-describedby={described("f-schedule_current", true)}
                   aria-invalid={errors.schedule_current ? true : undefined} />
            <p className="help" id="f-schedule_current-help">
              A schedule runs without anyone there, so making one needs the password again.
            </p>
            <FieldError id="f-schedule_current" error={errors.schedule_current} />
          </div>
        </div>
        <div className="card-foot">
          <button type="submit" className="button" aria-disabled={busy || undefined}>Save the schedule</button>
        </div>
      </form>
    </section>
  );
}
