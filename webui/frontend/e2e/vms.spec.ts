// The VMs page in Chromium, against the mock's fixture VMs (tests/webui-browser.sh starts the mock with
// --fixture-vms): sorting and filtering by keyboard, a refresh that keeps the rows and the focus where they
// are, and axe-core at phone width in the dark and light themes.

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { checkAxe, retype, tabTo } from "./helpers";

async function open(page: Page): Promise<void> {
  await page.goto("/vms");
  await page.locator("#main").waitFor();
  // The first listing waits up to 15 seconds for the guest agents (the fixtures have a slow one).
  await expect(page.getByText("Loading…")).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator("tbody tr").first()).toBeVisible();
}

function names(page: Page): Promise<string[]> {
  return page.locator("tbody th .vm-name").allTextContents();
}

function heading(page: Page, label: string) {
  return page.locator("thead th").filter({ has: page.getByRole("button", { name: label, exact: true }) });
}

test("sorts by keyboard, and says how it is sorted", async ({ page }) => {
  await open(page);
  await expect(heading(page, "VMID")).toHaveAttribute("aria-sort", "ascending");
  const name = page.getByRole("button", { name: "Name", exact: true });
  await tabTo(page, name);
  await page.keyboard.press("Enter");
  await expect(heading(page, "Name")).toHaveAttribute("aria-sort", "ascending");
  await expect(heading(page, "VMID")).not.toHaveAttribute("aria-sort", /.*/);
  const ascending = await names(page);
  expect(ascending, "sorted by name").toEqual([...ascending].sort((a, b) =>
    a.toLowerCase().localeCompare(b.toLowerCase(), undefined, { numeric: true })));
  await expect(page.locator("caption")).toContainText("sorted by Name, ascending");
  // Space works as well as Enter, and focus stays on the button.
  await page.keyboard.press("Space");
  await expect(heading(page, "Name")).toHaveAttribute("aria-sort", "descending");
  await expect(name).toBeFocused();
  expect(await names(page)).toEqual([...ascending].reverse());
});

test("filters by keyboard, and announces the count", async ({ page }) => {
  await open(page);
  const count = page.locator("#vm-count");
  await expect(count).toHaveAttribute("role", "status");
  const total = Number((await count.textContent())?.match(/^(\d+) VMs$/)?.[1]);
  expect(total, "the fixture VMs, the container and the flow's VM").toBeGreaterThanOrEqual(14);

  await tabTo(page, page.getByLabel("Find"));
  await retype(page, "debian");
  await expect(count).toHaveText(`1 of ${total} VMs shown`);
  expect(await names(page)).toEqual(["debian-13"]);
  await retype(page, "nothing matches this");
  await expect(page.getByText("No VM matches the filters.")).toBeVisible();

  // The filter for support: a native select, reached with Tab.
  await retype(page, "");
  const support = page.getByLabel("Playbook support");
  await tabTo(page, support);
  await support.selectOption("override");
  await expect(count).toHaveText(`1 of ${total} VMs shown`);
  expect(await names(page)).toEqual(["jammy"]);

  const clear = page.getByRole("button", { name: "Clear the filters" });
  await tabTo(page, clear);
  await page.keyboard.press("Enter");
  await expect(count).toHaveText(`${total} VMs`);
});

test("shows where each address came from, and guest strings as text", async ({ page }) => {
  await open(page);
  const row = (name: string) => page.locator("tbody tr").filter({ has: page.locator(".vm-name", { hasText: name }) });
  await expect(row("web-2604")).toContainText("from the guest agent");
  await expect(row("web-2604").getByRole("meter", { name: "CPU use of web-2604" })).toBeVisible();
  await expect(row("no-agent")).toContainText("from the cloud-init settings");
  await expect(row("srv-01")).toContainText("from the inventory");
  await expect(row("agent-forbidden")).toContainText("The token may not ask the guest agent");
  await expect(page.getByText(/add VM\.GuestAgent\.Audit/)).toBeVisible();
  // The hostile guest's markup is shown as text, never as an element.
  await expect(row("Bad_Name")).toContainText("<img src=x onerror=alert(1)>");
  expect(await page.locator("main img").count()).toBe(0);
});

