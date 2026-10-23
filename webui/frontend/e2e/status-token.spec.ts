// The status token for dashboards in a real browser: create it by keyboard (the new token gets the focus), use
// it as a dashboard would (another client, no cookie), check the section with axe-core in both themes, then
// revoke it by keyboard through the confirmation step and see the API answer that it is off.

import { expect, request, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { PASSWORD, checkAxe, tabTo } from "./helpers";

test.describe.configure({ mode: "serial" });

async function dashboard(baseURL: string, token: string) {
  const client = await request.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  const reply = await client.get("/api/status", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const answer = { status: reply.status(), body: await reply.json() as Record<string, unknown>,
                   cookie: reply.headers()["set-cookie"] };
  await client.dispose();
  return answer;
}

async function settled(page: Page): Promise<void> {
  await page.locator("#main").waitFor();
  await expect(page.getByText("Loading…")).toHaveCount(0);
}

test("create the token, use it from a dashboard, revoke it", async ({ page, baseURL, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/account");
  await settled(page);
  const section = page.getByRole("region", { name: "Status for dashboards" });
  expect((await dashboard(baseURL!, "")).status, "off before a token exists").toBe(404);

  let token = "";
  await test.step("create it by keyboard", async () => {
    await tabTo(page, section.getByLabel("Current password"));
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");
    const field = section.getByLabel("New token");
    await expect(field).toBeFocused();
    token = await field.inputValue();
    expect(token).toMatch(/^usas_st_[A-Za-z0-9_-]{43}$/);
    await expect(section.getByRole("status").filter({ hasText: "Created the token" })).toBeVisible();
    await expect(section.getByText(`usas_st_${token.slice(8, 12)}…`)).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(section.getByRole("button", { name: "Copy the token" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(section.getByText(/Copied the token|it is selected/)).toBeVisible();
  });

  await test.step("a dashboard reads the status with it", async () => {
    await expect(section.getByText("Not yet")).toBeVisible();
    const answer = await dashboard(baseURL!, token);
    expect(answer.status).toBe(200);
    expect(answer.cookie, "no session for a dashboard").toBeUndefined();
    expect(Object.keys(answer.body).sort()).toEqual(
      ["fetched", "last_run", "running", "schema", "servers", "setup", "version", "vms"]);
    expect(answer.body.schema).toBe(1);
    expect((answer.body.last_run as { playbook: string }).playbook, "the flow's run").toBeTruthy();
    expect((await dashboard(baseURL!, `${token.slice(0, -1)}x`)).status).toBe(401);
    await page.reload();
    await settled(page);
    await expect(section.getByText(/from (127\.0\.0\.1|::1)/)).toBeVisible();
  });

  await test.step("axe-core on the section in both themes", async () => {
    for (const theme of ["dark", "light"]) {
      await context.addCookies([{ name: "usas_theme", value: theme, url: baseURL! }]);
      await page.reload();
      await settled(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await checkAxe(page, `/account with a status token (${theme})`);
      await section.getByRole("button", { name: "Revoke the token" }).click();
      await checkAxe(page, `/account asking to revoke (${theme})`);
      await section.getByRole("button", { name: "Cancel" }).click();
      await expect(section.getByRole("button", { name: "Revoke the token" })).toBeFocused();
    }
  });

  await test.step("revoke it by keyboard", async () => {
    await page.reload();
    await settled(page);
    await tabTo(page, section.getByRole("button", { name: "Revoke the token" }));
    await page.keyboard.press("Enter");
    await expect(section.getByRole("button", { name: "Revoke it" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(section.getByRole("status").filter({ hasText: "Revoked the token" })).toBeVisible();
    await expect(section.getByRole("heading", { level: 2, name: "Status for dashboards" })).toBeFocused();
    await expect(section.getByRole("heading", { level: 3, name: "Create a token" })).toBeVisible();
    expect((await dashboard(baseURL!, token)).status).toBe(404);
  });
});
