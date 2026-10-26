// Formatting shared by the pages.

import type { Run } from "./types";

export const STATUS_LABELS: Record<string, string> = {
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  stopped: "Stopped",
  interrupted: "Interrupted",
};

// Times in the viewer's own time zone and locale.
export function when(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString();
}

export function isoTime(epoch: number): string {
  return new Date(epoch * 1000).toISOString();
}

export function seconds(total: number): string {
  const s = Math.max(0, Math.round(total));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function duration(run: Run, now: number = Date.now() / 1000): string {
  if (run.finished) return seconds(run.finished - run.started);
  return run.done ? "—" : seconds(now - run.started);
}

export function runOptions(run: Run): string {
  const parts = [run.check && "check", run.diff && "diff", run.tags?.length && `tags ${run.tags.join(",")}`,
    run.limit && `limit ${run.limit}`].filter(Boolean);
  return parts.length ? parts.join(" ") : "none";
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}
