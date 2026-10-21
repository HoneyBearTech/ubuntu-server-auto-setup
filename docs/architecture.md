# Architecture

ubuntu-server-auto-setup is a set of Ansible playbooks and roles run from one machine, the control node.
The playbooks have no server, daemon, database or agent of their own: each run reads the inventory and the
Vault, talks to Proxmox and the servers, and exits. The control node can be your own machine with a
checkout, or the project's container image, whose small web UI writes the inventory and the Vault and runs
the same playbooks ([below](#the-web-ui-and-the-container-image)). The security reasoning behind this
design is in the [assurance case](assurance-case.md); the interfaces it uses are listed in
[interfaces.md](interfaces.md).

## Components

```text
 control node (you)                      Proxmox VE node(s)                    new / existing servers
 ------------------                      ------------------                    ----------------------
 inventory/hosts.yml        SSH (root)   ISO storage                            Ubuntu 26.04 (or 24.04)
 Vault (encrypted)  ───────────────────▶ ubuntu-26.04.1-live-server-amd64.iso   admin user + GitHub keys
 ansible-playbook                        ...-autoinstall.iso (remastered)        sshd drop-in, UFW,
   │                                     seed-<host>.iso (temporary)             Fail2Ban, unattended
   │  HTTPS + API token                  VM <pve_vmid> (UEFI, q35, VirtIO)       upgrades, qemu-guest-agent
   ├────────────────────────────────────▶ Proxmox API                                 ▲
   │                                                                                  │ SSH (admin user, key)
   ├──────────────────────────────────────────────────────────────────────────────────┘
   │  HTTPS
   ├──▶ github.com/<github_user>.keys     (public SSH keys)
   └──▶ releases.ubuntu.com (from the Proxmox node: ISO + SHA256SUMS)
```

| Part | What it is | Where |
| --- | --- | --- |
| Inventory | Hosts, groups and variables: which Proxmox nodes, which servers, their IPs, VM IDs and sizes, and the baseline settings. | `inventory/hosts.yml` (gitignored; example in `inventory/hosts.example.yml`) |
| Vault | The admin password and the Proxmox API token, encrypted with Ansible Vault. | `inventory/group_vars/all/vault.yml` (gitignored; example in `inventory/vault.example.yml`) |
| Playbooks | `build-installer.yml`, `provision-vms.yml`, `baseline.yml`, and `site.yml` that imports all three. | `playbooks/` |
| `ubuntu_release` | Shared defaults only: the Ubuntu release, ISO names and URLs, and the ISO storage location. | `roles/ubuntu_release/` |
| `installer_iso` | Downloads and verifies the Ubuntu ISO on a Proxmox node and remasters it for hands-off installs. | `roles/installer_iso/` |
| `proxmox_vm` | Creates one VM per host through the Proxmox API, with a per-host seed ISO, and waits for the install. | `roles/proxmox_vm/` |
| `common` | Packages and upgrades, hostname, timezone, the admin user, sudo, GitHub keys, unattended upgrades, the guest agent. | `roles/common/` |
| `firewall` | UFW: default policies, SSH (rate-limited), extra allow rules. | `roles/firewall/` |
| `ssh_hardening` | The sshd drop-in, the optional port change and the switch from `ssh.socket`. | `roles/ssh_hardening/` |
| `fail2ban` | The `sshd` jail. | `roles/fail2ban/` |
| `Makefile` | Short commands for the playbooks (`make site`, `make provision`, ...), with `LIMIT=` and `ARGS=`. | `Makefile` |
| Web UI | Forms for the inventory and the Vault, the SSH key and host keys, and runs of the playbooks with a live log. | `webui/usas_web/` |
| Container image | ansible-core, the collections, the playbooks and roles, and the web UI. | `Dockerfile`, `docker-compose.yml`, `start.ps1` |

## How a server is built

### 1. The installer ISO (`build-installer.yml`, on `proxmox_nodes`, as root over SSH)

1. Install `xorriso` on the node.
2. Fetch `SHA256SUMS` from `releases.ubuntu.com` over HTTPS and pick the line for
   `ubuntu-26.04.1-live-server-amd64.iso`; stop if it isn't there (a newer point release has replaced it).
3. Download the ISO into the node's ISO storage, verifying that checksum (`get_url` with `checksum`).
4. If the remastered ISO doesn't exist yet (or `installer_iso_rebuild` is set): extract `boot/grub/grub.cfg`,
   add the `autoinstall` kernel argument to the installer entries and shorten the GRUB timeout, check that
   the argument is really there, and write a new ISO with `xorriso`, replaying the original boot records
   (BIOS and UEFI). Without the `autoinstall` argument, Ubuntu's installer stops and asks before it wipes a
   disk, even when it has an autoinstall configuration.

### 2. The VM (`provision-vms.yml`, on `ubuntu_servers`, from the control node)

For each host, after checking that the required variables are set:

1. List the cluster's VMs through the API; if `pve_vmid` exists anywhere, report it and **skip the host**.
2. Fetch the public keys from `https://github.com/<github_user>.keys`, add `admin_extra_ssh_keys`, keep
   only lines that look like public keys, and stop if none are left.
3. Hash the admin password with SHA-512 crypt on the control node.
4. On the node, write the autoinstall `user-data` and `meta-data` into a `0700` directory under
   `/var/tmp/ubuntu-server-auto-setup/` and pack them into `seed-<host>.iso` (volume label `CIDATA`, the
   cloud-init NoCloud convention), mode `0600`. The `user-data` sets the hostname, the static network
   configuration, the disk layout, the admin user with the password hash, the authorized keys, password SSH
   off, the packages (`qemu-guest-agent`), security updates during install, passwordless sudo for the admin
   user, and `shutdown: poweroff`.
5. Create the VM through the API (`community.proxmox.proxmox_kvm`): OVMF (UEFI) with an EFI disk, q35,
   VirtIO SCSI single with one disk, a VirtIO NIC on `pve_bridge` (optionally tagged), the guest agent
   enabled, the installer ISO and the seed ISO as CD-ROMs, and the boot order disk then installer.
6. Start the VM. The installer finds the seed ISO, installs without asking, and powers the VM off. The
   playbook polls the VM's status until it is stopped (up to `vm_install_timeout`).
7. Remove both CD-ROMs, set the boot order to the disk only, delete the seed ISO and its directory on the
   node, and start the VM.
8. Wait for SSH on port 22, replace the control node's `known_hosts` entry for the IP with the new host
   key (scanned once, trust on first use), and wait until Ansible can log in as the admin user (cloud-init
   finishes creating the user and keys on first boot).

### 3. The baseline (`baseline.yml`, on `ubuntu_servers`, as the admin user with sudo)

1. If `ssh_port` isn't 22, probe it; connect on whichever of `ssh_port` and 22 answers, so the same
   inventory works before and after the port change.
2. `common`: wait for cloud-init, run the upgrade (`apt_upgrade`), install base packages, set the hostname,
   `/etc/hosts` and timezone, create or update the admin user (the password only when the user is
   created), set passwordless sudo (or remove it), install the GitHub keys (exclusive by default, refusing
   to write an empty list), configure unattended upgrades, and reboot or report a pending reboot.
3. `firewall`: allow (rate-limit) SSH on `ssh_port` from `ufw_ssh_sources` and add `ufw_allow` rules
   **before** enabling UFW, so the running session survives; then set the default policies and enable it.
4. `ssh_hardening`: refuse to continue if `ssh_allow_users` would lock out the admin user; install
   `/etc/ssh/sshd_config.d/00-hardening.conf` (validated with `sshd -t`), and for a custom port enable
   `ssh.service` and disable `ssh.socket`; restart sshd; if the port changed, add a `known_hosts` entry for
   the new port from the host key read over the existing connection, reconnect on the new port, and only
   then remove the port 22 firewall rule.
5. `fail2ban`: write `/etc/fail2ban/jail.local` with the `sshd` jail on the systemd journal, on `ssh_port`,
   with incremental bans and `fail2ban_ignoreip`.

`site.yml` runs the three playbooks in that order. `make keys` runs only the `ssh_keys` tasks of the
baseline.

## The web UI and the container image

```text
 browser ──HTTP, 127.0.0.1:8080──▶ container "usas" (UID 10001)                 /data volume
 (sign-in, forms, live log)        ┌──────────────────────────────────────┐      ────────────
                                   │ uvicorn + FastAPI (usas_web)         │────▶ inventory/hosts.yml
                                   │   pages, sign-in, CSRF, host check   │      inventory/group_vars/all/
                                   │   schema: fields from roles/*/       │        vault.yml (Ansible Vault)
                                   │     defaults and vars                │      .vault-pass
                                   │   runner ──argv──▶ ansible-playbook ─┼──▶   ssh/ (key, known_hosts)
                                   │   ssh-keyscan, ssh (host key, CA)    │      pve-ca.pem, ca-bundle.pem
                                   │ /app: ansible.cfg, playbooks, roles, │      auth.json, runs/
                                   │       collections                    │
                                   └──────────────────────────────────────┘
                                     │ the same connections as the control node above:
                                     └─▶ Proxmox API (HTTPS), Proxmox node and servers (SSH), github.com
```

| Part | What it does | Where |
| --- | --- | --- |
| `schema.py` | Builds the form fields from the roles: each field's default and help text from `roles/<role>/defaults/main.yml`, its allowlist pattern or allowed values from `roles/<role>/vars/main.yml`. Fails at startup if a field names a variable or pattern no role has. | `webui/usas_web/schema.py` |
| `store.py` | Reads and writes `inventory/hosts.yml` (every typed value tagged `!unsafe`, so Ansible never templates it) and the Vault-encrypted `vault.yml`, atomically, mode `0600`. | `webui/usas_web/store.py` |
| `auth.py` | The password (argon2id), the first-run setup code, in-memory sessions, CSRF tokens, throttling of failed sign-ins. | `webui/usas_web/auth.py` |
| `runner.py` | Runs `ansible-playbook` with an argument list, one run at a time, writes the output (with the Vault's values masked) and the run's details to `runs/`. | `webui/usas_web/runner.py` |
| `sshkeys.py`, `remote.py` | The UI's key pair, scanning and trusting the node's host key, the cluster CA; the Proxmox connection test and the GitHub keys preview. | `webui/usas_web/` |
| `main.py`, `templates/`, `static/` | The pages (server-rendered HTML that works without JavaScript), the guard middleware (allowed hosts, sessions, Origin checks, security headers), the live log as server-sent events. | `webui/usas_web/` |

How a run works: the page posts the form; the runner checks the limit against the inventory's host and
group names and starts `ansible-playbook -i /data/inventory/hosts.yml --vault-password-file
/data/.vault-pass [--check] [--diff] [--limit=...] playbooks/<playbook>.yml` from `/app`, with
`ANSIBLE_CONFIG=/app/ansible.cfg` and, if a cluster CA was saved, `SSL_CERT_FILE` and
`REQUESTS_CA_BUNDLE` pointing at the bundle with that CA. The inventory points Ansible at the UI's key
(`ansible_ssh_private_key_file`) and its `known_hosts` (`StrictHostKeyChecking=yes`), and adds the UI's
public key to `admin_extra_ssh_keys`, so the baseline can log in to the servers it installed. The browser
follows the log through `/runs/<id>/stream`.

The image is built in two stages from `python:3.14-slim` (pinned by digest; Python 3.14 is the newest that ansible-core 2.20 supports); the runtime image carries no pip: the first installs the
hash-pinned `requirements-webui.txt` (wheels only) into a virtual environment and the collections from
`requirements.yml`; the second adds `openssh-client` and `tzdata`, the security updates published since the
base image, the playbooks, roles and web UI, and runs as UID 10001 with everything it writes in `/data`.
Release builds are multi-architecture (`linux/amd64`, `linux/arm64`).

## Key properties

- **Idempotent and safe to rerun.** Existing VMs are never touched by provisioning; the ISO is downloaded
  and built once; a second baseline run reports no changes.
- **Ordered to avoid lockouts.** The firewall opens the SSH port before it is enabled; the new SSH port is
  opened and verified before port 22 is closed; the key sync refuses to empty `authorized_keys`; the sshd
  drop-in is validated before it is installed.
- **Secrets stay on the control node and in Vault.** The password reaches the node only as a hash, in
  files readable by root only, deleted at the end of provisioning. Tasks that handle the token or the hash
  are hidden from the output (`pve_hide_secrets`, `no_log`).
- **No inbound network exposure during install.** The installer reads its configuration from a local
  CD-ROM image, not from a network datasource.
- **Works on servers it didn't install.** The baseline only assumes Ubuntu 24.04 or 26.04 and an account
  that can `sudo`.

## Technology

| Choice | Why |
| --- | --- |
| Ansible (ansible-core 2.20) | Agentless over SSH, idempotent modules, and Ansible Vault for the few secrets. No controller to run. |
| Ubuntu autoinstall (Subiquity) with a NoCloud seed ISO | Ubuntu's own unattended installer; a local seed needs no web server and exposes nothing on the network. |
| Remastering with `xorriso` | Adds one kernel argument while keeping Ubuntu's signed boot chain and boot records; no custom kernel or initrd. |
| `community.proxmox` and the Proxmox API with a token | A token with a narrow role instead of root credentials for VM management; the `uri` module covers what the collection doesn't (start, status, detach). |
| UFW, Fail2Ban, OpenSSH drop-ins, unattended-upgrades | The tools Ubuntu ships and documents, so the result is ordinary, inspectable Ubuntu configuration. |
| GitHub `.keys` endpoint | One place to manage the keys for every server; removing a key there removes it everywhere at the next sync. |
| FastAPI with server-rendered Jinja pages, a few lines of plain JavaScript | A small UI with no front-end build, no bundled third-party JavaScript, and a strict Content Security Policy; the same Python and Jinja as Ansible itself. |
| One container image with Docker volumes | Runs the same on Windows (Docker Desktop with WSL 2), Linux and macOS; Ansible doesn't run natively on Windows. |
