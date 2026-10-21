// Every page the app draws, checked like webui/tests/test_accessibility.py checks the server's HTML (one h1,
// landmarks, a label for every control, unique IDs, references that point at something, button names, the
// skip link), and with axe-core's WCAG 2.1 A and AA rules. Contrast is left to test/contrast.test.ts:
// jsdom doesn't lay pages out, so axe can't measure it here.

import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

export function checkStructure(root: HTMLElement, path: string): void {
  const all = [...root.querySelectorAll<HTMLElement>("*")];
  expect(root.querySelectorAll("h1").length, `${path}: one h1`).toBe(1);
  for (const tag of ["header", "main", "footer"]) expect(root.querySelector(tag), `${path}: ${tag}`).not.toBeNull();
  const skip = root.querySelector('a.skip[href="#main"]');
  expect(skip, `${path}: skip link`).not.toBeNull();
  const ids = all.map((e) => e.id).filter(Boolean);
  const duplicates = ids.filter((id, i) => ids.indexOf(id) !== i);
  expect(duplicates, `${path}: duplicate ids`).toEqual([]);
  const known = new Set(ids);
  for (const el of all) {
    for (const attr of ["aria-describedby", "aria-labelledby", "aria-controls"]) {
      for (const ref of (el.getAttribute(attr) ?? "").split(/\s+/).filter(Boolean)) {
        expect(known.has(ref), `${path}: ${attr}=${ref}`).toBe(true);
      }
    }
    if (el instanceof HTMLLabelElement && el.htmlFor) expect(known.has(el.htmlFor), `${path}: label for=${el.htmlFor}`).toBe(true);
    if (el instanceof HTMLAnchorElement && el.getAttribute("href")?.startsWith("#")) {
      expect(known.has(el.getAttribute("href")!.slice(1)), `${path}: link ${el.getAttribute("href")}`).toBe(true);
    }
    if (el.matches("input:not([type=hidden]), select, textarea")) {
      const labelled = (el.id && root.querySelector(`label[for="${el.id}"]`)) || el.closest("label") || el.getAttribute("aria-label");
      expect(labelled, `${path}: label for ${el.outerHTML.slice(0, 80)}`).toBeTruthy();
    }
    if (el instanceof HTMLButtonElement) {
      expect((el.getAttribute("aria-label") ?? el.textContent ?? "").trim(), `${path}: button name`).not.toBe("");
    }
  }
}

export async function checkAxe(path: string): Promise<void> {
  const result = await axe.run(document.body, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
    rules: { "color-contrast": { enabled: false } },
  });
  const problems = result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(problems, path).toEqual([]);
}

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector(".loading")).toBeNull());
}

const PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/run", "/runs", "/account",
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
    await userEvent.type(screen.getByLabelText("Password"), "nope{Enter}");
    await screen.findByText("That password is wrong.");
    expect(document.activeElement?.id).toBe("error-summary");
    checkStructure(document.body, "/login with an error");
    await checkAxe("/login with an error");
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
});
