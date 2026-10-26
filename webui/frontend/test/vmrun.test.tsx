// Running the baseline from the VMs page: selecting VMs, each VM's settings and host key, the review the server
// answers with, and the check run. The server's checks are its own (webui/tests/test_vmplan.py); this is what
// the page sends and shows, and that it stays usable by keyboard and screen readers.

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HOSTS_POLL_MS, outcome } from "../src/components/HostResults";
import { addressChoices, chosenAddress, defaultSettings, planForm } from "../src/pages/VmRun";
import type { Vm, VmPlan } from "../src/types";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

const app: Vm = {
  ...fixtures.vmList[0], vmid: 106, name: "app-01", inventory_name: "", ours: false, alias: "usas-olympus-106",
  addresses: ["10.0.0.26", "172.17.0.1"], ip: "10.0.0.26", pinned: [],
};
const vmsWithApp = { ...fixtures.vms, vms: [...fixtures.vmList, app] };

const plan: VmPlan = {
  preset: "full", preset_label: "Full baseline", tags: [], digest: "d1",
  warnings: ["The firewall's default for incoming traffic is deny: …", "Every package is upgraded (apt_upgrade: dist)."],
  targets: [{
    vmid: 106, name: "vm-106", pve_name: "app-01", node: "hephaestus", static: false, address: "10.0.0.26",
    source: "agent", port: null, user: "ubuntu", alias: "usas-olympus-106", fingerprints: ["SHA256:pin106"],
    manage_hostname: false, override: false, ufw: ["443/tcp"],
  }],
};

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector("tbody tr")).not.toBeNull());
}

function setup(): FakeApi {
  const api = new FakeApi({ "GET /api/vms": () => vmsWithApp });
  renderAt("/vms");
  return api;
}

