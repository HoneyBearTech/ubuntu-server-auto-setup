// What the browser tests share: the test settings from tests/webui-browser.sh, walking a page with the Tab key,
// axe-core, and posting a form the way the app does.

import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

export const PASSWORD = "browser test passphrase";
export const TOKEN_SECRET = "00000000-0000-4000-8000-000000000000";

function need(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} isn't set: run the browser tests with tests/webui-browser.sh`);
  return value;
}

export const env = {
  setupCode: () => need("USAS_SETUP_CODE"),
  pvePort: () => need("USAS_PVE_PORT"),
  pveCa: () => readFileSync(need("USAS_PVE_CA"), "utf8"),
  keysUrl: () => need("USAS_KEYS_URL"),
};

// The axe-core rules the UI must pass: WCAG 2.0 and 2.1, levels A and AA (docs/accessibility.md).
export const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

// One stop of the Tab key: the focused element, and whether its focus can be seen.
export interface Stop {
  key: string; // the same for the same element, within one page load
  label: string;
  ring: boolean; // a focus outline at least 2 pixels wide, on the element or what draws it
  inView: boolean; // the outline is within the viewport
  covered: string; // what is drawn over the outline, if anything (such as a sticky header)
}

// Describes the focused element, or null when focus has left the page (to the browser's own controls).
export async function focused(page: Page): Promise<Stop | null> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body || el === document.documentElement) return null;
    const w = window as unknown as { __stops?: WeakMap<Element, string>; __count?: number };
    w.__stops ??= new WeakMap();
    let key = w.__stops.get(el);
    if (key === undefined) {
      key = String((w.__count = (w.__count ?? 0) + 1));
      w.__stops.set(el, key);
    }
    const input = el as HTMLInputElement;
    const name = el.getAttribute("aria-label") ?? input.labels?.[0]?.textContent ?? el.textContent ?? "";
    const label = `${el.tagName.toLowerCase()}${input.type ? `[${input.type}]` : ""} "${name.replace(/\s+/g, " ").trim().slice(0, 60)}"`;
    // Styled controls draw their ring on the label (theme radios) or the next element (the log's switches).
    const candidates: Element[] = [el, ...Array.from(input.labels ?? [])];
    if (el.nextElementSibling) candidates.push(el.nextElementSibling);
    let ring: Element | null = null;
    for (const c of candidates) {
      for (const pseudo of [null, "::before", "::after"]) {
        const s = getComputedStyle(c, pseudo);
        if (!ring && s.outlineStyle !== "none" && parseFloat(s.outlineWidth) >= 2 && s.visibility !== "hidden") {
          ring = c;
        }
      }
    }
    const target = ring ?? el;
    const r = target.getBoundingClientRect();
    const inView = r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight &&
      r.left < innerWidth;
    let covered = "";
    if (inView) {
      const x = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
      const y = Math.min(Math.max(r.top + Math.min(r.height / 2, 12), 0), innerHeight - 1);
      const top = document.elementFromPoint(x, y);
      // The element's own label may lie over it (a whole card that selects the radio button).
      const ownLabel = Array.from(input.labels ?? []).some((l) => l.contains(top));
      if (top && !ownLabel && !(target.contains(top) || top.contains(target) || el.contains(top))) {
        covered = `${top.tagName.toLowerCase()}${top.className ? `.${String(top.className).split(" ")[0]}` : ""}`;
      }
    }
    return { key, label, ring: !!ring, inView, covered };
  });
}

// Presses Tab until focus wraps around (or leaves the page), and returns each stop. Fails if Tab comes back
// to an element before it has been everywhere: a keyboard trap.
export async function tabCycle(page: Page, max = 200): Promise<Stop[]> {
  const stops: Stop[] = [];
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const stop = await focused(page);
    if (!stop) {
      if (stops.length) return stops;
      continue;
    }
    if (stops.length && stop.key === stops[0].key) return stops;
    const seen = stops.findIndex((s) => s.key === stop.key);
    if (seen >= 0) {
      throw new Error(`Keyboard trap: Tab went back to ${stop.label} after ${stops.slice(seen).map((s) => s.label).join(", ")}`);
    }
    stops.push(stop);
  }
  throw new Error(`Tab didn't leave the page in ${max} presses; the last stops: ${stops.slice(-5).map((s) => s.label).join(", ")}`);
}

