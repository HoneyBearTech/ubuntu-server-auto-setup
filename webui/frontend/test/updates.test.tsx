// Installing updates from the VMs page: the Updates choice and its options, whether a snapshot is possible, the
// review, the per-server results of the updates playbook, and the real run's confirmation that names the VMs
// that may reboot. The server's checks are its own (webui/tests/test_app.py, test_vmplan.py).

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { rebootText, updateStatus } from "../src/components/UpdateResults";
import { DEFAULT_UPDATES, defaultSettings, planForm } from "../src/pages/VmRun";
import type { UpdateResult, Vm, VmPlan } from "../src/types";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

const app: Vm = {
  ...fixtures.vmList[0], vmid: 106, name: "app-01", inventory_name: "", ours: false, alias: "usas-olympus-106",
  addresses: ["10.0.0.26"], ip: "10.0.0.26", pinned: [{ keytype: "ssh-ed25519", fingerprint: "SHA256:pin106" }],
};

const target = {
  vmid: 106, name: "vm-106", pve_name: "app-01", node: "hephaestus", static: false, address: "10.0.0.26",
  source: "agent", port: null, user: "ubuntu", alias: "usas-olympus-106", fingerprints: ["SHA256:pin106"],
  manage_hostname: false, override: false, ufw: [],
};

const plan: VmPlan = {
  preset: "updates", preset_label: "Updates", tags: [], digest: "u1", playbook: "updates",
  options: { updates_scope: "all", updates_reboot: "if_required", updates_snapshot: "false", updates_ignore_window: "false" },
  warnings: ["These VMs reboot if an update asks for it, inside their reboot window: vm-106. Their health is checked after."],
  targets: [target],
};

async function choose(snapshots: unknown) {
  const api = new FakeApi({ "GET /api/vms": () => ({ ...fixtures.vms, vms: [...fixtures.vmList, app] }) });
  api.on("POST /api/vms/snapshots", () => snapshots);
  renderAt("/vms");
  await waitFor(() => expect(document.querySelector("tbody tr")).not.toBeNull());
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("Select app-01"));
  await user.click(screen.getByLabelText("Updates"));
  return { api, user };
}

