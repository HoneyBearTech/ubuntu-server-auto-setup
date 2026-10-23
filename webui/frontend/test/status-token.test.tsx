// The account page's status token for dashboards: creating it (shown once, with a Copy button), replacing and
// revoking it (with a confirmation step and the focus where the keyboard expects it), and its problems.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

const TOKEN = "usas_st_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcde";
const existing = {
  ...fixtures.account,
  status_token: { exists: true, broken: false, hint: "AbCd", created: 1792590061, last_used: 1792591331,
                  last_from: "10.0.0.5" },
};

function section() {
  return screen.findByRole("region", { name: "Status for dashboards" });
}

function clipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the status token", () => {
  it("is off until one is created", async () => {
    new FakeApi();
    renderAt("/account");
    const card = await section();
    within(card).getByText(/It is off until you create one/);
    within(card).getByRole("heading", { level: 3, name: "Create a token" });
    expect(within(card).queryByRole("button", { name: "Revoke the token" })).toBeNull();
    expect(within(card).queryByText("Starts with")).toBeNull();
    checkStructure(document.body, "/account status off");
    await checkAxe("/account status off");
  });

  it("creates one, shows it once and copies it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    clipboard(writeText);
    let made = false;
    const api = new FakeApi({
      "GET /api/account": () => (made ? existing : fixtures.account),
      "POST /api/status-token/create": () => {
        made = true;
        return { token: TOKEN, message: "Created the token. Copy it now: it isn't shown again." };
      },
    });
    renderAt("/account");
    const card = await section();
    await userEvent.type(within(card).getByLabelText("Current password"), "secret");
    await userEvent.click(within(card).getByRole("button", { name: "Create a token" }));
    const field = await within(card).findByLabelText("New token");
    expect((field as HTMLInputElement).value).toBe(TOKEN);
    expect((field as HTMLInputElement).readOnly).toBe(true);
    expect(document.activeElement).toBe(field);
    within(card).getByText("Created the token. Copy it now: it isn't shown again.");
    expect(api.posts("/api/status-token/create")[0].body).toEqual({ status_current: "secret" });
    // The page knows about the token now, and the password field is empty again.
    await within(card).findByText("usas_st_AbCd…");
    within(card).getByText(/from 10.0.0.5/);
    expect((within(card).getByLabelText("Current password") as HTMLInputElement).value).toBe("");
    within(card).getByRole("heading", { level: 3, name: "Replace the token" });
    await userEvent.click(within(card).getByRole("button", { name: "Copy the token" }));
    expect(writeText).toHaveBeenCalledWith(TOKEN);
    await within(card).findByText("Copied the token.");
    checkStructure(document.body, "/account status created");
    await checkAxe("/account status created");
  });

  it("selects it when the browser won't copy", async () => {
    clipboard(() => Promise.reject(new Error("denied")));
    new FakeApi({ "POST /api/status-token/create": () => ({ token: TOKEN, message: "Created the token." }) });
    renderAt("/account");
    const card = await section();
    await userEvent.type(within(card).getByLabelText("Current password"), "secret");
    await userEvent.click(within(card).getByRole("button", { name: "Create a token" }));
    const field = await within(card).findByLabelText("New token") as HTMLInputElement;
    field.blur();
    await userEvent.click(within(card).getByRole("button", { name: "Copy the token" }));
    await within(card).findByText(/it is selected, press Ctrl\+C/);
    expect(document.activeElement).toBe(field);
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, TOKEN.length]);
  });

  it("says the password is wrong, linked to the field", async () => {
    new FakeApi({
      "POST /api/status-token/create": () => reply(422, { errors: { status_current: "The current password is wrong." } }),
    });
    renderAt("/account");
    const card = await section();
    await userEvent.type(within(card).getByLabelText("Current password"), "wrong");
    await userEvent.click(within(card).getByRole("button", { name: "Create a token" }));
    const summary = await within(card).findByRole("alert");
    expect(summary.id).toBe("status-errors");
    expect(document.activeElement).toBe(summary);
    await userEvent.click(within(summary).getByRole("link", { name: "The current password is wrong." }));
    expect(document.activeElement?.id).toBe("f-status_current");
    expect(within(card).queryByLabelText("New token")).toBeNull();
    checkStructure(document.body, "/account status error");
    await checkAxe("/account status error");
  });

  it("says when too many passwords were wrong", async () => {
    new FakeApi({ "POST /api/status-token/create": () => reply(429, { error: "Too many failed attempts. Try again in 4 seconds." }) });
    renderAt("/account");
    const card = await section();
    await userEvent.type(within(card).getByLabelText("Current password"), "x");
    await userEvent.click(within(card).getByRole("button", { name: "Create a token" }));
    await within(card).findByText("Too many failed attempts. Try again in 4 seconds.");
  });

  it("revokes it after asking, with the focus on each step", async () => {
    let revoked = false;
    const api = new FakeApi({
      "GET /api/account": () => (revoked ? fixtures.account : existing),
      "POST /api/status-token/revoke": () => {
        revoked = true;
        return { message: "Revoked the token. The status API is off." };
      },
    });
    renderAt("/account");
    const card = await section();
    within(card).getByText("usas_st_AbCd…");
    within(card).getByText(/The old token stops working at once/);
    await userEvent.click(within(card).getByRole("button", { name: "Revoke the token" }));
    const question = within(card).getByRole("group", { name: /Revoke the token\? Dashboards/ });
    expect(document.activeElement).toBe(within(question).getByRole("button", { name: "Revoke it" }));
    checkStructure(document.body, "/account status confirm");
    await checkAxe("/account status confirm");
    await userEvent.click(within(question).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(document.activeElement).toBe(within(card).getByRole("button", { name: "Revoke the token" })));
    expect(api.posts("/api/status-token/revoke")).toHaveLength(0);

    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard("{Enter}");
    await within(card).findByText("Revoked the token. The status API is off.");
    expect(api.posts("/api/status-token/revoke")).toHaveLength(1);
    expect(document.activeElement?.id).toBe("status-heading");
    await within(card).findByRole("heading", { level: 3, name: "Create a token" });
    expect(within(card).queryByRole("button", { name: "Revoke the token" })).toBeNull();
  });

  it("says when revoking didn't work", async () => {
    new FakeApi({
      "GET /api/account": () => existing,
      "POST /api/status-token/revoke": () => reply(403, { error: "The form expired." }),
    });
    renderAt("/account");
    const card = await section();
    await userEvent.click(within(card).getByRole("button", { name: "Revoke the token" }));
    await userEvent.click(within(card).getByRole("button", { name: "Revoke it" }));
    await within(card).findByText("The form expired.");
  });

  it("shows a token that was never used", async () => {
    new FakeApi({ "GET /api/account": () => ({ ...existing, status_token: { ...existing.status_token, last_used: null } }) });
    renderAt("/account");
    const card = await section();
    within(card).getByText("Not yet");
  });

  it("explains a token file that can't be read", async () => {
    new FakeApi({
      "GET /api/account": () => ({
        ...fixtures.account,
        status_token: { exists: true, broken: true, hint: "", created: null, last_used: null, last_from: "" },
      }),
    });
    renderAt("/account");
    const card = await section();
    within(card).getByText(/token file can't be read/);
    within(card).getByRole("button", { name: "Revoke the token" });
    expect(within(card).queryByText("Starts with")).toBeNull();
  });

  it("says when the account can't be loaded", async () => {
    new FakeApi({ "GET /api/account": () => reply(500, { error: "Broken." }) });
    renderAt("/account");
    const card = await section();
    await within(card).findByText("Broken.");
  });
});
