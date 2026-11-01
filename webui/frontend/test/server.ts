// A fake of the server's API for the tests: answers fetch() from a table of handlers and records each call.

import { render } from "@testing-library/react";
import { createElement } from "react";
import { vi } from "vitest";
import { App } from "../src/App";
import { Router } from "../src/router";
import * as fixtures from "./fixtures";

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: Record<string, string | File>;
}

type Answer = { status?: number; body: unknown };
type Handler = (call: Call) => Answer | unknown;

export function defaultRoutes(): Record<string, Handler> {
  return {
    "GET /api/session": () => fixtures.session,
    "GET /api/account": () => fixtures.account,
    "GET /api/overview": () => fixtures.overview,
    "GET /api/proxmox": () => fixtures.proxmox,
    "GET /api/defaults": () => fixtures.defaults,
    "GET /api/hardening": () => fixtures.hardening,
    "GET /api/servers": () => fixtures.servers,
    "GET /api/clusters": () => fixtures.clusters,
    "GET /api/ssh": () => fixtures.ssh,
    "GET /api/run": () => fixtures.runPage,
    "GET /api/runs": () => ({ runs: fixtures.runs }),
    "GET /api/vms": () => fixtures.vms,
    [`GET /api/runs/${fixtures.running.id}`]: () => ({ run: fixtures.running, log: fixtures.log, offset: 512 }),
    [`GET /api/runs/${fixtures.finished.id}`]: () => ({ run: fixtures.finished, log: fixtures.log, offset: 512 }),
    "POST /api/theme": (c) => ({ theme: c.body.theme }),
  };
}

export class FakeApi {
  calls: Call[] = [];
  routes: Record<string, Handler>;

  constructor(routes: Record<string, Handler> = {}) {
    this.routes = { ...defaultRoutes(), ...routes };
    vi.stubGlobal("fetch", this.fetch);
  }

  on(key: string, handler: Handler): void {
    this.routes[key] = handler;
  }

  posts(path: string): Call[] {
    return this.calls.filter((c) => c.method === "POST" && c.path === path);
  }

  fetch = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(String(input), window.location.href);
    const body: Record<string, string | File> = {};
    if (init.body instanceof FormData) {
      for (const [k, v] of init.body.entries()) body[k] = v;
    }
    const call: Call = { method: init.method ?? "GET", path: url.pathname, headers: { ...(init.headers as Record<string, string>) }, body };
    this.calls.push(call);
    const handler = this.routes[`${call.method} ${call.path}`];
    if (!handler) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    const result = handler(call);
    const answer = (result && typeof result === "object" && "body" in (result as object) && "status" in (result as object))
      ? result as Answer : { body: result };
    return new Response(JSON.stringify(answer.body), {
      status: answer.status ?? 200, headers: { "Content-Type": "application/json" },
    });
  };
}

export function reply(status: number, body: unknown): Answer {
  return { status, body };
}

export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    (this.listeners[type] ??= []).push(listener);
  }

  close(): void {
    this.closed = true;
  }

  emit(data: string): void {
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  end(status: string): void {
    for (const l of this.listeners.end ?? []) l(new MessageEvent("end", { data: status }));
  }
}

// Renders the app at a URL, as the shell would start it.
export function renderAt(path: string) {
  window.history.replaceState({}, "", path);
  return render(createElement(Router, null, createElement(App, { version: "1.2.3" })));
}
