# Keeping servers updated

`playbooks/updates.yml` lists and installs package updates on servers that already run: the ones this
project installed (`ubuntu_servers`) and others you add to the inventory (`other_servers`). It never
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
2. **Updates, a batch at a time** (`updates_serial`, 25 % of the servers by default). It installs the
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
| `skipped` | Opted out, outside its window, no Python, an update needs a package you excluded, or not started after an earlier failure. The reason says which. |
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
  (`VM.GuestAgent.Audit`), otherwise its cloud-init `ipconfig0`.
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
  refuses to send the Vault admin password (`vault_admin_password`) to a server outside `ubuntu_servers`
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
and why. It ends with the services still running old code, the automatic-update line and the health
result. Package and service names come from the server, so they are checked against the role's patterns
and stripped of control characters, and each list stops at 5,000 entries. A hostile server can still lie
about its packages: the report says what the server claimed.

## Settings

Every setting, with its default, is in `roles/updates/defaults/main.yml` and
[interfaces.md](interfaces.md#inventory-variables). `updates_serial` is set with `-e` (it decides the
batches before any server's variables are read).
