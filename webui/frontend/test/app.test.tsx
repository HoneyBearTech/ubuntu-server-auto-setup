// The app's frame: the theme toggle, the router, focus and titles on a change of page, sign-out, and what
// happens when the server says a sign-in is needed.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { csrf, formData, get, location_, post, setCsrf } from "../src/api";
import { isCurrent } from "../src/components/Layout";
import { duration, plural, runOptions, seconds } from "../src/format";
import { Link, Router, runId } from "../src/router";
import { applyTheme, currentTheme } from "../src/theme";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector(".loading")).toBeNull());
}

describe("the theme", () => {
  it("is dark by default and switches to light, remembered by the server", async () => {
    const api = new FakeApi();
    renderAt("/");
    await loaded();
    expect(currentTheme()).toBe("dark");
    expect((screen.getByLabelText("Dark") as HTMLInputElement).checked).toBe(true);
    await userEvent.click(screen.getByLabelText("Light"));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.querySelector('meta[name="color-scheme"]')?.getAttribute("content")).toBe("light");
    const [call] = api.posts("/api/theme");
    expect(call.body.theme).toBe("light");
    expect(call.headers["X-CSRF-Token"]).toBe("test-token");
    await userEvent.click(screen.getByLabelText("System"));
    expect(document.documentElement.dataset.theme).toBe("system");
    expect(document.querySelector('meta[name="color-scheme"]')?.getAttribute("content")).toBe("dark light");
  });

  it("starts from what the server wrote into the page", async () => {
    document.documentElement.dataset.theme = "light";
    new FakeApi();
    renderAt("/");
    await loaded();
    expect((screen.getByLabelText("Light") as HTMLInputElement).checked).toBe(true);
  });

  it("says so when it couldn't be saved", async () => {
    new FakeApi({ "POST /api/theme": () => reply(403, { error: "The form expired" }) });
    renderAt("/");
    await loaded();
    await userEvent.click(screen.getByLabelText("Light"));
    await screen.findByText(/couldn't be saved/);
    expect(document.documentElement.dataset.theme).toBe("light"); // still shown on this page
  });

  it("treats an unknown value as dark", () => {
    document.documentElement.dataset.theme = "neon";
    expect(currentTheme()).toBe("dark");
    applyTheme("dark");
    expect(document.querySelector('meta[name="color-scheme"]')?.getAttribute("content")).toBe("dark");
  });
});

describe("the router", () => {
  it("changes the page without a reload, titles it and moves focus to its heading", async () => {
    new FakeApi();
    renderAt("/");
    await loaded();
    expect(document.title).toBe("Overview · USAS");
    // The brand spells out the name; the footer too.
    expect(screen.getAllByRole("link", { name: /^usas ?Unattended Server Auto-Setup$/ }).length).toBeGreaterThan(0);
    expect(screen.getByText(/USAS · Unattended Server Auto-Setup · web UI/)).toBeTruthy();
    const nav = screen.getByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByText("Servers"));
    expect(window.location.pathname).toBe("/servers");
    await loaded();
    expect(document.title).toBe("Servers · USAS");
    expect(document.activeElement?.id).toBe("page-title");
    expect(within(nav).getByText("Servers").closest("a")?.getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByText("Overview").closest("a")?.getAttribute("aria-current")).toBeNull();
    act(() => {
      window.history.back();
    });
    await waitFor(() => expect(window.location.pathname).toBe("/"));
    await waitFor(() => expect(document.title).toBe("Overview · USAS"));
  });

  it("leaves modified clicks, downloads and other sites to the browser", async () => {
    render(
      <Router>
        <Link href="/runs">runs</Link>
        <Link href="/runs/x/log" download>log</Link>
        <Link href="https://example.com/">out</Link>
      </Router>,
    );
    const events: boolean[] = [];
    const stop = (e: MouseEvent) => {
      events.push(e.defaultPrevented);
      e.preventDefault(); // jsdom can't follow links anyway
    };
    document.addEventListener("click", stop);
    const user = userEvent.setup();
    await user.click(screen.getByText("runs"));
    expect(window.location.pathname).toBe("/runs");
    window.history.replaceState({}, "", "/");
    await user.keyboard("{Control>}");
    await user.click(screen.getByText("runs"));
    await user.keyboard("{/Control}");
    await user.click(screen.getByText("log"));
    await user.click(screen.getByText("out"));
    document.removeEventListener("click", stop);
    expect(events).toEqual([true, false, false, false]);
    expect(window.location.pathname).toBe("/");
  });

  it("reads run IDs from paths", () => {
    expect(runId("/runs/20261021-140211-a1b2c3")).toBe("20261021-140211-a1b2c3");
    expect(runId("/runs")).toBeNull();
    expect(runId("/runs/a/b")).toBeNull();
    expect(isCurrent("/", "/")).toBe(true);
    expect(isCurrent("/", "/runs")).toBe(false);
    expect(isCurrent("/runs", "/runs/abc")).toBe(true);
  });

  it("shows a page for unknown paths", async () => {
    new FakeApi();
    renderAt("/nowhere");
    await screen.findByRole("heading", { name: "Page not found" });
    expect(document.title).toBe("Not found · USAS");
  });
});

