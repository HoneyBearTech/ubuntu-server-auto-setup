// WCAG 2.1 AA contrast of every colour pair the UI uses, read from src/styles/tokens.css for each theme:
// 4.5:1 for text (1.4.3), 3:1 for form borders, icons, the focus ring and other UI parts (1.4.11).

import { describe, expect, it } from "vitest";
import css from "../src/styles/tokens.css?raw";

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  const body = css.slice(start, css.indexOf("}", start));
  const tokens: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)) tokens[name] = value.toLowerCase();
  return tokens;
}

const dark = block(":root");
const light = block(':root[data-theme="light"]');
const system = block(':root[data-theme="system"]');

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function ratio(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const SURFACES = ["bg", "surface", "surface-2"];
const TEXT = 4.5;
const UI = 3;

// [foreground, background(s), minimum]
const PAIRS: [string, string[], number][] = [
  ["text", SURFACES, TEXT],
  ["muted", SURFACES, TEXT],
  ["accent", SURFACES, TEXT], // links
  ["accent-hover", SURFACES, TEXT],
  ["on-accent", ["accent", "accent-hover"], TEXT], // button labels
  ["text", ["accent-soft"], TEXT], // the selected nav item
  ["accent", ["accent-soft"], UI], // its icon
  ["border", SURFACES, UI], // form controls
  ["focus", SURFACES, UI],
  ["success", [...SURFACES, "success-bg"], TEXT],
  ["warning", [...SURFACES, "warning-bg"], TEXT],
  ["error", [...SURFACES, "error-bg"], TEXT],
  ["info", [...SURFACES, "info-bg"], TEXT],
  ["text", ["success-bg", "warning-bg", "error-bg", "info-bg"], TEXT], // notice text
  ["text", ["log-bg"], TEXT],
  ["muted", ["log-bg"], TEXT], // line numbers
  ["accent", ["log-bg"], TEXT], // PLAY lines
  ["success", ["log-bg"], TEXT],
  ["warning", ["log-bg"], TEXT],
  ["error", ["log-bg"], TEXT],
  ["info", ["log-bg"], TEXT],
  ["error", ["error-bg"], UI], // the error summary's border
];

describe.each([["dark", dark], ["light", light]])("the %s theme", (_name, tokens) => {
  it.each(PAIRS)("%s on %j is at least %s:1", (fg, bgs, min) => {
    for (const bg of bgs) {
      expect(tokens[fg], fg).toBeDefined();
      expect(tokens[bg], bg).toBeDefined();
      expect(ratio(tokens[fg], tokens[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(min);
    }
  });
});

describe("the themes", () => {
  it("define the same colour tokens", () => {
    expect(Object.keys(light).sort()).toEqual(Object.keys(dark).sort());
  });

  it("use the light colours for the system theme when the browser prefers light", () => {
    expect(system).toEqual(light);
  });

  it("compute contrast as WCAG does", () => {
    expect(ratio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(ratio("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });
});
