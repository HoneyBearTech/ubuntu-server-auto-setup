// Passkeys: the base64url helpers, when the page can use them, the browser calls (navigator.credentials stands
// in for the authenticator), the sign-in page and the account page.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPasskey, fromBase64url, getPasskey, passkeyError, passkeyProblem, toBase64url } from "../src/passkeys";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

const bytes = (text: string) => new TextEncoder().encode(text);

function fakeCredential(kind: "create" | "get", userHandle: string | null = "user") {
  return {
    id: "Y3JlZA", rawId: bytes("cred").buffer, type: "public-key", authenticatorAttachment: "platform",
    response: kind === "create"
      ? { clientDataJSON: bytes("cd").buffer, attestationObject: bytes("ao").buffer, getTransports: () => ["internal"] }
      : {
          clientDataJSON: bytes("cd").buffer, authenticatorData: bytes("ad").buffer, signature: bytes("sig").buffer,
          userHandle: userHandle === null ? null : bytes(userHandle).buffer,
        },
    getClientExtensionResults: () => ({}),
  };
}

const credentials = { create: vi.fn(), get: vi.fn() };

function setContext({ secure = true, webauthn = true }: { secure?: boolean; webauthn?: boolean } = {}) {
  Object.defineProperty(window, "isSecureContext", { value: secure, configurable: true });
  Object.defineProperty(window, "PublicKeyCredential", { value: webauthn ? class {} : undefined, configurable: true });
  Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
}

const creationOptions = {
  challenge: "Y2hhbGxlbmdl", rp: { name: "USAS", id: "localhost" },
  user: { id: "dXNlcg", name: "usas", displayName: "USAS web UI" },
  pubKeyCredParams: [{ type: "public-key" as const, alg: -7 }], timeout: 300000,
  excludeCredentials: [{ id: "b2xk", type: "public-key" as const, transports: ["internal" as AuthenticatorTransport] }],
  authenticatorSelection: { residentKey: "preferred" as const, userVerification: "required" as const },
  attestation: "none" as const,
};
const requestOptions = {
  challenge: "Y2hhbGxlbmdl", rpId: "localhost", timeout: 300000, userVerification: "required" as const,
  allowCredentials: [{ id: "Y3JlZA", type: "public-key" as const, transports: ["hybrid" as AuthenticatorTransport] }],
};
const text = (buffer: unknown) => new TextDecoder().decode(buffer as ArrayBuffer);
const value = (el: HTMLElement) => (el as HTMLInputElement).value;
// What a screen reader reads after the label: the text of the elements aria-describedby names.
const description = (el: HTMLElement) => (el.getAttribute("aria-describedby") ?? "").split(" ")
  .map((id) => document.getElementById(id)?.textContent ?? "").join(" ");

beforeEach(() => {
  credentials.create.mockReset();
  credentials.get.mockReset();
  setContext();
});

afterEach(() => {
  setContext();
});

describe("base64url", () => {
  it.each(["", "f", "fo", "foo", "foob", "ÿþý"])("round-trips %j", (value) => {
    const data = Uint8Array.from(value, (c) => c.charCodeAt(0));
    const encoded = toBase64url(data);
    expect(encoded).not.toMatch(/[+/=]/);
    expect(new Uint8Array(fromBase64url(encoded))).toEqual(data);
  });

  it("uses the URL-safe alphabet and takes views and buffers", () => {
    expect(toBase64url(Uint8Array.from([0xfb, 0xff, 0xbf]))).toBe("-_-_");
    expect(toBase64url(Uint8Array.from([0xfb, 0xff, 0xbf]).buffer)).toBe("-_-_");
    expect(toBase64url(new Uint8Array([9, 0xfb, 0xff, 0xbf]).subarray(1))).toBe("-_-_");
  });
});

