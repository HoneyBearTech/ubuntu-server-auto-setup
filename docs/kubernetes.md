# Kubernetes clusters (k3s)

This guide goes from an inventory with no cluster in it to a running [k3s](https://k3s.io/) cluster on Proxmox
VE, then through what comes after: upgrades, fleet updates that drain each node, the optional handoff to Argo
CD, and the web UI. The variables, the commands and what each step does in detail are in
[interfaces.md](interfaces.md#kubernetes-nodes); this page puts them in order and links there instead of
repeating them.

USAS builds the cluster and keeps its nodes patched. It doesn't run your workloads: what runs on the cluster
is yours, or your GitOps repository's (see [what it won't do](#what-it-wont-do)).

Kubernetes is opt-in. Without a `k8s_<cluster>_servers` or `k8s_<cluster>_agents` group in the inventory,
nothing on this page happens, and `make site` never installs k3s.

## What you need

- USAS set up as [installing.md](installing.md) describes, with the API token and the Vault.
- Three servers for a cluster that survives losing one (etcd needs two of three), and as many agents as your
  workloads need. One server works too, without that.
- Proxmox VE nodes enough to put each server on a different node: a cluster whose servers share a node goes
  down with it.
- One free IPv4 address on the servers' network for the API (the VIP), and, for LoadBalancer services, a free
  range or network beside it (the pool). No other host may use them, and no DHCP server may hand them out.
- A time service on every member (chrony or systemd-timesyncd, which the supported releases have), since etcd
  needs the servers' clocks in step.
- On the RHEL family: release 9. Rancher has no stable `k3s-selinux` build for release 10 yet, so a member on
  AlmaLinux, Rocky Linux or CentOS Stream 10 is refused before anything changes.

## 1. The inventory

A cluster is three groups under `servers`: `k8s_<cluster>` with the cluster's variables, and its two children
`k8s_<cluster>_servers` and `k8s_<cluster>_agents` with the members. The name is lowercase letters, digits and
`-`, starting with a letter. A server belongs to one cluster at most. `inventory/hosts.example.yml` has the same
example, commented out:

```yaml
servers:
  hosts:
    cp-1: {ansible_host: 10.10.10.71, pve_vmid: 4071, pve_node: pve-a, vm_spread_group: k8s-lab-servers}
    cp-2: {ansible_host: 10.10.10.72, pve_vmid: 4072, pve_node: pve-b, vm_spread_group: k8s-lab-servers}
    cp-3: {ansible_host: 10.10.10.73, pve_vmid: 4073, pve_node: pve-c, vm_spread_group: k8s-lab-servers}
    w-1:
      ansible_host: 10.10.10.81
      pve_vmid: 4081
      pve_node: pve-a
      vm_extra_disks: [{size_gb: 100, label: longhorn, mount: /var/lib/longhorn}]
  children:
    k8s_lab:
      vars:
        k8s_version: v1.36.5+k3s1              # one of roles/k3s/vars/catalog.yml's releases
        k8s_api_vip: 10.10.10.70               # the API's address: kube-vip holds it on one server at a time
        k8s_lb_pool: 10.10.10.90-10.10.10.99   # addresses for LoadBalancer services
        k8s_api_sources: [10.10.10.5]          # the control node, to run kubectl from it
      children:
        k8s_lab_servers:
          hosts: {cp-1: {}, cp-2: {}, cp-3: {}}
        k8s_lab_agents:
          hosts: {w-1: {}}
          vars: {k8s_longhorn_prereqs: true}
```

- **Addresses.** Each member's address in the cluster is its `ansible_host`, or `k8s_node_ip` if it has another
  one there. It must be an IPv4 address: a server on DHCP needs `k8s_node_ip`, since every firewall rule names
  one member's address.
- **`vm_spread_group`** makes `make provision` refuse to put two of the servers on one node
  ([disks and placement](interfaces.md#disks-and-placement)). It's a check, not a Proxmox HA rule: add an HA
  node affinity rule by hand if HA may move them.
- **`vm_extra_disks`** gives an agent a disk for Longhorn, formatted and mounted while blank.
  `k8s_longhorn_prereqs` installs what Longhorn needs on the node; Longhorn itself is your GitOps repository's.
- **`k8s_version` has no default.** A new USAS release with a newer k3s in its catalog never upgrades a
  cluster by itself: you change the version, and run an [upgrade](#5-upgrades).

Every other setting (the VIP's interface, `k8s_disable`, the taint, Secrets encryption, flannel's backend, the
read-only kubeconfig, the timeouts) is in [interfaces.md](interfaces.md#kubernetes-clusters), with its default.

## 2. The join tokens

Each cluster has two join tokens in the Vault: one for its servers, one for its agents. An agent holds only its
own, so a compromised agent can't join as a server. The playbook never makes them: you do, and nothing else
ever sees them. A `-` in the cluster's name is `_` in the variables' names.

```bash
openssl rand -hex 32    # once for the servers' token
openssl rand -hex 32    # and again for the agents'
ansible-vault edit inventory/group_vars/all/vault.yml
```

Add the two lines, each with its own value:

```yaml
vault_k8s_token_lab: <the first value>
vault_k8s_agent_token_lab: <the second value>
```

Keep a copy in your password manager. A token is part of a running cluster: changing it in the Vault later
doesn't change the cluster's, and needs k3s' own token rotation first ([rotating secrets](upgrading.md#rotating-secrets)). A missing or malformed token stops
`make kubernetes` in its checks, before any member changes.

## 3. Provision, baseline, then `make kubernetes`

```bash
make build-installer                         # the images the new VMs install from
make provision LIMIT=k8s_lab
make baseline LIMIT=k8s_lab
make kubernetes LIMIT=k8s_lab ARGS="--check --diff"
make kubernetes LIMIT=k8s_lab
```

- `make provision` creates the VMs that don't exist yet, checks the spread group and adds the extra disks
  ([architecture.md](architecture.md#how-a-server-is-built)).
- `make baseline` hardens them as any server, and its `kubernetes_node` role prepares each member: k3s' ports
  opened between the members only (each rule names one member's address), the kernel modules and sysctls k3s
  needs, swap off, Longhorn's prerequisites and, on the RHEL family, k3s' SELinux policy
  ([Kubernetes nodes](interfaces.md#kubernetes-nodes)). `make site` runs the first three steps in one go.
- `make kubernetes` checks every member first, then installs the k3s binary only if it matches the catalog's
  SHA-256, starts the first server, joins the others one at a time through the VIP, then the agents, and waits
  until every node is Ready ([Kubernetes clusters](interfaces.md#kubernetes-clusters)). Check mode shows what
  would change; the tasks that write the tokens show no diff. A second run changes nothing.

Then, on any server:

```bash
ssh sysadmin@10.10.10.71 'sudo k3s kubectl get nodes -o wide'
```

The admin kubeconfig stays on the servers (`/etc/rancher/k3s/k3s.yaml`, 0600): admin work is `sudo k3s
kubectl` there. For a kubeconfig on the control node, set `k8s_fetch_readonly_kubeconfig: true`: the next run
writes one of a read-only account to `inventory/kube/<cluster>-readonly.yaml` (0600), which reaches the API
only from an address in `k8s_api_sources`:

```bash
KUBECONFIG=inventory/kube/lab-readonly.yaml kubectl get nodes,pods -A
```

## 4. The VIP and the pool

With `k8s_api_vip`, [kube-vip](https://kube-vip.io/) runs on every server and one of them holds the address,
answering ARP for it. If that server stops, another takes the address within about 20 seconds (kube-vip's default lease), and it doesn't
move back by itself. The API's certificate is valid for the VIP, and every member joins the cluster through
it, so the first server isn't special once the cluster runs. Without a VIP, every member joins through the
first server's own address, and the cluster's API is that server's.

With `k8s_lb_pool` (it needs the VIP, and `servicelb` in `k8s_disable`, the default), kube-vip's cloud provider
gives each LoadBalancer service an address from the pool, and kube-vip announces it the same way: Traefik, which
k3s ships, gets one. Point DNS names at those addresses.

The holder's guest agent reports the VIP and the pool's addresses as that VM's own. The VMs page and the cluster
inventory leave out any `/32` address, the VIP and the pool, and the Servers page refuses them as a server's
address, so they are never taken for a VM's.

## 5. Upgrades

Set the new release in `k8s_version` (it must be in the catalog: the same release, a newer patch, or the next
minor), then:

```bash
make kubernetes-upgrade LIMIT=k8s_lab ARGS="--check --diff"
make kubernetes-upgrade LIMIT=k8s_lab
```

The run takes a lock in the cluster, then upgrades the servers one at a time and then the agents: each node
cordoned and drained, a server that holds the VIP first handing it to another, the new binary (checked against
the catalog), a restart, and the node uncordoned once it's Ready on the new release (and a server's etcd
healthy). Going back, or skipping a minor, is refused before anything changes: go through each minor's release
in turn. A run that stopped can be run again; the members already on the release are left alone. Details,
including what to do when a run left the lock: [upgrades](interfaces.md#upgrades).

Before a drain can finish, every pod on the node must be allowed to leave: a PodDisruptionBudget that allows no
eviction, or a pod no controller manages, stops the upgrade with that node cordoned. Fix the workload, then run
the upgrade again.

New k3s releases reach the catalog through USAS releases: the weekly catalog job opens an issue when one is out,
and `make k3s-catalog-check` checks every pin against upstream ([the k3s catalog](testing.md#the-k3s-catalog)).

## 6. Fleet updates

`make updates` treats the members of a cluster as such ([Kubernetes members](updates.md#kubernetes-members)):

- A member that needs a reboot is cordoned and drained first, and uncordoned once it's back and its health
  checks pass (`updates_k8s_drain`, on by default). A drain that doesn't finish is undone, and that server isn't
  rebooted.
- A wave never holds two servers of one cluster, nor a server and that cluster's agents, so etcd keeps its
  quorum and the server that drains an agent is up ([waves](updates.md#waves)).
- With `updates_k8s_wait_longhorn: true`, the next wave waits until every attached Longhorn volume is healthy
  again.

Package updates that need no reboot don't drain anything: k3s and its pods keep running through them. k3s itself
is never updated this way: its upgrades are `make kubernetes-upgrade`.

## 7. Handing the cluster over to Argo CD (optional)

With `k8s_bootstrap_argocd: true` and `k8s_bootstrap_repo_url` (an `https://` URL), the last step of
`make kubernetes` installs [Argo CD](https://argo-cd.readthedocs.io/) on the cluster and one root Application
that syncs the Applications in a folder of that repository. It does this once: from then on Argo CD manages
itself and everything else from Git, and later runs leave both alone. Argo CD's initial admin password is never
read or printed by USAS; how to read it, log in and delete it is in
[the handoff](interfaces.md#handing-a-cluster-over-to-argo-cd-optional). Whoever can merge to the branch the root
Application follows deploys to the cluster as its admin: protect that branch like the Vault.

## 8. In the web UI

The web UI does all of the above from its pages ([interfaces.md](interfaces.md#the-web-ui),
[web-ui.md](web-ui.md#security)):

- **Defaults → Kubernetes clusters:** a cluster's settings, and its two join tokens, write-only: typed into a
  password field, or made by the server and shown once ("Make a token and show it once"), for you to copy into
  your password manager. A saved token is never shown again.
- **Servers:** each server's cluster and role (server, agent or none).
- **Run:** `kubernetes.yml` for one cluster, to install or upgrade: a check run first, then the real run once,
  within 30 minutes and after a confirmation; an upgrade's confirmation is the version typed again. Each step is
  in the audit log.
- **VMs:** each member's cluster and role, a filter by cluster, and a warning when an update wouldn't drain a
  member. **Updates:** the drain and Longhorn settings.

The UI never shows a kubeconfig, never runs `kubectl` itself and never writes to a cluster except through those
runs. Groups and variables you set by hand in the inventory are kept when it saves.

## An existing cluster: fleet updates only

A cluster built by hand (or by an earlier tool) can join fleet updates without USAS ever installing k3s on it:
the drain, reboot, health check and uncordon rollout runs as above, one server per wave.

1. Put its members into the groups `k8s_<cluster>_servers` and `k8s_<cluster>_agents`. VMs this project
   installed are in `servers` already (by their `pve_vmid`); a cluster it didn't install goes under
   `other_servers`, groups included, each member with its own sudo
   ([updates.md](updates.md#servers-this-project-didnt-install)). Each member's inventory name must be its
   node's name in Kubernetes, or set `updates_k8s_node_name` for it.
2. **Leave its join tokens out of the Vault**, and don't set `k8s_version`. `make kubernetes` then stops in its
   checks before it changes anything, so it can't rewrite the cluster's `config.yaml` by mistake. Its settings
   would differ from the role's defaults (Secrets encryption, the agent token) and turning those on in a running
   cluster is k3s' own rotation, not a configuration change.
3. For members in `servers`, being in those groups also makes the baseline's `kubernetes_node` role apply at
   their next baseline run: per-member firewall rules beside the cluster's own, the sysctls, swap off (the
   baseline never runs on `other_servers`). Look first:

   ```bash
   make baseline LIMIT=k8s_lab ARGS="--check --diff"
   make updates-check LIMIT=k8s_lab
   make updates LIMIT=k8s_lab
   ```

Run `make kubernetes` against such a cluster only once `--check --diff` shows it would change nothing.

## Troubleshooting

**A server stops before joining, naming two hosts and a port.** Before a server joins, it must reach etcd's
2379 and 2380 and the API's 6443 on each server already in the cluster, and each of those its 2380. The message
names the pair that couldn't. Usually the baseline hasn't run on one of them since the member was added, so its
firewall has no rule for the other's address:

```bash
make baseline LIMIT=k8s_lab ARGS="--tags kubernetes"
ssh sysadmin@10.10.10.72 'sudo ufw show added | grep "usas-k8s lab"'     # firewalld: sudo firewall-cmd --list-rich-rules
```

If the rules are there, look between the two: the Proxmox firewall, a VLAN's ACL, a member with another
`k8s_node_ip` than the address it really has.

**SSH stops answering for about 30 seconds.** The baseline's UFW rate limit allows 6 new SSH connections from
one address in 30 seconds. Ansible reuses its connections, so the playbooks stay below it, but a loop of
separate `ssh` calls (a script that asks each node in turn) can hit it, and the next connections are dropped
until the 30 seconds are over. Reuse one connection (`ControlMaster`, `ControlPersist`), pace the calls, or put
your management address in `firewall_ssh_sources`. Fail2Ban bans only failed logins, not this.

**A reinstalled node won't join.** USAS never reinstalls a VM: you delete it and provision it again. Its new
installation has a new host key and no k3s data, while the cluster still knows the old node. On another server,
delete the old node first; k3s then removes its node password and, for a server, its etcd member:

```bash
ssh sysadmin@10.10.10.71 'sudo k3s kubectl delete node cp-2'
ssh sysadmin@10.10.10.71 'sudo k3s kubectl -n kube-system get secret cp-2.node-password.k3s'   # NotFound
ssh-keygen -R 10.10.10.72
make provision LIMIT=cp-2
make baseline LIMIT=k8s_lab
make kubernetes LIMIT=k8s_lab
```

The baseline runs on every member, not only the new one, in case the address changed. `make kubernetes` needs
all of a cluster's servers in the run; it joins the new server through the VIP, after the etcd port check, and
leaves the others as they are. If the old node's password Secret is still there, delete it too, or k3s refuses
the new node ("Node password rejected").

**An upgrade stopped and left the lock.** See [upgrades](interfaces.md#upgrades): check which node is cordoned,
then delete `kube-system/usas-upgrade-lock` and run the upgrade again.

**A member is refused with "another release".** It runs another k3s release than `k8s_version`. A run without
tags never changes a member's release: either set `k8s_version` to what the cluster runs, or upgrade with
`make kubernetes-upgrade`.

## What it won't do

- Run or deploy workloads, or hold manifests, Helm values or GitOps state: that's your GitOps repository's job
  (for example [homelab-kube](https://github.com/HoneyBearTech/homelab-kube)), or yours.
- Install Kubernetes distributions other than k3s.
- Expose the API, Argo CD or anything else to the internet: the API answers the members and `k8s_api_sources`.
- Configure Proxmox HA rules: placement is checked, and the HA rules are yours to add (the API token has no
  privilege for them).
- Copy the admin kubeconfig off the servers, or show any kubeconfig in the web UI.

What this means for security, and what it doesn't protect against, is in
[security.md](security.md#what-it-does-not-protect-against) and the [assurance case](assurance-case.md).