describe("signing out and in", () => {
  it("signs out with the token and goes to the sign-in page", async () => {
    const api = new FakeApi({ "POST /api/logout": () => ({ redirect: "/login" }) });
    renderAt("/");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(location_.assign).toHaveBeenCalledWith("/login"));
    expect(api.posts("/api/logout")[0].headers["X-CSRF-Token"]).toBe("test-token");
  });

  it("goes to the sign-in page when the session has expired", async () => {
    new FakeApi({ "GET /api/servers": () => reply(401, { error: "Sign in first.", redirect: "/login?next=/servers" }) });
    renderAt("/servers");
    await waitFor(() => expect(location_.assign).toHaveBeenCalledWith("/login?next=/servers"));
  });

  it("signs in and returns to the page asked for, with the new session's token", async () => {
    const api = new FakeApi({ "POST /api/login": () => ({ redirect: "/servers", csrf: "new-token" }) });
    renderAt("/login?next=/servers");
    await userEvent.type(await screen.findByLabelText("Password"), "correct horse battery staple{Enter}");
    await waitFor(() => expect(window.location.pathname).toBe("/servers"));
    expect(api.posts("/api/login")[0].body).toEqual({ next: "/servers", password: "correct horse battery staple" });
    expect(csrf()).toBe("new-token");
    await screen.findByRole("navigation", { name: "Main" });
  });

  it("sends a first sign-in to the first-run setup", async () => {
    new FakeApi({ "POST /api/login": () => reply(409, { error: "Set the password first.", redirect: "/setup" }) });
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Password"), "x{Enter}");
    await waitFor(() => expect(location_.assign).toHaveBeenCalledWith("/setup"));
  });

  it("sets the password on the first run and starts with the Proxmox page", async () => {
    const api = new FakeApi({
      "POST /api/setup": () => ({ redirect: "/proxmox", message: "Password set. Start with the Proxmox API.", csrf: "t2" }),
    });
    renderAt("/setup");
    await userEvent.type(screen.getByLabelText("Setup code"), "ABCD-EFGH-JKLM");
    await userEvent.type(screen.getByLabelText("Password"), "a long passphrase here");
    await userEvent.type(screen.getByLabelText("Password again"), "a long passphrase here");
    await userEvent.click(screen.getByRole("button", { name: "Set the password" }));
    await screen.findByText("Password set. Start with the Proxmox API.");
    expect(window.location.pathname).toBe("/proxmox");
    expect(api.posts("/api/setup")[0].body.setup_code).toBe("ABCD-EFGH-JKLM");
  });

  it("shows why the setup failed", async () => {
    new FakeApi({ "POST /api/setup": () => reply(422, { error: "That setup code is wrong." }) });
    renderAt("/setup");
    await userEvent.type(screen.getByLabelText("Setup code"), "WRONG");
    await userEvent.type(screen.getByLabelText("Password"), "a long passphrase here");
    await userEvent.type(screen.getByLabelText("Password again"), "a long passphrase here{Enter}");
    await screen.findByText("That setup code is wrong.");
  });

  it("changes the password", async () => {
    const api = new FakeApi({ "POST /api/account": () => reply(422, { error: "The current password is wrong." }) });
    renderAt("/account");
    await userEvent.type(screen.getByLabelText("Current password", { selector: "#current" }), "nope");
    await userEvent.type(screen.getByLabelText("New password"), "a new long password");
    await userEvent.type(screen.getByLabelText("New password again"), "a new long password");
    await userEvent.click(screen.getByRole("button", { name: "Change the password" }));
    await screen.findByText("The current password is wrong.");
    api.on("POST /api/account", () => ({ message: "Password changed. Other sessions were signed out." }));
    await userEvent.click(screen.getByRole("button", { name: "Change the password" }));
    await screen.findByText("Password changed. Other sessions were signed out.");
    expect((screen.getByLabelText("Current password", { selector: "#current" }) as HTMLInputElement).value).toBe("");
  });
});

describe("the API client", () => {
  it("reports a server that doesn't answer", async () => {
    window.fetch = (() => Promise.reject(new Error("offline"))) as typeof fetch;
    const r = await get("/api/overview");
    expect(r.status).toBe(0);
    expect((r.data as { error: string }).error).toMatch(/didn't answer/);
  });

  it("reports an answer that isn't JSON", async () => {
    window.fetch = (() => Promise.resolve(new Response("Cross-site request refused.", { status: 403 }))) as typeof fetch;
    const r = await post("/api/proxmox", { action: "save" });
    expect(r.ok).toBe(false);
    expect((r.data as { error: string }).error).toBe("Cross-site request refused.");
    window.fetch = (() => Promise.resolve(new Response("", { status: 500 }))) as typeof fetch;
    expect(((await get("/x")).data as { error: string }).error).toBe("The server answered 500.");
  });

  it("sends the button that submitted a form", () => {
    setCsrf("t");
    const form = document.createElement("form");
    form.innerHTML = '<input name="a" value="1"><button name="action" value="test">Test</button>';
    const data = formData(form, form.querySelector("button"));
    expect(Object.fromEntries(data.entries())).toEqual({ a: "1", action: "test" });
    expect(Object.fromEntries(formData(form, null).entries())).toEqual({ a: "1" });
  });

  it("formats durations and options", () => {
    expect(seconds(5)).toBe("5 s");
    expect(seconds(125)).toBe("2 min 5 s");
    expect(seconds(3725)).toBe("1 h 2 min");
    expect(duration(fixtures.finished)).toBe("1 min 48 s");
    expect(duration(fixtures.running, fixtures.running.started + 12)).toBe("12 s");
    expect(duration({ ...fixtures.finished, finished: null })).toBe("—");
    expect(runOptions(fixtures.running)).toBe("diff limit web-01");
    expect(runOptions({ ...fixtures.running, diff: false, limit: "" })).toBe("none");
    expect(plural(1, "line", "lines")).toBe("1 line");
  });
});

