// The UI set up and used by keyboard only, as someone who doesn't use a mouse would: the first-run setup, the
// Proxmox connection with its CA, picking the node from the dropdown, starting a run and following its output,
// signing out and in. Leaves a signed-in session (STATE) for pages.spec.ts.

import { expect, test } from "@playwright/test";
import { STATE } from "../playwright.config";
import { PASSWORD, TOKEN_SECRET, checkKeyboard, env, focused, post, retype, tabTo } from "./helpers";

test.describe.configure({ mode: "serial" });

test("set up, connect, pick the node and run a playbook by keyboard", async ({ page }) => {
  await test.step("the skip link moves focus to the page", async () => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/setup$/);
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to the content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Setup code")).toBeFocused();
  });

  await test.step("first-run setup", async () => {
    await checkKeyboard(page, "/setup");
    await page.goto("/setup");
    await tabTo(page, page.getByLabel("Setup code"));
    await page.keyboard.type(env.setupCode());
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Password again")).toBeFocused();
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/proxmox$/);
    await expect(page.getByRole("status").filter({ hasText: "Password set" })).toBeVisible();
    // After a change of page, focus is on the new page's heading, so a screen reader announces it.
    await expect(page.getByRole("heading", { level: 1, name: "Proxmox API" })).toBeFocused();
  });

  await test.step("the Proxmox form", async () => {
    await checkKeyboard(page, "/proxmox");
    await page.goto("/proxmox");
    const typeInto = async (label: string, text: string) => {
      await tabTo(page, page.getByLabel(label, { exact: true }));
      await retype(page, text);
    };
    await typeInto("API host (required)", "127.0.0.1");
    await typeInto("API port", env.pvePort());
    await typeInto("API user", "ansible@pve");
    await typeInto("Token name (required)", "test");
    await typeInto("Token secret (required)", TOKEN_SECRET);
    await typeInto("Node name (required)", "pve-test");
    // Enter in a field sends the form with its first button, Save.
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Saved the Proxmox settings." })).toBeVisible();
    await tabTo(page, page.getByRole("button", { name: "Test the connection" }));
    await page.keyboard.press("Enter");
    // The test fails on the mock's certificate until its CA is saved; the error is announced.
    await expect(page.getByRole("alert").filter({ hasText: "couldn't be verified" })).toBeVisible();

    await tabTo(page, page.getByLabel("CA certificate (PEM)"));
    await page.keyboard.insertText(env.pveCa());
    await tabTo(page, page.getByRole("button", { name: "Save the certificate" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Saved the CA certificate" })).toBeVisible();

    await tabTo(page, page.getByRole("button", { name: "Test the connection" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Connected to Proxmox VE" })).toBeVisible();
  });

  await test.step("pick the node from the dropdown", async () => {
    const node = page.getByRole("combobox", { name: "Node name (required)" });
    await tabTo(page, node);
    await expect(node).toHaveValue("pve-test");
    // A native select: the arrow keys choose, Escape leaves focus where it is, Tab moves on.
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Escape");
    await expect(node).toBeFocused();
    await expect(node).toHaveValue("pve-test");
    await page.keyboard.press("Tab");
    await expect(node).not.toBeFocused();
    expect(await focused(page)).not.toBeNull();

    await tabTo(page, page.getByRole("button", { name: "Save", exact: true }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Saved the Proxmox settings." })).toBeVisible();
  });

  await test.step("the other pages, as the smoke test fills them", async () => {
    await post(page, "/ssh", { action: "generate" });
    const defaults = {
      github_user: "test", github_keys_url: env.keysUrl(), admin_extra_ssh_keys: "", authorize_ui_key: "on",
      admin_user: "sysadmin", admin_password: "admin password", timezone: "America/Chicago",
      net_gateway: "10.10.10.1", net_prefix: "24", net_dns: "10.10.10.1", net_search: "example.lan",
      autoinstall_storage_layout: "lvm",
    };
    await post(page, "/defaults", { action: "save", ...defaults });
    await post(page, "/servers", {
      server_count: "1", "servers-0-name": "srv-01", "servers-0-ansible_host": "10.10.10.61",
      "servers-0-pve_vmid": "4061", "servers-0-firewall_ports": "443/tcp",
    });
  });

  await test.step("start a run", async () => {
    await checkKeyboard(page, "/run");
    await page.goto("/run");
    // The playbooks are radio buttons: Tab reaches the group, the arrow keys choose.
    const first = page.getByRole("group", { name: "What to run" }).getByRole("radio").first();
    await tabTo(page, first);
    const provision = page.getByRole("radio", { name: "provision" });
    for (let i = 0; i < 5 && !(await provision.isChecked()); i++) await page.keyboard.press("ArrowDown");
    await expect(provision).toBeChecked();
    await expect(provision).toBeFocused();
    await tabTo(page, page.getByLabel("Limit"));
    await page.keyboard.type("srv-01");
    await tabTo(page, page.getByRole("button", { name: "Start the run" }));
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/runs\/[0-9-]+[a-f0-9]{6}$/);
    await expect(page.getByRole("heading", { level: 1, name: "provision" })).toBeFocused();
  });

  await test.step("follow the output: announced politely, focus left alone", async () => {
    const status = page.locator("#run-status");
    await expect(status).toHaveAttribute("aria-live", "polite");
    // The log itself isn't live (a screen reader would read every line); it is a named, focusable region.
    const log = page.getByRole("region", { name: "The lines of the output" });
    await expect(log).not.toHaveAttribute("aria-live", /.+/);
    await expect(log).toHaveAttribute("tabindex", "0");
    await page.evaluate(() => {
      const el = document.getElementById("run-status");
      (window as unknown as { __status?: Element | null }).__status = el;
    });
    // Focus on the "Follow the output" switch while lines arrive and the run ends: it must stay there.
    const follow = page.getByRole("checkbox", { name: "Follow the output" });
    await tabTo(page, follow);
    await expect(status).toContainText("Succeeded", { timeout: 180_000 });
    await expect(log).toContainText("PLAY RECAP");
    await expect(follow).toBeFocused();
    // The status changed inside the live region that was there from the start, so it is announced.
    expect(await page.evaluate(() =>
      (window as unknown as { __status?: Element | null }).__status === document.getElementById("run-status"))).toBe(true);
    // The finished log scrolls with the keyboard.
    await tabTo(page, log);
    await expect(log).toBeFocused();
  });

  await page.context().storageState({ path: STATE });
});

test("sign in by keyboard", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  // The password field has focus when the page opens.
  await expect(page.getByLabel("Password")).toBeFocused();
  await checkKeyboard(page, "/login");
  await page.goto("/login");
  // Typing before the page has focused the field would send the keys nowhere.
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.keyboard.type("not the password");
  await page.keyboard.press("Enter");
  // A wrong password: the message takes focus, so it is read out and keyboard users start there.
  await expect(page.locator("#error-summary")).toBeFocused();
  await tabTo(page, page.getByLabel("Password"));
  await retype(page, PASSWORD);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeFocused();
  await context.close();
});
