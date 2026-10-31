# Keeping servers updated

`playbooks/updates.yml` lists and installs package updates on servers that already run: the ones this
project installed (`servers`; `ubuntu_servers` before 0.7.0, still read until 1.0) and others you add to the inventory (`other_servers`). It never
configures a server. It reads what is pending, installs it, reboots only when you ask it to, checks that
the server is healthy afterwards, and writes a report.

```bash
make updates-check                      # what each server would get; changes nothing
make updates LIMIT=web-01               # install on one server
make updates ARGS="-e updates_scope=security"
```

The playbook supports Ubuntu 22.04, 24.04 and 26.04. Debian and the RHEL family follow with the multi-OS
work. A server on another system or release is skipped with the reason (`unsupported`), never updated with
a generic fallback.

## What a run does

1. **Checks, on every server at once, changing nothing.** It checks the settings, the opt-out and the
   update window. Then it connects (on `ssh_port` if that answers, as the baseline does), checks that the
   server has Python, sudo and a supported release, and waits up to `updates_lock_wait` for automatic
   updates or another package manager to finish. It refreshes the package lists if they are older than
   `updates_refresh_max_age`, and asks apt what it would install. It also reads what the server's own
   automatic updates do and whether a reboot is already waiting. With `--check` the run stops here: this
   is the report-only mode.
