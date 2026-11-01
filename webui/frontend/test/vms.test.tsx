// The VMs page: what it shows of each VM, sorting and filtering, and how it refreshes (every 30 seconds while
// the tab is visible, without reordering the rows).

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BACKOFF_MS, FILL_MS, NO_CLUSTER, POLL_MS, arrange, bytes, compareVms, matches, statusLabel } from "../src/pages/Vms";
import type { Vm } from "../src/types";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector(".loading")).toBeNull());
}

function names(): string[] {
  return [...document.querySelectorAll("tbody th .vm-name")].map((e) => e.textContent ?? "");
}

function row(name: string): HTMLElement {
  return screen.getByText(name, { selector: ".vm-name" }).closest("tr") as HTMLElement;
}

function gets(api: FakeApi): number {
  return api.calls.filter((c) => c.path === "/api/vms").length;
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});

describe("the VMs page", () => {
  it("lists every VM with its status, use, address and support", async () => {
    new FakeApi();
    renderAt("/vms");
    await loaded();
    expect(screen.getByText("Playbooks · cluster olympus · Proxmox VE 9.1.1")).toBeTruthy();
    expect(screen.getByText(/add VM.GuestAgent.Audit/)).toBeTruthy();
    expect(document.getElementById("vm-count")?.textContent).toBe("6 VMs");
    expect(names()).toEqual(["web-01", "db-01", "jammy", "win-11", "golden", "ct-dns"]); // by VMID

    const web = within(row("web-01"));
    expect(web.getByText("Running")).toBeTruthy();
    const cpu = web.getByLabelText("CPU use of web-01") as HTMLMeterElement;
    expect(cpu.tagName).toBe("METER");
    expect(cpu.value).toBeCloseTo(0.12);
    expect(web.getByText("12 % of 4 vCPUs")).toBeTruthy();
    expect((web.getByLabelText("Memory use of web-01") as HTMLMeterElement).max).toBe(4294967296);
    expect(web.getByText("1.5 GiB of 4 GiB")).toBeTruthy();
    expect(web.getByText("10.0.0.21")).toBeTruthy();
    expect(web.getByText("from the guest agent")).toBeTruthy();
    expect(web.getByText("Also 192.168.50.21")).toBeTruthy();
    expect(web.getByText("Supported").className).toContain("badge-success");
    expect(web.getByText("Installed by USAS")).toBeTruthy();

    const db = within(row("db-01"));
    expect(db.getByText("from the inventory")).toBeTruthy();
    expect(db.getByText("Inventory: database")).toBeTruthy();
    expect(db.getByText("The token may not ask the guest agent")).toBeTruthy();
    expect(db.getByText("Confirm the OS")).toBeTruthy();

    const jammy = within(row("jammy"));
    expect(jammy.getByText("Stopped")).toBeTruthy();
    expect(jammy.queryByRole("meter")).toBeNull();
    expect(jammy.getByText("from the cloud-init settings")).toBeTruthy();
    expect(jammy.getByText("Untested release")).toBeTruthy();

    const win = within(row("win-11"));
    expect(win.getByText("Paused")).toBeTruthy();
    expect(win.getByText("Not known")).toBeTruthy();
    expect(win.getByText("Locked: backup")).toBeTruthy();
    expect(win.getByText("Not supported")).toBeTruthy();
    expect(within(row("ct-dns")).getByText("Container (LXC)")).toBeTruthy();
    expect(within(row("golden")).getByText("Template")).toBeTruthy();
    // Only the running Ubuntu VMs can be selected; the others say why not.
    const boxes = [...document.querySelectorAll<HTMLInputElement>("tbody input[type=checkbox]")];
    expect(boxes.filter((b) => !b.disabled).map((b) => b.id)).toEqual(["vm-select-101", "vm-select-102"]);
    const stopped = document.getElementById("vm-select-103") as HTMLInputElement;
    expect(document.getElementById(stopped.getAttribute("aria-describedby") ?? "")?.textContent)
      .toBe("Can't be selected: It isn't running.");
    // The nav has the page, first in the Playbooks group.
    const playbooks = document.querySelector('[aria-labelledby="nav-playbooks"]') as HTMLElement;
    expect(within(playbooks).getAllByRole("link")[0].getAttribute("aria-current")).toBe("page");
  });

  it("sorts by a column, both ways, and says so", async () => {
    new FakeApi();
    renderAt("/vms");
    await loaded();
    const head = (label: string) => screen.getByRole("button", { name: label }).closest("th") as HTMLElement;
    expect(head("VMID").getAttribute("aria-sort")).toBe("ascending");
    expect(head("Name").hasAttribute("aria-sort")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Name" }));
    expect(head("Name").getAttribute("aria-sort")).toBe("ascending");
    expect(head("VMID").hasAttribute("aria-sort")).toBe(false);
    expect(names()).toEqual(["ct-dns", "db-01", "golden", "jammy", "web-01", "win-11"]);
    expect(document.querySelector("caption")?.textContent).toContain("sorted by Name, ascending");
    await userEvent.click(screen.getByRole("button", { name: "Name" }));
    expect(head("Name").getAttribute("aria-sort")).toBe("descending");
    expect(names()[0]).toBe("win-11");
    await userEvent.click(screen.getByRole("button", { name: "CPU" }));
    expect(names()).toEqual(["jammy", "win-11", "golden", "web-01", "ct-dns", "db-01"]); // VMs not running first
    await userEvent.click(screen.getByRole("button", { name: "Address" }));
    expect(names().slice(0, 3)).toEqual(["web-01", "db-01", "jammy"]);
    await userEvent.click(screen.getByRole("button", { name: "Playbooks" }));
    expect(names()[0]).toBe("web-01");
  });

  it("filters, and announces the count", async () => {
    new FakeApi();
    renderAt("/vms");
    await loaded();
    const count = document.getElementById("vm-count") as HTMLElement;
    expect(count.getAttribute("role")).toBe("status");
    await userEvent.type(screen.getByLabelText("Find"), "10.0.0.2");
    expect(count.textContent).toBe("3 of 6 VMs shown");
    await userEvent.clear(screen.getByLabelText("Find"));
    await userEvent.selectOptions(screen.getByLabelText("Node"), "ares");
    expect(names()).toEqual(["db-01"]);
    await userEvent.click(screen.getByRole("button", { name: "Clear the filters" }));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "other");
    expect(names()).toEqual(["win-11"]);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "stopped");
    await userEvent.selectOptions(screen.getByLabelText("Playbook support"), "override");
    expect(names()).toEqual(["jammy"]);
    await userEvent.click(screen.getByRole("button", { name: "Clear the filters" }));
    expect(count.textContent).toBe("6 VMs");
    expect(screen.queryByRole("button", { name: "Clear the filters" })).toBeNull();
  });

  it("says what to set up first, and shows an empty cluster", async () => {
    const api = new FakeApi({
      "GET /api/vms": () => reply(409, { error: "Set up the Proxmox API first: the host and the token.", setup: "/proxmox" }),
    });
    renderAt("/vms");
    await screen.findByText("Set up the Proxmox API first: the host and the token.");
    expect(screen.getByRole("link", { name: "Open the Proxmox page" }).getAttribute("href")).toBe("/proxmox");
    api.on("GET /api/vms", () => ({ ...fixtures.vms, vms: [], notes: [], cluster: "", version: "" }));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("The cluster has no VMs or containers.");
    expect(screen.getByText("Playbooks · Proxmox VE ?")).toBeTruthy();
  });
});

