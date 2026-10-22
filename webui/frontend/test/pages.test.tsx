// The pages: what each shows from the API, what its forms post (the same field names the server-rendered
// forms had), and how it shows the server's answer.

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LogView, lineClass, splitLines } from "../src/components/LogView";
import * as fixtures from "./fixtures";
import { FakeApi, FakeEventSource, reply, renderAt } from "./server";

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector(".loading")).toBeNull());
}

function input(label: string | RegExp): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe("the overview", () => {
  it("shows the steps, the next one, the current run and the recent runs", async () => {
    new FakeApi();
    renderAt("/");
    await loaded();
    expect(screen.getByText("4 of 5 done")).toBeTruthy();
    expect(screen.getByText("cluster olympus · node hephaestus")).toBeTruthy();
    const steps = screen.getAllByRole("listitem").filter((li) => li.classList.contains("step"));
    expect(steps).toHaveLength(5);
    expect(steps[2].classList.contains("next")).toBe(true);
    expect(within(steps[2]).getByText("To do")).toBeTruthy();
    expect(within(steps[0]).getByRole("link").getAttribute("href")).toBe("/proxmox");
    expect(screen.getByText(/A run is going now/)).toBeTruthy();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Running")).toBeTruthy();
    expect(within(table).getByText("Failed · exit 2")).toBeTruthy();
    // The nav marks the set-up pages done or to do, in words for screen readers too.
    await waitFor(() => expect(document.querySelector('.nav a[href="/ssh"]')?.textContent).toBe("SSH key (to do)"));
    expect(document.querySelector('.nav a[href="/servers"]')?.textContent).toBe("Servers (done)");
  });

  it("says when there are no runs, and when the server fails", async () => {
    const api = new FakeApi({ "GET /api/overview": () => ({ ...fixtures.overview, runs: [], current_run: null, node: "" }) });
    renderAt("/");
    await screen.findByText("No runs yet.");
    api.on("GET /api/runs", () => reply(500, { error: "Something broke." }));
    await userEvent.click(screen.getByRole("link", { name: "History" }));
    await screen.findByText("Something broke.");
  });
});