test("a refresh keeps the rows and the focus where they are", async ({ page }) => {
  await page.clock.install();
  await open(page);
  const cpu = page.getByRole("button", { name: "CPU", exact: true });
  await tabTo(page, cpu);
  await page.keyboard.press("Enter");
  const before = await names(page);
  const refreshed = page.waitForResponse((r) => r.url().endsWith("/api/vms"));
  await page.clock.runFor(31_000);
  await refreshed;
  await expect(cpu).toBeFocused();
  expect(await names(page)).toEqual(before);
});

for (const theme of ["dark", "light"] as const) {
  test(`axe-core at 375 pixels in the ${theme} theme, without sideways scrolling`, async ({ page, context, baseURL }) => {
    await context.addCookies([{ name: "usas_theme", value: theme, url: baseURL! }]);
    await page.setViewportSize({ width: 375, height: 812 });
    await open(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await checkAxe(page, `/vms at 375 pixels, ${theme}`);
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width, "the page fits the phone's width").toBeLessThanOrEqual(375);
    await page.getByLabel("Find").fill("ubuntu");
    await checkAxe(page, `/vms at 375 pixels, ${theme}, filtered`);
  });
}

// Selecting VMs and getting the run ready, by keyboard. The fixture VMs' addresses don't answer SSH, so the
// server's review stops at the host keys: that is what this checks, with the focus moving as it should.
test("selects VMs and reviews a run by keyboard", async ({ page }) => {
  await open(page);
  const box = page.getByLabel("Select web-2604", { exact: true });
  await tabTo(page, box);
  await page.keyboard.press("Space");
  await expect(box).toBeChecked();
  await expect(page.getByRole("heading", { name: "Run the baseline on 1 selected VM" })).toBeVisible();
  await expect(page.locator("#vm-count")).toContainText("1 selected");
  // A VM that can't be selected says why, and its box can't be reached.
  const windows = page.getByLabel("Select windows-11", { exact: true });
  await expect(windows).toBeDisabled();
  const why = await windows.getAttribute("aria-describedby");
  await expect(page.locator(`#${why}`)).toContainText("Windows");

  const part = page.getByRole("group", { name: /web-2604/ });
  await expect(part.getByLabel("Address")).toHaveValue("10.10.10.21");
  await tabTo(page, part.getByLabel("SSH port"));
  await page.keyboard.type("2222");
  await tabTo(page, page.getByRole("button", { name: "Review the plan" }));
  await page.keyboard.press("Enter");
  const summary = page.locator("#vm-errors");
  await expect(summary).toBeFocused();
  await expect(summary).toContainText("Trust VM 101's host key first");
  await tabTo(page, summary.getByRole("link", { name: /Trust VM 101/ }));
  await page.keyboard.press("Enter");
  await expect(page.locator("#f-vm-101-hostkey")).toBeFocused();
  await checkAxe(page, "/vms with a run being set up");

  // A scan of an address nothing answers on says so, politely, in the VM's part.
  await tabTo(page, part.getByRole("button", { name: "Scan the host key" }));
  await page.keyboard.press("Enter");
  await expect(part.locator(".notice-error")).toBeVisible({ timeout: 30_000 });
  await expect(part.locator(".notice-error")).toContainText("10.10.10.21");
});

for (const theme of ["dark", "light"] as const) {
  test(`a run being set up passes axe-core at 375 pixels in the ${theme} theme`, async ({ page, context, baseURL }) => {
    await context.addCookies([{ name: "usas_theme", value: theme, url: baseURL! }]);
    await page.setViewportSize({ width: 375, height: 812 });
    await open(page);
    await page.getByLabel("Select all VMs shown that can be selected").check();
    await expect(page.getByRole("heading", { name: /Run the baseline on \d+ selected VMs/ })).toBeVisible();
    // The presets are styled radio buttons: chosen with the keyboard, as a user would.
    await page.getByRole("radio", { name: "Custom" }).focus();
    await page.keyboard.press("Space");
    await expect(page.getByRole("radio", { name: "Custom" })).toBeChecked();
    await page.getByRole("button", { name: "Review the plan" }).click();
    await expect(page.locator("#vm-errors")).toBeFocused();
    await checkAxe(page, `/vms run section at 375 pixels, ${theme}`);
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width, "the page fits the phone's width").toBeLessThanOrEqual(375);
  });
}