describe("refreshing", () => {
  it("asks again every 30 seconds, keeps the rows in place and adds new VMs at the end", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const api = new FakeApi();
    renderAt("/vms");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(gets(api)).toBe(1);
    await act(async () => { screen.getByRole("button", { name: "CPU" }).click(); });
    const before = names();
    // The use changes and a VM appears: the order stays, the new VM goes last.
    const busier = fixtures.vmList.map((vm) => ({ ...vm, cpu: 1 - vm.cpu }));
    const added: Vm = { ...fixtures.vmList[0], vmid: 150, name: "new-vm" };
    api.on("GET /api/vms", () => ({ ...fixtures.vms, vms: [...busier.filter((vm) => vm.vmid !== 104), added] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS - 1); });
    expect(gets(api)).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(gets(api)).toBe(2);
    expect(names()).toEqual([...before.filter((n) => n !== "win-11"), "new-vm"]);
  });

  it("pauses while the tab is hidden and catches up when it shows", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const api = new FakeApi();
    renderAt("/vms");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { setHidden(true); });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 4); });
    expect(gets(api)).toBe(1);
    await act(async () => { setHidden(false); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(gets(api)).toBe(2);
    // Shown again soon after a load: it waits for the rest of the interval.
    await act(async () => { setHidden(true); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await act(async () => { setHidden(false); });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS - 1001); });
    expect(gets(api)).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(gets(api)).toBe(3);
  });

  it("comes back sooner while agents are answering, and later after an error", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const api = new FakeApi({ "GET /api/vms": () => ({ ...fixtures.vms, complete: false }) });
    renderAt("/vms");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(FILL_MS); });
    expect(gets(api)).toBe(2);
    api.on("GET /api/vms", () => reply(502, { error: "Couldn't connect to pve:8006." }));
    await act(async () => { await vi.advanceTimersByTimeAsync(FILL_MS); });
    expect(gets(api)).toBe(3);
    // The last list stays, with the error above it.
    expect(screen.getByText("The last refresh failed: Couldn't connect to pve:8006.")).toBeTruthy();
    expect(names()).toHaveLength(6);
    await act(async () => { await vi.advanceTimersByTimeAsync(BACKOFF_MS - 1); });
    expect(gets(api)).toBe(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(gets(api)).toBe(4);
  });

  it("refreshes on request, once at a time", async () => {
    const api = new FakeApi();
    renderAt("/vms");
    await loaded();
    const button = screen.getByRole("button", { name: "Refresh" });
    await userEvent.click(button);
    await waitFor(() => expect(gets(api)).toBe(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy());
  });
});