describe("the Proxmox page", () => {
  it("draws the fields from the API, with the node and the ISO storage as dropdowns", async () => {
    new FakeApi();
    renderAt("/proxmox");
    await loaded();
    expect(input(/API host/).value).toBe("pve.example.lan");
    expect(input(/Token secret/).value).toBe("");
    expect(screen.getByText("A value is saved. Leave this empty to keep it.")).toBeTruthy();
    // A required secret can be replaced but not removed.
    expect(screen.queryByLabelText("Remove the saved value")).toBeNull();
    const node = screen.getByLabelText(/Node name/) as HTMLSelectElement;
    expect(node.tagName).toBe("SELECT");
    expect([...node.options].map((o) => o.text)).toEqual(["ares (online)", "hephaestus (online)", "hermes (offline)"]);
    expect(node.value).toBe("hephaestus");
    const iso = screen.getByLabelText("ISO storage") as HTMLSelectElement;
    expect([...iso.options].map((o) => o.value)).toEqual(["local", "synology-nfs"]);
    expect(screen.getByText(/The nodes of cluster/)).toBeTruthy();
    const facts = screen.getByRole("complementary", { name: "Last connection test" });
    expect(within(facts).getByText("olympus")).toBeTruthy();
    expect(within(facts).getByText("offline")).toBeTruthy();
    // The help says which variable a field sets, and its default.
    expect(document.getElementById("f-pve_api_port-help")?.textContent).toContain("Variable pve_api_port, default 8006.");
  });

  it("keeps a node that isn't in the cluster visible, and lists none before a test", async () => {
    new FakeApi({
      "GET /api/proxmox": () => ({ ...fixtures.proxmox, cluster: {}, values: { ...fixtures.proxmox.values, pve_node: "" } }),
    });
    renderAt("/proxmox");
    await loaded();
    expect(input(/Node name/).tagName).toBe("INPUT");
    expect(screen.getByText(/to pick the node from a list/)).toBeTruthy();
    expect(screen.getByText(/No test yet/)).toBeTruthy();
  });

  it("shows a saved node the cluster doesn't have", async () => {
    new FakeApi({
      "GET /api/proxmox": () => ({ ...fixtures.proxmox, values: { ...fixtures.proxmox.values, pve_node: "athena", pve_iso_storage: "backup" } }),
    });
    renderAt("/proxmox");
    await loaded();
    expect(screen.getByText("athena (not in the cluster)")).toBeTruthy();
    expect(screen.getByText("backup (doesn't allow ISO images)")).toBeTruthy();
  });

  it("saves, and shows the saved values again", async () => {
    const api = new FakeApi({ "POST /api/proxmox": () => ({ message: "Saved the Proxmox settings." }) });
    renderAt("/proxmox");
    await loaded();
    await userEvent.clear(input(/API port/));
    await userEvent.type(input(/API port/), "8007");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved the Proxmox settings.");
    const [call] = api.posts("/api/proxmox");
    expect(call.body.action).toBe("save");
    expect(call.body.pve_api_port).toBe("8007");
    expect(call.body.pve_node).toBe("hephaestus");
    expect(call.body.pve_validate_certs).toBe("on");
    expect(Object.keys(call.body)).toEqual(expect.arrayContaining(fixtures.proxmox.fields.map((f) => f.name)
      .filter((n) => n !== "pve_validate_certs" || call.body[n])));
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/api/proxmox" && c.method === "GET")).toHaveLength(2));
  });

  it("tests the connection, then offers the cluster's nodes without losing what was typed", async () => {
    const api = new FakeApi({
      "GET /api/proxmox": () => ({ ...fixtures.proxmox, cluster: {} }),
      "POST /api/proxmox": () => ({
        message: "Connected to Proxmox VE 9.1.1, cluster lab. Nothing was saved yet.",
        cluster: { name: "lab", nodes: { "pve-1": "online", "pve-2": "online" }, iso_storages: { local: "dir" } },
        test_result: { version: "9.1.1" },
      }),
    });
    renderAt("/proxmox");
    await loaded();
    await userEvent.clear(input(/API host/));
    await userEvent.type(input(/API host/), "pve2.lan");
    await userEvent.clear(input(/Node name/));
    await userEvent.type(input(/Node name/), "pve-2");
    await userEvent.click(screen.getByRole("button", { name: "Test the connection" }));
    await screen.findByText(/Connected to Proxmox VE 9.1.1/);
    expect(api.posts("/api/proxmox")[0].body.action).toBe("test");
    const node = screen.getByLabelText(/Node name/) as HTMLSelectElement;
    expect(node.tagName).toBe("SELECT");
    expect(node.value).toBe("pve-2");
    expect(input(/API host/).value).toBe("pve2.lan");
  });

  it("shows a failed connection test", async () => {
    new FakeApi({ "POST /api/proxmox": () => ({ test_error: "Proxmox refused the token (401)." }) });
    renderAt("/proxmox");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Test the connection" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Proxmox refused the token (401).");
  });

  it("shows errors by their fields and in a summary that links to them", async () => {
    new FakeApi({
      "POST /api/proxmox": () => reply(422, { errors: { pve_node: "hephaestus2 isn't a node in the cluster.", pve_vlan: "VLAN tag must be between 1 and 4094." } }),
    });
    renderAt("/proxmox");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const summary = await screen.findByRole("alert");
    expect(document.activeElement).toBe(summary);
    const link = within(summary).getByText("VLAN tag must be between 1 and 4094.");
    expect(link.getAttribute("href")).toBe("#f-pve_vlan");
    await userEvent.click(link);
    expect(document.activeElement?.id).toBe("f-pve_vlan");
    expect(input("VLAN tag").getAttribute("aria-describedby")).toBe("f-pve_vlan-help f-pve_vlan-error");
  });

  it("saves, fetches and removes the cluster's CA certificate", async () => {
    const api = new FakeApi({
      "GET /api/proxmox": () => ({ ...fixtures.proxmox, ca: [{ subject: "CN=Proxmox Virtual Environment", fingerprint: "AB:CD", expires: "2036-01-01" }] }),
      "POST /api/proxmox": (c) => c.body.action === "save-ca" ? reply(422, { ca_error: "That isn't a PEM certificate." })
        : { message: c.body.action === "remove-ca" ? "The CA certificate was removed." : "Saved the CA certificate." },
    });
    renderAt("/proxmox");
    await loaded();
    expect(screen.getByText("CN=Proxmox Virtual Environment")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("CA certificate (PEM)"), "junk");
    await userEvent.click(screen.getByRole("button", { name: "Save the certificate" }));
    await screen.findByText("That isn't a PEM certificate.");
    await userEvent.click(screen.getByRole("button", { name: "Fetch it from the node" }));
    await screen.findByText("Saved the CA certificate.");
    await userEvent.click(screen.getByRole("button", { name: "Remove it" }));
    await screen.findByText("The CA certificate was removed.");
    expect(api.posts("/api/proxmox").map((c) => c.body.action)).toEqual(["save-ca", "fetch-ca", "remove-ca"]);
  });

  it("ignores a second click while a post is on its way", async () => {
    let release = () => undefined as void;
    const api = new FakeApi();
    api.on("POST /api/proxmox", () => ({ message: "Saved." }));
    const slow = api.fetch;
    api.fetch = async (input, init) => {
      if (init?.method === "POST") await new Promise<void>((r) => { release = r; });
      return slow(input, init);
    };
    window.fetch = api.fetch as typeof fetch;
    renderAt("/proxmox");
    await loaded();
    const save = screen.getByRole("button", { name: "Save" });
    await userEvent.click(save);
    expect(save.getAttribute("aria-disabled")).toBe("true");
    await userEvent.click(save);
    act(() => release());
    await screen.findByText("Saved.");
    expect(api.posts("/api/proxmox")).toHaveLength(1);
  });
});

