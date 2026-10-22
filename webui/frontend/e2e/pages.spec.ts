// Every page, signed in (the session flow.spec.ts left) and out: axe-core with real layout in each theme and
// accent palette, and the keyboard checks (Tab order, visible focus, no traps); then the phone layout's menu
// and the theme switch by keyboard.

import { expect, test } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { checkAxe, checkKeyboard, retype, tabTo } from "./helpers";

// The pages the sidebar links to, and the account page; the run page from the flow is added below.
const PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/vms", "/run", "/runs", "/account"];

// The themes as the server applies them (the usas_theme cookie writes <html data-theme>); "System" follows the
// browser, so it is checked with the browser preferring each.
const THEMES = [
  { name: "dark", scheme: "dark" },
  { name: "light", scheme: "light" },
  { name: "system", scheme: "light" },
  { name: "system", scheme: "dark" },
] as const;

// The accent palettes the UI offers. There is one, Ember (src/styles/tokens.css); a palette picker would list
// its palettes here, with how to choose one, and every check below would run with each.
const PALETTES = [{ name: "Ember", choose: async (_page: Page) => {} }];

async function runPage(page: Page): Promise<string> {
  const reply = await page.request.get("/api/runs", { headers: { "X-USAS-Page": "/runs" } });
  const { runs } = await reply.json() as { runs: { id: string }[] };
  expect(runs.length, "the flow's run is in the history").toBeGreaterThan(0);
  return `/runs/${runs[0].id}`;
}

async function settled(page: Page): Promise<void> {
  await page.locator("#main").waitFor();
  await expect(page.getByText("Loading…")).toHaveCount(0);
}

for (const theme of THEMES) {
  for (const palette of PALETTES) {
    test.describe(`${theme.name} theme${theme.name === "system" ? ` (browser prefers ${theme.scheme})` : ""}, ${palette.name}`, () => {
      test.use({ colorScheme: theme.scheme, reducedMotion: "reduce" });
      test.beforeEach(async ({ context, baseURL }) => {
        await context.addCookies([{ name: "usas_theme", value: theme.name, url: baseURL! }]);
      });

      test("axe-core on every page", async ({ page }) => {
        const pages = [...PAGES, await runPage(page)];
        for (const path of pages) {
          await page.goto(path);
          await settled(page);
          await palette.choose(page);
          await expect(page.locator("html")).toHaveAttribute("data-theme", theme.name);
          await checkAxe(page, path);
        }
      });

      test("axe-core on forms with errors and on the phone menu", async ({ page }) => {
        await page.goto("/proxmox");
        await settled(page);
        await palette.choose(page);
        const host = page.getByLabel("API host (required)", { exact: true });
        await tabTo(page, host);
        await retype(page, "not a host!");
        await page.keyboard.press("Enter");
        await expect(page.locator("#error-summary")).toBeFocused();
        await checkAxe(page, "/proxmox with an error");

        await page.setViewportSize({ width: 375, height: 812 });
        await page.goto("/servers");
        await settled(page);
        await page.getByRole("button", { name: "Menu" }).click();
        await checkAxe(page, "/servers at 375 pixels with the menu open");
      });

      test("axe-core signed out", async ({ browser, baseURL }) => {
        const page = await signedOut(browser, baseURL!, theme.name, theme.scheme);
        await palette.choose(page);
        await checkAxe(page, "/login");
        await page.context().close();
      });
    });
  }
}

async function signedOut(browser: Browser, baseURL: string, theme: string, scheme: "dark" | "light"): Promise<Page> {
  const context = await browser.newContext({ baseURL, colorScheme: scheme, reducedMotion: "reduce" });
  await context.addCookies([{ name: "usas_theme", value: theme, url: baseURL }]);
  const page = await context.newPage();
  await page.goto("/login");
  await settled(page);
  return page;
}

