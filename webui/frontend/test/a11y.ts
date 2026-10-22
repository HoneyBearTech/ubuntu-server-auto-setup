// The accessibility checks the page tests share: the document's structure (headings, landmarks, labels) and
// axe-core's WCAG 2.1 A and AA rules.

import axe from "axe-core";
import { expect } from "vitest";

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
