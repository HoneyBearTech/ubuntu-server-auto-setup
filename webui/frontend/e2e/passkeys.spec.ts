// Passkeys in a real browser, with Chromium's virtual authenticator (DevTools WebAuthn domain) standing in for
// a phone or security key that verifies the user: add one by keyboard, sign in with it, require it, and see
// the explanation where the page can't use it. Runs last ("passkeys" project): requiring a passkey signs out
// the session the other tests use.

import { expect, test } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";
import { STATE } from "../playwright.config";
import { PASSWORD, checkAxe, checkKeyboard, tabTo } from "./helpers";

test.describe.configure({ mode: "serial" });

interface Credential {
  credentialId: string;
  isResidentCredential: boolean;
  rpId: string;
  signCount: number;
}

async function virtualAuthenticator(context: BrowserContext, page: Page) {
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true,
      isUserVerified: true, automaticPresenceSimulation: true,
    },
  });
  return {
    credentials: async () => (await cdp.send("WebAuthn.getCredentials", { authenticatorId })).credentials as Credential[],
  };
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
}

async function signInWithPasskey(page: Page) {
  const button = page.getByRole("button", { name: "Sign in with a passkey" });
  await tabTo(page, button);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeFocused();
}

test("add a passkey, sign in with it and require it", async ({ browser }) => {
  const context = await browser.newContext({ storageState: STATE });
  const page = await context.newPage();
  const authenticator = await virtualAuthenticator(context, page);

  await test.step("add a passkey by keyboard", async () => {
    await page.goto("/account");
    await expect(page.getByText("No passkeys yet.")).toBeVisible();
    await tabTo(page, page.getByLabel("Name", { exact: true }));
    await page.keyboard.type("Virtual authenticator");
    await tabTo(page, page.locator("#f-passkey_current"));
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Added the passkey" })).toBeVisible();
    const table = page.getByRole("region", { name: "Your passkeys" });
    await expect(table.getByRole("cell", { name: "Virtual authenticator", exact: true })).toBeVisible();
    const [credential] = await authenticator.credentials();
    expect(credential.rpId).toBe("localhost");
    expect(credential.isResidentCredential).toBe(true);
    await checkAxe(page, "/account with a passkey");
    await checkKeyboard(page, "/account");
  });

  await test.step("sign in with it", async () => {
    await page.goto("/");
    await signOut(page);
    await checkKeyboard(page, "/login");
    await checkAxe(page, "/login with a passkey");
    await page.goto("/login");
    await signInWithPasskey(page);
    await page.goto("/account");
    await expect(page.getByRole("region", { name: "Your passkeys" }).getByText("This session")).toBeVisible();
    expect((await authenticator.credentials())[0].signCount).toBeGreaterThan(0);
  });

  await test.step("require a passkey: the password no longer signs in", async () => {
    const box = page.getByRole("checkbox", { name: "Require a passkey" });
    await tabTo(page, box);
    await page.keyboard.press("Space");
    await expect(box).toBeChecked();
    await tabTo(page, page.getByRole("button", { name: "Save", exact: true }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "A passkey is now required" })).toBeVisible();
    await signOut(page);
    await expect(page.getByText(/signing in with the password is turned off/)).toBeVisible();
    await expect(page.getByLabel("Password")).toHaveCount(0);
    await checkAxe(page, "/login with a passkey required");
    await signInWithPasskey(page);
  });

  await test.step("where passkeys can't be used, the page says why", async () => {
    // 127.0.0.1 is allowed, but passkeys belong to localhost.
    const other = await browser.newContext();
    const there = await other.newPage();
    await there.goto(page.url().replace("localhost", "127.0.0.1").replace(/\/$/, "/login"));
    await expect(there.getByText(/Passkeys here belong to localhost/)).toBeVisible();
    await expect(there.getByRole("button", { name: "Sign in with a passkey" })).toHaveCount(0);
    await other.close();
  });

  await test.step("turn the requirement off and remove the passkey", async () => {
    await page.goto("/account");
    await page.getByRole("checkbox", { name: "Require a passkey" }).uncheck();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "password works again" })).toBeVisible();
    await tabTo(page, page.getByRole("button", { name: "Remove the passkey Virtual authenticator" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Removed the passkey" })).toBeVisible();
    await expect(page.locator("#passkeys-heading")).toBeFocused();
    await expect(page.getByText("No passkeys yet.")).toBeVisible();
  });

  await context.close();
});