test.describe("keyboard", () => {
  test("every page: Tab order, visible focus, no traps", async ({ page }) => {
    for (const path of [...PAGES, await runPage(page)]) {
      const stops = await checkKeyboard(page, path);
      // The skip link comes first, then the sidebar, then the page.
      expect(stops[0].label, path).toContain("Skip to the content");
    }
  });

  test("the phone layout: the menu opens, Escape closes it and focus returns to the button", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await checkKeyboard(page, "/hardening");
    await page.goto("/hardening");
    await settled(page);
    const menu = page.getByRole("button", { name: "Menu" });
    await tabTo(page, menu);
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("Enter");
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(nav.getByRole("link", { name: /Overview/ })).toBeFocused();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Escape");
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(nav).toBeHidden();
    await expect(menu).toBeFocused();

    // Open again and walk through it: every link and control in the menu, then on to the page, and no trap.
    await page.keyboard.press("Enter");
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await tabTo(page, page.getByRole("button", { name: "Sign out" }));
    await page.keyboard.press("Tab");
    // Leaving the menu closes it, so it doesn't cover the page that focus moved to.
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#main").locator(":focus")).toHaveCount(1);
  });

  test("the servers table: adding and removing a row keep focus in the table", async ({ page }) => {
    await page.goto("/servers");
    await settled(page);
    const add = page.getByRole("button", { name: "Add a row" });
    await tabTo(page, add);
    await page.keyboard.press("Enter");
    // Focus moves to the new row's first field.
    await expect(page.getByRole("textbox", { name: /, row 2$/ }).first()).toBeFocused();
    await tabTo(page, page.getByRole("button", { name: "Remove row 2" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Row removed" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: /, row 1$/ }).first()).toBeFocused();
  });

  test("the servers table: a server's node is picked from the cluster by keyboard", async ({ page }) => {
    await page.goto("/servers");
    await settled(page);
    // The flow's connection test listed the mock cluster's two nodes; empty is the node on the Proxmox page.
    const node = page.getByRole("combobox", { name: "Node, row 1" });
    await expect(node.locator("option")).toHaveText(["Default (pve-test)", "pve-test", "pve-test-2"]);
    await tabTo(page, node);
    await page.keyboard.type("pve-test-2"); // type-ahead picks the option, on every platform
    await expect(node).toHaveValue("pve-test-2");
    await expect(node).toBeFocused();
  });

  test("the servers table: DHCP by keyboard, and a network card the role would refuse is explained", async ({ page }) => {
    await page.goto("/servers");
    await settled(page);
    const mode = page.getByRole("combobox", { name: "Address, row 1" });
    await tabTo(page, mode);
    await page.keyboard.press("d");  // type-to-select: the same in every browser and platform
    await expect(mode).toHaveValue("dhcp");
    await tabTo(page, page.getByRole("textbox", { name: "Extra network cards, row 1" }));
    await retype(page, "vmbr1 vlan 9999 dhcp");
    await page.keyboard.press("Enter");
    // The summary takes focus and links to the field; nothing was saved.
    const summary = page.getByRole("alert").filter({ hasText: "vlan takes a VLAN tag 1-4094" });
    await expect(summary).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Extra network cards, row 1" })).toHaveAttribute("aria-invalid", "true");
    await checkAxe(page, "/servers with a network card error");
  });

  test("the theme switch: arrow keys choose, the page follows at once", async ({ page }) => {
    await page.goto("/");
    await settled(page);
    const dark = page.getByRole("radio", { name: "Dark" });
    await tabTo(page, dark);
    await expect(dark).toBeChecked();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("radio", { name: "Light" })).toBeFocused();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.keyboard.press("ArrowRight");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "system");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("radio", { name: "System" })).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(dark).toBeFocused();
  });

  test("the UI offers only the accent palettes listed here", async ({ page }) => {
    await page.goto("/");
    await settled(page);
    // If a palette picker appears, add its palettes to PALETTES so every check runs with each of them.
    const pickers = page.getByRole("group", { name: /accent|palette|colour|color/i });
    expect(await pickers.count() > 0 ? await pickers.getByRole("radio").count() : 1).toBe(PALETTES.length);
  });
});
