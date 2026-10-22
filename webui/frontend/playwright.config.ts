// Browser tests of the web UI (e2e/), in Chromium with real layout: keyboard use, focus, live regions and
// axe-core. They need the server, the mock Proxmox API and a fresh data directory, which tests/webui-browser.sh
// starts before it runs them (see docs/testing.md). "flow" sets the UI up by keyboard and leaves a signed-in
// session for "pages", which checks every page (and the VMs page in more detail, in vms.spec.ts); "passkeys"
// adds a passkey with a virtual authenticator and signs in with it.
import { defineConfig, devices } from "@playwright/test";

const url = process.env.USAS_URL;
if (!url) throw new Error("Run the browser tests with tests/webui-browser.sh (USAS_URL isn't set).");

export const STATE = "e2e/.auth/state.json";

export default defineConfig({
  testDir: "e2e",
  // One server with one data directory: the tests run in order, one at a time.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["github"]] : "list",
  use: {
    baseURL: url,
    ...devices["Desktop Chrome"],
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "flow", testMatch: "flow.spec.ts" },
    { name: "pages", testMatch: /(pages|vms)\.spec\.ts/, dependencies: ["flow"], use: { storageState: STATE } },
    // Requiring a passkey signs out the session "pages" uses, so this runs after it.
    { name: "passkeys", testMatch: "passkeys.spec.ts", dependencies: ["pages"] },
  ],
});