describe("the defaults page", () => {
  it("groups the fields, suggests time zones and previews the GitHub keys", async () => {
    const api = new FakeApi({
      "POST /api/defaults": (c) => c.body.action === "preview"
        ? { github_user: "octocat", preview: [{ type: "ssh-ed25519", fingerprint: "SHA256:abc", comment: "laptop" }], extra_keys: [], preview_error: null }
        : { message: "Saved the defaults." },
    });
    renderAt("/defaults");
    await loaded();
    expect(screen.getAllByRole("group").map((g) => g.querySelector("legend")?.textContent)).toEqual(
      expect.arrayContaining(["SSH keys for the admin user", "Admin user", "Network", "Installation"]));
    expect(input("Time zone").getAttribute("list")).toBe("timezones");
    expect(document.querySelectorAll("#timezones option")).toHaveLength(3);
    expect((screen.getByLabelText("Also authorize this UI's SSH key") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/Create it on the/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Preview the keys" }));
    const table = await screen.findByRole("table");
    expect(within(table).getByText("laptop")).toBeTruthy();
    expect(within(table).getByText(/Keys on GitHub for octocat/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved the defaults.");
    expect(api.posts("/api/defaults").map((c) => c.body.action)).toEqual(["preview", "save"]);
    expect(api.posts("/api/defaults")[1].body.authorize_ui_key).toBe("on");
  });

  it("shows a preview that found nothing", async () => {
    new FakeApi({ "POST /api/defaults": () => ({ preview: [], preview_error: "Enter a GitHub user first." }) });
    renderAt("/defaults");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Preview the keys" }));
    await screen.findByText("Enter a GitHub user first.");
  });

  it("shows the UI's key when there is one, and a server error", async () => {
    new FakeApi({
      "GET /api/defaults": () => ({ ...fixtures.defaults, ui_key: "ssh-ed25519 AAAA ui" }),
      "POST /api/defaults": () => reply(403, { error: "The form expired." }),
    });
    renderAt("/defaults");
    await loaded();
    expect(screen.getByText("ssh-ed25519 AAAA ui")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("The form expired.");
  });
});

describe("the hardening page", () => {
  it("groups SSH, the firewall, Fail2Ban, the extras, notifications and the relay, and saves", async () => {
    const api = new FakeApi({ "POST /api/hardening": () => ({ message: "Saved the hardening settings." }) });
    renderAt("/hardening");
    await loaded();
    const form = document.querySelector("main form") as HTMLFormElement;
    expect([...form.querySelectorAll("fieldset > legend")].map((l) => l.textContent)).toEqual(
      ["SSH", "Firewall (UFW)", "Fail2Ban", "Extra hardening (off by default)", "Notifications", "Mail relay"]);
    expect(input("Kernel and network settings (sysctl)").checked).toBe(false);
    expect(input("Webhook for bans").type).toBe("password");
    // Nothing is saved yet, so there is nothing to remove.
    expect(screen.queryByLabelText("Remove the saved value")).toBeNull();
    expect(screen.getByText("Wrong values can lock you out")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved the hardening settings.");
    expect(api.posts("/api/hardening")[0].body.ssh_port).toBe("22");
  });

  it("offers to remove saved optional secrets", async () => {
    const fields = fixtures.hardening.fields.map((f) =>
      f.kind === "secret" ? { ...f, saved: true } : f);
    const api = new FakeApi({
      "GET /api/hardening": () => ({ ...fixtures.hardening, fields }),
      "POST /api/hardening": () => ({ message: "Saved the hardening settings." }),
    });
    renderAt("/hardening");
    await loaded();
    const webhook = within(input("Webhook for bans").closest(".field") as HTMLElement);
    expect(webhook.getByText("A value is saved. Leave this empty to keep it.")).toBeTruthy();
    await userEvent.click(webhook.getByLabelText("Remove the saved value"));
    expect(screen.getAllByLabelText("Remove the saved value")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved the hardening settings.");
    const body = api.posts("/api/hardening")[0].body;
    expect(body.fail2ban_webhook_url__clear).toBe("on");
    expect(body.mail_relay_password__clear).toBeUndefined();
  });
});

describe("the servers page", () => {
  it("adds and removes rows, renumbering the fields, and saves them", async () => {
    const api = new FakeApi({ "POST /api/servers": () => ({ message: "Saved 2 server(s)." }) });
    renderAt("/servers");
    await loaded();
    expect(screen.getByText(/Empty sizes use the defaults \(cores 2, memory \(mb\) 2048, disk \(gb\) 32\)/)).toBeTruthy();
    expect(input("Name, row 1").value).toBe("web-01");
    expect(screen.getByText("Servers (2 rows)")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Add a row" }));
    expect(document.activeElement?.id).toBe("servers-2-name");
    await userEvent.type(input("Name, row 3"), "web-02");
    await userEvent.click(screen.getByRole("button", { name: "Remove row 1" }));
    expect(screen.getByText("Row removed. Save to keep the change.")).toBeTruthy();
    expect(input("Name, row 1").value).toBe("db-01");
    expect(input("Name, row 2").value).toBe("web-02");
    expect(document.activeElement?.id).toBe("servers-0-name");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved 2 server(s).");
    const body = api.posts("/api/servers")[0].body;
    expect(body.server_count).toBe("2");
    expect(body["servers-0-name"]).toBe("db-01");
    expect(body["servers-0-vm_memory_mb"]).toBe("8192");
    expect(body["servers-1-name"]).toBe("web-02");
  });

  it("picks each server's node from the cluster, the default node first", async () => {
    const api = new FakeApi({ "POST /api/servers": () => ({ message: "Saved 2 server(s)." }) });
    renderAt("/servers");
    await loaded();
    const node = (row: number) => screen.getByLabelText(`Node, row ${row}`) as HTMLSelectElement;
    expect([...node(1).options].map((o) => o.textContent)).toEqual(["Default (hephaestus)", "ares", "hephaestus", "hermes"]);
    expect(node(1).value).toBe("");
    expect(node(2).value).toBe("ares");
    await userEvent.selectOptions(node(1), "hermes");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved 2 server(s).");
    expect(api.posts("/api/servers")[0].body["servers-0-pve_node"]).toBe("hermes");
    expect(api.posts("/api/servers")[0].body["servers-1-pve_node"]).toBe("ares");
  });

  it("takes a typed node before a connection test, and keeps a node the cluster no longer lists", async () => {
    new FakeApi({ "GET /api/servers": () => ({ ...fixtures.servers, nodes: [] }) });
    renderAt("/servers");
    await loaded();
    expect(input("Node, row 2").value).toBe("ares");
    expect(input("Node, row 1").placeholder).toBe("hephaestus");
    expect(screen.getByText(/test the connection there to pick from the cluster's nodes/)).toBeTruthy();
  });

  it("keeps a node the cluster no longer lists in the dropdown", async () => {
    new FakeApi({ "GET /api/servers": () => ({ ...fixtures.servers, nodes: ["hephaestus"] }) });
    renderAt("/servers");
    await loaded();
    const node = screen.getByLabelText("Node, row 2") as HTMLSelectElement;
    expect(node.value).toBe("ares");
    expect([...node.options].map((o) => o.value)).toEqual(["", "hephaestus", "ares"]);
  });

  it("keeps one empty row when the last is removed", async () => {
    new FakeApi({ "GET /api/servers": () => ({ ...fixtures.servers, rows: [] }) });
    renderAt("/servers");
    await loaded();
    expect(screen.getByText("Servers (1 row)")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Remove row 1" }));
    expect(screen.getByText("Servers (1 row)")).toBeTruthy();
    expect(document.activeElement?.id).toBe("add-row");
  });

  it("shows errors in their cells", async () => {
    new FakeApi({
      "POST /api/servers": () => reply(422, { errors: { "servers-0-pve_vmid": "VM ID must be a whole number." } }),
    });
    renderAt("/servers");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Row 1: VM ID must be a whole number.");
    expect(input("VM ID, row 1").getAttribute("aria-invalid")).toBe("true");
    expect(input("VM ID, row 1").getAttribute("aria-describedby")).toBe("servers-0-pve_vmid-error");
  });

  it("shows a server error", async () => {
    new FakeApi({ "POST /api/servers": () => reply(500, {}) });
    renderAt("/servers");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("The server answered 500.");
  });
});

describe("the SSH page", () => {
  it("shows the key, the trusted and offered host keys", async () => {
    new FakeApi();
    renderAt("/ssh");
    await loaded();
    expect(screen.getByText(/SHA256:q9Vx1y2z3fQk/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download the public key/ }).getAttribute("href")).toBe("/ssh/id_ed25519.pub");
    expect(screen.getByText("SHA256:trusted1")).toBeTruthy();
    expect(screen.getByText("SHA256:offered1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create a new key" })).toBeTruthy();
    // One part per node: the default node, a server's node, and a node no server uses any more.
    const parts = screen.getAllByRole("group", { name: /^Node / });
    expect(parts.map((p) => p.querySelector("h3")?.textContent)).toEqual(["Node hephaestus", "Node ares", "Node hermes"]);
    expect(within(parts[0]).getByText("The default node, used by web-01.")).toBeTruthy();
    expect(within(parts[0]).getByRole("button", { name: "Scan again" })).toBeTruthy();
    expect(within(parts[0]).getByRole("button", { name: "Forget the host key" })).toBeTruthy();
    expect(within(parts[1]).getByText("Used by db-01.")).toBeTruthy();
    expect(within(parts[1]).getByText(/The host key of ares isn't trusted yet/)).toBeTruthy();
    expect(within(parts[1]).getByRole("button", { name: "Scan the host key" })).toBeTruthy();
    expect(within(parts[1]).queryByRole("button", { name: "Remove this node" })).toBeNull();
    expect(within(parts[2]).getByText("No server uses it now.")).toBeTruthy();
    expect(within(parts[2]).getByRole("button", { name: "Remove this node" })).toBeTruthy();
    // The offered keys show under the node they were scanned for, only.
    expect(within(parts[1]).queryByText("SHA256:offered1")).toBeNull();
  });

  it("posts each action and shows its answer where it was asked", async () => {
    const api = new FakeApi({
      "POST /api/ssh": (c) => {
        switch (c.body.action) {
          case "generate": return reply(422, { area: "key", error: "Tick the box to confirm replacing the current key." });
          case "address": return { message: "Saved node hephaestus's SSH address 192.168.1.30.", node: c.body.node };
          case "trust": return reply(422, { area: "hostkey", node: c.body.node, error: "Scan again, compare the fingerprints and tick the box." });
          case "test": return { message: "Logged in to root@192.168.1.20 with the UI's key.", node: c.body.node };
          case "scan": return { node: c.body.node };
          default: return {};
        }
      },
    });
    renderAt("/ssh");
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Create a new key" }));
    await screen.findByText("Tick the box to confirm replacing the current key.");
    // The parts are drawn again after each answer, so each is looked up again.
    const part = (i: number) => within(screen.getAllByRole("group", { name: /^Node / })[i]);
    await userEvent.clear(input("SSH address of hephaestus"));
    await userEvent.type(input("SSH address of hephaestus"), "192.168.1.30");
    await userEvent.click(part(0).getByRole("button", { name: "Save the address" }));
    await waitFor(() => part(0).getByText("Saved node hephaestus's SSH address 192.168.1.30."));
    await userEvent.click(screen.getByLabelText("The fingerprints match what node hephaestus shows"));
    await userEvent.click(screen.getByRole("button", { name: "Trust these keys" }));
    await waitFor(() => part(0).getByText(/compare the fingerprints and tick the box/));
    await userEvent.click(screen.getByRole("button", { name: "Test the SSH login" }));
    await waitFor(() => part(0).getByText(/Logged in to root/));
    await userEvent.click(part(1).getByRole("button", { name: "Scan the host key" }));
    const posts = api.posts("/api/ssh");
    expect(posts.map((c) => c.body.action)).toEqual(["generate", "address", "trust", "test", "scan"]);
    expect(posts.map((c) => c.body.node)).toEqual([undefined, "hephaestus", "hephaestus", "hephaestus", "ares"]);
    expect(posts[1].body.node_address).toBe("192.168.1.30");
    expect(posts[2].body.confirm).toBe("on");
  });

  it("uploads a key file", async () => {
    const api = new FakeApi({ "POST /api/ssh": () => ({ message: "Saved the uploaded key." }) });
    renderAt("/ssh");
    await loaded();
    await userEvent.click(screen.getByText("Upload a private key instead"));
    await userEvent.upload(screen.getByLabelText("Private key file"), new File(["-----BEGIN"], "id_ed25519"));
    expect((screen.getByLabelText("Private key file") as HTMLInputElement).files?.[0].name).toBe("id_ed25519");
    await userEvent.click(screen.getByRole("button", { name: "Upload the key" }));
    await screen.findByText("Saved the uploaded key.");
    const body = api.posts("/api/ssh")[0].body;
    expect(body.action).toBe("upload");
    // jsdom's FormData keeps the entry but not the file's name or contents; a browser sends both.
    expect(body).toHaveProperty("key_file");
  });

  it("starts without a key or an address", async () => {
    new FakeApi({
      "GET /api/ssh": () => ({ ...fixtures.ssh, public_key: "", key_info: null, nodes: [], pending: null, pve_node: "" }),
    });
    renderAt("/ssh");
    await loaded();
    expect(screen.getByText(/There is no key yet/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create a key" })).toBeTruthy();
    expect(screen.queryByText("Host key")).toBeNull();
    expect(screen.getByText(/There are no nodes yet/)).toBeTruthy();
  });

  it("asks for a node's address before its host key", async () => {
    new FakeApi({
      "GET /api/ssh": () => ({ ...fixtures.ssh, pending: null,
                               nodes: [{ ...fixtures.ssh.nodes[1], address: "" }] }),
    });
    renderAt("/ssh");
    await loaded();
    expect(input("SSH address of ares").value).toBe("");
    expect(screen.queryByText("Host key")).toBeNull();
  });
});

describe("the run page", () => {
  it("warns about missing steps and starts a run", async () => {
    const api = new FakeApi({ "POST /api/run": () => ({ redirect: `/runs/${fixtures.running.id}`, run: fixtures.running }) });
    renderAt("/run");
    await loaded();
    expect(screen.getByText(/Some steps aren't done yet/)).toBeTruthy();
    expect((screen.getByLabelText("build-installer") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/playbooks\/baseline.yml --tags ssh_keys/)).toBeTruthy();
    expect(screen.getByText(/Known: all, db-01, ubuntu_servers, web-01/)).toBeTruthy();
    await userEvent.click(screen.getByLabelText("provision"));
    await userEvent.click(screen.getByLabelText("Check mode"));
    await userEvent.type(input("Limit"), "web-01");
    await userEvent.click(screen.getByRole("button", { name: "Start the run" }));
    await waitFor(() => expect(window.location.pathname).toBe(`/runs/${fixtures.running.id}`));
    expect(api.posts("/api/run")[0].body).toEqual({ playbook: "provision", check: "on", limit: "web-01" });
  });

  it("shows where build-installer puts the ISO", async () => {
    new FakeApi();
    renderAt("/run");
    await loaded();
    expect(screen.getByText(/is shared by the cluster: the ISO is built once, on one node/)).toBeTruthy();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "ares (no SSH address yet)db-01synology-nfs (shared)this node",
      "hephaestusweb-01synology-nfs (shared)ares",
    ]);
  });

  it("explains the ISO plan before a connection test, and without servers", async () => {
    new FakeApi({
      "GET /api/run": () => ({ ...fixtures.runPage, iso_plan: { ...fixtures.runPage.iso_plan, known: false,
                                                                    shared: false, storage: "local" } }),
    });
    renderAt("/run");
    await loaded();
    expect(screen.getByText(/is each node's own: the ISO is built on one node/)).toBeTruthy();
    expect(screen.getByText(/to find out whether local is/)).toBeTruthy();
    cleanup();
    new FakeApi({ "GET /api/run": () => ({ ...fixtures.runPage, iso_plan: { ...fixtures.runPage.iso_plan, pairs: [], builders: [] } }) });
    renderAt("/run");
    await loaded();
    expect(screen.getByText(/No server has a node yet/)).toBeTruthy();
  });

  it("shows a refused run and a run that is going", async () => {
    new FakeApi({
      "GET /api/run": () => ({ ...fixtures.runPage, current_run: fixtures.running }),
      "POST /api/run": () => reply(422, { error: "Another run is going." }),
    });
    renderAt("/run");
    await loaded();
    expect(screen.getByText("A run is going. Only one runs at a time.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Start the run" }));
    await screen.findByText("Another run is going.");
  });
});

describe("the run history and a run", () => {
  it("lists the runs", async () => {
    new FakeApi();
    renderAt("/runs");
    await loaded();
    expect(screen.getByText("The last 3 runs, newest first")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("says when there are none", async () => {
    new FakeApi({ "GET /api/runs": () => ({ runs: [] }) });
    renderAt("/runs");
    await screen.findByText(/No runs yet/);
  });

  it("streams a running run's log, stops it and shows how it ended", async () => {
    let done = false;
    const api = new FakeApi({
      [`GET /api/runs/${fixtures.running.id}`]: () => ({
        run: done ? { ...fixtures.running, status: "stopped", done: true, finished: fixtures.running.started + 30, returncode: -2 } : fixtures.running,
        log: fixtures.log, offset: 512,
      }),
      [`POST /api/runs/${fixtures.running.id}/stop`]: () => ({ kind: "warning", message: "Stopping the run (like Ctrl-C)." }),
    });
    renderAt(`/runs/${fixtures.running.id}`);
    await loaded();
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByText("Elapsed")).toBeTruthy();
    expect(screen.getByText("9 lines")).toBeTruthy();
    const [source] = FakeEventSource.instances;
    expect(source.url).toBe(`/runs/${fixtures.running.id}/stream?offset=512`);
    act(() => {
      source.emit("TASK [common : Set the hostname] ***");
      source.emit("changed: [web-01]");
    });
    await screen.findByText("11 lines");
    expect(screen.getByText("changed: [web-01]").className).toBe("l-changed");
    await userEvent.click(screen.getByRole("button", { name: "Stop the run" }));
    await screen.findByText("Stopping the run (like Ctrl-C).");
    expect(api.posts(`/api/runs/${fixtures.running.id}/stop`)).toHaveLength(1);
    done = true;
    act(() => source.end("stopped"));
    await screen.findByText("Stopped · exit -2");
    expect(source.closed).toBe(true);
    expect(screen.queryByRole("button", { name: "Stop the run" })).toBeNull();
    expect(screen.getByText("Duration")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download the log/ }).getAttribute("href")).toBe(`/runs/${fixtures.running.id}/log`);
  });

  it("goes back to the history for a run that doesn't exist", async () => {
    new FakeApi({ "GET /api/runs/gone": () => reply(404, { error: "There is no such run.", redirect: "/runs" }) });
    renderAt("/runs/gone");
    await waitFor(() => expect(window.location.pathname).toBe("/runs"));
  });
});

describe("the log", () => {
  it("colours Ansible's lines", () => {
    expect(lineClass("PLAY [x] ***")).toBe("l-play");
    expect(lineClass("PLAY RECAP ***")).toBe("l-play");
    expect(lineClass("TASK [x] ***")).toBe("l-task");
    expect(lineClass("RUNNING HANDLER [x] ***")).toBe("l-task");
    expect(lineClass("ok: [web-01]")).toBe("l-ok");
    expect(lineClass("changed: [web-01]")).toBe("l-changed");
    expect(lineClass("skipping: [web-01]")).toBe("l-skip");
    expect(lineClass("fatal: [web-01]: FAILED!")).toBe("l-error");
    expect(lineClass("ERROR! the playbook could not be found")).toBe("l-error");
    expect(lineClass("FAILED - RETRYING: [web-01]")).toBe("l-muted");
    expect(lineClass("...ignoring")).toBe("l-muted");
    expect(lineClass("[WARNING]: something")).toBe("l-warn");
    expect(lineClass("ARGS -i inventory")).toBe("");
    expect(splitLines("a\r\nb\n")).toEqual(["a", "b"]);
    expect(splitLines("")).toEqual([]);
  });

  it("colours the recap's counts that aren't zero", () => {
    render(<LogView initial={"web-01                     : ok=6    changed=2    unreachable=0    failed=1    skipped=1\n"} stream={null} />);
    expect(screen.getByText("ok=6").className).toBe("l-ok");
    expect(screen.getByText("changed=2").className).toBe("l-changed");
    expect(screen.getByText("failed=1").className).toBe("l-error");
    expect(screen.getByText("skipped=1").className).toBe("l-skip");
    expect(screen.getByText("web-01").className).toBe("l-task");
    expect(document.querySelector("#log")?.textContent).toContain("unreachable=0");
  });

  it("follows the output until the user scrolls up, and wraps long lines on request", async () => {
    render(<LogView initial={"one\ntwo\n"} stream="/runs/x/stream" />);
    const pre = document.getElementById("log") as HTMLPreElement;
    Object.defineProperty(pre, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(pre, "clientHeight", { value: 200, configurable: true });
    act(() => FakeEventSource.instances[0].emit("three"));
    await screen.findByText("3 lines");
    expect(pre.scrollTop).toBe(1000);
    const follow = screen.getByLabelText("Follow the output") as HTMLInputElement;
    expect(follow.checked).toBe(true);
    pre.scrollTop = 100;
    act(() => {
      pre.dispatchEvent(new Event("scroll"));
    });
    expect(follow.checked).toBe(false);
    await userEvent.click(screen.getByLabelText("Wrap long lines"));
    expect(pre.classList.contains("wrap")).toBe(true);
    await userEvent.click(follow);
    expect(pre.scrollTop).toBe(1000);
  });

  it("says when there is no output yet, and draws long logs in blocks", () => {
    const { unmount } = render(<LogView initial="" stream={null} />);
    expect(screen.getByText("No output yet.")).toBeTruthy();
    unmount();
    const long = Array.from({ length: 1000 }, (_, i) => `line ${i}`).join("\n");
    render(<LogView initial={long} stream={null} />);
    expect(screen.getByText("1000 lines")).toBeTruthy();
    expect(document.querySelectorAll("#log .line")).toHaveLength(1000);
  });
});