2. **Updates, a wave at a time** ([Waves](#waves): at most `updates_serial` servers, 25 % by default, and
   never two of an update group or too many on one Proxmox node together). With
   `updates_snapshot: true` it first takes a Proxmox snapshot of each VM ([Snapshots](#snapshots)). It installs the
   chosen updates in the background on the server, so a restarted sshd or a dropped connection doesn't
   stop them. It then reboots if `updates_reboot: if_required` and the server needs it, and checks the
   server's health. When a server fails, no further server is started (`updates_max_fail_percentage: 0`).
3. **Reports** on the control node: one result per server, shown at the end and written to
   `updates_report_dir` (`reports/updates/<run id>.json` and `.txt` in the checkout by default, readable
   only by you). The run fails if a server failed or couldn't be reached. Busy, skipped and unsupported
   servers don't fail it.

Each server ends with one status:

| Status | Meaning |
| --- | --- |
| `updated` | Updates were installed, or the server was rebooted. |
| `nothing` | Nothing to install (held, phased and excluded packages are listed, but don't count). |
| `pending` | Check runs only: updates are waiting. |
| `busy` | Automatic updates or another package manager kept apt's lock past `updates_lock_wait`. Run again later. |
| `skipped` | Opted out, outside its window, no Python, an update needs a package you excluded, no snapshot when one is required, or not started after an earlier failure. The reason says which. |
| `unsupported` | A system or release the playbook doesn't update. |
| `unreachable` | No SSH connection. |
| `failed` | Something went wrong while updating, or the health check failed. The reason and the log above say what. |

## Choosing what to install

| Setting | Default | What it does |
| --- | --- | --- |
| `updates_scope` | `all` | `security` installs only updates from a security suite: Ubuntu's `-security` pockets and Ubuntu Pro's ESM. The server's own unattended-upgrades already installs those daily; a full run adds the rest. |
| `updates_apt_upgrade` | `safe` | `safe` works like `apt-get upgrade --with-new-pkgs`: new kernels and new dependencies come in, and nothing is removed (apt stops instead). `dist` installs everything and may remove packages, so it is refused together with `security`, excludes or `updates_kernel: false`. |
| `updates_exclude_packages` | `[]` | Shell globs (`linux-*`, `postgresql-*`) left alone in this run. Nothing is written to the server: no holds. If an update would need an excluded package, the server is skipped with the packages named, rather than upgraded anyway. |
| `updates_kernel` | `true` | `false` adds the kernel packages to the excludes. |
| `updates_restart_services` | `host` | `host` keeps the server's own needrestart setting (Ubuntu 24.04 and later restart services on their own). `list` only lists the services still running old code; `auto` restarts them. |

Packages you hold (`apt-mark hold`) stay held, and Ubuntu's phased updates stay phased. The report lists
both, apart from what is pending. Signature checks are never turned off: apt checks every package against
the server's own keyrings as always.

## Reboots

`updates_reboot: never` (the default) only reports "reboot pending" when a server needs one. With
`if_required` the playbook reboots a server that asks for it (`/var/run/reboot-required` on Ubuntu, or
needrestart reporting a newer kernel), waits up to `updates_reboot_timeout` for it to come back, and
checks its health. `updates_reboot_window` limits when that may happen. Outside it, the reboot waits and
the report says so.

**Encrypted VMs.** A VM whose disk unlocks with the TPM (`vm_disk_encryption`, or `tpm2-device` in
`/etc/crypttab`) is bound to its Secure Boot state, PCR 7. An update to `shim-signed`, the signed GRUB or
`secureboot-db` (the revocation list) changes that state, and the next boot would wait for the passphrase
at the console. After such an update the reboot is held, with the packages named. Reboot it from the
Proxmox console and re-enroll the TPM, as [security.md](security.md#secure-boot-and-disk-encryption)
("Recovery") describes.

## Health checks

After an update or a reboot, the server must:

- finish starting (`systemctl is-system-running --wait`, up to `updates_health_timeout`), and its state must
  be no worse than before (`running`, then `degraded`, then anything else), with no unit failed since
  before the update (the report names each one);
- still run every enabled service that ran before, except those matching `updates_health_ignore_services`.
  Services started on demand are left out, since they stop on their own;
- run each service in `updates_health_services`;
- answer each of `updates_health_urls`, called from the control node with the certificate checked:
  `[{url: "https://app.example.com/health", status: 200}]`.

A server that fails one of these is `failed`, and no further server is started.

## Windows and opting out

`updates_window`, per server or group, says when a server may be updated:

```yaml
updates_window: {days: [sun], start: "02:00", duration: "3h", timezone: "Europe/Berlin"}
```

The window opens at `start` on each listed day, in its time zone, and stays open for `duration`. On the
day clocks go forward, a start time that doesn't exist opens when the clocks jump past it (02:30 opens at
03:00); on the day they go back, the first of the two opens it. Outside its window a server is skipped, and a server isn't started once its
window has closed. Check runs ignore the window and say whether an apply would run.
`updates_ignore_window: true` overrides it for a run by hand. `updates_enabled: false` opts a server out
of every run.

## Automatic updates that are already running

The playbook doesn't fight them: it waits for them, and then reports the server as `busy`. It never stops,
masks or reconfigures their timers; on the servers this project installed, the baseline sets them up. The
report shows what each server does on its own as one line, such as "Automatic: security daily, no reboot".

## From the web UI

The web UI's **VMs** page runs this playbook on the VMs you select, after you pin their host keys: choose
**Updates**, then what to install, whether a VM may reboot, a snapshot, and whether to ignore the update
windows. A check run comes first and lists what each VM would install; its page then offers the real run
for 30 minutes, with a confirmation that names the VMs that may reboot
([web-ui.md](web-ui.md#walking-through-it)). A VM outside the inventory logs in as the user you give, with
passwordless sudo, and gets none of the Vault's secrets; its Proxmox tags `usas-group-<name>` and its HA
state reach the waves as with the cluster inventory. The **Run** page offers `updates-check` and `updates`
for the servers in the inventory. Either way the report goes to the web UI's data folder
(`/data/updates/reports`).

## Every VM on the cluster

The cluster inventory adds every VM of the Proxmox VE cluster to the run, whether you put it in the
inventory or not. Copy `inventory/pve.usas_pve.example.yml` to `inventory/pve.usas_pve.yml`.
`make updates-check` and `make updates` then use it; by hand, add `-i inventory/pve.usas_pve.yml` after your
inventory. It reads the API settings from your inventory (`pve_api_host`, `pve_api_token_id`, ...), so the file
can be just `plugin: usas_pve`, plus optional filters: `nodes`, `tags_any`, `tags_all`, `pools`,
`name_patterns` and `exclude` (VM IDs or names). A VM must pass all of them.

- **Your servers stay yours.** A VM whose ID is a server's `pve_vmid` is that server: same name, address,
  login and settings. It only gains what the cluster knows, such as the node it runs on now, its tags and
  the OS its guest reports.
- **Other VMs are `vm-<vmid>`,** in the group `usas_pve_vms`, never named after their Proxmox name or tags.
  Give that group its login in `inventory/group_vars/usas_pve_vms.yml` (as for `other_servers`: its own user,
  passwordless sudo or a sudo password of its own; never the admin password). A VM is reached at its
  `ansible_host` if you list `vm-<vmid>` in your inventory, otherwise at the address its guest agent reports
  (`VM.GuestAgent.Audit`), otherwise its cloud-init `ipconfig0`. If your inventory has a host named
  `vm-<vmid>` whose `pve_vmid` is another VM, that VM is left out with a warning, never given the host's
  variables.
- **Their host keys are pinned by VM ID,** as `usas-<cluster>-<vmid>`, never trusted on first sight: see
  [host-keys.md](host-keys.md#4-vms-you-didnt-create-the-updates-playbooks-cluster-inventory). Until you pin
  one, its report says so and the run fails for it.
- **Some VMs are reported, never contacted** (group `usas_pve_skipped`, with the reason): containers (LXC),
  templates, stopped or paused VMs (never started by this playbook), VMs Proxmox has locked (a backup,
  migration or snapshot: `busy`), Windows, VMs with no address, and VMs tagged **`usas-no-updates`**. That tag
  lets a VM's owner opt out; no tag ever opts a VM in or gives it credentials.
- **Groups for ordering only:** each VM is also in `pve_node_<node>`, `pve_tag_<tag>` and `pve_pool_<pool>`
  (lowercase, other characters as `_`).
- **A cluster that doesn't answer stops the run** rather than updating only part of it
  (`any_unparsed_is_failed` in `ansible.cfg`).

## Waves

Before updating, the run plans the waves (batches) the servers go in, on the control node and without
connecting to them. In inventory order, each server goes into the first wave that has room for it:

- **At most `updates_serial` servers** per wave: a number, or a percentage of the servers being updated
  (`25%` by default, at least one).
- **At most `updates_group_max_parallel` (1) servers of each update group.** Put servers that do the same
  job (two web servers behind a load balancer, the members of a database cluster) in one group, and they are
  never updated, or rebooted, at the same time. A server's groups are `updates_group` (a name or a list, per
  host or group in the inventory) and, for a VM on the cluster, each Proxmox tag `usas-group-<name>`.
- **At most `updates_node_max_parallel` (2) servers that may reboot** (`updates_reboot: if_required`) **on
  each Proxmox node**, so that one node doesn't boot many VMs at once. The node is the one the VM runs on
  now (with the [cluster inventory](#every-vm-on-the-cluster)), otherwise its `pve_node`; a server that isn't
  on a cluster has no node limit.

`0` turns off either limit. The run takes these three settings from the first server, so set them for all
servers (`group_vars/all`, or `-e`). A wave starts once the one before has finished, and when a server fails,
no later server starts (`updates_max_fail_percentage`). The report gives each server's wave ("wave 2 of 3"),
its groups, and whether Proxmox HA manages the VM: HA may start a VM elsewhere if a node fails while it is
rebooting. The playbook can't read the cluster's HA rules (that needs `Sys.Audit`, which the token doesn't
have), so put VMs that HA keeps apart in one update group too. A check run (`--check`) plans the waves as
well, so you can review them before an apply.

Tags only order the waves. Anyone who can change a VM's tags on the cluster can delay its update to a later
wave, but no tag changes how a server is reached, what is installed, or with which credentials.

## Snapshots

With `updates_snapshot: true`, each VM gets a Proxmox snapshot of its disks just before its updates are
installed, so a Proxmox admin can roll it back if the update breaks it. Off by default. A server with nothing to
install gets none.

- **Where:** on the node the VM runs on at that moment, found through the API (`/cluster/resources`), so a VM
  that HA or an admin has moved since the inventory was written is still found. The VM ID is the server's
  `pve_vmid`, or for a VM the cluster inventory found, its own.
- **What:** the disks only, not the memory (`vmstate` 0), named `usas_upd_<date>_<time>` (UTC) with the
  description "USAS updates run <run id>; safe to delete after <date>".
- **Clean-up:** at the start of each VM's update, this playbook's snapshots older than
  `updates_snapshot_max_age` days (7) are deleted. After a healthy update, all but the newest
  `updates_snapshot_keep` (1) are deleted; `0` deletes the new one too. **Only snapshots with both the
  `usas_upd_` name and that description are ever deleted.** Your own snapshots, even one you name
  `usas_upd_...`, are left alone. A VM whose health check fails keeps its snapshot, and the report says so.
- **When a VM can't have one:** Proxmox decides from the VM's disks. Raw files on directory, NFS or CIFS
  storage, thick LVM and passthrough disks don't support snapshots. A VM like that, or one that is locked
  (a backup running) or has no VM ID, is `skipped` with the reason. An error from Proxmox (a missing
  privilege, a failed task) fails the VM. With `updates_snapshot_required: false`, both are updated without
  a snapshot, and the report gives the reason.
- **Space:** a snapshot on thin storage (LVM-thin, ZFS, Ceph) grows with every write after it. The report
  warns when a storage that holds the VM's disks is more than `updates_snapshot_storage_warn` (85) % used.

It uses the API settings of your inventory (`pve_api_host`, `pve_api_user`, `pve_api_token_id`,
`pve_api_token_secret`) and needs one more privilege, `VM.Snapshot`, which the token's role doesn't have
unless you add it. See [installing.md](installing.md#2-create-the-proxmox-api-token), which also shows a role limited to
some VMs. Looking (the VM's node, lock, disks and storage use) needs only `VM.Audit` and `Datastore.Audit`,
which it already has.

### Rolling back

Rolling back is never done by this playbook, and its token has no privilege to do it (`VM.Snapshot.Rollback`).
A Proxmox admin does it, after reading the report:

1. In the Proxmox web UI, select the VM, open **Snapshots**, select the `usas_upd_...` snapshot the report
   names, and click **Rollback**. Or, on the VM's node: `qm rollback <vmid> usas_upd_<date>_<time>`.
2. Start the VM if it was running (a snapshot without memory rolls back to a stopped VM).
3. Run `make updates-check LIMIT=<server>`: the updates that broke it are pending again. Exclude the package
   that caused the problem (`updates_exclude_packages`) before the next run.

Know what a rollback means:

- **Everything written since is lost**: data, logs, and the security updates of that run.
- **An encrypted VM's TPM state rolls back with its disks** (`tpmstate0` is part of the snapshot), so the disk
  still unlocks with the TPM.
- **An HA-managed VM**: Proxmox may start it again on its own; set its HA state to `ignored` or `stopped`
  before rolling back, and back afterwards.
- **A snapshot isn't a backup.** It lives on the same storage as the VM's disks and goes with them. Keep your
  backups (Proxmox Backup Server, `vzdump`) as before.

## Notifications

Once per run, after the report, the control node can send its summary by mail and to a webhook. The summary
has the counts and one line per server, never package lists. Set these for all servers
(`inventory/group_vars/all`), since one run sends one message:

```yaml
updates_notify: on-change            # or always, or only-on-error
updates_notify_mail: ops@example.com # default: unattended_upgrades_mail
mail_relay_host: smtp.example.com    # the same relay settings as the servers' mail
mail_relay_port: 587
mail_relay_tls: starttls             # tls (implicit, usually 465) or "off"
mail_relay_from: usas@example.com
mail_relay_user: usas@example.com
mail_relay_password: "{{ vault_mail_relay_password }}"
updates_webhook_url: "{{ vault_fail2ban_webhook_url }}"   # or a URL of its own, from the Vault
updates_webhook_format: text         # text (ntfy) or json (Slack, Mattermost, Gotify, Discord)
```

- `on-change` notifies when an apply updated or rebooted a server, when a check found updates waiting, and
  when a server failed or couldn't be reached. `only-on-error` notifies only on those failures.
- The mail goes from the control node (your machine, or the container) straight to the relay, with
  STARTTLS or TLS and the relay's certificate checked against the CAs the control node trusts. A relay
  with a certificate from your own CA needs that CA trusted: add it to the system's store, or set
  `SSL_CERT_FILE` for the run (the web UI does this with the cluster CA bundle).
- The webhook is posted with its certificate checked; its URL stays out of the output.
- A notification that can't be sent fails the run, after the report is written. The servers' own mail
  (`mail_relay_enabled`, unattended-upgrades' reports) is separate and unchanged.

## Servers this project didn't install

Add them to the `other_servers` group, with their own login:

```yaml
    other_servers:
      vars:
        ansible_user: ops
        ansible_become_password: "{{ vault_become_password_other_servers }}"
      hosts:
        legacy-web:
          ansible_host: 10.10.10.90
```

- **Login.** Put the public key the playbooks log in with (yours, or the web UI's from its SSH key page) in that user's
  `authorized_keys`, and the server's host key in `known_hosts` after comparing its fingerprint on the
  console ([host-keys.md](host-keys.md)). The playbook checks host keys strictly and never trusts a new
  one on its own.
- **Sudo.** Use passwordless sudo, or a sudo password of the server's own, kept in the Vault. The playbook
  refuses to send the Vault admin password (`vault_admin_password`) to a server outside `servers` (or `ubuntu_servers`)
  with a `pve_vmid`: root on that server could read it. `updates_allow_shared_become_password: true`
  allows it for a host or group that you trust anyway. A sudo rule limited to apt is still root: package
  scripts run as root. A separate user such as `usas-updates` is for knowing who did what, not for
  limiting what the playbook can do.
- **Python** must be installed (Ubuntu Server has it); a server without it is skipped.
- **sudo-rs** (Ubuntu 26.04): with a sudo password, the server needs the classic sudo (`/usr/bin/sudo.ws`,
  from the `sudo` package), as the baseline does. With passwordless sudo, sudo-rs is fine.

## The report

Each server's result in `<run id>.json` holds its status and reason, and the OS from its facts. It also
lists the pending updates (name, versions, suites, whether each is a security update), the planned,
upgraded, held, excluded and deferred (phased or kept back) packages, and the reboot: needed, done or held,
and why. It ends with the services still running old code, the automatic-update line, the health
result, the snapshot (taken, removed or none and why, any snapshots deleted, a storage warning) and the
wave (its number, the number of waves, the server's update groups and whether HA manages it). Package and service names come from the server, so they are checked against the role's patterns
and stripped of control characters, and each list stops at 5,000 entries. A hostile server can still lie
about its packages: the report says what the server claimed.

## Settings

Every setting, with its default, is in `roles/updates/defaults/main.yml` and
[interfaces.md](interfaces.md#inventory-variables). The wave settings (`updates_serial`,
`updates_group_max_parallel`, `updates_node_max_parallel`) and the notification settings are read once per
run, from the first server: set them for all servers.
