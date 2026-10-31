// The Updates page (each server's newest result, the schedules, the settings) and making a schedule on a
// passing updates check run's page. The server's checks are its own (webui/tests/test_app.py, test_scheduler.py).

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { windowText } from "../src/pages/Updates";
import type { Schedule, UpdatesData } from "../src/types";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

const schedule: Schedule = {
  id: "a1b2c3d4e5f6", name: "Nightly web", kind: "apply", kind_label: "Install the updates (after a passing check)",
  days: ["mon", "thu"], start: "02:30", minutes: 120, timezone: "Europe/Berlin",
  targets: [{ vmid: 106, name: "vm-106", address: "10.0.0.26" }], options: { updates_scope: "all" },
  created: 1792591000, made_from: "20261031-090000-abcdef", next: 1792700000, open: false, last_window: "",
  last_outcome: "Not installed: vm-106 unreachable.", last_time: 1792600000, last_run: "20261031-100000-abcdef",
};

const page: UpdatesData = {
  ...fixtures.updates,
  hosts: {
    "vm-106": {
      run: "20261031-100000-abcdef", check: true, finished: 1792600000,
      result: {
        status: "pending", reason: "", scope: "all", os: "Ubuntu 26.04", pending: [], planned: ["openssl", "tool"],
        upgraded: [], security_count: 1, held: [], excluded: [], deferred: [], removals: [], restart_services: [],
        reboot: { required: true, done: false, held: false, why: "" }, health: { status: "skipped", problems: [] },
        snapshot: { status: "off", name: "", reason: "", warning: "" }, wave: { number: 1, of: 1, groups: [], ha: false },
        auto_updates: "",
      },
    },
  },
  schedules: [schedule],
};

describe("the Updates page", () => {
  it("shows each server's newest result and the schedules, deletes one, and saves the settings", async () => {
    let schedules = [schedule];
    const api = new FakeApi({ "GET /api/updates": () => ({ ...page, schedules }) });
    api.on("POST /api/updates/schedules", () => {
      schedules = [];
      return { message: "Deleted the schedule Nightly web." };
    });
    api.on("POST /api/updates", (c) => (c.body.updates_serial === "x"
      ? reply(422, { errors: { updates_serial: "Servers per wave isn't in the accepted format." } })
      : { message: "Saved the updates settings." }));
    renderAt("/updates");
    const servers = within((await screen.findByRole("heading", { name: "Servers" })).closest("section") as HTMLElement);
    const row = within(servers.getByText("vm-106").closest("tr") as HTMLElement);
    expect(row.getByText("Updates waiting")).toBeTruthy();
    expect(row.getByText("2 packages")).toBeTruthy();
    expect(row.getByText("Reboot waiting")).toBeTruthy();
    expect(row.getByRole("link", { name: "Check" }).getAttribute("href")).toBe("/runs/20261031-100000-abcdef");
    const list = within(screen.getByRole("heading", { name: "Schedules" }).closest("section") as HTMLElement);
    expect(list.getByText("Monday, Thursday, from 02:30 for 2 hours (Europe/Berlin)")).toBeTruthy();
    expect(list.getByText("Not installed: vm-106 unreachable.")).toBeTruthy();
    expect(list.getByText("a1b2c3d4e5f6")).toBeTruthy();
    expect(screen.getByLabelText("Servers per wave")).toBeTruthy();
    checkStructure(document.body, "/updates");
    await checkAxe("/updates");
    const user = userEvent.setup();
    await user.click(list.getByRole("button", { name: "Delete the schedule Nightly web" }));
    expect(api.posts("/api/updates/schedules")[0].body).toEqual({ action: "delete", id: schedule.id });
    expect(await list.findByText("Deleted the schedule Nightly web.")).toBeTruthy();
    await waitFor(() => expect(list.getByText("No schedules.")).toBeTruthy());
    await user.clear(screen.getByLabelText("Servers per wave"));
    await user.type(screen.getByLabelText("Servers per wave"), "x");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Servers per wave");
  });

  it("says when there is nothing yet and when the scheduler is off", async () => {
    new FakeApi({ "GET /api/updates": () => ({ ...page, hosts: {}, schedules: [], scheduler: false }) });
    renderAt("/updates");
    expect(await screen.findByText(/No updates run yet/)).toBeTruthy();
    expect(screen.getByText(/The scheduler is off/)).toBeTruthy();
    expect(windowText({ days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], start: "01:00", minutes: 45,
                        timezone: "Etc/UTC" })).toBe("Every day, from 01:00 for 45 minutes (Etc/UTC)");
  });
});

describe("making a schedule", () => {
  const check = { ...fixtures.finished, id: "20261031-090000-abcdef", action: "updates", status: "succeeded",
                  returncode: 0, check: true, diff: false, kind: "updates", targets: [], plan: "u1",
                  playbook: "playbooks/updates.yml", options: { updates_scope: "all", updates_reboot: "never" } };

  it("is offered on a passing updates check's page and needs the password", async () => {
    const api = new FakeApi({
      [`GET /api/runs/${check.id}`]: () => ({ run: check, log: "", offset: 0, hosts: null,
                                              apply: { possible: true, problem: "", until: 1792592000 } }),
      [`GET /api/runs/${check.id}/hosts`]: () => ({ hosts: null, done: true }),
      "GET /api/updates": () => page,
    });
    let attempt = 0;
    api.on("POST /api/updates/schedules", () => (++attempt === 1
      ? reply(422, { errors: { schedule_current: "The current password is wrong.", "day-mon": "Choose at least one day." } })
      : { message: "Saved the schedule Nightly.", redirect: "/updates" }));
    renderAt(`/runs/${check.id}`);
    const part = within((await screen.findByRole("heading", { name: "Schedule this plan" })).closest("section") as HTMLElement);
    checkStructure(document.body, "a check run that can be scheduled");
    await checkAxe("a check run that can be scheduled");
    const user = userEvent.setup();
    await user.type(part.getByLabelText("Name"), "Nightly");
    await user.click(part.getByLabelText("Install the updates (after a passing check)"));
    await user.click(part.getByRole("button", { name: "Save the schedule" }));
    const summary = await screen.findByRole("alert");
    expect(summary.textContent).toContain("There are 2 problems");
    await user.click(within(summary).getByText("Choose at least one day."));
    expect(document.activeElement?.id).toBe("f-day-mon");
    await user.click(part.getByLabelText("Thursday"));
    await user.type(part.getByLabelText("Current password"), "secret words");
    await user.click(part.getByRole("button", { name: "Save the schedule" }));
    expect(api.posts("/api/updates/schedules").at(-1)?.body).toMatchObject({
      action: "create", run_id: check.id, name: "Nightly", kind: "apply", "day-thu": "on", start: "02:00",
      minutes: "180", timezone: page.timezone, schedule_current: "secret words",
    });
    await waitFor(() => expect(window.location.pathname).toBe("/updates"));
  });
});
