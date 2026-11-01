// Kubernetes clusters in the app: the Defaults page's clusters and their write-only join tokens, the Servers
// page's cluster and role, the Run page's check-first Kubernetes runs with the upgrade's typed version, and the
// VMs page's members. The server's own checks are webui/tests/test_clusters.py.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ClustersData, Run, RunPageData, Vm } from "../src/types";
import { checkAxe, checkStructure } from "./a11y";
import * as fixtures from "./fixtures";
import { FakeApi, reply, renderAt } from "./server";

async function loaded(): Promise<void> {
  await waitFor(() => expect(document.querySelector(".loading")).toBeNull());
}

const TOKEN = "f".repeat(64);

function clusterForm(): HTMLFormElement {
  return screen.getByRole("form", { name: "Kubernetes clusters" }) as HTMLFormElement;
}

describe("the Defaults page's clusters", () => {
  it("draws each cluster from the roles' fields, with the members and the Vault names", async () => {
    new FakeApi();
    renderAt("/defaults");
    await loaded();
    const form = await waitFor(clusterForm);
    expect(within(form).getByRole("heading", { name: "lab" })).toBeTruthy();
    expect((within(form).getByLabelText(/^Name/) as HTMLInputElement).value).toBe("lab");
    expect((within(form).getByLabelText(/^k3s version/) as HTMLSelectElement).value).toBe("v1.36.5+k3s1");
    expect((within(form).getByLabelText("API address (VIP)") as HTMLInputElement).value).toBe("10.0.0.99");
    const disable = within(form).getByRole("group", { name: "Packaged components to leave out" });
    expect((within(disable).getByLabelText("servicelb") as HTMLInputElement).checked).toBe(true);
    expect((within(disable).getByLabelText("traefik") as HTMLInputElement).checked).toBe(false);
    expect(within(form).getByText(/Servers: web-01\. Agents: none/)).toBeTruthy();
    expect(within(form).getByText("vault_k8s_agent_token_lab")).toBeTruthy();
    // The server token is saved: replacing it takes the box, the agent token isn't yet.
    const server = within(form).getByLabelText("Server join token") as HTMLInputElement;
    expect(server.type).toBe("password");
    expect(within(form).getByText("A token is saved. Leave this empty to keep it.")).toBeTruthy();
    expect(within(form).getByText("Not set yet.")).toBeTruthy();
    expect(within(form).getAllByRole("button", { name: "Make a token and show it once" })[0]
      .getAttribute("aria-disabled")).toBe("true");
  });

  it("saves the clusters and never keeps a typed token in the page", async () => {
    const api = new FakeApi({ "POST /api/clusters": () => reply(422, { errors: {
      "clusters-0-k8s_lb_pool": "A LoadBalancer pool needs the API address: kube-vip announces both.",
      "clusters-0-k8s_disable": "Packaged components to leave out takes only servicelb.",
      clusters: "Cluster old still has members (web-09): set their Kubernetes cluster to none on the Servers page." } }) });
    renderAt("/defaults");
    await loaded();
    const form = await waitFor(clusterForm);
    await userEvent.type(within(form).getByLabelText("Agent join token"), TOKEN);
    await userEvent.click(within(form).getByLabelText("traefik"));
    await userEvent.click(within(form).getByRole("button", { name: "Save the clusters" }));
    await screen.findByText("There are 3 problems");
    const body = api.posts("/api/clusters")[0].body;
    expect(body.cluster_count).toBe("1");
    expect(body["clusters-0-original"]).toBe("lab");
    expect(body["clusters-0-k8s_agent_token"]).toBe(TOKEN);
    expect(body["clusters-0-k8s_api_vip"]).toBe("10.0.0.99");
    expect((within(form).getByLabelText("Agent join token") as HTMLInputElement).value).toBe("");
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(screen.getByRole("link", { name: /Cluster 1: A LoadBalancer pool/ }).getAttribute("href"))
      .toBe("#f-clusters-0-k8s_lb_pool");
    expect(screen.getByRole("link", { name: /Clusters: Cluster old/ }).getAttribute("href")).toBe("#clusters-error");
    checkStructure(document.body, "/defaults with cluster errors");
    await checkAxe("/defaults with cluster errors");

    api.on("POST /api/clusters", () => ({ message: "Saved 1 cluster(s)." }));
    await userEvent.click(within(form).getByLabelText("Replace the saved token"));
    await userEvent.type(within(form).getByLabelText("Server join token"), TOKEN);
    await userEvent.click(within(form).getByRole("button", { name: "Save the clusters" }));
    await screen.findByText("Saved 1 cluster(s).");
    expect(api.posts("/api/clusters")[1].body["clusters-0-k8s_token__replace"]).toBe("on");
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("adds and removes clusters", async () => {
    const api = new FakeApi({ "POST /api/clusters": () => reply(500, { error: "Something broke." }) });
    renderAt("/defaults");
    await loaded();
    const form = await waitFor(clusterForm);
    await userEvent.click(within(form).getByRole("button", { name: "Add a cluster" }));
    expect(within(form).getByRole("heading", { name: "New cluster 2" })).toBeTruthy();
    expect(within(form).getByText("Save the cluster first to make its tokens here.")).toBeTruthy();
    expect((within(form).getAllByLabelText("servicelb")[1] as HTMLInputElement).checked).toBe(true);
    await userEvent.click(within(form).getByRole("button", { name: "Remove cluster lab" }));
    expect(within(form).getByText("Cluster removed from the form. Save to keep the change.")).toBeTruthy();
    await userEvent.click(within(form).getByRole("button", { name: "Remove cluster New cluster 1" }));
    expect(within(form).getByText("No clusters yet.")).toBeTruthy();
    await userEvent.click(within(form).getByRole("button", { name: "Save the clusters" }));
    await screen.findByText("Something broke.");
    expect(api.posts("/api/clusters")[0].body.cluster_count).toBe("0");
  });

  it("makes a token, shows it once and hides it", async () => {
    const empty: ClustersData = { ...fixtures.clusters, clusters: [{
      ...fixtures.clusters.clusters[0], tokens: { k8s_token: false, k8s_agent_token: false } }] };
    const api = new FakeApi({ "GET /api/clusters": () => empty });
    api.on("POST /api/clusters/token", (c) => (c.body.token === "k8s_token"
      ? { token: TOKEN, variable: "vault_k8s_token_lab", message: "Saved." }
      : reply(422, { error: "A token is saved already." })));
    renderAt("/defaults");
    await loaded();
    const form = await waitFor(clusterForm);
    const [makeServer, makeAgent] = within(form).getAllByRole("button", { name: "Make a token and show it once" });
    await userEvent.click(makeServer);
    const shown = await screen.findByRole("heading", { name: "Server join token of lab" });
    expect(shown).toBeTruthy();
    expect(screen.getByText(TOKEN)).toBeTruthy();
    expect(api.posts("/api/clusters/token")[0].body).toEqual({ cluster: "lab", token: "k8s_token" });
    checkStructure(document.body, "/defaults with a token shown");
    await checkAxe("/defaults with a token shown");
    await userEvent.click(screen.getByRole("button", { name: "I have copied it: hide it" }));
    expect(document.body.innerHTML).not.toContain(TOKEN);
    await userEvent.click(makeAgent.isConnected ? makeAgent
      : within(clusterForm()).getAllByRole("button", { name: "Make a token and show it once" })[1]);
    await screen.findByText("A token is saved already.");
  });

  it("asks to replace before making a token over a saved one", async () => {
    const api = new FakeApi({ "POST /api/clusters/token": () => ({ token: TOKEN, variable: "vault_k8s_token_lab" }) });
    renderAt("/defaults");
    await loaded();
    const form = await waitFor(clusterForm);
    const make = within(form).getAllByRole("button", { name: "Make a token and show it once" })[0];
    await userEvent.click(make);
    expect(api.posts("/api/clusters/token")).toHaveLength(0);
    await userEvent.click(within(form).getByLabelText("Replace the saved token"));
    await userEvent.click(make);
    await screen.findByText(TOKEN);
    expect(api.posts("/api/clusters/token")[0].body.replace).toBe("on");
  });
});

describe("the Servers page's cluster and role", () => {
  it("picks a cluster from the Defaults page and a role", async () => {
    const api = new FakeApi({ "POST /api/servers": () => ({ message: "Saved." }) });
    renderAt("/servers");
    await loaded();
    const cluster = screen.getByLabelText("Kubernetes cluster, row 1") as HTMLSelectElement;
    expect(cluster.value).toBe("lab");
    expect([...cluster.options].map((o) => o.text)).toEqual(["None", "lab"]);
    expect((screen.getByLabelText("Kubernetes role, row 1") as HTMLSelectElement).value).toBe("server");
    expect((screen.getByLabelText("Kubernetes role, row 2") as HTMLSelectElement).value).toBe("none");
    await userEvent.selectOptions(screen.getByLabelText("Kubernetes cluster, row 2"), "lab");
    await userEvent.selectOptions(screen.getByLabelText("Kubernetes role, row 2"), "agent");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved.");
    const body = api.posts("/api/servers")[0].body;
    expect([body["servers-1-k8s_cluster"], body["servers-1-k8s_role"]]).toEqual(["lab", "agent"]);
  });

  it("keeps a cluster the Defaults page no longer has, and says when there are none", async () => {
    new FakeApi({ "GET /api/servers": () => ({ ...fixtures.servers, clusters: undefined }) });
    renderAt("/servers");
    await loaded();
    const cluster = screen.getByLabelText("Kubernetes cluster, row 1") as HTMLSelectElement;
    expect([...cluster.options].map((o) => o.value)).toEqual(["", "lab"]);
    expect(screen.getByText(/a cluster from the Defaults page \(add one there first\)/)).toBeTruthy();
  });
});

const k8sPage: RunPageData = {
  ...fixtures.runPage,
  kubernetes: {
    clusters: [
      { cluster: "lab", version: "v1.37.1+k3s1", members: ["cp-1", "w-1"], needs: [] },
      { cluster: "edge", version: "", members: [], needs: ["a k3s version (Defaults page)", "at least one server (Servers page)"] },
    ],
    actions: [{ key: "install", label: "Build the cluster, or add new members" },
      { key: "upgrade", label: "Upgrade the cluster to its version" }],
  },
};

const k8sCheck: Run = {
  ...fixtures.finished, id: "20261101-100000-aaaaaa", action: "kubernetes", status: "succeeded", returncode: 0,
  check: true, diff: true, limit: "k8s_lab", kind: "kubernetes", tags: ["upgrade"],
  playbook: "playbooks/kubernetes.yml",
  targets: [{ cluster: "lab", action: "upgrade", version: "v1.37.1+k3s1", members: ["cp-1", "w-1"] }] as never,
};

describe("the Run page's Kubernetes runs", () => {
  it("starts a check for a cluster, and says what a cluster still needs", async () => {
    const api = new FakeApi({ "GET /api/run": () => k8sPage });
    api.on("POST /api/kubernetes/run", (c) => (c.body.action === "install"
      ? reply(422, { error: "A run is going." }) : { redirect: `/runs/${k8sCheck.id}`, run: k8sCheck }));
    api.on(`GET /api/runs/${k8sCheck.id}`, () => ({ run: k8sCheck, log: "", offset: 0, hosts: null,
      apply: { possible: true, problem: "", until: 1792592000 } }));
    renderAt("/run");
    await loaded();
    const card = screen.getByRole("region", { name: "Kubernetes clusters" });
    expect(within(card).getByText(/Members: cp-1, w-1/)).toBeTruthy();
    expect(within(card).getByText(/needs a k3s version \(Defaults page\), at least one server/)).toBeTruthy();
    checkStructure(document.body, "/run with clusters");
    await checkAxe("/run with clusters");
    await userEvent.click(within(card).getByRole("button", { name: "Check install for cluster lab" }));
    await screen.findByText("A run is going.");
    await userEvent.click(within(card).getByRole("button", { name: "Check upgrade to v1.37.1+k3s1 for cluster lab" }));
    await waitFor(() => expect(window.location.pathname).toBe(`/runs/${k8sCheck.id}`));
    expect(api.posts("/api/kubernetes/run").map((c) => c.body)).toEqual([
      { cluster: "lab", action: "install" }, { cluster: "lab", action: "upgrade" }]);
  });

  it("runs an upgrade for real only with the version typed again", async () => {
    const real: Run = { ...k8sCheck, id: "20261101-100500-bbbbbb", check: false, applied_from: k8sCheck.id,
      status: "running", done: false, finished: null };
    const api = new FakeApi({
      [`GET /api/runs/${k8sCheck.id}`]: () => ({ run: k8sCheck, log: "", offset: 0, hosts: null,
        apply: { possible: true, problem: "", until: 1792592000 } }),
      [`GET /api/runs/${real.id}`]: () => ({ run: { ...real, status: "succeeded", done: true }, log: "", offset: 0 }),
    });
    api.on("POST /api/kubernetes/apply", (c) => (c.body.confirm_version !== "v1.37.1+k3s1"
      ? reply(422, { error: "Type the version the cluster is upgraded to, v1.37.1+k3s1, to confirm the upgrade." })
      : { redirect: `/runs/${real.id}`, run: real }));
    renderAt(`/runs/${k8sCheck.id}`);
    await loaded();
    expect(screen.getByText("lab, upgrade (k3s v1.37.1+k3s1)")).toBeTruthy();
    const part = screen.getByRole("heading", { name: "Run it for real" }).closest("section") as HTMLElement;
    expect(within(part).getByText(/2 members/)).toBeTruthy();
    checkStructure(document.body, "a Kubernetes check run");
    await checkAxe("a Kubernetes check run");
    await userEvent.click(within(part).getByLabelText(/want to upgrade lab to v1.37.1\+k3s1 now/));
    await userEvent.type(within(part).getByLabelText("Type the version to confirm the upgrade"), "v1.37");
    await userEvent.click(within(part).getByRole("button", { name: "Upgrade the cluster for real" }));
    await screen.findByText(/Type the version the cluster is upgraded to/);
    await userEvent.type(within(part).getByLabelText("Type the version to confirm the upgrade"), ".1+k3s1");
    await userEvent.click(within(part).getByRole("button", { name: "Upgrade the cluster for real" }));
    await waitFor(() => expect(window.location.pathname).toBe(`/runs/${real.id}`));
    expect(api.posts("/api/kubernetes/apply").at(-1)?.body).toEqual(
      { run_id: k8sCheck.id, confirm_version: "v1.37.1+k3s1", confirm: "on" });
  });

  it("installs for real without a version, and says why a check can't run any more", async () => {
    const install: Run = { ...k8sCheck, tags: ["install"],
      targets: [{ cluster: "lab", action: "install", version: "v1.36.5+k3s1", members: ["cp-1"] }] as never };
    const api = new FakeApi({ [`GET /api/runs/${k8sCheck.id}`]: () => ({ run: install, log: "", offset: 0,
      apply: { possible: true, problem: "", until: null } }) });
    api.on("POST /api/kubernetes/apply", () => reply(422, { error: "Settings were saved since the check run." }));
    renderAt(`/runs/${k8sCheck.id}`);
    await loaded();
    expect(screen.queryByLabelText("Type the version to confirm the upgrade")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Install for real" }));
    await screen.findByText("Settings were saved since the check run.");
    expect(api.posts("/api/kubernetes/apply")[0].body).toEqual({ run_id: k8sCheck.id });

    api.on(`GET /api/runs/${k8sCheck.id}`, () => ({ run: { ...install, targets: [] }, log: "", offset: 0,
      apply: { possible: false, problem: "The check run is more than 30 minutes old: run the check again.", until: 1 } }));
    renderAt(`/runs/${k8sCheck.id}`);
    await screen.findAllByText(/more than 30 minutes old/);
    expect(screen.getAllByRole("link", { name: "Open the Run page" }).length).toBeGreaterThan(0);
  });
});

describe("the VMs page's cluster members", () => {
  it("shows each member's cluster and role, warns when updates don't drain it, and filters by cluster", async () => {
    const members: Vm[] = [
      { ...fixtures.vmList[0], k8s: { cluster: "lab", role: "server", inventory: true, drain: true } },
      { ...fixtures.vmList[1], k8s: { cluster: "lab", role: "agent", inventory: false, drain: false } },
      ...fixtures.vmList.slice(2),
    ];
    new FakeApi({ "GET /api/vms": () => ({ ...fixtures.vms, vms: members }) });
    renderAt("/vms");
    await waitFor(() => expect(document.querySelector("tbody tr")).not.toBeNull());
    expect(screen.getAllByText((_, el) => el?.textContent === "Kubernetes: server of lab")).toHaveLength(1);
    expect(screen.getByText(/\(Proxmox tag only, not in the inventory\)/)).toBeTruthy();
    expect(screen.getAllByText("Updates don't drain it first")).toHaveLength(1);
    checkStructure(document.body, "/vms with cluster members");
    await checkAxe("/vms with cluster members");
    const rows = () => document.querySelectorAll("tbody tr").length;
    const all = rows();
    await userEvent.selectOptions(screen.getByLabelText("Kubernetes cluster"), "lab");
    expect(rows()).toBe(2);
    await userEvent.selectOptions(screen.getByLabelText("Kubernetes cluster"), "Not in a cluster");
    expect(rows()).toBe(all - 2);
  });
});
