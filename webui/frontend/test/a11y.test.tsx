// Every page the app draws, checked like webui/tests/test_accessibility.py checks the server's HTML (one h1,
// landmarks, a label for every control, unique IDs, references that point at something, button names, the
// skip link), and with axe-core's WCAG 2.1 A and AA rules. Contrast is left to test/contrast.test.ts:
// jsdom doesn't lay pages out, so axe can't measure it here.

import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector(".loading")).toBeNull());
}

const PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/vms", "/run", "/runs", "/account",
  `/runs/${fixtures.running.id}`, `/runs/${fixtures.finished.id}`, "/nowhere"];

describe("every page", () => {
  it.each(PAGES)("%s", async (path) => {
    document.documentElement.lang = "en";
    new FakeApi();
    renderAt(path);
    await loaded();
    checkStructure(document.body, path);
    await checkAxe(path);
  });

  it.each(["/login", "/setup"])("%s", async (path) => {
    new FakeApi();
    renderAt(path);
    await loaded();
    checkStructure(document.body, path);
    await checkAxe(path);
  });
});

describe("pages with errors", () => {
  it("the Proxmox page", async () => {
    const api = new FakeApi({
      "POST /api/proxmox": () => reply(422, { errors: { pve_api_host: "API host is required.", pve_api_timeout: "Too short." } }),
    });
    renderAt("/proxmox");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("There are 2 problems");
    expect(api.posts("/api/proxmox")).toHaveLength(1);
    expect(document.activeElement?.id).toBe("error-summary");
    expect(document.getElementById("f-pve_api_host")?.getAttribute("aria-invalid")).toBe("true");
    expect(document.querySelector("details")?.open).toBe(true); // the advanced field with an error shows
    checkStructure(document.body, "/proxmox with errors");
    await checkAxe("/proxmox with errors");
  });

  it("the servers page", async () => {
    new FakeApi({ "POST /api/servers": () => reply(422, { errors: { "servers-1-name": "Another server already uses this." } }) });
    renderAt("/servers");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Row 2: Another server already uses this.");
    checkStructure(document.body, "/servers with errors");
    await checkAxe("/servers with errors");
  });

  it("the sign-in page", async () => {
    new FakeApi({ "POST /api/login": () => reply(401, { error: "That password is wrong." }) });
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Password"), "nope{Enter}");
    await screen.findByText("That password is wrong.");
    expect(document.activeElement?.id).toBe("error-summary");
    checkStructure(document.body, "/login with an error");
    await checkAxe("/login with an error");
  });

  it("the VMs page, filtered to nothing, sorted, and before the Proxmox API is set up", async () => {
    new FakeApi();
    renderAt("/vms");
    await loaded();
    await userEvent.type(screen.getByLabelText("Find"), "nothing-matches");
    await screen.findByText("No VM matches the filters.");
    await userEvent.click(screen.getByRole("button", { name: "CPU" }));
    checkStructure(document.body, "/vms filtered");
    await checkAxe("/vms filtered");
    cleanup();
    new FakeApi({ "GET /api/vms": () => reply(409, { error: "Set up the Proxmox API first: the host and the token.", setup: "/proxmox" }) });
    renderAt("/vms");
    await screen.findByText(/Proxmox API first/);
    checkStructure(document.body, "/vms before the API is set up");
    await checkAxe("/vms before the API is set up");
  });

  it("the phone menu", async () => {
    new FakeApi();
    renderAt("/");
    await loaded();
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    await act(() => userEvent.click(menu));
    expect(menu.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById("sidebar-panel")?.classList.contains("open")).toBe(true);
    await checkAxe("/ with the menu open");
  });

  it("the phone menu closes with Escape, focus back on its button", async () => {
    new FakeApi();
    renderAt("/");
    await loaded();
    const menu = screen.getByRole("button", { name: "Menu" });
    await act(() => userEvent.click(menu));
    await userEvent.tab();
    expect(document.activeElement?.textContent).toContain("Overview");
    await userEvent.keyboard("{Escape}");
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(menu);
    await userEvent.keyboard("{Escape}"); // closed already: nothing happens
    expect(document.activeElement).toBe(menu);
  });

  it("the phone menu closes when focus leaves it for the page", async () => {
    new FakeApi();
    renderAt("/");
    await loaded();
    const menu = screen.getByRole("button", { name: "Menu" });
    await act(() => userEvent.click(menu));
    act(() => document.getElementById("sidebar-panel")?.querySelector("a")?.focus());
    expect(menu.getAttribute("aria-expanded")).toBe("true"); // within the menu: still open
    act(() => document.getElementById("main")?.focus());
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement?.id).toBe("main");
  });
});