// The checks for one page by keyboard: from a fresh load, Tab reaches every control in the order of the page
// with a visible, uncovered focus ring and no trap, and Shift+Tab goes back through the same stops. Returns
// the stops for further checks.
export async function checkKeyboard(page: Page, path: string): Promise<Stop[]> {
  await page.goto(path);
  await page.locator("#main").waitFor();
  await expect(page.getByText("Loading…")).toHaveCount(0);
  const stops = await tabCycle(page);
  const problems = stops.flatMap((s) => [
    ...(s.ring ? [] : [`${s.label}: no visible focus outline`]),
    ...(s.inView ? [] : [`${s.label}: focused but not in view`]),
    ...(s.covered ? [`${s.label}: focus outline covered by ${s.covered}`] : []),
  ]);
  expect(problems, `focus on ${path}`).toEqual([]);
  // Nothing changes the order the page is written in.
  expect(await page.locator("[tabindex]:not([tabindex='0']):not([tabindex='-1'])").count(), "positive tabindex").toBe(0);
  const inOrder = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll<HTMLElement>("*"));
    const w = window as unknown as { __stops: WeakMap<Element, string> };
    const order = all.map((el) => w.__stops.get(el)).filter((k): k is string => k !== undefined).map(Number);
    return order.every((k, i) => i === 0 || k > order[i - 1]);
  });
  expect(inOrder, `Tab follows the order of ${path}`).toBe(true);

  // Back again with Shift+Tab, from where the forward walk ended.
  const back: string[] = [];
  for (let i = 0; i < stops.length * 2 + 4 && back.length < stops.length; i++) {
    await page.keyboard.press("Shift+Tab");
    const stop = await focused(page);
    if (stop && !(back.length === 0 && stop.key === stops[0].key)) back.push(stop.key);
  }
  expect(back, `Shift+Tab on ${path}`).toEqual(stops.map((s) => s.key).reverse());
  return stops;
}

// Presses Tab until the element has focus; fails if it never gets it.
export async function tabTo(page: Page, target: Locator, max = 120): Promise<void> {
  await expect(target, "the element to Tab to").toHaveCount(1);
  for (let i = 0; i < max; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab never reached ${target}`);
}

// Replaces a text field's value by keyboard.
export async function retype(page: Page, text: string): Promise<void> {
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("Backspace");
  if (text) await page.keyboard.type(text);
}

// axe-core on the page as it is now; any WCAG 2.1 A or AA violation fails, with what and where.
export async function checkAxe(page: Page, what: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const found = results.violations.map((v) =>
    `${v.id} (${v.impact}): ${v.help}\n${v.nodes.map((n) => `  ${n.target.join(" ")}: ${n.failureSummary}`).join("\n")}`);
  expect(found, `axe-core on ${what}`).toEqual([]);
}

// Posts a form to the API as the app does (the CSRF token from the page, the page's path), for setting up
// pages the keyboard tests don't fill in themselves.
export async function post(page: Page, path: string, fields: Record<string, string>): Promise<Record<string, unknown>> {
  const reply = await page.evaluate(async ({ path, fields }) => {
    const body = new FormData();
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    const csrf = document.getElementById("root")?.dataset.csrf ?? "";
    const response = await fetch(`/api${path}`, {
      method: "POST", body, headers: { Accept: "application/json", "X-CSRF-Token": csrf, "X-USAS-Page": path },
    });
    return { status: response.status, data: await response.json() as Record<string, unknown> };
  }, { path, fields });
  expect(reply.status, `POST /api${path}: ${JSON.stringify(reply.data)}`).toBe(200);
  return reply.data;
}