describe("the helpers", () => {
  const vm = fixtures.vmList[0];

  it("format sizes and statuses", () => {
    expect(bytes(4294967296)).toBe("4 GiB");
    expect(bytes(1610612736)).toBe("1.5 GiB");
    expect(bytes(12884901888 * 2)).toBe("24 GiB");
    expect(bytes(536870912)).toBe("512 MiB");
    expect(statusLabel("running")).toBe("Running");
    expect(statusLabel("")).toBe("Unknown");
  });

  it("compare by number, by text and by address, then by VMID", () => {
    const a = { ...vm, vmid: 1, ip: "10.0.0.9", name: "vm10" };
    const b = { ...vm, vmid: 2, ip: "10.0.0.10", name: "vm9" };
    expect(compareVms(a, b, { key: "ip", ascending: true })).toBeLessThan(0);
    expect(compareVms(a, b, { key: "name", ascending: true })).toBeGreaterThan(0); // numeric
    expect(compareVms(a, { ...b, ip: "" }, { key: "ip", ascending: true })).toBeLessThan(0); // no address last
    expect(compareVms(a, { ...a, vmid: 2 }, { key: "node", ascending: false })).toBeLessThan(0);
    expect(compareVms({ ...a, maxmem: 0 }, b, { key: "mem", ascending: true })).toBeLessThan(0);
  });

  it("arrange keeps the order and drops VMs that are gone", () => {
    const list = fixtures.vmList;
    expect(arrange([], list, { key: "vmid", ascending: false })).toEqual([200, 105, 104, 103, 102, 101]);
    expect(arrange([103, 101, 999], list.slice(0, 3), { key: "vmid", ascending: true })).toEqual([103, 101, 102]);
  });

  it("matches every word of the search", () => {
    const none = { text: "", node: "", status: "", support: "", cluster: "" };
    expect(matches(vm, { ...none, text: "web ubuntu" })).toBe(true);
    expect(matches(vm, { ...none, cluster: NO_CLUSTER })).toBe(true);
    expect(matches(vm, { ...none, cluster: "lab" })).toBe(false);
    expect(matches({ ...vm, k8s: { cluster: "lab", role: "agent", inventory: true, drain: false } },
      { ...none, cluster: "lab" })).toBe(true);
    expect(matches(vm, { ...none, text: "web debian" })).toBe(false);
    expect(matches(vm, { ...none, text: "192.168.50" })).toBe(true);
    expect(matches(vm, { ...none, support: "unsupported" })).toBe(false);
  });
});