describe("updates from the VMs page", () => {
  it("offers the updates' choices, with no snapshot when Proxmox can't take one", async () => {
    const { api, user } = await choose({ vms: [{ vmid: 106, possible: false, reason: "The API token's role lacks VM.Snapshot." }] });
    expect(screen.getByRole("heading", { name: "Update 1 selected VM" })).toBeTruthy();
    const snapshot = screen.getByLabelText("Take a Proxmox snapshot of each VM before its update") as HTMLInputElement;
    await waitFor(() => expect(screen.getByText(/Not possible: VM 106: The API token's role lacks VM.Snapshot/)).toBeTruthy());
    expect(snapshot.disabled && !snapshot.checked).toBe(true);
    expect(api.posts("/api/vms/snapshots")[0].body).toEqual({ vmid: "106" });
    // The baseline's per-VM settings don't apply to updates.
    expect(screen.queryByLabelText("Extra firewall rules")).toBeNull();
    api.on("POST /api/vms/plan", () => ({ plan_id: "p1", plan, expires: 1 }));
    api.on("POST /api/vms/run", () => ({ redirect: `/runs/${fixtures.running.id}`, run: fixtures.running }));
    await user.click(screen.getByLabelText("When an update asks for it"));
    await user.click(screen.getByLabelText("Ignore each server's update window"));
    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    expect(api.posts("/api/vms/plan")[0].body).toMatchObject({
      preset: "updates", "updates-scope": "all", "updates-reboot": "if_required", "updates-window": "on", vmid: "106",
    });
    expect(api.posts("/api/vms/plan")[0].body).not.toHaveProperty("updates-snapshot");
    expect(api.posts("/api/vms/plan")[0].body).not.toHaveProperty("vm-106-ufw");
    const heading = await screen.findByRole("heading", { name: "Review: Updates" });
    const review = within(heading.closest("section") as HTMLElement);
    expect(review.getByText(/installs nothing and reboots nothing/)).toBeTruthy();
    expect(review.getByText(/reboot if an update asks for it/)).toBeTruthy();
    checkStructure(document.body, "/vms updates review");
    await checkAxe("/vms updates review");
    await user.click(review.getByRole("button", { name: "Run the check (nothing changes)" }));
    expect(api.posts("/api/vms/run")[0].body).toEqual({ plan_id: "p1", mode: "check" });
  });

  it("ticks the snapshot when every VM can have one, and keeps an unticked box", async () => {
    const { api, user } = await choose({ vms: [{ vmid: 106, possible: true, reason: "" }] });
    const snapshot = screen.getByLabelText("Take a Proxmox snapshot of each VM before its update") as HTMLInputElement;
    await waitFor(() => expect(snapshot.checked).toBe(true));
    api.on("POST /api/vms/plan", () => reply(422, { errors: { "updates-snapshot": "No snapshot is possible for VM 106" } }));
    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    expect(api.posts("/api/vms/plan")[0].body).toMatchObject({ "updates-snapshot": "on" });
    expect((await screen.findByRole("alert")).textContent).toContain("No snapshot is possible");
    await user.click(snapshot);
    await user.click(screen.getByRole("button", { name: "Review the plan" }));
    expect(api.posts("/api/vms/plan")[1].body).not.toHaveProperty("updates-snapshot");
  });

  it("sends the updates' choices and no baseline settings", () => {
    const settings = { 106: { ...defaultSettings(app, "ubuntu"), ufw: "443", hostname: true, override: true } };
    const form = Object.fromEntries(planForm([app], settings, "updates", ["ssh"],
                                             { ...DEFAULT_UPDATES, scope: "security", snapshot: true }));
    expect(form).toEqual({ preset: "updates", "updates-scope": "security", "updates-reboot": "never",
                           "updates-snapshot": "on", vmid: "106", "vm-106-address": "10.0.0.26", "vm-106-port": "",
                           "vm-106-user": "ubuntu" });
  });
});

const result = (over: Partial<UpdateResult>): UpdateResult => ({
  status: "pending", reason: "", scope: "all", os: "Ubuntu 26.04 LTS", pending: [], planned: [], upgraded: [],
  security_count: 0, held: [], excluded: [], deferred: [], removals: [], restart_services: [],
  reboot: { required: false, done: false, held: false, why: "" }, health: { status: "skipped", problems: [] },
  snapshot: { status: "off", name: "", reason: "", warning: "" }, wave: { number: 1, of: 2, groups: [], ha: false },
  auto_updates: "", ...over,
});

describe("an updates run's page", () => {
  const check = { ...fixtures.finished, id: "20261031-090000-abcdef", action: "updates", status: "succeeded",
                  returncode: 0, check: true, diff: false, kind: "updates", targets: [target], plan: "u1",
                  playbook: "playbooks/updates.yml", options: plan.options };
  const updates = {
    hosts: {
      "vm-106": result({
        pending: [{ name: "openssl", current: "3.0.1", candidate: "3.0.2", security: true },
                  { name: "<b>tool</b>", current: "1.0", candidate: "1.1", security: false }],
        planned: ["openssl", "<b>tool</b>"], security_count: 1, deferred: ["usas-test-phased"],
        reboot: { required: true, done: false, held: false, why: "" }, wave: { number: 1, of: 2, groups: ["web"], ha: true },
      }),
      "web-01": result({ status: "unreachable", reason: "couldn't be reached", wave: { number: 0, of: 2, groups: [], ha: false } }),
    },
    summary: { pending: 1, unreachable: 1 },
  };
  const hosts = { finished: true, updated: 1, hosts: {}, updates };

  it("shows what each server would install, its reboot and wave, then offers the real run naming the reboots", async () => {
    const real = { ...check, id: "20261031-091000-fedcba", check: false, applied_from: check.id, done: false,
                   status: "running", finished: null, returncode: null };
    const api = new FakeApi({
      [`GET /api/runs/${check.id}`]: () => ({ run: check, log: "", offset: 0, hosts,
                                              apply: { possible: true, problem: "", until: 1792592000 } }),
      [`GET /api/runs/${check.id}/hosts`]: () => ({ hosts, done: true }),
      [`GET /api/runs/${real.id}`]: () => ({ run: real, log: "", offset: 0, hosts: null, apply: null }),
      [`GET /api/runs/${real.id}/hosts`]: () => ({ hosts: null, done: false }),
    });
    api.on("POST /api/vms/apply", () => ({ redirect: `/runs/${real.id}`, run: real }));
    renderAt(`/runs/${check.id}`);
    const heading = await screen.findByRole("heading", { name: "Updates per server" });
    const table = within(heading.closest("section") as HTMLElement);
    const row = within(table.getByText("vm-106").closest("tr") as HTMLElement);
    expect(row.getByText("Updates waiting")).toBeTruthy();
    expect(row.getByText("2 packages to install")).toBeTruthy();
    expect(row.getByText("openssl 3.0.1 → 3.0.2 (security)")).toBeTruthy();
    // A server's package names are text, never markup.
    expect(row.getByText("<b>tool</b> 1.0 → 1.1")).toBeTruthy();
    expect(row.getByText("1 deferred package (phased or kept back)")).toBeTruthy();
    expect(row.getByText("Reboot waiting")).toBeTruthy();
    expect(row.getByText("1 of 2")).toBeTruthy();
    expect(row.getByText("Managed by Proxmox HA")).toBeTruthy();
    expect(within(table.getByText("web-01").closest("tr") as HTMLElement).getByText("Unreachable")).toBeTruthy();
    expect(screen.getByText("The VMs page (updates)")).toBeTruthy();
    expect(screen.getByText(/updates_reboot if_required/)).toBeTruthy();
    const apply = within((await screen.findByRole("heading", { name: "Run it for real" })).closest("section") as HTMLElement);
    expect(apply.getByText(/These VMs may reboot, inside their reboot window, if an update asks for it: vm-106/)).toBeTruthy();
    checkStructure(document.body, "an updates check run");
    await checkAxe("an updates check run");
    const user = userEvent.setup();
    await user.click(apply.getByLabelText(/want to update this VM now, rebooting it if needed/));
    await user.click(apply.getByRole("button", { name: "Install the updates for real" }));
    expect(api.posts("/api/vms/apply").at(-1)?.body).toEqual({ run_id: check.id, confirm: "on" });
    await waitFor(() => expect(window.location.pathname).toBe(`/runs/${real.id}`));
  });

  it("says when nothing reboots, and History marks updates runs", async () => {
    const quiet = { ...check, options: { ...plan.options, updates_reboot: "never" } };
    new FakeApi({
      [`GET /api/runs/${check.id}`]: () => ({ run: quiet, log: "", offset: 0, hosts: null,
                                              apply: { possible: true, problem: "", until: 1792592000 } }),
      [`GET /api/runs/${check.id}/hosts`]: () => ({ hosts: null, done: true }),
      "GET /api/runs": () => ({ runs: [check] }),
    });
    renderAt(`/runs/${check.id}`);
    expect(await screen.findByText("Nothing reboots (reboots: never).")).toBeTruthy();
    expect(screen.getByLabelText(/want to update this VM now$/)).toBeTruthy();
  });

  it("words each result", () => {
    expect(updateStatus(result({ status: "nothing" })).word).toBe("Up to date");
    expect(updateStatus(result({ status: "bogus" as UpdateResult["status"] })).word).toBe("Failed");
    expect(rebootText(result({ reboot: { required: true, done: true, held: false, why: "" } }), false)).toBe("Rebooted");
    expect(rebootText(result({ reboot: { required: true, done: false, held: true, why: "PCR 7" } }), false)).toBe("Held: PCR 7");
    expect(rebootText(result({ reboot: { required: true, done: false, held: false, why: "" } }), false)).toBe("Reboot pending");
    expect(rebootText(result({}), true)).toBe("None needed");
  });
});

describe("History", () => {
  it("marks the VMs page's updates runs", async () => {
    const run = { ...fixtures.finished, kind: "updates", action: "updates", check: true };
    new FakeApi({ "GET /api/runs": () => ({ runs: [run] }) });
    renderAt("/runs");
    expect(await screen.findByText("VMs page, updates check")).toBeTruthy();
  });
});