describe("running the baseline from the VMs page", () => {
  it("selects VMs by keyboard, all shown at once, and keeps the selection through filters", async () => {
    setup();
    await loaded();
    const user = userEvent.setup();
    const all = screen.getByLabelText("Select all VMs shown that can be selected") as HTMLInputElement;
    expect(screen.queryByRole("heading", { name: /Run the baseline/ })).toBeNull();
    (screen.getByLabelText("Select web-01") as HTMLInputElement).focus();
    await user.keyboard(" ");
    expect(all.indeterminate).toBe(true);
    expect(screen.getByRole("heading", { name: "Run the baseline on 1 selected VM" })).toBeTruthy();
    expect(document.getElementById("vm-count")?.textContent).toBe("7 VMs, 1 selected");
    await user.click(all);
    expect(all.checked && !all.indeterminate).toBe(true);
    expect(screen.getByRole("heading", { name: "Run the baseline on 3 selected VMs" })).toBeTruthy();
    // A filter hides rows but keeps them selected.
    await user.type(screen.getByLabelText("Find"), "app");
    expect(screen.getByRole("heading", { name: "Run the baseline on 3 selected VMs" })).toBeTruthy();
    await user.click(all);
    expect(screen.getByRole("heading", { name: "Run the baseline on 2 selected VMs" })).toBeTruthy();
  });

  it("pins a VM's host key only after the fingerprints were compared", async () => {
    const api = setup();
    api.on("POST /api/vms/hostkey", (c) => {
      if (c.body.action === "scan") return { vmid: 106, scanned: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:offered" }] };
      if (c.body.action === "trust" && !c.body.confirm) return reply(422, { error: "Scan again, compare the fingerprints and tick the box." });
      return { message: "Pinned VM 106's host keys.", pinned: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:offered" }] };
    });
    await loaded();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Select app-01"));
    const part = within(screen.getByRole("group", { name: /app-01/ }));
    expect(part.getByText(/Not pinned yet/)).toBeTruthy();
    // The address: the guest agent's, or another typed in.
    const address = part.getByLabelText("Address") as HTMLSelectElement;
    expect([...address.options].map((o) => o.value)).toEqual(["10.0.0.26", "172.17.0.1", "manual"]);
    expect((part.getByLabelText("Login user") as HTMLInputElement).value).toBe("sysadmin");
    await user.selectOptions(address, "manual");
    await user.type(part.getByLabelText("IPv4 address"), "10.0.0.99");
    await user.clear(part.getByLabelText("SSH port"));
    await user.type(part.getByLabelText("SSH port"), "2222");

    await user.click(part.getByRole("button", { name: "Scan the host key" }));
    const scan = api.posts("/api/vms/hostkey")[0].body;
    expect(scan).toMatchObject({ action: "scan", vmid: "106", address: "10.0.0.99", port: "2222" });
    expect(await part.findByText("SHA256:offered")).toBeTruthy();
    await user.click(part.getByRole("button", { name: "Pin these keys" }));
    expect(await part.findByText(/compare the fingerprints and tick the box/)).toBeTruthy();
    await user.click(part.getByRole("button", { name: "Scan again" }));
    await user.click(await part.findByLabelText("The fingerprints match what app-01 shows"));
    await user.click(part.getByRole("button", { name: "Pin these keys" }));
    expect(await part.findByText("Pinned VM 106's host keys.")).toBeTruthy();
    expect(api.posts("/api/vms/hostkey").at(-1)?.body).toMatchObject({ action: "trust", confirm: "on" });
    expect(part.getByText("Pinned to VM 106:")).toBeTruthy();
    expect(part.getByRole("button", { name: "Test the login" })).toBeTruthy();
    checkStructure(document.body, "/vms with a VM selected");
    await checkAxe("/vms with a VM selected");
  });

  it("shows the server's errors, then the review, then starts the check run", async () => {
    const api = setup();
    let attempt = 0;
    api.on("POST /api/vms/plan", () => (++attempt === 1
      ? reply(422, { errors: { "vm-106-user": "Enter a Linux user name.", "vm-106-hostkey": "Trust VM 106's host key first." } })
      : { plan_id: "p1", plan, expires: 1 }));
    api.on("POST /api/vms/run", () => ({ redirect: `/runs/${fixtures.running.id}`, run: fixtures.running }));
    await loaded();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Select app-01"));
    await user.click(screen.getByLabelText("SSH keys only"));
    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(document.activeElement).toBe(summary));
    expect(summary.textContent).toContain("There are 2 problems");
    await user.click(within(summary).getByText("Enter a Linux user name."));
    expect(document.activeElement?.id).toBe("f-vm-106-user");
    expect(document.getElementById("f-vm-106-user")?.getAttribute("aria-invalid")).toBe("true");
    await user.click(within(summary).getByText("Trust VM 106's host key first."));
    expect(document.activeElement?.id).toBe("f-vm-106-hostkey");
    expect(api.posts("/api/vms/plan")[0].body).toMatchObject({ preset: "keys", "vm-106-user": "sysadmin" });

    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    const heading = await screen.findByRole("heading", { name: "Review: Full baseline" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    const review = within(heading.closest("section") as HTMLElement);
    expect(review.getByText("Every package is upgraded (apt_upgrade: dist).")).toBeTruthy();
    expect(review.getByText("vm-106")).toBeTruthy();
    expect(review.getByText("SHA256:pin106")).toBeTruthy();
    checkStructure(document.body, "/vms review");
    await checkAxe("/vms review");
    // Changing a setting makes the review stale.
    await user.type(screen.getByLabelText("Extra firewall rules"), "443");
    expect(screen.queryByRole("heading", { name: "Review: Full baseline" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    await user.click(await screen.findByRole("button", { name: "Run the check (nothing changes)" }));
    expect(api.posts("/api/vms/run")[0].body).toEqual({ plan_id: "p1", mode: "check" });
    await waitFor(() => expect(window.location.pathname).toBe(`/runs/${fixtures.running.id}`));
  });

  it("chooses parts of the baseline, and says when the run can't start", async () => {
    const api = setup();
    api.on("POST /api/vms/plan", () => ({ plan_id: "p2", plan, expires: 1 }));
    api.on("POST /api/vms/run", () => reply(422, { error: "Another run is still going." }));
    await loaded();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Select db-01"));
    const part = within(screen.getByRole("group", { name: /db-01/ }));
    expect(part.getByText(/on the/).textContent).toContain("Server database");
    expect(part.queryByLabelText("Address")).toBeNull(); // a server keeps its own settings
    // A server whose guest didn't report its OS runs as from the Run page: no confirmation.
    expect(part.queryByLabelText("It runs Ubuntu Server: run the baseline anyway")).toBeNull();
    await user.click(screen.getByLabelText("Custom"));
    const parts = within(screen.getByRole("group", { name: "Parts of the baseline" }));
    await user.click(parts.getByLabelText("firewall"));
    await user.click(parts.getByLabelText("fail2ban"));
    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    expect(api.posts("/api/vms/plan")[0].body).toMatchObject({ preset: "custom", vmid: "102", tags: "fail2ban" });
    expect(api.posts("/api/vms/plan")[0].body["vm-102-address"]).toBeUndefined();
    await user.click(await screen.findByRole("button", { name: "Run the check (nothing changes)" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Another run is still going.");
  });

  it("warns without a key, while a run goes, and about VMs that can't be selected any more", async () => {
    let listing = { ...vmsWithApp, run: { ...vmsWithApp.run, key: false, busy: true } };
    new FakeApi({ "GET /api/vms": () => listing });
    renderAt("/vms");
    await loaded();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Select app-01"));
    expect(screen.getByText(/no SSH key yet/)).toBeTruthy();
    expect(screen.getByText(/A run is going/)).toBeTruthy();
    listing = { ...listing, vms: listing.vms.map((vm) => (vm.vmid === 106 ? { ...vm, selectable: false, status: "stopped", why_not: "It isn't running." } : vm)) };
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Left out, because they can't be selected any more: app-01 \(It isn't running.\)/)).toBeTruthy();
  });
});

describe("the plan's form", () => {
  it("sends each VM once, with its settings", () => {
    const s = { ...defaultSettings(app, "ubuntu"), port: " 22 ", ufw: "443/tcp ", hostname: true, override: true };
    const form = planForm([app, fixtures.vmList[0]], { 106: s, 101: defaultSettings(fixtures.vmList[0], "x") }, "custom",
                          ["ssh", "fail2ban"]);
    expect(form.getAll("vmid")).toEqual(["106", "101"]);
    expect(form.getAll("tags")).toEqual(["ssh", "fail2ban"]);
    expect(Object.fromEntries([...form.entries()].filter(([k]) => k.startsWith("vm-")))).toEqual({
      "vm-106-override": "on", "vm-106-address": "10.0.0.26", "vm-106-port": "22", "vm-106-user": "ubuntu",
      "vm-106-ufw": "443/tcp", "vm-106-hostname": "on",
    });
    expect(planForm([app], {}, "full", ["ssh"]).getAll("tags")).toEqual([]);
  });

  it("offers the agent's and cloud-init's addresses", () => {
    expect(addressChoices({ ...app, addresses: [], ip: "10.0.0.9", ip_source: "ipconfig0" })).toEqual(["10.0.0.9"]);
    const none = defaultSettings({ ...app, addresses: [], ip: "", ip_source: null }, "u");
    expect(none.address).toBe("manual");
    expect(chosenAddress({ ...none, manual: " 10.1.1.1 " })).toBe("10.1.1.1");
  });
});

describe("a VMs page run's page", () => {
  const target = { ...plan.targets[0], fingerprints: ["SHA256:pin106"] };
  const vmsRun = { ...fixtures.running, id: "20261026-090000-abcdef", action: "baseline", check: true, kind: "vms",
                   tags: ["ssh_keys"], limit: "vm-106,web-01", targets: [target] };
  const hosts = {
    finished: false, updated: 1,
    hosts: {
      "vm-106": { ...{ ok: 5, changed: 2, failed: 0, unreachable: 0, skipped: 3, rescued: 0, ignored: 0 },
                  changed_tasks: ["Install the authorized keys", "Set the timezone"], failed_tasks: [] },
      "web-01": { ok: 1, changed: 0, failed: 1, unreachable: 0, skipped: 0, rescued: 0, ignored: 0, changed_tasks: [],
                  failed_tasks: [{ task: "Gather facts", message: "<b>sudo: a password is required</b>" }] },
    },
  };

  it("shows each host's result and asks again while the run goes", async () => {
    let answer: unknown = { hosts: null, done: false };
    const api = new FakeApi({
      [`GET /api/runs/${vmsRun.id}`]: () => ({ run: vmsRun, log: "", offset: 0, hosts: null }),
      [`GET /api/runs/${vmsRun.id}/hosts`]: () => answer,
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderAt(`/runs/${vmsRun.id}`);
    const table = await screen.findByRole("table");
    expect(screen.getByText("check diff tags ssh_keys limit vm-106,web-01")).toBeTruthy();
    expect(screen.getByText("The VMs page")).toBeTruthy();
    expect(within(table).getByText("Waiting")).toBeTruthy();
    expect(within(table).getByText("VM 106 (app-01), 10.0.0.26")).toBeTruthy();
    answer = { hosts, done: false };
    await act(async () => { await vi.advanceTimersByTimeAsync(HOSTS_POLL_MS + 10); });
    await waitFor(() => expect(within(table).getByText("Would change", { selector: ".badge" })).toBeTruthy());
    const web = within(within(table).getByText("web-01").closest("tr") as HTMLElement);
    expect(web.getByText("Failed")).toBeTruthy();
    // A host's message is text, never markup.
    expect(web.getByText(/<b>sudo: a password is required<\/b>/)).toBeTruthy();
    expect(within(table).getByText("2 tasks would change something")).toBeTruthy();
    expect(api.calls.filter((c) => c.path.endsWith("/hosts")).length).toBeGreaterThanOrEqual(1);
    vi.useRealTimers();
    checkStructure(document.body, "a VMs page run");
    await checkAxe("a VMs page run");
  });

  it("says which results are final once the run is done", async () => {
    const done = { ...vmsRun, check: false, done: true, status: "failed", returncode: 2, finished: 1792591400 };
    new FakeApi({
      [`GET /api/runs/${vmsRun.id}`]: () => ({ run: done, log: "", offset: 0, hosts: { ...hosts, finished: true } }),
      [`GET /api/runs/${vmsRun.id}/hosts`]: () => ({ hosts: { ...hosts, finished: true, hosts: { "web-01": hosts.hosts["web-01"] } }, done: true }),
    });
    renderAt(`/runs/${vmsRun.id}`);
    const table = await screen.findByRole("table");
    await waitFor(() => expect(within(table).getByText("Not reached")).toBeTruthy());
    expect(within(table).queryByText("Would change", { selector: ".badge" })).toBeNull();
    expect(outcome({ ...hosts.hosts["vm-106"], changed: 0 }, false, true).word).toBe("No changes");
    expect(outcome({ ...hosts.hosts["vm-106"], unreachable: 1 }, false, true).word).toBe("Unreachable");
    expect(outcome(hosts.hosts["vm-106"], false, true).word).toBe("Changed");
  });
});
