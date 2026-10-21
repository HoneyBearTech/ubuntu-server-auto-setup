// The colour theme: dark (the default), light, or the browser's preference. The server keeps the choice in a
// cookie and writes it into <html data-theme>, so a page opens in the right theme with no flash.

import { post } from "./api";
import type { Theme } from "./types";

export const THEMES: { value: Theme; label: string }[] = [
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
  { value: "system", label: "System" },
];

export function currentTheme(): Theme {
  const value = document.documentElement.dataset.theme;
  return value === "light" || value === "system" ? value : "dark";
}

// Switches at once: colour transitions are paused for the switch, so nothing fades between the themes.
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.add("theme-switching");
  root.dataset.theme = theme;
  const meta = document.querySelector('meta[name="color-scheme"]');
  meta?.setAttribute("content", theme === "system" ? "dark light" : theme);
  void getComputedStyle(root).color; // apply the new colours before transitions come back
  setTimeout(() => root.classList.remove("theme-switching"), 0);
}

// Shows the theme at once and asks the server to remember it for this browser.
export async function chooseTheme(theme: Theme): Promise<boolean> {
  applyTheme(theme);
  const reply = await post("/api/theme", { theme });
  return reply.ok;
}
