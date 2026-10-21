// Loads a page's data from the API, and loads it again after a change.

import { useCallback, useEffect, useState } from "react";
import { get } from "./api";

export interface PageData<T> {
  data: T | null;
  error: string | null;
  // Counts the loads that succeeded: forms use it as their key, so they show the saved values after a save.
  loaded: number;
  reload: () => void;
}

export function errorText(data: unknown, status: number): string {
  const error = (data as { error?: unknown })?.error;
  return typeof error === "string" && error ? error : `The server answered ${status}.`;
}

export function useData<T>(path: string): PageData<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loaded: number }>(
    { data: null, error: null, loaded: 0 });
  const [asked, setAsked] = useState(0);

  useEffect(() => {
    let live = true;
    get<T>(path).then((reply) => {
      if (!live) return;
      if (reply.ok) {
        setState((s) => ({ data: reply.data, error: null, loaded: s.loaded + 1 }));
      } else if (reply.status !== 401) {
        setState((s) => ({ ...s, error: errorText(reply.data, reply.status) }));
      }
    });
    return () => {
      live = false;
    };
  }, [path, asked]);

  const reload = useCallback(() => setAsked((n) => n + 1), []);
  return { ...state, reload };
}
