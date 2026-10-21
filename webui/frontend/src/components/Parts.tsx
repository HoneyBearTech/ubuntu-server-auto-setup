// Small parts every page uses: the page heading, notices, the error summary and status badges.

import { useEffect, useRef } from "react";
import type { MouseEvent, ReactNode } from "react";
import { STATUS_LABELS } from "../format";
import type { Errors, Notice as NoticeT } from "../types";
import { Icon } from "./Icons";

export function PageHead({ title, crumb, actions, badge }: {
  title: ReactNode;
  crumb?: ReactNode;
  actions?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        {crumb ? <p className="crumb">{crumb}</p> : null}
        <div className="title-row">
          <h1 id="page-title" tabIndex={-1}>{title}</h1>
          {badge}
        </div>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

const NOTICE_ICONS = { ok: "check", warning: "alert", error: "alert", info: "info" } as const;

// A message after an action. Errors are announced at once (role=alert), the others politely (role=status).
export function Notice({ notice, children }: { notice: NoticeT; children?: ReactNode }) {
  return (
    <div className={`notice notice-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
      <Icon name={NOTICE_ICONS[notice.kind]} />
      <div>
        <p>{notice.message}</p>
        {children}
      </div>
    </div>
  );
}

// The problems with a form, at its top. It takes the focus when it appears, so screen readers read it and
// keyboard users start there; each problem links to its field. label names a field for its error key.
export function ErrorSummary({ errors, label }: { errors: Errors; label?: (key: string) => string }) {
  const ref = useRef<HTMLDivElement>(null);
  const entries = Object.entries(errors);
  useEffect(() => {
    if (Object.keys(errors).length) ref.current?.focus();
  }, [errors]);
  if (!entries.length) return null;
  const jump = (id: string) => (event: MouseEvent) => {
    const target = document.getElementById(id);
    if (!target) return;
    event.preventDefault();
    target.closest("details")?.setAttribute("open", "");
    target.focus();
    target.scrollIntoView?.({ block: "center" });
  };
  return (
    <div className="error-summary" role="alert" tabIndex={-1} id="error-summary" ref={ref}>
      <h2>{entries.length === 1 ? "There is a problem" : `There are ${entries.length} problems`}</h2>
      <ul>
        {entries.map(([key, message]) => {
          const id = label ? key : `f-${key}`;
          return (
            <li key={key}>
              <a href={`#${id}`} onClick={jump(id)}>{label ? `${label(key)}: ${message}` : message}</a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// A single error that isn't about one field, such as a wrong password, at the top of a form.
export function ErrorMessage({ message }: { message: string | null }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  if (!message) return null;
  return (
    <div className="error-summary" role="alert" tabIndex={-1} id="error-summary" ref={ref}>
      <p>{message}</p>
    </div>
  );
}

const BADGE_KINDS: Record<string, string> = {
  running: "info badge-live",
  succeeded: "success",
  failed: "error",
  interrupted: "error",
  stopped: "warning",
};

export function StatusBadge({ status, returncode }: { status: string; returncode?: number | null }) {
  const label = STATUS_LABELS[status] ?? status;
  const exit = returncode !== undefined && returncode !== null && returncode !== 0 ? ` · exit ${returncode}` : "";
  return <span className={`badge badge-${BADGE_KINDS[status] ?? "info"}`}>{label}{exit}</span>;
}

export function DoneBadge({ done }: { done: boolean }) {
  return <span className={`badge ${done ? "badge-success" : "badge-warning"}`}>{done ? "Done" : "To do"}</span>;
}

export function Loading({ error }: { error: string | null }) {
  if (error) return <Notice notice={{ kind: "error", message: error }} />;
  return <p className="loading" role="status">Loading…</p>;
}
