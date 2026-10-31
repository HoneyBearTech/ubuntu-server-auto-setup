import { DoneBadge, Loading, PageHead, StatusBadge } from "../components/Parts";
import { Icon } from "../components/Icons";
import { useData } from "../data";
import { duration, isoTime, runOptions, when } from "../format";
import { Link } from "../router";
import type { OverviewData, Run } from "../types";

export function RunsTable({ runs, caption }: { runs: Run[]; caption: string }) {
  return (
    <div className="card table-wrap" tabIndex={0} role="region" aria-labelledby="runs-caption">
      <table className="stacked">
        <caption id="runs-caption">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Started</th>
            <th scope="col">Playbook</th>
            <th scope="col">Options</th>
            <th scope="col">Status</th>
            <th scope="col">Duration</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td data-label="Started">
                <Link href={`/runs/${run.id}`}><time dateTime={isoTime(run.started)}>{when(run.started)}</time></Link>
              </td>
              <td data-label="Playbook">
                {run.action}
                {run.kind === "vms" ? <span className="sub">{run.check ? "VMs page, check" : "VMs page"}</span> : null}
                {run.kind === "updates" ? (
                  <span className="sub">{run.check ? "VMs page, updates check" : "VMs page, updates"}</span>
                ) : null}
              </td>
              <td data-label="Options" className="mono">{runOptions(run)}</td>
              <td data-label="Status"><StatusBadge status={run.status} returncode={run.returncode} /></td>
              <td data-label="Duration" className="mono">{duration(run)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Overview() {
  const { data, error } = useData<OverviewData>("/api/overview");
  const done = data ? data.steps.filter((s) => s.done).length : 0;
  const next = data?.steps.findIndex((s) => !s.done) ?? -1;
  const crumb = data?.node ? `${data.cluster ? `cluster ${data.cluster} · ` : ""}node ${data.node}` : undefined;
  return (
    <>
      <PageHead title="Overview" crumb={crumb}
                actions={<Link className="button" href="/run"><Icon name="play" />Run a playbook</Link>} />
      <p className="lead">
        Work through the steps, then <Link href="/run">run</Link> the playbooks: build the installer once,
        provision the servers, and apply the baseline. Everything is saved in the data volume as an ordinary
        Ansible inventory and an encrypted Vault.
      </p>
      {!data ? <Loading error={error} /> : (
        <>
          <section className="stack" aria-labelledby="steps-heading">
            <h2 className="section-title" id="steps-heading">
              Steps <span className="count">{done} of {data.steps.length} done</span>
            </h2>
            <ol className="steps">
              {data.steps.map((step, i) => (
                <li key={step.label} className={`card step${i === next ? " next" : ""}`}>
                  <div className="step-top">
                    <span className="step-number" aria-hidden="true">{i + 1}</span>
                    <DoneBadge done={step.done} />
                  </div>
                  <Link href={step.href}>{step.label}</Link>
                </li>
              ))}
            </ol>
          </section>
          <section className="stack" aria-labelledby="recent-heading">
            <h2 id="recent-heading">Recent runs</h2>
            {data.current_run ? (
              <p>
                A run is going now:{" "}
                <Link href={`/runs/${data.current_run.id}`}>{data.current_run.action}</Link>.
              </p>
            ) : null}
            {data.runs.length ? <RunsTable runs={data.runs} caption="The last runs, newest first" /> : (
              <p className="lead">No runs yet.</p>
            )}
          </section>
        </>
      )}
    </>
  );
}