describe("when the page can use passkeys", () => {
  it("needs a secure page, WebAuthn and the passkeys' host name", () => {
    expect(passkeyProblem("localhost", "")).toBeNull();
    setContext({ secure: false });
    expect(passkeyProblem("localhost", "")).toMatch(/secure page.*http:\/\/localhost:<port>/);
    expect(passkeyProblem("usas.example.lan", "https://usas.example.lan")).toMatch(/at https:\/\/usas.example.lan/);
    setContext({ webauthn: false });
    expect(passkeyProblem("localhost", "")).toBe("This browser doesn't support passkeys.");
    setContext();
    expect(passkeyProblem("usas.example.lan", "https://usas.example.lan"))
      .toBe("Passkeys here belong to usas.example.lan. Open the UI at https://usas.example.lan to use them.");
  });

  it.each([
    ["NotAllowedError", "cancelled or timed out"],
    ["AbortError", "cancelled or timed out"],
    ["InvalidStateError", "has a passkey for this UI already"],
    ["SecurityError", "doesn't belong to localhost"],
    ["NotSupportedError", "can't make a passkey"],
    ["UnknownError", "failed: boom"],
  ])("explains %s", (name, message) => {
    expect(passkeyError(new DOMException("boom", name), "localhost")).toContain(message);
  });

  it("explains anything else", () => {
    expect(passkeyError("odd", "localhost")).toBe("The passkey request failed.");
  });
});

describe("the browser calls", () => {
  it("makes a passkey from the server's options and answers in JSON", async () => {
    credentials.create.mockResolvedValue(fakeCredential("create"));
    const answer = JSON.parse(await createPasskey(creationOptions));
    const { publicKey } = credentials.create.mock.calls[0][0];
    expect(text(publicKey.challenge)).toBe("challenge");
    expect(text(publicKey.user.id)).toBe("user");
    expect(publicKey.user.name).toBe("usas");
    expect(text(publicKey.excludeCredentials[0].id)).toBe("old");
    expect(publicKey.authenticatorSelection.userVerification).toBe("required");
    expect(answer).toEqual({
      id: "Y3JlZA", rawId: "Y3JlZA", type: "public-key", authenticatorAttachment: "platform",
      response: { clientDataJSON: "Y2Q", attestationObject: "YW8", transports: ["internal"] },
      clientExtensionResults: {},
    });
  });

  it("copes without getTransports and with no credential", async () => {
    const credential = fakeCredential("create");
    delete (credential.response as { getTransports?: unknown }).getTransports;
    credentials.create.mockResolvedValue({ ...credential, authenticatorAttachment: null });
    const answer = JSON.parse(await createPasskey({ ...creationOptions, excludeCredentials: undefined }));
    expect(answer.response.transports).toEqual([]);
    expect(answer.authenticatorAttachment).toBeUndefined();
    credentials.create.mockResolvedValue(null);
    await expect(createPasskey(creationOptions)).rejects.toMatchObject({ name: "NotAllowedError" });
  });

  it("uses a passkey and answers in JSON", async () => {
    credentials.get.mockResolvedValue(fakeCredential("get"));
    const answer = JSON.parse(await getPasskey(requestOptions));
    const { publicKey } = credentials.get.mock.calls[0][0];
    expect(text(publicKey.challenge)).toBe("challenge");
    expect(text(publicKey.allowCredentials[0].id)).toBe("cred");
    expect(publicKey.allowCredentials[0].transports).toEqual(["hybrid"]);
    expect(answer.response).toEqual({
      clientDataJSON: "Y2Q", authenticatorData: "YWQ", signature: "c2ln", userHandle: "dXNlcg",
    });
    credentials.get.mockResolvedValue(fakeCredential("get", null));
    expect(JSON.parse(await getPasskey(requestOptions)).response.userHandle).toBeNull();
    credentials.get.mockResolvedValue(null);
    await expect(getPasskey(requestOptions)).rejects.toMatchObject({ name: "NotAllowedError" });
  });
});

function withPasskeys(passkeys: Partial<typeof fixtures.session.passkeys>) {
  return () => ({ ...fixtures.session, passkeys: { ...fixtures.session.passkeys, ...passkeys } });
}

describe("the sign-in page", () => {
  it("offers no passkey when none is registered", async () => {
    new FakeApi();
    renderAt("/login");
    await screen.findByLabelText("Password");
    expect(screen.queryByRole("button", { name: "Sign in with a passkey" })).toBeNull();
  });

  it("signs in with a passkey and goes to the page asked for", async () => {
    const api = new FakeApi({
      "GET /api/session": withPasskeys({ registered: true }),
      "POST /api/passkeys/login-options": () => ({ options: requestOptions }),
      "POST /api/passkeys/login": () => ({ redirect: "/servers", csrf: "new-token" }),
    });
    credentials.get.mockResolvedValue(fakeCredential("get"));
    renderAt("/login?next=/servers");
    await screen.findByLabelText("Password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in with a passkey" }));
    await waitFor(() => expect(window.location.pathname).toBe("/servers"));
    const sent = api.posts("/api/passkeys/login")[0].body;
    expect(sent.next).toBe("/servers");
    expect(JSON.parse(String(sent.credential)).id).toBe("Y3JlZA");
  });

  it("says why a passkey didn't work, and takes the focus there", async () => {
    const api = new FakeApi({
      "GET /api/session": withPasskeys({ registered: true }),
      "POST /api/passkeys/login-options": () => ({ options: requestOptions }),
      "POST /api/passkeys/login": () => reply(401, { error: "The passkey couldn't be verified." }),
    });
    credentials.get.mockRejectedValueOnce(new DOMException("no", "NotAllowedError"));
    renderAt("/login");
    const button = await screen.findByRole("button", { name: "Sign in with a passkey" });
    await userEvent.click(button);
    await screen.findByText(/cancelled or timed out/);
    expect(document.activeElement?.id).toBe("error-summary");
    expect(api.posts("/api/passkeys/login")).toHaveLength(0);
    credentials.get.mockResolvedValue(fakeCredential("get"));
    await userEvent.click(button);
    await screen.findByText("The passkey couldn't be verified.");
    api.on("POST /api/passkeys/login-options", () => reply(429, { error: "Too many failed attempts." }));
    await userEvent.click(button);
    await screen.findByText("Too many failed attempts.");
    checkStructure(document.body, "/login with a passkey error");
    await checkAxe("/login with a passkey error");
    // A password attempt replaces the passkey's message with its own.
    api.on("POST /api/login", () => reply(401, { error: "That password is wrong." }));
    await userEvent.type(screen.getByLabelText("Password"), "nope{Enter}");
    await screen.findByText("That password is wrong.");
    expect(screen.queryByText("Too many failed attempts.")).toBeNull();
  });

  it("shows only the passkey when one is required", async () => {
    new FakeApi({ "GET /api/session": withPasskeys({ registered: true, required: true }) });
    renderAt("/login");
    await screen.findByRole("button", { name: "Sign in with a passkey" });
    expect(screen.queryByLabelText("Password")).toBeNull();
    screen.getByText(/signing in with the password is turned off/);
    screen.getByText(/Lost every passkey/);
    checkStructure(document.body, "/login, passkey required");
    await checkAxe("/login, passkey required");
  });

  it("explains why passkeys can't be used on this page", async () => {
    setContext({ secure: false });
    new FakeApi({ "GET /api/session": withPasskeys({ registered: true }) });
    renderAt("/login");
    await screen.findByLabelText("Password");
    expect(screen.queryByRole("button", { name: "Sign in with a passkey" })).toBeNull();
    screen.getByText(/Passkeys need a secure page/);
  });

  it("warns when a passkey is required but can't be used here", async () => {
    setContext({ secure: false });
    new FakeApi({ "GET /api/session": withPasskeys({ registered: true, required: true }) });
    renderAt("/login");
    await screen.findByText(/Passkeys need a secure page/);
    expect(screen.queryByRole("button", { name: "Sign in with a passkey" })).toBeNull();
  });

  it("says when the session can't be read", async () => {
    new FakeApi({ "GET /api/session": () => reply(500, { error: "Broken." }) });
    renderAt("/login");
    await screen.findByText("Broken.");
  });
});

async function accountPage() {
  renderAt("/account");
  return screen.findByRole("region", { name: "Your passkeys" });
}

describe("the account page", () => {
  it("lists the passkeys", async () => {
    new FakeApi();
    const table = await accountPage();
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    within(rows[1]).getByText("This session");
    within(rows[2]).getByText("Synced");
    within(rows[2]).getByText("Never");
    expect(screen.getByRole("heading", { level: 2, name: /Passkeys/ }).textContent).toContain("2");
    checkStructure(document.body, "/account");
    await checkAxe("/account");
  });

  it("adds a passkey", async () => {
    const api = new FakeApi({
      "POST /api/passkeys/register-options": () => ({ options: creationOptions }),
      "POST /api/passkeys/register": () => ({ message: "Added the passkey “Desk key”." }),
    });
    credentials.create.mockResolvedValue(fakeCredential("create"));
    await accountPage();
    await userEvent.type(screen.getByLabelText("Name"), "Desk key");
    await userEvent.type(screen.getByLabelText("Current password", { selector: "#f-passkey_current" }), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
    await screen.findByText("Added the passkey “Desk key”.");
    expect(api.posts("/api/passkeys/register-options")[0].body).toEqual({ passkey_name: "Desk key", passkey_current: "secret" });
    expect(JSON.parse(String(api.posts("/api/passkeys/register")[0].body.credential)).response.attestationObject).toBe("YW8");
    // The form is empty again, and the list was loaded again.
    expect(value(screen.getByLabelText("Name"))).toBe("");
    expect(api.calls.filter((c) => c.path === "/api/account" && c.method === "GET").length).toBeGreaterThan(1);
  });

  it("shows the problems with the form, linked to the fields", async () => {
    new FakeApi({
      "POST /api/passkeys/register-options": () => reply(422, {
        errors: { passkey_name: "Name the passkey.", passkey_current: "The current password is wrong." },
      }),
    });
    await accountPage();
    await userEvent.type(screen.getByLabelText("Name"), "x");
    await userEvent.type(screen.getByLabelText("Current password", { selector: "#f-passkey_current" }), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
    const summary = await screen.findByRole("alert");
    expect(summary.id).toBe("passkeys-errors");
    expect(document.activeElement).toBe(summary);
    await userEvent.click(within(summary).getByRole("link", { name: "Name the passkey." }));
    expect(document.activeElement?.id).toBe("f-passkey_name");
    expect(screen.getByLabelText("Name").getAttribute("aria-invalid")).toBe("true");
    expect(description(screen.getByLabelText("Name"))).toMatch(/Error: Name the passkey/);
    checkStructure(document.body, "/account with errors");
    await checkAxe("/account with errors");
  });

  it("says why adding didn't work", async () => {
    const api = new FakeApi({
      "POST /api/passkeys/register-options": () => ({ options: creationOptions }),
      "POST /api/passkeys/register": () => reply(422, { error: "The passkey couldn't be verified." }),
    });
    credentials.create.mockRejectedValueOnce(new DOMException("dup", "InvalidStateError"));
    await accountPage();
    await userEvent.type(screen.getByLabelText("Name"), "Laptop");
    await userEvent.type(screen.getByLabelText("Current password", { selector: "#f-passkey_current" }), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
    await screen.findByText(/has a passkey for this UI already/);
    expect(api.posts("/api/passkeys/register")).toHaveLength(0);
    credentials.create.mockResolvedValue(fakeCredential("create"));
    await userEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
    await screen.findByText("The passkey couldn't be verified.");
    // What was typed stays for another try.
    expect(value(screen.getByLabelText("Name"))).toBe("Laptop");
  });

  it("removes a passkey and puts the focus on the section", async () => {
    const api = new FakeApi({ "POST /api/passkeys/remove": () => ({ message: "Removed the passkey “Phone”." }) });
    await accountPage();
    await userEvent.click(screen.getByRole("button", { name: "Remove the passkey Phone" }));
    await screen.findByText("Removed the passkey “Phone”.");
    expect(api.posts("/api/passkeys/remove")[0].body).toEqual({ id: "cred-phone" });
    expect(document.activeElement?.id).toBe("passkeys-heading");
    api.on("POST /api/passkeys/remove", () => reply(409, { error: "This is the only passkey." }));
    await userEvent.click(screen.getByRole("button", { name: "Remove the passkey Laptop" }));
    await screen.findByText("This is the only passkey.");
  });

  it("turns the requirement on", async () => {
    const api = new FakeApi({ "POST /api/passkeys/require": () => ({ message: "A passkey is now required to sign in." }) });
    await accountPage();
    const box = screen.getByRole("checkbox", { name: "Require a passkey" });
    expect((box as HTMLInputElement).checked).toBe(false);
    expect(description(box)).toMatch(/delete passkeys.json/);
    await userEvent.click(box);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("A passkey is now required to sign in.");
    expect(api.posts("/api/passkeys/require")[0].body).toEqual({ require_passkey: "on" });
  });

  it("explains why no passkey can be added on this page, and a broken file", async () => {
    setContext({ secure: false });
    new FakeApi({ "GET /api/account": () => ({ ...fixtures.account, passkeys: [], broken: true }) });
    renderAt("/account");
    await screen.findByText("No passkeys yet.");
    screen.getByText(/Passkeys need a secure page/);
    screen.getByText(/passkeys file can't be read/);
    expect(screen.queryByRole("button", { name: "Add a passkey" })).toBeNull();
  });

  it("says when the passkeys can't be loaded", async () => {
    new FakeApi({ "GET /api/account": () => reply(500, { error: "Broken." }) });
    renderAt("/account");
    // Both sections that load it say so: the passkeys and the status token.
    expect(await screen.findAllByText("Broken.")).toHaveLength(2);
  });
});
